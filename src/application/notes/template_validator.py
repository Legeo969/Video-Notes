"""V0.5 TemplateValidator — Markdown 输出校验 + 模板文件校验。

检查生成的笔记是否满足模板要求的章节结构。
V0.5.1 新增模板文件结构校验（--template-validate）。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from src.domain.models.note_template import (  # noqa: E402
    NoteTemplate,
    TemplateOutputOptions,
    TemplateSection,
    TemplateValidationResult,
    TemplateValidationWarning,
)

# Markdown heading 匹配：## 标题
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

# 模板 ID 安全格式
_TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class TemplateValidator:
    """校验生成的 Markdown 笔记是否符合模板要求。

    Usage::

        validator = TemplateValidator()
        result = validator.validate_markdown(notes, template)
        if not result.passed:
            print(result.to_text())
    """

    # ── 公开 API ──────────────────────────────────────────

    def validate_markdown(
        self,
        content: str,
        template: NoteTemplate,
    ) -> TemplateValidationResult:
        """校验 Markdown 内容是否满足模板要求。

        Args:
            content: 生成的笔记 Markdown 全文。
            template: 要求的模板。

        Returns:
            TemplateValidationResult，包含所有警告和错误。
        """
        result = TemplateValidationResult()

        headings = self._extract_headings(content)

        # 1. 检查 required sections 是否存在
        self._check_required_sections(headings, template, result)

        # 2. 检查空章节
        self._check_empty_sections(content, template, result)

        # 3. 检查重复标题
        self._check_duplicate_headings(headings, result)

        # 4. 检查输出是否过短
        self._check_content_length(content, result)

        return result

    # ── 内部检查 ──────────────────────────────────────────

    def _extract_headings(self, content: str) -> list[str]:
        """从 Markdown 中提取所有 heading 文本（忽略层级）。"""
        return [m.group(1).strip() for m in _HEADING_RE.finditer(content)]

    def _check_required_sections(
        self,
        headings: list[str],
        template: NoteTemplate,
        result: TemplateValidationResult,
    ) -> None:
        """检查所有 required sections 是否存在于 heading 列表中。"""
        heading_set = {h.lower() for h in headings}
        for section in template.sections:
            if section.required:
                if section.title.lower() not in heading_set:
                    # 也用 id 检查（有些模板可能用不同命名）
                    if section.id.lower() not in heading_set:
                        result.add_error(
                            section.id,
                            f"缺少必需章节：{section.title}",
                        )

    def _check_empty_sections(
        self,
        content: str,
        template: NoteTemplate,
        result: TemplateValidationResult,
    ) -> None:
        """检查 found sections 下方是否有实际内容。"""
        for section in template.sections:
            # 查找对应标题的位置
            pattern = re.compile(
                rf"^#{{1,6}}\s+{re.escape(section.title)}\s*$",
                re.MULTILINE | re.IGNORECASE,
            )
            match = pattern.search(content)
            if match:
                # 提取该标题到下一个标题之间的内容
                start = match.end()
                next_heading = re.search(r"^#{1,6}\s+", content[start:], re.MULTILINE)
                if next_heading:
                    end = start + next_heading.start()
                else:
                    end = len(content)
                section_body = content[start:end].strip()

                # 检查是否基本为空（少于 20 个有效字符）
                text = re.sub(r"\s+", "", section_body)
                if len(text) < 20:
                    result.add_warning(
                        section.id,
                        f"章节内容过短：{section.title}（< 20 字符），可能生成不完整",
                    )

    def _check_duplicate_headings(
        self,
        headings: list[str],
        result: TemplateValidationResult,
    ) -> None:
        """检查是否有重复的标题。"""
        seen: dict[str, int] = {}
        for h in headings:
            key = h.lower()
            seen[key] = seen.get(key, 0) + 1

        for key, count in seen.items():
            if count > 1:
                result.add_warning(
                    None,
                    f"标题重复：'{key}' 出现 {count} 次",
                )

    def _check_content_length(
        self,
        content: str,
        result: TemplateValidationResult,
    ) -> None:
        """检查整体输出是否过短。"""
        text = re.sub(r"\s+", "", content)
        if len(text) < 100:
            result.add_warning(
                None,
                f"输出内容较短（{len(text)} 字符），可能生成不完整",
            )

    # ── 文件级模板校验 (V0.5.1) ─────────────────────────────

    def validate_template_file(self, filepath: str | Path) -> TemplateValidationResult:
        """校验模板 YAML 文件的结构合法性。

        检查内容：
        - YAML 是否合法
        - 必需字段是否存在 (id, name, description, prompt, sections)
        - template id 是否安全（仅含 a-z/0-9/_/-）
        - section id 是否重复
        - prompt 是否非空
        - 是否至少有一个 required section

        Args:
            filepath: 模板 YAML 文件路径。

        Returns:
            TemplateValidationResult（passed=False 表示文件级别有错误）。
        """
        result = TemplateValidationResult()
        filepath = Path(filepath)

        if not filepath.exists():
            result.add_error(None, f"文件不存在: {filepath}")
            return result

        # 1. YAML 解析
        try:
            import yaml
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except ImportError:
            result.add_error(None, "缺少 PyYAML 依赖，无法校验模板文件")
            return result
        except yaml.YAMLError as e:
            result.add_error(None, f"YAML 解析失败: {e}")
            return result

        if not isinstance(data, dict):
            result.add_error(None, "YAML 顶层必须是一个字典")
            return result

        # 2. 必需字段检查
        required_fields = ["id", "name", "description", "sections", "prompt"]
        for field in required_fields:
            if field not in data or data[field] is None:
                result.add_error(None, f"缺少必需字段: '{field}'")

        has_id = "id" in data and isinstance(data["id"], str)

        # 3. template id 安全校验
        if has_id:
            tid = data["id"]
            if not _TEMPLATE_ID_RE.match(tid):
                result.add_error(
                    None,
                    f"模板 ID 不安全: '{tid}'，只能包含小写字母、数字、下划线和连字符",
                )

        # 4. prompt 非空检查
        if "prompt" in data:
            prompt = data["prompt"]
            if not isinstance(prompt, str) or not prompt.strip():
                result.add_error(None, "'prompt' 字段为空或缺失")

        # 5. sections 检查
        if "sections" in data:
            sections = data["sections"]
            if not isinstance(sections, list) or len(sections) == 0:
                result.add_error(None, "'sections' 必须是非空列表")
            else:
                seen_ids: set[str] = set()
                has_required = False
                for i, sec in enumerate(sections):
                    if not isinstance(sec, dict):
                        result.add_error(None, f"sections[{i}] 必须是一个字典")
                        continue

                    sid = sec.get("id", "")
                    if not isinstance(sid, str) or not sid.strip():
                        result.add_error(None, f"sections[{i}] 缺少 'id'")
                    elif sid in seen_ids:
                        result.add_error(
                            sid,
                            f"section id 重复: '{sid}'（第 {i + 1} 个 section）",
                        )
                    else:
                        seen_ids.add(sid)

                    stitle = sec.get("title", "")
                    if not isinstance(stitle, str) or not stitle.strip():
                        result.add_error(
                            sid if sid else f"sections[{i}]",
                            f"sections[{i}] 缺少 'title'",
                        )

                    if sec.get("required", True):
                        has_required = True

                if not has_required:
                    result.add_error(
                        None,
                        "至少需要一个 required section（required: true）",
                    )

        # 6. optional: output 字段检查
        if "output" in data and data["output"] is not None:
            output = data["output"]
            if not isinstance(output, dict):
                result.add_warning(None, "'output' 字段应为字典，已忽略")

        return result

    # ── 工具方法 (V0.5.1) ────────────────────────────────────

    def to_validation_dict(
        self,
        result: TemplateValidationResult,
        template_id: str,
    ) -> dict[str, Any]:
        """将校验结果转换为可 JSON 序列化的字典。

        用于写入 artifacts/template_validation.json。
        """
        from datetime import datetime, timezone

        return {
            "template_id": template_id,
            "valid": result.passed,
            "warnings": [
                {
                    "level": w.level,
                    "section_id": w.section_id,
                    "message": w.message,
                }
                for w in result.warnings
            ],
            "warning_count": len(result.warnings),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
