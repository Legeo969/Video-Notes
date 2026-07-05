"""V0.5.1 模板推荐器 — 基于关键词规则推荐最佳模板。

先做 heuristics，不做模型识别。后续 GUI 做"自动推荐模板"时可直接复用。
"""

from __future__ import annotations

from .template_loader import get_template_registry
from src.domain.models.note_template import NoteTemplate  # noqa: E402


# ── 关键词→模板映射 ──────────────────────────────────────────

# 格式：(模板ID, 匹配度权重 0-1, 关键词列表)
_RULES: list[tuple[str, float, list[str]]] = [
    # coding_tutorial: 编程、技术教学类
    ("coding_tutorial", 0.9, [
        "tutorial", "coding", "code", "programming",
        "python", "javascript", "react", "vue", "angular",
        "docker", "kubernetes", "git", "api", "framework",
        "debug", "debugging", "refactor", "deploy",
        "编程", "代码", "教程", "开发", "实战",
    ]),
    # meeting: 会议类
    ("meeting", 0.9, [
        "meeting", "standup", "sync", "sprint", "retro",
        "review", "规划", "周会", "站会", "复盘",
        "会议", "讨论", "评审", "对齐", "同步",
    ]),
    # lecture: 讲座类
    ("lecture", 0.85, [
        "lecture", "talk", "keynote", "presentation",
        "seminar", "演讲", "讲座", "主题分享", "报告",
    ]),
    # interview: 访谈类
    ("interview", 0.9, [
        "interview", "podcast", "对话", "访谈",
        "播客", "采访", "人物", "嘉宾",
    ]),
    # product_demo: 产品演示
    ("product_demo", 0.85, [
        "demo", "product", "launch", "showcase",
        "演示", "发布会", "新品", "产品介绍",
        "上手", "体验", "评测", "开箱",
    ]),
    # research: 论文/学术
    ("research", 0.9, [
        "paper", "research", "arxiv", "thesis",
        "论文", "研究", "学术", "期刊",
        "实验", "survey", "综述",
    ]),
    # study: 课程/学习（较低权重，让 coding_tutorial 等更具体的优先）
    ("study", 0.7, [
        "course", "lesson", "class", "learn",
        "课程", "学习", "教学", "入门", "基础",
    ]),
]


def recommend_templates(
    query: str,
    source_url: str = "",
) -> list[tuple[NoteTemplate, str, float]]:
    """根据查询文本推荐模板。

    返回按匹配度降序排列的 [(模板对象, 匹配关键词, 权重), ...]。
    如果没有匹配到任何规则，返回 [("default", "", 0.0)]。

    用法:
        results = recommend_templates("React hooks tutorial")
        for tmpl, keyword, score in results:
            print(f"{tmpl.id} ({score:.0%}) — matched '{keyword}'")
    """
    query_lower = query.lower()
    if source_url:
        query_lower += " " + source_url.lower()

    scored: dict[str, tuple[NoteTemplate, str, float]] = {}

    for template_id, weight, keywords in _RULES:
        for kw in keywords:
            if kw.lower() in query_lower:
                # 只保留最高权重的匹配
                if template_id not in scored or weight > scored[template_id][2]:
                    scored[template_id] = (None, kw, weight)  # type: ignore[assignment]
                break  # 已匹配此规则，无需继续检查同规则的其他关键词

    if not scored:
        # 返回 default
        registry = get_template_registry()
        try:
            default_tmpl = registry.get("default")
        except Exception:
            default_tmpl = None
        return [(default_tmpl, "", 0.0)] if default_tmpl else []

    # 解析模板对象
    registry = get_template_registry()
    results: list[tuple[NoteTemplate, str, float]] = []
    for template_id, (_tmpl, kw, weight) in scored.items():
        try:
            tmpl = registry.get(template_id)
        except Exception:
            continue
        results.append((tmpl, kw, weight))

    # 按权重降序排列
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def best_template(query: str, source_url: str = "") -> NoteTemplate | None:
    """返回最佳匹配的模板，如果没有匹配到任何规则则返回 default。

    用法:
        tmpl = best_template("Docker container tutorial")
        print(tmpl.id)  # "coding_tutorial"
    """
    results = recommend_templates(query, source_url)
    if not results:
        return None
    return results[0][0]
