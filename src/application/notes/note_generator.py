"""AI 笔记生成模块 - 使用 Provider 抽象 + V0.5 模板系统"""

from __future__ import annotations

import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from src.application.llm import get_provider
from src.application.llm.prompts import (
    SYSTEM_PROMPT,
    build_global_summary_prompt,
    load_template,
)
from src.application.notes.invoker import call_provider_for_notes

# 模板系统（V0.5）
from src.domain.models.note_template import NoteContext
from src.application.notes.template_loader import get_template_registry
from src.application.notes.template_validator import TemplateValidator

if TYPE_CHECKING:
    from src.domain.models.note_template import NoteTemplate

logger = logging.getLogger(__name__)

# 单段最大字符数，留 token 余量给 prompt 和输出
_MAX_CHARS = 12000

# 速率控制：最大并发数上限（防止过多线程同时调用 API）
_MAX_WORKERS_LIMIT = 4

# 重试参数
_RETRY_MAX = 3
_RETRY_BASE_DELAY = 1.0   # 初始等待秒数（指数退避）
_RETRY_MAX_DELAY = 30.0   # 最大等待秒数


def _split_transcript(text: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """将长文本按段落边界切分成多段，每段不超过 max_chars 字符。

    切割优先级：段落分隔 \\n\\n > 单换行 \\n > 空格 > 硬切。
    每次切割位置必须 > max_chars // 2（至少保留一半内容）。
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text
    min_chunk = max_chars // 2

    while len(remaining) > max_chars:
        # 在 max_chars 附近找最佳切割点
        window = remaining[:max_chars]
        cut = max_chars

        # 优先段落边界
        for sep in ("\n\n", "\n", "。", ". ", " "):
            pos = window.rfind(sep, min_chunk)
            if pos > 0:
                cut = pos + len(sep)
                break

        chunk = remaining[:cut].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[cut:]

    if remaining.strip():
        chunks.append(remaining.strip())

    return chunks


def _strip_leading_title(text: str) -> str:
    """去掉 Markdown 开头的 #~###### 标题行。"""
    return re.sub(r"^#{1,6}\s+[^\n]+\n*", "", text).strip()


def _call_with_retry(
    provider,
    chunk: str,
    video_title: str,
    chunk_info: str | None,
    model: str,
    frames,
    template_content,
    temperature: float,
    style: str | None,
    timeout: int | None,
    max_retries: int = 3,
    chunk_label: str = "",
    # V0.5: pre-built prompts (template mode)
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> str:
    """带指数退避重试的 LLM 调用封装。"""
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return call_provider_for_notes(
                provider,
                chunk,
                video_title,
                chunk_info=chunk_info or "",
                model=model,
                frames=frames,
                template_content=template_content,
                temperature=temperature,
                style=style,
                timeout=timeout,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
                jitter = random.uniform(0, delay * 0.3)
                wait = delay + jitter
                label = f" ({chunk_label})" if chunk_label else ""
                logger.warning(f"重试 {attempt}/{max_retries}{label}: {e} (等待 {wait:.1f}s)")
                time.sleep(wait)

    raise last_error  # type: ignore[misc]


# ── 公开入口 ──────────────────────────────────────────────────


def generate_notes(
    transcript: str,
    video_title: str = "未知视频",
    model: str = "mimo-v2.5",
    api_key: str | None = None,
    base_url: str | None = None,
    frames: list[dict] | None = None,
    template: str | None = None,            # 旧：模板文件路径
    template_id: str | None = None,         # 新：YAML 模板 ID
    temperature: float = 0.3,
    style: str | None = None,
    smart_summary: bool = False,
    provider: str | None = None,
    request_timeout: int | None = None,
    request_max_retries: int = 3,
    max_parallel_chunks: int = 1,
) -> str:
    """使用 AI 生成结构化笔记

    长文本自动分段处理：每段单独生成笔记片段，最后合并。
    api_key/base_url 可选传入，避免依赖 os.environ。
    provider 可选指定供应商名称，为 None 时自动推断。
    frames 可选传入视频截图信息，AI 会在笔记中按需插入图片。
    template 可选传入模板文件路径（旧方式，向后兼容）。
    template_id 可选指定 YAML 模板 ID（study/meeting/...），V0.5 新方式。
    temperature 控制生成随机性（0.0-2.0），默认 0.3。
    style 可选笔记风格（旧方式，与 template 互斥）。
    request_timeout 单次 API 请求超时秒数。
    request_max_retries 单次 API 请求最大重试次数，默认 3。
    max_parallel_chunks 最大并发 chunk 数，受 _MAX_WORKERS_LIMIT 上限保护。
    """
    # ── 确定模板模式 ──────────────────────────────────────────
    template_obj: NoteTemplate | None = None
    use_template_mode = False

    # 旧：文件路径模板
    template_content = None
    if template:
        template_content = load_template(template)
    elif template_id:
        # V0.5：YAML 模板；"auto" 根据标题和转录内容自动推荐。
        registry = get_template_registry()
        if template_id == "auto":
            from src.application.notes.template_recommender import best_template

            query = f"{video_title}\n{(transcript or '')[:8000]}"
            template_obj = best_template(query)
            if template_obj is None:
                template_obj = registry.get_or_default(None)
            logger.info("自动推荐笔记模板: %s", template_obj.id)
        else:
            template_obj = registry.get(template_id)
        use_template_mode = True
    else:
        # 默认使用 "default" 模板（V0.5 新行为）
        try:
            registry = get_template_registry()
            template_obj = registry.get_or_default(None)
            use_template_mode = True
        except Exception:
            # 如果 registry 加载失败，回退到旧逻辑
            pass

    # ── 构建 NoteContext ──────────────────────────────────────
    note_context = NoteContext(
        title=video_title,
        with_citations=(
            template_obj.output.with_citations if template_obj else False
        ),
    )

    if not transcript or not transcript.strip():
        logger.warning("转录内容为空，跳过笔记生成")
        return f"# {video_title} — 学习笔记\n\n（转录内容为空，无法生成笔记）\n"

    provider_instance = get_provider(provider, api_key=api_key, base_url=base_url)

    chunks = _split_transcript(transcript)
    total = len(chunks)

    if total == 1:
        # ── 短文本：单次调用 ──
        logger.info("正在生成笔记...")

        if use_template_mode and template_obj:
            from src.application.notes.prompt_builder import (
                build_system_prompt,
                build_user_prompt,
            )
            sys_p = build_system_prompt(template_obj)
            user_p = build_user_prompt(template_obj, transcript, note_context, style=style)
            notes = _call_with_retry(
                provider_instance, transcript, video_title,
                chunk_info=None, model=model, frames=frames,
                template_content=None,
                temperature=temperature, style=style,
                timeout=request_timeout,
                max_retries=request_max_retries,
                chunk_label="1/1",
                system_prompt=sys_p,
                user_prompt=user_p,
            )
        else:
            notes = _call_with_retry(
                provider_instance, transcript, video_title,
                chunk_info=None, model=model, frames=frames,
                template_content=template_content,
                temperature=temperature, style=style,
                timeout=request_timeout,
                max_retries=request_max_retries,
                chunk_label="1/1",
            )

        # 模板校验
        if use_template_mode and template_obj:
            _validate_output(notes, template_obj)

        logger.info("笔记生成完成")
        return notes

    # ── 长文本分段处理 ────────────────────────────────────────
    logger.info("转录文本较长（%s 字符），分 %d 段处理...", len(transcript), total)

    effective_workers = max(1, min(max_parallel_chunks, _MAX_WORKERS_LIMIT))

    # 构建分段 prompt（模板模式 vs 旧模式）
    def _get_chunk_prompt(chunk: str, i: int) -> tuple[str | None, str | None]:
        """返回 (system_prompt, user_prompt) 元组或 (None, None) 走旧路径。"""
        if not (use_template_mode and template_obj):
            return None, None

        from src.application.notes.prompt_builder import (
            build_system_prompt,
            build_chunk_user_prompt,
        )
        chunk_info = (
            f"这是长文的第 {i}/{total} 段，"
            f"请只生成本段的详细内容和要点，不要包含核心主题和总结章节"
        )
        return (
            build_system_prompt(template_obj),
            build_chunk_user_prompt(
                template_obj, chunk, chunk_info, note_context, style=style,
            ),
        )

    def _process_chunk(i: int, chunk: str) -> tuple[int, str | None]:
        logger.info("正在生成第 %d/%d 段笔记...", i, total)
        sys_p, user_p = _get_chunk_prompt(chunk, i)
        try:
            result = _call_with_retry(
                provider_instance, chunk, video_title,
                chunk_info=(
                    f"这是长文的第 {i}/{total} 段，"
                    f"请只生成本段的详细内容和要点，不要包含核心主题和总结章节"
                ),
                model=model,
                frames=frames if i == 1 else None,
                template_content=template_content,
                temperature=temperature, style=style,
                timeout=request_timeout,
                max_retries=request_max_retries,
                chunk_label=f"{i}/{total}",
                system_prompt=sys_p,
                user_prompt=user_p,
            )
            logger.info("第 %d/%d 段完成", i, total)
            return i, result
        except Exception as exc:
            logger.warning("第 %d/%d 段生成失败，将用占位内容代替: %s", i, total, exc)
            return i, None

    parts: list[str | None] = [None] * total

    if effective_workers > 1 and total > 1:
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = {
                executor.submit(_process_chunk, i, chunk): i
                for i, chunk in enumerate(chunks, 1)
            }
            for future in as_completed(futures):
                idx, result = future.result()
                parts[idx - 1] = result
    else:
        for i, chunk in enumerate(chunks, 1):
            _, result = _process_chunk(i, chunk)
            parts[i - 1] = result

    # 对失败的 chunk 用占位文本替换
    final_parts: list[str] = []
    for i, p in enumerate(parts, 1):
        if p is None:
            final_parts.append(f"> ⚠️ 第 {i}/{total} 段内容生成失败，请手动补充。\n")
        else:
            final_parts.append(p)

    # ── 合并策略 ─────────────────────────────────────────────
    if use_template_mode and template_obj and len(final_parts) > 1:
        # V0.5 模板模式：LLM 合并（去重 + 按模板重组 + 补齐 required sections）
        from src.application.notes.prompt_builder import (
            build_system_prompt,
            build_merge_user_prompt,
        )
        logger.info("正在按模板合并各段笔记...")
        sys_p = build_system_prompt(template_obj)
        merge_prompt = build_merge_user_prompt(template_obj, final_parts, note_context, style=style)

        try:
            merged = provider_instance.chat(
                model=model,
                messages=[
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": merge_prompt},
                ],
                temperature=temperature,
                max_tokens=4096,
            )
            # 如果 merged 没有标题，补充一个
            if not merged.strip().startswith("#"):
                merged = f"# {video_title} — {template_obj.name}\n\n{merged}"
            logger.info("模板合并完成")

            _validate_output(merged, template_obj)
            return merged
        except Exception as e:
            logger.warning("模板合并失败（回退到字符串合并）: %s", e)
            # fall through to string concat

    # 字符串合并（旧方式 / 回退）
    final_parts = [_strip_leading_title(p) for p in final_parts]
    merged = f"# {video_title} — 学习笔记\n\n"
    merged += "\n\n---\n\n".join(final_parts)

    # 全局总结（仅旧模式支持）
    if smart_summary and total > 1 and not use_template_mode:
        logger.info("正在生成全局总结...")
        try:
            summary_prompt = build_global_summary_prompt(merged, video_title)
            summary = provider_instance.chat(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=temperature,
                max_tokens=4096,
            )
            summary = _strip_leading_title(summary)
            merged = f"# {video_title} — 学习笔记\n\n"
            merged += f"## 全局总结\n\n{summary}\n\n---\n\n"
            merged += "\n\n---\n\n".join(final_parts)
            logger.info("全局总结生成完成")
        except Exception as e:
            logger.warning("全局总结生成失败（保留原始分段笔记）: %s", e)

    logger.info("全部段落生成完成，已合并")

    # 模板校验（字符串合并的模板模式）
    if use_template_mode and template_obj:
        _validate_output(merged, template_obj)

    return merged


# ── 校验 ──────────────────────────────────────────────────────


def _validate_output(notes: str, template_obj: "NoteTemplate") -> None:
    """对生成结果进行模板校验并输出警告。"""
    try:
        validator = TemplateValidator()
        result = validator.validate_markdown(notes, template_obj)
        if result.warnings:
            logger.info(result.to_text())
    except Exception as e:
        logger.warning("Template validation failed (non-fatal): %s", e)  # 校验失败不影响笔记生成
