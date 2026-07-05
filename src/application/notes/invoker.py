"""LLM invoker — build prompts and call the provider for note generation."""

from src.application.llm.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt as _build_default_user_prompt,
    build_template_prompt,
)


def call_provider_for_notes(
    provider,
    transcript: str,
    video_title: str,
    chunk_info: str = "",
    model: str = "mimo-v2.5",
    frames: list[dict] | None = None,
    template_content: str | None = None,
    temperature: float = 0.3,
    style: str | None = None,
    timeout: int | None = None,
    # ── V0.5: pre-built prompt overrides ──
    system_prompt: str | None = None,
    user_prompt: str | None = None,
) -> str:
    """Build the prompt and call a provider for note generation.

    Args:
        provider: LLMProvider instance.
        transcript: 转录文本。
        video_title: 视频标题。
        chunk_info: 分段信息（长文本分段时）。
        model: 模型名称。
        frames: 视频帧信息列表。
        template_content: 旧模板内容（文件路径加载的 Markdown 文本）。
        temperature: 生成温度。
        style: 笔记风格。
        timeout: 可选超时（秒）。
        system_prompt: V0.5 预构建 system prompt（覆盖 SYSTEM_PROMPT）。
        user_prompt: V0.5 预构建 user prompt（跳过内部构建逻辑）。
    """
    # ── V0.5: 预构建 prompt 优先 ──
    if user_prompt is not None:
        sys_prompt = system_prompt or SYSTEM_PROMPT
        # chunk_info 追加到预构建 prompt 前面
        if chunk_info:
            user_prompt = f"（{chunk_info}）\n\n{user_prompt}"

        kwargs: dict = {}
        if timeout is not None:
            kwargs["timeout"] = timeout

        return provider.chat(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=4096,
            **kwargs,
        )

    # ── 旧逻辑（向后兼容）──────────────────────
    if template_content:
        user_prompt = build_template_prompt(
            transcript, video_title, template_content, frames=frames,
        )
    else:
        user_prompt = _build_default_user_prompt(
            transcript, video_title, frames=frames, style=style,
        )
    if chunk_info:
        user_prompt = f"（{chunk_info}）\n\n{user_prompt}"

    kwargs = {}
    if timeout is not None:
        kwargs["timeout"] = timeout

    return provider.chat(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=4096,
        **kwargs,
    )
