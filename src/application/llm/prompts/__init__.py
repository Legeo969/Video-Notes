"""笔记生成 Prompt 模板"""

SYSTEM_PROMPT = """你是一个专业的视频内容分析师，擅长从转录文本中提取结构化知识。

转录文本不限领域，但可能包含：
- 专业术语和概念定义
- 操作步骤和参数设置
- 工作流程描述
- 代码片段或表达式

请根据这些特点生成高质量的学习笔记。当 transcript 附带了截图素材时，请根据上下文内容在笔记中合适的位置插入相关截图。"""


def load_template(template_path: str) -> str:
    """从文件加载 Markdown 模板

    Args:
        template_path: 模板文件路径

    Returns:
        模板文件内容（纯文本）
    """
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def build_template_prompt(
    transcript: str,
    video_title: str,
    template_content: str,
    frames: list[dict] | None = None,
) -> str:
    """根据模板内容构建 AI prompt，告诉 AI 按照模板结构来组织笔记

    Args:
        transcript: 转录文本
        video_title: 视频标题
        template_content: Markdown 模板内容（含 {{variable_name}} 占位符）
        frames: 可选，帧信息列表
    """
    # 替换模板中的 {{video_title}} 为实际标题
    template_content = template_content.replace("{{video_title}}", video_title)

    frame_context = ""
    if frames:
        frame_lines = "\n".join(
            f"- `{f['filename']}` — 视频第 {f['timestamp_sec']} 秒处的画面"
            for f in frames
        )
        frame_context = f"""

## 视频截图素材

以下是视频关键时间点的截图文件，输出笔记时请根据上下文在合适的段落中插入相关图片。插入方式：在需要配图的位置写 `![图片描述](frames/文件名)`。

{frame_lines}
"""

    user_prompt = f"""请根据以下视频转录文本，生成结构化的学习笔记。

视频标题：{video_title}{frame_context}

请严格按照以下 Markdown 模板结构来组织笔记内容，填充对应的变量区域：

{template_content}

要求：
1. 用 Markdown 格式输出
2. 严格按照提供的模板结构来组织内容，填充模板中的变量区域（如 {{core_theme}}、{{key_points}} 等）
3. 模板中的 {{video_title}} 已为你填充好，不需要再次填写
4. 每个变量区域填充相应内容，如果有其他自定义变量区域，根据上下文合理填充
5. 代码、公式、快捷键、参数设置保留原样
6. 如果转录有明显的口误或识别错误，在不影响理解的前提下修正
7. 简洁但完整，突出重点

转录文本：
{transcript}"""

    return user_prompt


def build_global_summary_prompt(
    merged_notes: str, video_title: str,
) -> str:
    """构建全局总结的用户 prompt

    Args:
        merged_notes: 合并的分段笔记内容
        video_title: 视频标题
    """
    user_prompt = f"""请根据以下已生成的分段笔记，生成一个全局总结。

视频标题：{video_title}

要求：
1. 用 Markdown 格式输出
2. 只输出 `## 全局总结` 章节的内容，不要包含视频标题
3. 总结应专注于：
   - 核心主题和学习目标
   - 内容结构和逻辑脉络
   - 关键结论和重要观点
   - 重要术语或容易踩坑的地方
   - 实用要点或行动建议（如果有的话）
4. 简洁但完整，突出重点
5. 不要重复分段笔记中的详细内容，只提炼精华

分段笔记内容：
{merged_notes}"""

    return user_prompt


def build_user_prompt(
    transcript: str, video_title: str, frames: list[dict] | None = None,
    style: str | None = None,
) -> str:
    """构建默认格式的用户 prompt，支持图片帧插入和笔记风格设置

    Args:
        transcript: 转录文本
        video_title: 视频标题
        frames: 可选，帧信息列表，每项含 filename 和 timestamp_sec
        style: 可选，笔记风格（简洁/详细/教程风格/以学习笔记形式）
    """
    frame_context = ""
    if frames:
        frame_lines = "\n".join(
            f"- `{f['filename']}` — 视频第 {f['timestamp_sec']} 秒处的画面"
            for f in frames
        )
        frame_context = f"""

## 视频截图素材

以下是视频关键时间点的截图文件，输出笔记时请根据上下文在合适的段落中插入相关图片。插入方式：在需要配图的位置写 `![图片描述](frames/文件名)`。

{frame_lines}
"""

    analysis_context = ""
    ocr_context = ""
    if frames:
        analyzed = [f for f in frames if f.get("analysis")]
        if analyzed:
            analysis_lines = "\n".join(
                f"- `{f['filename']}`: {f['analysis']}"
                for f in analyzed
            )
            analysis_context = f"""

## 关键帧视觉识别

AI 已经分析了以下视频截图的内容，请在生成笔记时参考这些视觉信息：

{analysis_lines}
"""

        ocr_frames = [f for f in frames if f.get("ocr_text")]
        if ocr_frames:
            ocr_sections = []
            for f in ocr_frames:
                ocr_sections.append(
                    f"### `{f['filename']}` 中的文字\n\n{f['ocr_text']}"
                )
            ocr_context = "\n\n".join(ocr_sections)
            ocr_context = f"""

## 画面文字识别（OCR）

以下文字是从视频截图中识别出来的，可以补充笔记中的细节：

{ocr_context}
"""

    style_prompt = ""
    if style == "简洁":
        style_prompt = """
要求：
1. 用 Markdown 格式输出
2. 只输出以下章节，不要多余内容：
   - **核心主题** — 一句话
   - **关键要点** — 3-5 个点，每点一句话
   - **详细内容** — 按逻辑顺序简述，省略例子和细节
3. 代码、公式、快捷键保留原样
4. 总长度不超过 500 字"""
    elif style == "详细":
        style_prompt = """
要求：
1. 用 Markdown 格式输出
2. 包含以下章节，每个章节都要充分展开：
   - **核心主题** — 概述视频解决的问题和适用场景
   - **背景与动机** — 为什么需要这些操作
   - **分步详解** — 按操作顺序逐步展开，包含每个步骤的前置条件、具体操作和预期结果
   - **注意事项与踩坑** — 容易出错的地方、常见误区
   - **工具/术语速查** — 提到的软件、插件、快捷键、参数
   - **总结** — 核心收获
3. 代码、公式、快捷键、参数设置保留原样
4. 如果转录有明显的口误或识别错误，在不影响理解的前提下修正
5. 详细但不啰嗦，保留重要细节和例子"""
    elif style == "教程风格":
        style_prompt = """
要求：
1. 用 Markdown 格式输出
2. 以教程形式组织，站在学习者视角，包含以下内容：
   - **概述** — 学会这个技能后能做什么
   - **前置准备** — 需要安装的软件、需要了解的概念
   - **分步教程** — 按步骤编号（1. 2. 3. ...），每步用动词开头，包含操作截图指引
   - **常见问题** — 新手容易卡住的地方，附解决方法
   - **进阶提示** — 如果已经掌握基础，可以尝试什么
3. 代码、公式、快捷键、参数设置保留原样
4. 语气亲切，用"你"称呼读者
5. 步骤清晰，每一步都可以独立执行"""
    elif style == "以学习笔记形式":
        style_prompt = """
要求：
1. 用 Markdown 格式输出
2. 以学习笔记的形式组织，适合复习回顾：
   - **一句话总结** — 这个视频的核心收获
   - **关键概念** — 每个概念用粗体 + 一句话定义 + 为什么重要
   - **操作速查** — 快捷键、参数、命令的速查表格
   - **复习问题** — 3-5 个自测问题，覆盖核心知识点
   - **行动清单** — 看完视频后可以尝试的具体操作
3. 代码、公式、快捷键、参数设置保留原样
4. 逻辑清晰，适合后续快速翻阅复习"""
    else:
        style_prompt = """
要求：
1. 用 Markdown 格式输出
2. 包含以下章节：
   - **核心主题** — 一句话概括视频内容
   - **关键要点** — 分点列出核心知识点（5-10 个）
   - **详细内容** — 按视频逻辑顺序展开，保留重要细节
   - **工具/术语速查** — 提到的软件、插件、快捷键、参数等
   - **总结** — 2-3 句话总结学习收获
3. 代码、公式、快捷键、参数设置保留原样
4. 如果转录有明显的口误或识别错误，在不影响理解的前提下修正
5. 简洁但完整，突出重点"""

    user_prompt = f"""请根据以下视频转录文本，生成结构化的学习笔记。

视频标题：{video_title}{frame_context}{analysis_context}{ocr_context}
{style_prompt}

转录文本：
{transcript}"""

    return user_prompt
