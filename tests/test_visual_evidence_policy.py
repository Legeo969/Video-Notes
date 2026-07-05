from dataclasses import dataclass

from src.application.services.artifact_writer import ArtifactWriter
from src.application.llm.reduce_stage import REDUCE_SYSTEM_PROMPT


@dataclass
class Insight:
    image_path: str
    visual_summary: str
    visual_importance: str
    importance_score: float


def _frames(count: int):
    return [
        {"filename": f"frame_{i:04d}.jpg", "path": f"/tmp/frame_{i:04d}.jpg"}
        for i in range(1, count + 1)
    ]


def _insights(count: int):
    return [
        Insight(
            image_path=f"/tmp/frame_{i:04d}.jpg",
            visual_summary=f"真实画面 {i}",
            visual_importance=f"学习意义 {i}",
            importance_score=0.60 + i / 100,
        )
        for i in range(1, count + 1)
    ]


def test_each_chapter_is_capped_at_four_validated_images():
    body = ["# 章节笔记", "", "## 第一章", "", "### 视觉证据", ""]
    for i in range(1, 7):
        body.extend([
            f"![图{i}](<frames/frame_{i:04d}.jpg>)",
            "",
            f"**展示了什么：** 模型原文 {i}",
            "",
        ])
    body.extend(["### 相关转录", "", "正文"])

    result = ArtifactWriter._sanitize_visual_evidence(
        "\n".join(body), _frames(6), _insights(6)
    )

    assert result.count("](<frames/") == 4
    # highest four scores selected, original order restored
    assert "frame_0001.jpg" not in result
    assert "frame_0002.jpg" not in result
    for i in range(3, 7):
        assert f"frame_{i:04d}.jpg" in result


def test_empty_visual_evidence_section_is_removed_completely():
    notes = (
        "## 第一章\n\n### 讲解\n\n正文。\n\n"
        "### 视觉证据\n\n"
        "（该章节未提供具体视觉元素文件名，但包含以下典型视觉内容）\n\n"
        "### 相关转录\n\n引文。\n"
    )

    result = ArtifactWriter._sanitize_visual_evidence(notes, _frames(2), [])

    assert "### 视觉证据" not in result
    assert "典型视觉内容" not in result
    assert "### 相关转录" in result


def test_bold_wrapper_is_removed_and_description_comes_from_insight():
    notes = (
        "## 第一章\n\n### 视觉证据\n\n"
        "- **![界面](<frames/frame_0001.jpg>)**：错误猜测\n\n"
        "**展示了什么：** 编造内容\n\n"
        "**为什么重要：** 编造意义\n\n"
        "### 相关转录\n\n文本\n"
    )

    result = ArtifactWriter._sanitize_visual_evidence(notes, _frames(1), _insights(1))

    assert "**![" not in result
    assert "![界面](<frames/frame_0001.jpg>)" in result
    assert "真实画面 1" in result
    assert "学习意义 1" in result
    assert "错误猜测" not in result
    assert "编造内容" not in result


def test_failed_vision_cannot_leave_images_or_placeholder_claims():
    notes = (
        "## 第一章\n\n### 视觉证据\n\n"
        "![猜测](<frames/frame_0001.jpg>)\n\n"
        "**展示了什么：** 典型视觉内容：软件界面。\n\n"
        "### 相关转录\n\n文本\n"
    )

    result = ArtifactWriter._sanitize_visual_evidence(notes, _frames(1), None)

    assert "### 视觉证据" not in result
    assert "frame_0001.jpg" not in result
    assert "典型视觉内容" not in result


def test_complex_multi_step_chapter_may_keep_five_distinct_images():
    body = [
        "# 章节笔记",
        "",
        "## 多步骤配置流程",
        "",
        "### 讲解",
        "",
        "首先安装组件，随后配置插件，然后导入素材，最后完成烘焙和导出。",
        "",
        "### 视觉证据",
        "",
    ]
    for i in range(1, 7):
        body.extend([
            f"![步骤{i}](<frames/frame_{i:04d}.jpg>)",
            "",
            f"**展示了什么：** 原说明 {i}",
            "",
        ])
    body.extend(["### 相关转录", "", "正文"])

    result = ArtifactWriter._sanitize_visual_evidence(
        "\n".join(body), _frames(6), _insights(6)
    )

    assert result.count("](<frames/") == 5
    assert "frame_0001.jpg" not in result
    for i in range(2, 7):
        assert f"frame_{i:04d}.jpg" in result


def test_similar_evidence_is_not_used_to_fill_a_quota():
    frames = _frames(4)
    insights = [
        Insight(
            image_path=f"/tmp/frame_{i:04d}.jpg",
            visual_summary="同一个设置窗口的几乎相同截图",
            visual_importance="重复界面",
            importance_score=0.90 - i / 100,
        )
        for i in range(1, 5)
    ]
    body = ["## 普通章节", "", "### 视觉证据", ""]
    for i in range(1, 5):
        body.extend([f"![图{i}](<frames/frame_{i:04d}.jpg>)", ""])
    body.extend(["### 相关转录", "", "正文"])

    result = ArtifactWriter._sanitize_visual_evidence(
        "\n".join(body), frames, insights
    )

    assert result.count("](<frames/") == 1


def test_document_visual_evidence_never_exceeds_twenty_eight_images():
    frames = _frames(35)
    insights = _insights(35)
    body = ["# 章节笔记", ""]
    current = 1
    for chapter in range(1, 8):
        body.extend([
            f"## 复杂流程 {chapter}",
            "",
            "### 讲解",
            "",
            "首先创建，随后配置，然后导入，接着处理，最后导出。",
            "",
            "### 视觉证据",
            "",
        ])
        for _ in range(5):
            body.extend([
                f"![步骤{current}](<frames/frame_{current:04d}.jpg>)",
                "",
            ])
            current += 1
        body.extend(["### 相关转录", "", "正文", ""])

    result = ArtifactWriter._sanitize_visual_evidence(
        "\n".join(body), frames, insights
    )

    assert result.count("](<frames/") == 28


def test_reduce_prompt_contains_adaptive_visual_policy():
    assert "优先保留 2～3 张" in REDUCE_SYSTEM_PROMPT
    assert "普通章节最多 4 张" in REDUCE_SYSTEM_PROMPT
    assert "复杂章节" in REDUCE_SYSTEM_PROMPT
    assert "最多 5 张" in REDUCE_SYSTEM_PROMPT
    assert "15～24 张" in REDUCE_SYSTEM_PROMPT
    assert "不得超过 28 张" in REDUCE_SYSTEM_PROMPT
    assert "禁止用相似帧" in REDUCE_SYSTEM_PROMPT
    assert "删除整个“### 视觉证据”小节" in REDUCE_SYSTEM_PROMPT
    assert "典型视觉内容" in REDUCE_SYSTEM_PROMPT
    assert "不要放在 **粗体**" in REDUCE_SYSTEM_PROMPT
