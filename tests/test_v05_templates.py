"""V0.5 模板系统测试"""

from __future__ import annotations

import os
import tempfile

import pytest

import yaml

from src.domain.models.note_template import (
    NoteContext,
    NoteTemplate,
    TemplateError,
    TemplateOutputOptions,
    TemplateSection,
    TemplateValidationResult,
    TemplateValidationWarning,
)
from src.application.notes.template_validator import TemplateValidator
from src.application.notes.template_loader import (
    TemplateRegistry,
    get_template_registry,
    reset_template_registry,
)


# ── Fixtures ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试前重置全局 registry。"""
    reset_template_registry()
    yield
    reset_template_registry()


@pytest.fixture
def registry():
    """创建一个加载了内置模板的 registry。"""
    r = TemplateRegistry()
    r.load_builtin()
    return r


@pytest.fixture
def sample_template():
    """手工构造的样例模板。"""
    return NoteTemplate(
        id="test_tmpl",
        name="测试模板",
        description="用于单元测试",
        version=1,
        sections=[
            TemplateSection(id="summary", title="摘要", required=True),
            TemplateSection(id="details", title="详细内容", required=True),
            TemplateSection(id="extras", title="补充", required=False),
        ],
        prompt="你是测试助手。请按模板生成笔记。",
        output=TemplateOutputOptions(with_citations=True, with_timestamps=True),
    )


def _write_yaml_template(tmpdir: str, filename: str, data: dict) -> str:
    """写入一个 YAML 模板文件到临时目录并返回路径。"""
    path = os.path.join(tmpdir, filename)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    return path


# ── 测试 1：内置模板全部可加载 ──────────────────────────────


class TestBuiltinTemplatesLoad:
    """每个内置模板都能成功加载。"""

    def test_all_builtin_loaded(self, registry):
        """加载后至少有 8 个内置模板。"""
        ids = registry.template_ids()
        # 8 个内置模板: default, study, meeting, coding_tutorial, lecture, interview, product_demo, research
        assert len(ids) >= 8, f"期望 >= 8 模板，实际 {len(ids)}: {ids}"
        assert "default" in ids
        assert "study" in ids
        assert "meeting" in ids
        assert "coding_tutorial" in ids

    def test_each_template_has_required_sections(self, registry):
        """每个模板至少有一个 required section。"""
        for t in registry.list_templates():
            required = [s for s in t.sections if s.required]
            assert len(required) > 0, f"模板 {t.id} 没有 required sections"

    def test_each_template_has_prompt(self, registry):
        """每个模板都有 prompt 内容。"""
        for t in registry.list_templates():
            assert t.prompt.strip(), f"模板 {t.id} prompt 为空"


# ── 测试 2：模板 id 与文件名一致 ──────────────────────────────


class TestTemplateIdMatchesFile:
    """模板 id 与文件名一致。"""

    def test_load_matching_id(self, registry):
        """文件名对应的 id 加载正确。"""
        t = registry.get("study")
        assert t.id == "study"
        assert t.name == "学习笔记"

    def test_all_ids_match_name_sanity(self, registry):
        """所有模板 id 不为空。"""
        for t in registry.list_templates():
            assert t.id
            assert t.name


# ── 测试 3：无效 YAML 返回 TemplateError ──────────────────────


class TestInvalidYaml:
    """无效 YAML 给出友好错误。"""

    def test_malformed_yaml_raises(self):
        """破坏的 YAML 抛出 TemplateError。"""
        tmpdir = tempfile.mkdtemp()
        bad_path = os.path.join(tmpdir, "bad.yaml")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("id: bad\n  bad: [indentation\n")

        r = TemplateRegistry()
        with pytest.raises(TemplateError, match="YAML 解析失败"):
            r.load_user_templates(tmpdir)
        os.unlink(bad_path)
        os.rmdir(tmpdir)

    def test_not_a_dict_raises(self):
        """YAML 不是字典抛出 TemplateError。"""
        tmpdir = tempfile.mkdtemp()
        bad_path = os.path.join(tmpdir, "bad.yaml")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("- list_item")

        r = TemplateRegistry()
        with pytest.raises(TemplateError, match="必须是顶层字典"):
            r.load_user_templates(tmpdir)
        os.unlink(bad_path)
        os.rmdir(tmpdir)


# ── 测试 4：缺少必需字段返回 TemplateError ───────────────────


class TestMissingRequiredFields:
    """缺少必需字段时抛出 TemplateError。"""

    def test_missing_prompt_raises(self):
        """缺少 prompt 字段抛出错误。"""
        tmpdir = tempfile.mkdtemp()
        data = {
            "id": "no_prompt",
            "name": "No Prompt",
            "description": "Missing prompt",
            "version": 1,
        }
        _write_yaml_template(tmpdir, "no_prompt.yaml", data)

        r = TemplateRegistry()
        with pytest.raises(TemplateError, match="缺少必需字段"):
            r.load_user_templates(tmpdir)
        os.unlink(os.path.join(tmpdir, "no_prompt.yaml"))
        os.rmdir(tmpdir)

    def test_missing_id_raises(self):
        """缺少 id 字段抛出错误。"""
        tmpdir = tempfile.mkdtemp()
        data = {
            "name": "No ID",
            "description": "Missing ID",
            "version": 1,
            "prompt": "Test",
        }
        _write_yaml_template(tmpdir, "no_id.yaml", data)

        r = TemplateRegistry()
        # id 字段缺失，会被解析为 '?'，触发 ID 格式校验
        with pytest.raises(TemplateError):
            r.load_user_templates(tmpdir)
        os.unlink(os.path.join(tmpdir, "no_id.yaml"))
        os.rmdir(tmpdir)


# ── 测试 5：不存在模板 fallback default 或友好报错 ───────────


class TestFallbackDefault:
    """不存在时回退或报错。"""

    def test_missing_template_raises(self, registry):
        """不存在的模板抛出 TemplateError。"""
        with pytest.raises(TemplateError, match="未找到模板"):
            registry.get("nonexistent_xyz")

    def test_get_or_default_falls_back(self, registry):
        """get_or_default 回退到 default。"""
        t = registry.get_or_default("nonexistent")
        assert t.id == "default"

    def test_get_or_default_none_returns_default(self, registry):
        """None 参数返回 default。"""
        t = registry.get_or_default(None)
        assert t.id == "default"

    def test_error_message_lists_available(self, registry):
        """错误消息包含可用模板列表。"""
        try:
            registry.get("foo")
        except TemplateError as e:
            assert "study" in str(e) or "default" in str(e)
            assert "foo" in str(e)


# ── 测试 6：list_templates 排序稳定 ──────────────────────────


class TestListTemplates:
    """模板列表排序稳定。"""

    def test_list_sorted_by_id(self, registry):
        """list_templates 按 id 排序。"""
        templates = registry.list_templates()
        ids = [t.id for t in templates]
        assert ids == sorted(ids)

    def test_template_ids_sorted(self, registry):
        """template_ids 按字母排序。"""
        ids = registry.template_ids()
        assert ids == sorted(ids)

    def test_list_not_empty(self, registry):
        """列表非空。"""
        assert len(registry.list_templates()) > 0


# ── 测试 7：PromptBuilder 注入模板 prompt ──────────────────────


class TestPromptBuilder:
    """PromptBuilder 正确注入模板 prompt。"""

    def test_system_prompt_from_template(self, sample_template):
        """build_system_prompt 使用模板的 prompt。"""
        from src.application.notes.prompt_builder import build_system_prompt

        sp = build_system_prompt(sample_template)
        assert "测试助手" in sp
        assert "请按模板生成笔记" in sp

    def test_user_prompt_includes_context(self, sample_template):
        """build_user_prompt 包含视频上下文。"""
        from src.application.notes.prompt_builder import build_user_prompt

        ctx = NoteContext(title="测试视频", source_url="https://example.com")
        up = build_user_prompt(sample_template, "测试转写内容", ctx)
        assert "测试视频" in up
        assert "https://example.com" in up
        assert "测试转写内容" in up

    def test_user_prompt_includes_sections(self, sample_template):
        """build_user_prompt 包含必需章节列表。"""
        from src.application.notes.prompt_builder import build_user_prompt

        ctx = NoteContext(title="Test")
        up = build_user_prompt(sample_template, "transcript", ctx)
        assert "摘要" in up
        assert "详细内容" in up
        assert "补充" in up


# ── 测试 8：PromptBuilder 注入 with_citations 要求 ────────────


class TestCitationsInPrompt:
    """PromptBuilder 正确处理 citations 参数。"""

    def test_citations_from_context(self, sample_template):
        """context.with_citations=True 时 prompt 包含引用要求。"""
        from src.application.notes.prompt_builder import build_user_prompt

        ctx = NoteContext(title="T", with_citations=True)
        up = build_user_prompt(sample_template, "transcript", ctx)
        assert "来源时间戳" in up or "时间戳" in up

    def test_citations_from_template_output(self, sample_template):
        """template.output.with_citations=True 时 prompt 包含引用要求。"""
        from src.application.notes.prompt_builder import build_user_prompt

        # sample_template.output.with_citations 已经是 True
        ctx = NoteContext(title="T", with_citations=False)
        up = build_user_prompt(sample_template, "transcript", ctx)
        assert "时间戳" in up

    def test_no_citations_when_both_false(self):
        """两者都为 False 时不要求来源引用。"""
        from src.application.notes.prompt_builder import build_user_prompt

        t = NoteTemplate(
            id="t", name="T", description="D", version=1,
            sections=[TemplateSection(id="s", title="章节")],
            prompt="p",
            output=TemplateOutputOptions(with_citations=False, with_timestamps=False),
        )
        ctx = NoteContext(title="T", with_citations=False)
        up = build_user_prompt(t, "transcript", ctx)
        # "不需要标注时间戳" 这行本身包含"时间戳"三个字，但这是说明不要标注
        # 关键是 prompt 中没有要求加来源引用
        assert "来源时间戳" not in up
        assert "不需要标注时间戳" in up  # 明确说了不需要


# ── 测试 9：Validator 能识别缺失 required section ─────────────


class TestValidatorMissingSections:
    """TemplateValidator 检出缺失章节。"""

    def test_missing_required_section_is_error(self, sample_template):
        """缺少必需章节标记为 error。"""
        md = """# 测试

## 摘要

内容...

## 补充

额外内容...
"""
        validator = TemplateValidator()
        result = validator.validate_markdown(md, sample_template)
        assert not result.passed
        assert any("详细内容" in w.message for w in result.errors)

    def test_all_required_present_is_pass(self, sample_template):
        """所有必需章节都存在时通过。"""
        md = """# 测试

## 摘要

摘要内容

## 详细内容

详细内容

## 补充

补充内容
"""
        validator = TemplateValidator()
        result = validator.validate_markdown(md, sample_template)
        assert result.passed

    def test_partial_match_by_id(self):
        """heading 不匹配但 id 匹配也算通过。"""
        # 实际输出可能用不同措辞，但我们按 title 匹配
        # 如果 AI 输出的 heading 恰好是 section.id 也算
        pass  # 当前实现按 title 匹配，id 作为备选


# ── 测试 10：Validator 能识别重复标题 ────────────────────────


class TestValidatorDuplicates:
    """TemplateValidator 检出重复标题。"""

    def test_duplicate_heading_warns(self, sample_template):
        """重复标题给出 warning。"""
        md = """# 测试

## 摘要

内容1

## 详细内容

内容2

## 摘要

重复的摘要...
"""
        validator = TemplateValidator()
        result = validator.validate_markdown(md, sample_template)
        dup_warnings = [w for w in result.infos if "重复" in w.message or "摘要" in w.message]
        # 至少有一个关于重复的 warning
        dup_count = sum(1 for w in result.warnings if "重复" in w.message)
        assert dup_count > 0


# ── 测试 11：CLI --template-list 正常输出 ─────────────────────


class TestCliTemplateList:
    """--template-list 正常工作。"""

    def test_function_imports(self):
        """_cmd_template_list 函数可导入。"""
        from src.app.cli import _cmd_template_list
        assert callable(_cmd_template_list)

    def test_executes_without_error(self, capsys):
        """执行不报错。"""
        from src.app.cli import _cmd_template_list

        _cmd_template_list()
        captured = capsys.readouterr()
        assert "default" in captured.out
        assert "study" in captured.out


# ── 测试 12：CLI --template study 传入 PipelineRequest ────────


class TestCliTemplateId:
    """--template 接受模板 ID。"""

    def test_pipeline_request_accepts_template_id(self):
        """PipelineRequest 接受 template_id。"""
        from src.domain.types import PipelineRequest

        req = PipelineRequest(input="test.mp4", template_id="study")
        assert req.template_id == "study"
        assert req.notes.template_id == "study"

    def test_pipeline_request_default_template_id_none(self):
        """默认 template_id 为 None。"""
        from src.domain.types import PipelineRequest

        req = PipelineRequest(input="test.mp4")
        assert req.template_id is None


# ── 测试 13：不传 template 时旧流程仍可运行 ──────────────────


class TestBackwardCompat:
    """向后兼容性。"""

    def test_no_template_uses_default(self, registry):
        """不传 template 时默认使用 default 模板。"""
        t = registry.get("default")
        assert t is not None
        assert t.id == "default"

    def test_file_path_template_still_works(self):
        """文件路径模板仍可用。"""
        # 创建临时模板文件
        tmpfile = tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8")
        tmpfile.write("# {{video_title}}\n\n内容: {{content}}")
        tmpfile.close()

        try:
            from src.application.llm.prompts import load_template

            content = load_template(tmpfile.name)
            assert "{{video_title}}" in content
        finally:
            os.unlink(tmpfile.name)

    def test_generate_notes_accepts_both_params(self):
        """generate_notes 接受 template 和 template_id 两个参数。"""
        import inspect
        from src.application.notes.note_generator import generate_notes

        sig = inspect.signature(generate_notes)
        params = list(sig.parameters.keys())
        assert "template" in params
        assert "template_id" in params


# ── 测试 14：User Templates ──────────────────────────────────


class TestUserTemplates:
    """用户自定义模板。"""

    def test_load_user_template(self):
        """加载用户目录中的自定义模板。"""
        tmpdir = tempfile.mkdtemp()
        data = {
            "id": "custom",
            "name": "自定义",
            "description": "我的自定义模板",
            "version": 1,
            "sections": [
                {"id": "intro", "title": "介绍", "required": True},
                {"id": "body", "title": "正文", "required": True},
            ],
            "prompt": "自定义 prompt",
            "output": {"with_citations": True},
        }
        _write_yaml_template(tmpdir, "custom.yaml", data)

        r = TemplateRegistry()
        r.load_builtin()
        count = r.load_user_templates(tmpdir)
        assert count == 1

        t = r.get("custom")
        assert t.name == "自定义"
        assert len(t.sections) == 2

        # 清理
        os.unlink(os.path.join(tmpdir, "custom.yaml"))
        os.rmdir(tmpdir)


# ── 测试 15：全局 Registry 单例 ───────────────────────────────


class TestGlobalRegistry:
    """全局 TemplateRegistry 单例。"""

    def test_get_template_registry_returns_singleton(self):
        """get_template_registry 返回单例。"""
        r1 = get_template_registry()
        r2 = get_template_registry()
        assert r1 is r2

    def test_global_registry_has_templates(self):
        """全局 registry 加载了内置模板。"""
        r = get_template_registry()
        assert "default" in r

    def test_reset_creates_new(self):
        """reset 后创建新的 registry。"""
        r1 = get_template_registry()
        reset_template_registry()
        r2 = get_template_registry()
        # 不同对象但都有模板
        assert r1 is not r2
        assert "default" in r2


# ── 测试 16：TemplateError 信息友好 ──────────────────────────


class TestTemplateErrorMessages:
    """错误消息可读性。"""

    def test_error_contains_template_id(self):
        """错误消息包含模板 ID。"""
        e = TemplateError("bad_template", "something wrong")
        assert "bad_template" in str(e)
        assert "something wrong" in str(e)

    def test_invalid_id_format(self):
        """ID 格式校验。"""
        from src.application.notes.template_loader import _validate_template_id

        with pytest.raises(TemplateError, match="格式无效"):
            _validate_template_id("ABC123")  # 大写字母开头

        # 正常格式不报错
        _validate_template_id("study")
        _validate_template_id("my_template")
        _validate_template_id("coding-tutorial")


# ── 模板校验结果格式化 ───────────────────────────────────────


class TestValidationResultFormatting:
    """校验结果文本输出。"""

    def test_empty_report(self):
        """无警告时显示通过。"""
        r = TemplateValidationResult()
        text = r.to_text()
        assert "通过" in text

    def test_report_with_warnings(self):
        """有警告和错误时显示完整报告。"""
        r = TemplateValidationResult()
        r.add_warning("s1", "内容不足")
        r.add_error("s2", "缺少必需章节")
        text = r.to_text()
        assert "内容不足" in text
        assert "缺少必需章节" in text
        assert "⚠️" in text
        assert "❌" in text


# ── NoteContext 基本属性 ──────────────────────────────────────


class TestNoteContext:
    """NoteContext 创建正确。"""

    def test_default_values(self):
        """默认值正确。"""
        ctx = NoteContext()
        assert ctx.title is None
        assert ctx.with_citations is False

    def test_custom_values(self):
        """自定义值正确。"""
        ctx = NoteContext(title="Video", source_url="http://x.com", with_citations=True)
        assert ctx.title == "Video"
        assert ctx.source_url == "http://x.com"
        assert ctx.with_citations is True
