"""ArtifactWriter — Markdown / 字幕 / Obsidian / 交叉引用写入"""

import json
import os
import re
import shutil
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import unquote
from src.domain.types import PipelineRequest
from src.infrastructure.transcription.subtitle_writer import write_srt, write_ass, write_timestamped_txt
from src.utils.system import _safe_dirname
from src.vault_writer import archive_to_obsidian
import logging

logger = logging.getLogger(__name__)


_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)")
_FALLBACK_KEYFRAME_LIMIT = 8


class ArtifactWriter:
    """将所有管线产物写入磁盘。"""

    @staticmethod
    def write(
        request: PipelineRequest,
        transcript: str,
        notes: str,
        segments: list[dict] | None,
        frames: list[dict] | None,
        insights: list | None = None,
        *,
        job_id: str | None = None,
    ) -> tuple[str, str]:
        """Write a self-contained output bundle using directory-level commit.

        New tasks use a deterministic per-job directory so two videos with the
        same title never overwrite each other::

            <output>/<safe-title>/run_<job-id-prefix>/

        Every file is first produced under a sibling staging directory.  Only
        after transcript, note, subtitles, frame copies, metadata and the run
        manifest all succeed is the whole directory swapped into place.  A
        failed re-export therefore leaves the previous valid bundle untouched,
        and a shorter re-export cannot retain stale frame files.
        """
        from datetime import datetime, timezone
        import uuid

        title = request.title or "untitled"
        title_dir = os.path.join(request.output_dir, _safe_dirname(title))
        layout = str(getattr(request.output, "artifact_layout", "versioned") or "versioned")
        if layout == "legacy":
            video_dir = title_dir
            run_key = job_id or "legacy"
            staging_parent = os.path.dirname(title_dir) or request.output_dir
        else:
            if job_id:
                run_key = f"run_{job_id.replace('-', '')[:12]}"
            else:
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                run_key = f"run_{stamp}_{uuid.uuid4().hex[:8]}"
            video_dir = os.path.join(title_dir, run_key)
            staging_parent = title_dir

        os.makedirs(staging_parent, exist_ok=True)
        staging_dir = os.path.join(
            staging_parent,
            f".{os.path.basename(video_dir)}.staging-{uuid.uuid4().hex}",
        )
        os.makedirs(staging_dir, exist_ok=False)

        # Normalize visual evidence before deciding which files belong in the
        # final bundle. Markdown references remain relative to ``frames/``.
        notes = ArtifactWriter._normalize_frame_refs(notes, frames)
        notes = ArtifactWriter._sanitize_visual_evidence(notes, frames, insights)
        # Deliberately do not append a duplicate Key Frames appendix.  Only
        # images explicitly referenced by the generated note are exported in
        # clean mode; full mode remains available for an archival bundle.
        notes = ArtifactWriter._append_frame_links(notes, frames, allow_fallback=False)

        export_mode = getattr(request.output, "export_mode", "clean")
        valid_names = {f["filename"] for f in (frames or []) if f.get("filename")}
        referenced = ArtifactWriter._referenced_frame_filenames(notes, valid_names)
        selected_names = valid_names if export_mode == "full" else referenced
        copied_count = 0

        safe_name = re.sub(r'[\/:*?"<>|]', '_', title)[:80] or "笔记"
        staged_transcript = os.path.join(staging_dir, "transcript.txt")
        staged_notes = os.path.join(staging_dir, f"{safe_name}.md")

        try:
            ArtifactWriter._atomic_write_text(staged_transcript, transcript)

            if selected_names:
                staged_frames = os.path.join(staging_dir, "frames")
                os.makedirs(staged_frames, exist_ok=False)
                by_name = {
                    str(item.get("filename")): item
                    for item in (frames or [])
                    if item.get("filename")
                }
                for fname in sorted(selected_names):
                    info = by_name.get(fname)
                    if info is None:
                        raise FileNotFoundError(f"帧元数据缺失: {fname}")
                    src = str(info.get("path") or "")
                    if not os.path.isfile(src):
                        raise FileNotFoundError(
                            f"帧文件不存在，拒绝生成含失效图片的笔记: {src}"
                        )
                    dst = os.path.join(staged_frames, fname)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    copied_count += 1

            keyframes: list[dict[str, Any]] = []
            for info in frames or []:
                fname = info.get("filename")
                if not fname or fname not in selected_names:
                    continue
                ts = info.get("timestamp_sec", 0)
                entry: dict[str, Any] = {
                    "filename": fname,
                    "timestamp": ts,
                    "timestamp_str": ArtifactWriter._seconds_to_hms(ts),
                    "referenced_in_note": fname in referenced,
                }
                for ins in insights or []:
                    image_path = getattr(ins, "image_path", "") or ""
                    if os.path.basename(str(image_path)) == fname:
                        entry["visual_summary"] = getattr(ins, "visual_summary", "")
                        entry["importance_score"] = getattr(ins, "importance_score", 0)
                        break
                keyframes.append(entry)

            if keyframes:
                ArtifactWriter._atomic_write_json(
                    os.path.join(staging_dir, "keyframes.json"),
                    {"video": title, "job_id": job_id, "frames": keyframes},
                )

            ArtifactWriter._atomic_write_text(staged_notes, notes)

            run_manifest = {
                "format_version": 2,
                "job_id": job_id,
                "run_key": run_key,
                "title": title,
                "source": getattr(request, "input", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "notes": os.path.basename(staged_notes),
                "transcript": os.path.basename(staged_transcript),
                "frames_count": copied_count,
                "export_mode": export_mode,
                "layout": layout,
            }
            ArtifactWriter._atomic_write_json(
                os.path.join(staging_dir, ".video-notes-run.json"), run_manifest
            )

            if request.subtitle_format != "none" and segments is not None:
                ArtifactWriter._write_subtitles(
                    request.subtitle_format, segments, staging_dir
                )

            # Commit the complete bundle in one directory swap.  The helper
            # restores the previous directory if the new rename fails.
            ArtifactWriter._atomic_replace_directory(staging_dir, video_dir)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        transcript_path = os.path.join(video_dir, "transcript.txt")
        notes_path = os.path.join(video_dir, f"{safe_name}.md")
        frames_dir = os.path.join(video_dir, "frames")

        # Update in-memory paths only after commit, so downstream provenance
        # never sees temporary staging locations.
        copied_paths: dict[str, str] = {}
        for info in frames or []:
            fname = info.get("filename")
            if fname in selected_names:
                final_path = os.path.join(frames_dir, str(fname))
                info["path"] = final_path
                copied_paths[str(fname)] = final_path
        if insights:
            for ins in insights:
                image_path = getattr(ins, "image_path", "") or ""
                final = copied_paths.get(os.path.basename(str(image_path)))
                if final:
                    ins.image_path = final

        logger.info("\U0001f4c4 转录文本已保存: %s", transcript_path)
        if selected_names:
            mode_label = (
                "完整导出模式"
                if export_mode == "full"
                else f"笔记引用了 {len(referenced)} 张"
            )
            logger.info(
                "\U0001f5bc\U0000fe0f  帧截图已保存: %d/%d 帧（%s）",
                copied_count,
                len(frames or []),
                mode_label,
            )
            logger.info(
                "\U0001f4cb 关键帧元数据已保存: %s",
                os.path.join(video_dir, "keyframes.json"),
            )
        logger.info("\U0001f4dd 笔记已保存: %s", notes_path)

        if request.vault_path is not None:
            archive_to_obsidian(notes_path, request.vault_path, title)

        return transcript_path, notes_path

    @staticmethod
    def _atomic_write_text(path: str, text: str) -> None:
        import uuid
        tmp = f"{path}.tmp-{uuid.uuid4().hex}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @staticmethod
    def _atomic_write_json(path: str, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        ArtifactWriter._atomic_write_text(path, payload + "\n")

    @staticmethod
    def _remove_path(path: str) -> None:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=False)
        elif os.path.lexists(path):
            os.remove(path)

    @staticmethod
    def _atomic_replace_directory(staging: str, target: str) -> None:
        """Swap a fully prepared directory into place with rollback support."""
        import uuid
        old = f"{target}.old-{uuid.uuid4().hex}"
        moved_old = False
        try:
            if os.path.lexists(target):
                os.replace(target, old)
                moved_old = True
            os.replace(staging, target)
        except Exception:
            if moved_old and not os.path.lexists(target) and os.path.lexists(old):
                os.replace(old, target)
            raise
        finally:
            if os.path.isdir(old):
                shutil.rmtree(old, ignore_errors=True)
            elif os.path.lexists(old):
                try:
                    os.remove(old)
                except OSError:
                    pass

    @staticmethod
    def _sanitize_visual_evidence(
        notes: str,
        frames: list[dict] | None,
        insights: list | None,
        *,
        max_per_chapter: int = 4,
        complex_max_per_chapter: int = 5,
        total_hard_limit: int = 28,
    ) -> str:
        """Normalize chapter visual evidence using only validated vision results.

        Final policy:
        - prefer 2–3 high-value, distinct images per chapter;
        - ordinary chapters may contain at most 4 images;
        - a clearly multi-step chapter may contain at most 5 images;
        - the whole note may never exceed ``total_hard_limit`` images;
        - sections without a validated image are removed completely;
        - descriptions come from successful ``FrameInsight`` results only.

        The preferred range is not a quota. A chapter may contain zero or one image
        when that is all the validated, non-duplicative evidence available.
        """
        if not notes:
            return notes

        # Remove bold wrappers around Markdown image syntax anywhere in the note.
        image_pattern = r"!\[[^\]]*\]\((?:<[^>]+>|[^)]+)\)"
        notes = re.sub(
            rf"(?:\*\*|__)\s*({image_pattern})\s*(?:\*\*|__)",
            r"\1",
            notes,
        )

        valid_frame_names = {
            str(frame.get("filename"))
            for frame in (frames or [])
            if isinstance(frame, dict) and frame.get("filename")
        }

        insight_by_name: dict[str, Any] = {}
        for insight in insights or []:
            image_path = getattr(insight, "image_path", "") or ""
            filename = os.path.basename(str(image_path))
            summary = str(getattr(insight, "visual_summary", "") or "").strip()
            why = str(getattr(insight, "visual_importance", "") or "").strip()
            try:
                score = float(getattr(insight, "importance_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0

            # A real file match and a non-empty vision description are required.
            if filename and filename in valid_frame_names and summary:
                insight_by_name[filename] = {
                    "summary": summary,
                    "why": why,
                    "score": score,
                }

        # Remove known hallucinated placeholder prose even when it appeared outside
        # a correctly formed visual-evidence block.
        placeholder_line = re.compile(
            r"(?mi)^\s*(?:[-*]\s*)?[（(]?(?:"
            r"该章节未提供具体视觉元素文件名|"
            r"未提供具体视觉元素文件名|"
            r"无具体图片文件名|"
            r"包含以下典型视觉内容|"
            r"该部分包含软件界面截图|"
            r"典型视觉内容|"
            r"本章无有效图片|"
            r"本节无有效图片|"
            r"无有效图片.*视觉证据"
            r").*?[）)]?\s*$\n?"
        )
        notes = placeholder_line.sub("", notes)

        section_re = re.compile(
            r"(?ms)^###\s*视觉证据(?:（[^）]*）|\([^)]*\))?\s*\n(?P<body>.*?)(?=^#{1,3}\s|\Z)"
        )
        chapter_re = re.compile(r"(?m)^##\s+(.+?)\s*$")
        chapter_matches = list(chapter_re.finditer(notes))

        def chapter_context(section_start: int) -> tuple[str, str]:
            current = None
            for chapter_match in chapter_matches:
                if chapter_match.start() >= section_start:
                    break
                current = chapter_match
            if current is None:
                return "", ""
            next_start = len(notes)
            for chapter_match in chapter_matches:
                if chapter_match.start() > current.start():
                    next_start = chapter_match.start()
                    break
            return current.group(1).strip(), notes[current.start():next_start]

        def normalized_summary(text: str) -> str:
            return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()

        def is_distinct(candidate: dict[str, Any], selected: list[dict[str, Any]]) -> bool:
            candidate_text = normalized_summary(candidate["summary"])
            if not candidate_text:
                return False
            for item in selected:
                existing = normalized_summary(item["summary"])
                if not existing:
                    continue
                if candidate_text in existing or existing in candidate_text:
                    return False
                if SequenceMatcher(None, candidate_text, existing).ratio() >= 0.90:
                    return False
            return True

        complex_heading_terms = (
            "流程", "工作流", "配置", "整合", "装配", "准备", "清理",
            "自动化", "模拟", "处理", "导入", "导出", "多角色", "多步骤",
        )
        procedural_terms = (
            "首先", "随后", "然后", "接着", "最后", "步骤", "设置", "配置",
            "启用", "选择", "添加", "创建", "导入", "导出", "调整", "安装",
            "处理", "烘焙", "重定向", "修正", "生成", "加载",
        )

        records: list[dict[str, Any]] = []
        for match in section_re.finditer(notes):
            body = match.group("body")
            candidates: list[dict[str, Any]] = []
            seen: set[str] = set()
            for order, image_match in enumerate(_MD_IMAGE_RE.finditer(body)):
                link = ArtifactWriter._clean_frame_link(image_match.group(2))
                filename = os.path.basename(link)
                insight = insight_by_name.get(filename)
                if insight is None or filename in seen:
                    continue
                seen.add(filename)
                candidates.append({
                    "filename": filename,
                    "alt": image_match.group(1).strip(),
                    "order": order,
                    **insight,
                })

            heading, context = chapter_context(match.start())
            ranked = sorted(candidates, key=lambda item: (-item["score"], item["order"]))
            diverse_ranked: list[dict[str, Any]] = []
            for candidate in ranked:
                if is_distinct(candidate, diverse_ranked):
                    diverse_ranked.append(candidate)

            procedure_hits = sum(1 for term in procedural_terms if term in context)
            is_complex = (
                len(diverse_ranked) >= 5
                and (
                    any(term in heading for term in complex_heading_terms)
                    or procedure_hits >= 4
                )
            )
            limit = max(1, int(max_per_chapter))
            if is_complex:
                limit = max(limit, int(complex_max_per_chapter))

            selected = diverse_ranked[:limit]
            selected.sort(key=lambda item: item["order"])
            records.append({
                "match": match,
                "selected": selected,
                "is_complex": is_complex,
            })

        # Enforce a document-wide hard ceiling while preserving at least two images
        # in each populated chapter whenever the ceiling permits it.
        hard_limit = max(1, int(total_hard_limit))
        total_selected = sum(len(record["selected"]) for record in records)
        while total_selected > hard_limit:
            removable: list[tuple[float, int, int]] = []
            for record_index, record in enumerate(records):
                selected = record["selected"]
                if len(selected) > 2:
                    for item_index, item in enumerate(selected):
                        removable.append((item["score"], record_index, item_index))
            if not removable:
                for record_index, record in enumerate(records):
                    selected = record["selected"]
                    if len(selected) > 1:
                        for item_index, item in enumerate(selected):
                            removable.append((item["score"], record_index, item_index))
            if not removable:
                break
            _, record_index, item_index = min(removable, key=lambda item: item[0])
            del records[record_index]["selected"][item_index]
            total_selected -= 1

        # Replace sections from the end so earlier match offsets remain valid.
        for record in reversed(records):
            match = record["match"]
            selected = record["selected"]
            if not selected:
                replacement = ""
            else:
                blocks: list[str] = []
                for item in selected:
                    image = ArtifactWriter._markdown_frame_link(
                        item["filename"],
                        item["alt"] or None,
                    )
                    block = [
                        image,
                        f'**展示了什么：** {item["summary"]}',
                    ]
                    if item["why"]:
                        block.append(f'**为什么重要：** {item["why"]}')
                    blocks.append("\n\n".join(block))
                replacement = "### 视觉证据\n\n" + "\n\n".join(blocks) + "\n\n"
            notes = notes[:match.start()] + replacement + notes[match.end():]

        # Remove extracted-frame links not backed by a successful FrameInsight.
        # Ordinary local/remote Markdown images remain untouched.
        def remove_unvalidated_frame(match: re.Match) -> str:
            clean = ArtifactWriter._clean_frame_link(match.group(2))
            if clean.startswith("frames/"):
                filename = os.path.basename(clean)
                if filename not in insight_by_name:
                    return ""
            return match.group(0)

        notes = _MD_IMAGE_RE.sub(remove_unvalidated_frame, notes)
        notes = re.sub(r"\n{4,}", "\n\n\n", notes)
        return notes.strip() + "\n"

    @staticmethod
    def _append_frame_links(
        notes: str,
        frames: list[dict] | None,
        *,
        allow_fallback: bool = True,
    ) -> str:
        """仅在正文完全没有有效帧引用时，追加少量关键帧作为降级展示。

        降级策略：正文已有至少一张有效图片引用时不追加，
        避免章节内"视觉证据"和文末无说明的 Key Frames 重复。
        """
        if not allow_fallback or not frames:
            return notes

        valid_frames = [
            f for f in frames
            if isinstance(f, dict) and f.get("filename")
        ]
        if not valid_frames:
            return notes

        frame_filenames = {
            str(f["filename"])
            for f in valid_frames
        }

        referenced = ArtifactWriter._referenced_frame_filenames(
            notes,
            frame_filenames,
        )

        # 正文已有有效帧引用，不追加未使用帧。
        if referenced:
            return notes

        # 防止已有关键帧章节时再次生成。
        if re.search(
            r"(?mi)^##\s*(?:Key Frames|关键帧)\s*$",
            notes,
        ):
            return notes

        links: list[str] = []

        for frame in valid_frames[:_FALLBACK_KEYFRAME_LIMIT]:
            filename = str(frame["filename"])
            alt_text = os.path.splitext(os.path.basename(filename))[0]

            links.append(
                ArtifactWriter._markdown_frame_link(
                    filename,
                    alt_text,
                )
            )

        if not links:
            return notes

        return (
            notes.rstrip()
            + "\n\n## 关键帧\n\n"
            + "\n\n".join(links)
            + "\n"
        )

    @staticmethod
    def _normalize_frame_refs(notes: str, frames: list[dict] | None) -> str:
        if not frames:
            return notes

        filenames = [
            f_info.get("filename")
            for f_info in frames
            if f_info.get("filename")
        ]
        if not filenames:
            return notes

        used: set[str] = set()

        def resolve(link: str, allow_fallback: bool = True) -> str | None:
            clean = ArtifactWriter._clean_frame_link(link)
            basename = os.path.basename(clean)
            if basename in filenames:
                used.add(basename)
                return basename

            ordinal = ArtifactWriter._extract_ref_ordinal(basename)
            if ordinal is not None and 0 <= ordinal < len(filenames):
                filename = filenames[ordinal]
                used.add(filename)
                return filename

            if not allow_fallback:
                return None

            for filename in filenames:
                if filename not in used:
                    used.add(filename)
                    return filename
            return None

        def replace_markdown(match: re.Match) -> str:
            alt = match.group(1).strip()
            link = match.group(2)
            clean = ArtifactWriter._clean_frame_link(link)
            filename = resolve(link, allow_fallback=False)
            is_external = clean.startswith(("http://", "https://"))
            if filename is None and not is_external and not clean.startswith("frames/"):
                candidate = f"{alt} {clean}"
                if ArtifactWriter._looks_like_frame_placeholder(candidate):
                    filename = resolve(link)
            if filename is None:
                # Preserve normal Markdown images that are unrelated to extracted
                # video frames (for example assets/diagram.png). Only unresolved
                # frame placeholders or broken frames/... links are removed.
                if is_external or (
                    not clean.startswith("frames/")
                    and not ArtifactWriter._looks_like_frame_placeholder(f"{alt} {clean}")
                ):
                    return match.group(0)
                return ""
            return ArtifactWriter._markdown_frame_link(filename, alt)

        def replace_wikilink(match: re.Match) -> str:
            filename = resolve(match.group(1))
            if filename is None:
                return ""
            return ArtifactWriter._markdown_frame_link(filename)

        notes = _MD_IMAGE_RE.sub(replace_markdown, notes)
        return re.sub(r"!\[\[([^\]]+)\]\]", replace_wikilink, notes)

    @staticmethod
    def _markdown_frame_link(filename: str, alt: str | None = None) -> str:
        alt_text = alt or os.path.splitext(filename)[0]
        return f"![{alt_text}](<frames/{filename}>)"

    @staticmethod
    def _extract_ref_ordinal(filename: str) -> int | None:
        stem = os.path.splitext(filename)[0]
        match = re.search(r"(?:^|[-_])0*([1-9]\d*)$", stem)
        if not match:
            return None
        return int(match.group(1)) - 1

    @staticmethod
    def _clean_frame_link(link: str) -> str:
        link = link.strip()
        if link.startswith("<") and link.endswith(">"):
            link = link[1:-1].strip()
        link = link.split("|", 1)[0]
        link = link.split("#", 1)[0].split("?", 1)[0]
        return unquote(link).replace("\\", "/").strip()

    @staticmethod
    def _looks_like_frame_placeholder(value: str) -> bool:
        pattern = r"frame|screenshot|snapshot|keyframe|截图|图片"
        return re.search(pattern, value, re.IGNORECASE) is not None

    @staticmethod
    def _has_existing_frame_ref(notes: str, frame_filenames: set[str]) -> bool:
        return bool(ArtifactWriter._referenced_frame_filenames(notes, frame_filenames))

    @staticmethod
    def _referenced_frame_filenames(notes: str, frame_filenames: set[str]) -> set[str]:
        if not frame_filenames:
            return set()

        referenced = set()
        for match in _MD_IMAGE_RE.finditer(notes):
            link = ArtifactWriter._clean_frame_link(match.group(2))
            if link.startswith("frames/") and os.path.basename(link) in frame_filenames:
                referenced.add(os.path.basename(link))

        for match in re.finditer(r"!\[\[([^\]]+)\]\]", notes):
            link = ArtifactWriter._clean_frame_link(match.group(1))
            if os.path.basename(link) in frame_filenames:
                referenced.add(os.path.basename(link))

        return referenced

    @staticmethod
    def _seconds_to_hms(seconds: float) -> str:
        """Convert seconds to HH:MM:SS format."""
        h, r = divmod(int(seconds), 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @staticmethod
    def _write_subtitles(fmt: str, segments: list[dict], video_dir: str) -> None:
        """写入字幕文件。"""
        ext_map = {"srt": ".srt", "ass": ".ass", "txt": "_timestamped.txt"}
        writer_map = {"srt": write_srt, "ass": write_ass, "txt": write_timestamped_txt}

        ext = ext_map.get(fmt, "." + fmt)
        writer = writer_map.get(fmt)
        if writer is not None:
            path = os.path.join(video_dir, f"transcript{ext}")
            writer(segments, path)
            logger.info(f"\U0001f3ac 字幕已保存: {path}")