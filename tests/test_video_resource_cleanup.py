import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


try:
    __import__("openai")
except ImportError:
    openai_stub = types.ModuleType("openai")

    class _OpenAIError(Exception):
        status_code = None

    openai_stub.OpenAI = object
    openai_stub.AuthenticationError = _OpenAIError
    openai_stub.APITimeoutError = _OpenAIError
    openai_stub.APIStatusError = _OpenAIError
    openai_stub.APIConnectionError = _OpenAIError
    sys.modules["openai"] = openai_stub

if "ctranslate2" not in sys.modules:
    ctranslate2_stub = types.ModuleType("ctranslate2")
    ctranslate2_stub.get_cuda_device_count = lambda: 0
    sys.modules["ctranslate2"] = ctranslate2_stub

if "faster_whisper" not in sys.modules:
    faster_whisper_stub = types.ModuleType("faster_whisper")
    faster_whisper_stub.WhisperModel = object
    sys.modules["faster_whisper"] = faster_whisper_stub

if "yt_dlp" not in sys.modules:
    yt_dlp_stub = types.ModuleType("yt_dlp")

    class _YoutubeDL:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def download(self, _urls):
            return None

    yt_dlp_stub.YoutubeDL = _YoutubeDL
    sys.modules["yt_dlp"] = yt_dlp_stub

if "PIL" not in sys.modules:
    pil_stub = types.ModuleType("PIL")
    image_stub = types.ModuleType("PIL.Image")
    image_stub.Image = object
    image_stub.open = lambda *_args, **_kwargs: object()
    pil_stub.Image = image_stub
    sys.modules["PIL"] = pil_stub
    sys.modules["PIL.Image"] = image_stub



import pytest

class VideoResourceCleanupTests(unittest.TestCase):
    @pytest.mark.core
    def test_archive_to_obsidian_copies_referenced_frames(self):
        from src.vault_writer import archive_to_obsidian

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            note = output_note_dir / "Video.md"
            note.write_text(
                "# Video\n\n![example](frames/frame_0001.jpg)\n",
                encoding="utf-8",
            )
            (frames_dir / "frame_0001.jpg").write_bytes(b"image")

            with patch("builtins.print"):
                self.assertTrue(archive_to_obsidian(str(note), str(vault), "Video"))

            self.assertTrue(
                (vault / "video-notes" / "frames" / "frame_0001.jpg").is_file()
            )

    @pytest.mark.core
    def test_archive_to_obsidian_rewrites_frame_links_with_spaces_and_percent(self):
        from src.vault_writer import archive_to_obsidian

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            frame_name = "frame_Holopix AI_效率提升200%_0029.jpg"
            note = output_note_dir / "Video.md"
            note.write_text(
                f"# Video\n\n![图片描述](frames/{frame_name.replace(' ', '%20')})\n",
                encoding="utf-8",
            )
            (frames_dir / frame_name).write_bytes(b"image")

            with patch("builtins.print"):
                self.assertTrue(archive_to_obsidian(str(note), str(vault), "Video"))

            archived = vault / "video-notes" / "Video.md"
            content = archived.read_text(encoding="utf-8")

            self.assertIn(f"![图片描述](<frames/{frame_name}>)", content)
            self.assertTrue((vault / "video-notes" / "frames" / frame_name).is_file())

    @pytest.mark.core
    def test_archive_to_obsidian_skips_frames_when_note_has_no_image_links(self):
        from src.vault_writer import archive_to_obsidian

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            note = output_note_dir / "Video.md"
            note.write_text("# Video\n\nNo explicit image links.\n", encoding="utf-8")
            (frames_dir / "frame_0001.jpg").write_bytes(b"image")

            with patch("builtins.print"):
                self.assertTrue(archive_to_obsidian(str(note), str(vault), "Video"))

            self.assertFalse((vault / "video-notes" / "frames").exists())

    @pytest.mark.core
    def test_archive_to_obsidian_copies_only_referenced_frames(self):
        from src.vault_writer import archive_to_obsidian

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            note = output_note_dir / "Video.md"
            note.write_text(
                "# Video\n\n![example](frames/frame_0001.jpg)\n",
                encoding="utf-8",
            )
            (frames_dir / "frame_0001.jpg").write_bytes(b"image")
            (frames_dir / "frame_0002.jpg").write_bytes(b"unused")

            with patch("builtins.print"):
                self.assertTrue(archive_to_obsidian(str(note), str(vault), "Video"))

            archived_frames = vault / "video-notes" / "frames"
            self.assertTrue((archived_frames / "frame_0001.jpg").is_file())
            self.assertFalse((archived_frames / "frame_0002.jpg").exists())

    @pytest.mark.core
    def test_append_frame_links_ignores_unresolved_frame_placeholder(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "### 视觉证据\n\n![[frame_filename]]\n"
        frames = [{
            "filename": "frame_0001.jpg",
            "ocr_text": "",
            "analysis": "Nuke 节点图全景",
        }]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertIn("![[frame_filename]]", result)
        self.assertIn("![frame_0001](<frames/frame_0001.jpg>)", result)

    @pytest.mark.core
    def test_append_frame_links_uses_angle_paths_for_special_filenames(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# Video\n\nContent"
        frames = [
            {
                "filename": "frame_Houdini Terrain_效率提升200%_0029.jpg",
                "timestamp_sec": 12.0,
                "ocr_text": "HeightField Noise",
                "analysis": "",
            }
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertIn(
            "![frame_Houdini Terrain_效率提升200%_0029](<frames/frame_Houdini Terrain_效率提升200%_0029.jpg>)",
            result,
        )

    def test_append_frame_links_includes_plain_extracted_frames(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# Video\n\nContent"
        frames = [
            {
                "filename": "frame_reed-30s_0001.jpg",
                "timestamp_sec": 10.0,
                "ocr_text": "",
                "analysis": "",
            }
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertIn(
            "![frame_reed-30s_0001](<frames/frame_reed-30s_0001.jpg>)",
            result,
        )

    def test_does_not_append_unreferenced_frames_when_body_has_visual_evidence(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = (
            "# 视频笔记\n\n"
            "### 视觉证据\n\n"
            "![画面](<frames/frame_0001.jpg>)\n"
        )
        frames = [
            {"filename": "frame_0001.jpg"},
            {"filename": "frame_0002.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)
        self.assertNotIn("frame_0002.jpg", result)
        self.assertNotIn("## Key Frames", result)
        self.assertNotIn("## 关键帧", result)

    @pytest.mark.core
    def test_append_frame_links_skips_when_actual_wikilink_exists(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "### 视觉证据\n\n![[frame_0001.jpg]]\n"
        frames = [{
            "filename": "frame_0001.jpg",
            "ocr_text": "",
            "analysis": "Nuke 节点图全景",
        }]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)

    @pytest.mark.core
    def test_append_frame_links_skips_existing_angle_link_with_parentheses(self):
        from src.application.services.artifact_writer import ArtifactWriter

        frame_name = "frame_Demo (Part 1)_0001.jpg"
        notes = f"# Video\n\n![frame](<frames/{frame_name}>)\n"
        frames = [{
            "filename": frame_name,
            "ocr_text": "Demo",
            "analysis": "",
        }]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)
        self.assertNotIn("## Key Frames", result)

    @pytest.mark.core
    def test_normalize_frame_refs_rewrites_guessed_wikilinks_to_real_paths(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "### Visuals\n\n![[BV1xx-01.jpg]]\n\n![[BV1xx-02.jpg]]\n"
        frames = [
            {"filename": "frame_real_0001.jpg"},
            {"filename": "frame_real_0002.jpg"},
        ]

        result = ArtifactWriter._normalize_frame_refs(notes, frames)

        self.assertNotIn("![[BV1xx-01.jpg]]", result)
        self.assertIn("![frame_real_0001](<frames/frame_real_0001.jpg>)", result)
        self.assertIn("![frame_real_0002](<frames/frame_real_0002.jpg>)", result)

    @pytest.mark.core
    def test_normalize_frame_refs_preserves_non_frame_markdown_images(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = (
            "# Video\n\n"
            "![diagram](assets/diagram.png)\n\n"
            "![remote](https://example.com/a.png)\n\n"
            "![frame](frames/frame_0001.jpg)\n"
        )
        frames = [{"filename": "frame_0001.jpg"}]

        result = ArtifactWriter._normalize_frame_refs(notes, frames)

        self.assertIn("![diagram](assets/diagram.png)", result)
        self.assertIn("![remote](https://example.com/a.png)", result)
        self.assertIn("![frame](<frames/frame_0001.jpg>)", result)

    @pytest.mark.core
    def test_normalize_frame_refs_preserves_angle_link_with_parentheses(self):
        from src.application.services.artifact_writer import ArtifactWriter

        frame_name = "frame_Demo (Part 1)_0001.jpg"
        link = f"![frame](<frames/{frame_name}>)"
        notes = f"# Video\n\n{link}\n"
        frames = [{"filename": frame_name}]

        result = ArtifactWriter._normalize_frame_refs(notes, frames)

        self.assertIn(link, result)
        self.assertNotIn(">)_0001.jpg>)", result)

    @pytest.mark.core
    def test_cleanup_temp_removes_temp_dir_keeps_artifacts(self):
        from src.application.services.cleanup_manager import CleanupManager

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = CleanupManager.create_job_dir(tmp)
            (Path(job_dir) / "temp" / "audio.wav").write_bytes(b"audio")
            (Path(job_dir) / "artifacts" / "notes.md").write_text("# notes")

            CleanupManager.cleanup_temp(job_dir)

            self.assertFalse((Path(job_dir) / "temp").exists())
            self.assertTrue((Path(job_dir) / "artifacts" / "notes.md").is_file())

    @pytest.mark.core
    def test_cleanup_job_removes_entire_job_dir(self):
        from src.application.services.cleanup_manager import CleanupManager

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = CleanupManager.create_job_dir(tmp)
            (Path(job_dir) / "temp" / "audio.wav").write_bytes(b"audio")
            (Path(job_dir) / "artifacts" / "notes.md").write_text("# notes")

            CleanupManager.cleanup_job(job_dir)

            self.assertFalse(Path(job_dir).exists())

    @pytest.mark.core
    def test_cleanup_job_refuses_non_job_directory(self):
        from src.application.services.cleanup_manager import CleanupManager

        with tempfile.TemporaryDirectory() as tmp:
            result = CleanupManager.cleanup_job(tmp)
            self.assertFalse(result)
            self.assertTrue(Path(tmp).exists())

    @pytest.mark.core

    # ── _append_frame_links 新规则测试 ──────────────────────────────

    def test_appends_fallback_keyframes_when_body_has_no_frame_refs(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n\n正文内容\n"
        frames = [
            {"filename": "frame_0001.jpg"},
            {"filename": "frame_0002.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertIn("## 关键帧", result)
        self.assertIn("frames/frame_0001.jpg", result)
        self.assertIn("frames/frame_0002.jpg", result)

    def test_fallback_keyframes_are_limited_to_eight(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n\n正文内容\n"
        frames = [
            {"filename": f"frame_{index:04d}.jpg"}
            for index in range(20)
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result.count("](<frames/"), 8)
        self.assertIn("frame_0007.jpg", result)
        self.assertNotIn("frame_0008.jpg", result)

    def test_does_not_duplicate_existing_keyframes_section(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n\n## Key Frames\n\n现有内容\n"
        frames = [
            {"filename": "frame_0001.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)
        self.assertEqual(result.count("## Key Frames"), 1)

    def test_does_not_duplicate_existing_chinese_keyframes_section(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n\n## 关键帧\n\n现有内容\n"
        frames = [
            {"filename": "frame_0001.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)
        self.assertEqual(result.count("## 关键帧"), 1)

    def test_returns_unchanged_when_frames_are_empty(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n"
        self.assertEqual(ArtifactWriter._append_frame_links(notes, None), notes)
        self.assertEqual(ArtifactWriter._append_frame_links(notes, []), notes)

    def test_ignores_frames_without_filename(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频笔记\n"
        frames = [
            {},
            {"filename": ""},
            {"path": "frame.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)

    def test_chapter_visual_evidence_prevents_keyframes_appendix(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = (
            "# 章节笔记\n"
            "\n"
            "## 1. 第一章\n"
            "\n"
            "### 讲解\n"
            "\n"
            "正文。\n"
            "\n"
            "### 视觉证据\n"
            "\n"
            "![界面](<frames/frame_0001.jpg>)\n"
            "\n"
            "**展示了什么：** 某个设置界面。\n"
            "\n"
            "**为什么重要：** 这是关键操作。\n"
            "\n"
            "## 2. 第二章\n"
            "\n"
            "### 视觉证据\n"
            "\n"
            "![时间轴](<frames/frame_0002.jpg>)\n"
            "\n"
            "**展示了什么：** 动画时间轴。\n"
        )
        frames = [
            {"filename": "frame_0001.jpg"},
            {"filename": "frame_0002.jpg"},
            {"filename": "frame_0003.jpg"},
            {"filename": "frame_0004.jpg"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertEqual(result, notes)
        self.assertEqual(result.count("frame_0001.jpg"), 1)
        self.assertEqual(result.count("frame_0002.jpg"), 1)
        self.assertNotIn("frame_0003.jpg", result)
        self.assertNotIn("frame_0004.jpg", result)
        self.assertNotIn("## Key Frames", result)
        self.assertNotIn("## 关键帧", result)


class ManagedMediaWorkspaceTests(unittest.TestCase):
    """Generated MP4/WAV files must stay inside the current job workspace."""

    @pytest.mark.core
    def test_resolve_media_stage_passes_current_job_dir(self):
        from src.application.pipeline.context import ProcessingContext
        from src.application.pipeline.stages.resolve_media import ResolveMediaStage
        from src.domain.types import PipelineRequest
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / ".jobs" / "job-1"
            (job_dir / "artifacts").mkdir(parents=True)
            (job_dir / "temp").mkdir()
            audio_path = job_dir / "temp" / "audio.wav"
            audio_path.write_bytes(b"audio")

            request = PipelineRequest(input="https://example.com/video", output_dir=tmp)
            ctx = ProcessingContext(
                request=request,
                job_dir=str(job_dir),
                job_id="job-1",
            )
            resolver = MagicMock()
            resolver.resolve.return_value = (str(audio_path), None, [])

            ResolveMediaStage(media_resolver=resolver).run(ctx, {})

            resolver.resolve.assert_called_once_with(request, job_dir=str(job_dir))

    @pytest.mark.core
    def test_url_media_is_created_under_current_job_temp(self):
        from src.application.services.media_resolver import MediaResolver
        from src.domain.types import PipelineRequest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".jobs" / "job-1"
            (job_dir / "artifacts").mkdir(parents=True)
            (job_dir / "temp").mkdir()
            download_dir = job_dir / "temp" / ".dl_tmp" / "download-1"
            video_path = download_dir / "source.mp4"
            audio_path = download_dir / "source.wav"

            def fake_download(_url, output_dir):
                self.assertEqual(Path(output_dir), job_dir / "temp")
                download_dir.mkdir(parents=True)
                video_path.write_bytes(b"video")
                return str(video_path)

            def fake_extract(source, output_dir=None):
                self.assertEqual(Path(source), video_path)
                self.assertEqual(Path(output_dir), download_dir)
                audio_path.write_bytes(b"audio")
                return str(audio_path)

            request = PipelineRequest(
                input="https://example.com/video",
                output_dir=str(root),
                vision_enabled=True,
            )

            with patch("src.application.services.media_resolver.check_ytdlp", return_value=True), \
                 patch("src.application.services.media_resolver.check_ffmpeg", return_value=True), \
                 patch("src.application.services.media_resolver.download_video", side_effect=fake_download), \
                 patch("src.application.services.media_resolver.extract_audio", side_effect=fake_extract):
                audio, video, owned = MediaResolver.resolve(request, job_dir=str(job_dir))

            self.assertEqual(Path(audio), audio_path)
            self.assertEqual(Path(video), video_path)
            self.assertEqual(owned, [])
            self.assertFalse(any(root.glob("*.mp4")))
            self.assertFalse(any(root.glob("*.wav")))

    @pytest.mark.core
    def test_local_input_is_preserved_and_audio_uses_job_temp(self):
        from src.application.services.media_resolver import MediaResolver
        from src.domain.types import PipelineRequest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            output_dir = root / "output"
            source_dir.mkdir()
            output_dir.mkdir()
            source = source_dir / "user-video.mp4"
            source.write_bytes(b"important")
            job_dir = output_dir / ".jobs" / "job-2"
            (job_dir / "artifacts").mkdir(parents=True)
            (job_dir / "temp").mkdir()
            audio_path = job_dir / "temp" / "audio.wav"

            def fake_extract(input_path, output_dir=None):
                self.assertEqual(Path(input_path), source)
                self.assertEqual(Path(output_dir), job_dir / "temp")
                audio_path.write_bytes(b"audio")
                return str(audio_path)

            request = PipelineRequest(input=str(source), output_dir=str(output_dir))
            with patch("src.application.services.media_resolver.check_ffmpeg", return_value=True), \
                 patch("src.application.services.media_resolver.extract_audio", side_effect=fake_extract):
                audio, video, owned = MediaResolver.resolve(request, job_dir=str(job_dir))

            self.assertEqual(Path(audio), audio_path)
            self.assertEqual(Path(video), source)
            self.assertEqual(owned, [])
            self.assertTrue(source.is_file())
            self.assertEqual(source.read_bytes(), b"important")

    @pytest.mark.core
    def test_downloader_does_not_copy_video_to_output_root(self):
        from src.infrastructure.video import downloader

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def download(self, _urls):
                outtmpl = self.opts["outtmpl"]
                target_dir = Path(outtmpl).parent
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / "sample.mp4").write_bytes(b"video")

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(downloader.yt_dlp, "YoutubeDL", FakeYoutubeDL), \
             patch.object(downloader, "apply_yt_dlp_compat"):
            result = Path(downloader.download_video("https://example.com/v", tmp))

            self.assertTrue(result.is_file())
            self.assertIn(".dl_tmp", result.parts)
            self.assertFalse(any(Path(tmp).glob("*.mp4")))

    @pytest.mark.core
    def test_safe_remove_rejects_arbitrary_output_root_as_boundary(self):
        from src.application.services.cleanup_manager import CleanupManager

        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "user-video.mp4"
            file_path.write_bytes(b"important")

            result = CleanupManager.safe_remove(
                str(file_path),
                job_dir=tmp,
                label="用户文件",
            )

            self.assertFalse(result)
            self.assertTrue(file_path.is_file())


if __name__ == "__main__":
    unittest.main()
