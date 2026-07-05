"""V0.5.1 模板质量闭环测试

覆盖:
- --template-preview (preview_template)
- --template-validate (validate_template_file)
- template_validation.json 写入
- 启发式推荐 (recommend_templates)
- --job-status 模板校验显示
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.domain.models.note_template import (
    NoteTemplate,
    TemplateOutputOptions,
    TemplateSection,
    TemplateValidationResult,
)
from src.application.notes.template_validator import TemplateValidator
from src.application.notes.template_loader import (
    TemplateRegistry,
    get_template_registry,
    reset_template_registry,
)
from src.application.notes.template_recommender import (
    best_template,
    recommend_templates,
)


pytestmark = pytest.mark.core


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前重置全局 registry。"""
    reset_template_registry()
    yield
    reset_template_registry()


@pytest.fixture
def registry():
    r = TemplateRegistry()
    r.load_builtin()
    return r


@pytest.fixture
def sample_template():
    return NoteTemplate(
        id="test_tmpl",
        name="Test Template",
        description="A test template",
        version=1,
        sections=[
            TemplateSection(id="summary", title="摘要", required=True),
            TemplateSection(id="details", title="详细内容", required=True),
            TemplateSection(id="optional", title="补充说明", required=False),
        ],
        prompt="You are a helpful assistant.",
        output=TemplateOutputOptions(with_timestamps=True, with_citations=False),
    )


# ── TemplateRecommender ───────────────────────────────────────


class TestTemplateRecommender:
    """启发式模板推荐测试。"""

    def test_coding_tutorial_match(self, registry):
        """编程关键词 → coding_tutorial。"""
        registry.load_builtin()
        results = recommend_templates("React hooks tutorial for beginners")
        assert len(results) > 0
        assert results[0][0].id == "coding_tutorial"

    def test_meeting_match(self, registry):
        """会议关键词 → meeting。"""
        registry.load_builtin()
        results = recommend_templates("weekly standup meeting sync")
        assert len(results) > 0
        assert results[0][0].id == "meeting"

    def test_lecture_match(self, registry):
        """讲座关键词 → lecture。"""
        registry.load_builtin()
        results = recommend_templates("MIT lecture on machine learning")
        assert len(results) > 0
        assert results[0][0].id == "lecture"

    def test_interview_match(self, registry):
        """访谈关键词 → interview。"""
        registry.load_builtin()
        results = recommend_templates("podcast interview with CEO")
        assert len(results) > 0
        assert results[0][0].id == "interview"

    def test_product_demo_match(self, registry):
        """产品演示关键词 → product_demo。"""
        registry.load_builtin()
        results = recommend_templates("iPhone 16 product demo launch")
        assert len(results) > 0
        assert results[0][0].id == "product_demo"

    def test_research_match(self, registry):
        """论文关键词 → research。"""
        registry.load_builtin()
        results = recommend_templates("arxiv paper on transformers research")
        assert len(results) > 0
        assert results[0][0].id == "research"

    def test_study_match(self, registry):
        """学习关键词 → study。"""
        registry.load_builtin()
        results = recommend_templates("learn python course")
        assert len(results) > 0
        # study 可能和 coding_tutorial 同时匹配，但 study 权重更低
        ids = [t[0].id for t in results]
        assert "study" in ids or "coding_tutorial" in ids

    def test_default_fallback(self, registry):
        """无匹配关键词 → 返回 default。"""
        registry.load_builtin()
        results = recommend_templates("xyz random nonsense text")
        assert len(results) == 1
        assert results[0][0].id == "default"
        assert results[0][1] == ""  # no keyword matched

    def test_chinese_keywords(self, registry):
        """中文关键词匹配。"""
        registry.load_builtin()
        results = recommend_templates("Python编程教程 实战")
        assert len(results) > 0
        assert results[0][0].id == "coding_tutorial"

        results2 = recommend_templates("周会讨论产品需求")
        assert len(results2) > 0
        assert results2[0][0].id == "meeting"

    def test_source_url_considered(self, registry):
        """source_url 也参与关键词匹配。"""
        registry.load_builtin()
        results = recommend_templates(
            "Episode 42",
            source_url="https://youtube.com/watch?v=xxx-docker-tutorial",
        )
        assert len(results) > 0
        assert results[0][0].id == "coding_tutorial"

    def test_best_template_helper(self, registry):
        """best_template() 返回最佳匹配。"""
        registry.load_builtin()
        tmpl = best_template("debugging react components tutorial")
        assert tmpl is not None
        assert tmpl.id == "coding_tutorial"

    def test_higher_weight_wins(self, registry):
        """更高权重的模板优先。"""
        registry.load_builtin()
        # "course" 匹配 study (0.7), "tutorial" 匹配 coding_tutorial (0.9)
        results = recommend_templates("python course tutorial coding")
        assert len(results) > 0
        assert results[0][0].id == "coding_tutorial"  # 0.9 > 0.7


# ── TemplateValidator.validate_template_file ──────────────────


class TestTemplateFileValidation:
    """模板文件结构校验测试。"""

    def _write_temp_yaml(self, data: dict, filename: str = "test.yaml") -> str:
        """写入临时 YAML 文件，返回路径。"""
        tmp = Path(tempfile.gettempdir()) / f"v051_{filename}"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)
        return str(tmp)

    def test_valid_yaml_passes(self):
        """合法 YAML 应通过校验。"""
        data = {
            "id": "test_tmpl",
            "name": "Test Template",
            "description": "A test",
            "version": 1,
            "prompt": "You are helpful.",
            "sections": [
                {"id": "summary", "title": "摘要", "required": True},
                {"id": "details", "title": "详细内容", "required": True},
            ],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert result.passed
        assert len(result.errors) == 0

    def test_missing_required_field(self):
        """缺少必需字段应报错。"""
        data = {
            "id": "test",
            "name": "Test",
            # 缺少 description, prompt, sections
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        assert len(result.errors) > 0
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("description" in m for m in messages)
        assert any("prompt" in m for m in messages)

    def test_invalid_template_id(self):
        """非法的模板 ID 应报错。"""
        data = {
            "id": "Invalid-ID!",
            "name": "Test",
            "description": "Test",
            "version": 1,
            "prompt": "Test prompt",
            "sections": [{"id": "s1", "title": "S1", "required": True}],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("不安全" in m for m in messages)

    def test_duplicate_section_ids(self):
        """重复的 section id 应报错。"""
        data = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "version": 1,
            "prompt": "Test prompt",
            "sections": [
                {"id": "dup", "title": "Section A", "required": True},
                {"id": "dup", "title": "Section B", "required": True},
            ],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("重复" in m for m in messages)

    def test_empty_prompt(self):
        """prompt 为空应报错。"""
        data = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "version": 1,
            "prompt": "",
            "sections": [{"id": "s1", "title": "S1", "required": True}],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("prompt" in m.lower() for m in messages)

    def test_no_required_sections(self):
        """没有任何 required section 应报错。"""
        data = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "version": 1,
            "prompt": "Test prompt",
            "sections": [
                {"id": "s1", "title": "S1", "required": False},
                {"id": "s2", "title": "S2", "required": False},
            ],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("至少" in m for m in messages)

    def test_nonexistent_file(self):
        """不存在的文件应报错。"""
        validator = TemplateValidator()
        result = validator.validate_template_file("/nonexistent/path/template.yaml")
        assert not result.passed
        assert any("不存在" in w.message for w in result.warnings if w.level == "error")

    def test_invalid_yaml_syntax(self):
        """YAML 语法错误应报错。"""
        tmp = Path(tempfile.gettempdir()) / "v051_bad.yaml"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("id: [unclosed\n  bad: ::: syntax\n")
        validator = TemplateValidator()
        result = validator.validate_template_file(str(tmp))
        assert not result.passed
        assert any("YAML" in w.message for w in result.warnings if w.level == "error")

    def test_non_dict_yaml(self):
        """YAML 顶层不是字典应报错。"""
        tmp = Path(tempfile.gettempdir()) / "v051_list.yaml"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("- item1\n- item2\n")
        validator = TemplateValidator()
        result = validator.validate_template_file(str(tmp))
        assert not result.passed
        assert any("字典" in w.message for w in result.warnings if w.level == "error")

    def test_missing_section_title(self):
        """section 缺少 title 应报错。"""
        data = {
            "id": "test",
            "name": "Test",
            "description": "Test",
            "version": 1,
            "prompt": "Test",
            "sections": [
                {"id": "s1", "required": True},  # 缺少 title
            ],
        }
        path = self._write_temp_yaml(data)
        validator = TemplateValidator()
        result = validator.validate_template_file(path)
        assert not result.passed
        messages = [w.message for w in result.warnings if w.level == "error"]
        assert any("title" in m.lower() for m in messages)


# ── TemplateRegistry.preview_template ────────────────────────


class TestTemplatePreview:
    """模板预览测试。"""

    def test_preview_contains_name_and_description(self, registry):
        """预览应包含名称和描述。"""
        registry.load_builtin()
        preview = registry.preview_template("study")
        assert "study" in preview
        assert "学习笔记" in preview or "study" in preview.lower()

    def test_preview_contains_sections(self, registry):
        """预览应包含章节信息。"""
        registry.load_builtin()
        preview = registry.preview_template("study")
        assert "必需章节" in preview or "required" in preview.lower()

    def test_preview_contains_output_options(self, registry):
        """预览应包含输出选项。"""
        registry.load_builtin()
        preview = registry.preview_template("study")
        assert "时间戳" in preview or "timestamps" in preview.lower()
        assert "引用" in preview or "citation" in preview.lower()

    def test_preview_contains_prompt(self, registry):
        """预览应包含 prompt 内容。"""
        registry.load_builtin()
        preview = registry.preview_template("default")
        assert "Prompt" in preview

    def test_preview_nonexistent_raises_error(self, registry):
        """不存在的模板应抛出 TemplateError。"""
        registry.load_builtin()
        from src.domain.models.note_template import TemplateError
        with pytest.raises(TemplateError):
            registry.preview_template("nonexistent_template_xyz")

    def test_preview_custom_template(self, sample_template, registry):
        """自定义模板也能预览。"""
        registry.register(sample_template)
        preview = registry.preview_template("test_tmpl")
        assert "test_tmpl" in preview
        assert "Test Template" in preview


# ── TemplateValidator.to_validation_dict ─────────────────────


class TestValidationDict:
    """validation JSON 序列化测试。"""

    def test_structure(self, sample_template):
        """to_validation_dict 返回正确的结构。"""
        validator = TemplateValidator()
        result = validator.validate_markdown(
            "# 摘要\n\nContent\n\n# 详细内容\n\nMore content",
            sample_template,
        )
        d = validator.to_validation_dict(result, "test_tmpl")
        assert d["template_id"] == "test_tmpl"
        assert "valid" in d
        assert "warnings" in d
        assert "warning_count" in d
        assert "checked_at" in d
        assert isinstance(d["warnings"], list)
        assert d["warning_count"] == len(d["warnings"])

    def test_valid_output(self, sample_template):
        """有效输出 → valid=True。"""
        validator = TemplateValidator()
        result = validator.validate_markdown(
            "# 摘要\n\n" + "x" * 50 + "\n\n# 详细内容\n\n" + "y" * 50,
            sample_template,
        )
        d = validator.to_validation_dict(result, "test_tmpl")
        assert d["valid"] is True

    def test_missing_required_section(self, sample_template):
        """缺少必需章节 → valid=False。"""
        validator = TemplateValidator()
        result = validator.validate_markdown(
            "# Only Summary\n\nSome content here\n\n# 补充说明\n\nExtra",
            sample_template,
        )
        d = validator.to_validation_dict(result, "test_tmpl")
        assert d["valid"] is False
        errors = [w for w in d["warnings"] if w["level"] == "error"]
        assert len(errors) > 0
        assert any("详细内容" in e["message"] for e in errors)

    def test_warnings_have_all_fields(self, sample_template):
        """每个 warning 包含 level, section_id, message。"""
        validator = TemplateValidator()
        result = validator.validate_markdown(
            "# 摘要\n\nshort\n\n# 详细内容\n\nshort\n\n# 补充说明\n\nshort",
            sample_template,
        )
        d = validator.to_validation_dict(result, "test_tmpl")
        for w in d["warnings"]:
            assert "level" in w
            assert "section_id" in w
            assert "message" in w


# ── CLI 集成测试 ──────────────────────────────────────────────


class TestCliIntegration:
    """CLI 命令函数测试。"""

    def test_preview_function_works(self, registry):
        """_cmd_template_preview 可以在 registry 中运行。"""
        registry.load_builtin()
        # 直接测试 preview 方法（CLI 函数在其上包装）
        preview = registry.preview_template("default")
        assert len(preview) > 0
        assert "default" in preview

    def test_validate_function_works(self):
        """_cmd_template_validate 可以校验合法文件。"""
        data = {
            "id": "valid",
            "name": "Valid Template",
            "description": "A valid template",
            "version": 1,
            "prompt": "Be helpful",
            "sections": [{"id": "s1", "title": "Section 1", "required": True}],
        }
        tmp = Path(tempfile.gettempdir()) / "v051_cli_valid.yaml"
        with open(tmp, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True)

        validator = TemplateValidator()
        result = validator.validate_template_file(str(tmp))
        assert result.passed

    def test_recommend_function_works(self, registry):
        """_cmd_template_recommend 返回合理结果。"""
        registry.load_builtin()
        results = recommend_templates("meeting about Q2 planning")
        assert len(results) > 0
        assert results[0][0].id in ["meeting", "default"]


# ── JobRecord 模板字段 ────────────────────────────────────────


class TestJobStatusTemplateInfo:
    """--job-status 模板信息显示测试。"""

    def test_read_template_validation_from_artifacts(self, tmp_path):
        """从 artifacts 读取 template_validation.json。"""
        artifacts = tmp_path / "test_job5" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        val_data = {
            "template_id": "study",
            "valid": True,
            "warnings": [
                {"level": "warning", "section_id": "summary", "message": "章节内容过短"}
            ],
            "warning_count": 1,
            "checked_at": "2026-06-24T10:00:00+00:00",
        }
        val_path = artifacts / "template_validation.json"
        with open(val_path, "w", encoding="utf-8") as f:
            json.dump(val_data, f, ensure_ascii=False)

        # 模拟 --job-status 的读取逻辑
        if val_path.exists():
            with open(val_path, "r", encoding="utf-8") as f:
                tv = json.load(f)
            assert tv["template_id"] == "study"
            assert tv["warning_count"] == 1
            assert tv["valid"] is True

    def test_no_validation_file_no_crash(self, tmp_path):
        """template_validation.json 不存在时不崩溃。"""
        artifacts = tmp_path / "test_job6" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)

        val_path = artifacts / "template_validation.json"
        if val_path.exists():  # 确认不存在
            os.remove(val_path)

        # 模拟 --job-status：找不到文件就跳过
        found = os.path.isfile(str(val_path))
        assert not found  # 文件不存在，正常跳过
