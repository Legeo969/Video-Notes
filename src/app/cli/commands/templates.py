import sys
import os
import argparse

from src.app.cli.registry import CliCommand
from src.application.notes.template_loader import get_template_registry


def _cmd_template_list() -> None:
    registry = get_template_registry()
    templates = registry.list_templates()
    if not templates:
        print("📭 没有可用模板")
        return

    print("\n可用模板：\n")
    for t in templates:
        required = [s.title for s in t.sections if s.required]
        optional = [s.title for s in t.sections if not s.required]
        print(f"  {t.id:<20} {t.name}")
        print(f"  {'':>20} {t.description}")
        print(f"  {'':>20} 必需章节：{', '.join(required)}")
        if optional:
            print(f"  {'':>20} 可选章节：{', '.join(optional)}")
        if t.output.with_citations:
            print(f"  {'':>20} 📎 默认启用来源引用")
        print()


def _cmd_template_preview(spec: str) -> None:
    from src.application.notes.template_loader import _load_yaml_file
    from pathlib import Path

    registry = get_template_registry()

    if os.path.isfile(spec):
        try:
            template = _load_yaml_file(Path(spec))
            registry.register(template)
        except Exception as e:
            print(f"❌ 无法加载模板文件: {e}")
            sys.exit(1)
    elif spec in registry:
        template = registry.get(spec)
    else:
        print(f"❌ 未找到模板 '{spec}'。用 --template-list 查看可用模板。")
        sys.exit(1)

    print()
    print(registry.preview_template(template.id))


def _cmd_template_validate(filepath: str) -> None:
    from src.application.notes.template_validator import TemplateValidator

    validator = TemplateValidator()
    result = validator.validate_template_file(filepath)

    if not result.warnings:
        print(f"\n✅ 模板文件 '{filepath}' 验证通过，没有发现任何问题。")
        return

    error_count = len(result.errors)
    warning_count = len(result.warnings) - error_count

    print(f"\n📋 模板校验结果: {filepath}")
    print(f"   错误: {error_count} 个")
    print(f"   警告: {warning_count} 个")
    print()

    for w in result.warnings:
        icon = "❌" if w.level == "error" else "⚠️ "
        section_info = f" [{w.section_id}]" if w.section_id else ""
        print(f"   {icon}{section_info} {w.message}")

    if not result.passed:
        print(f"\n❌ 模板有 {error_count} 个错误，需要修复后才能使用。")
    else:
        print(f"\n✅ 模板结构合法，可以正常使用（{warning_count} 个非致命警告）。")


def _cmd_template_recommend(query: str) -> None:
    from src.application.notes.template_recommender import recommend_templates

    results = recommend_templates(query)

    if not results or results[0][0] is None:
        print("📭 未找到匹配的模板。可用模板列表：")
        _cmd_template_list()
        return

    print(f"\n🔍 输入: {query}")
    print(f"   推荐模板（按匹配度排序）：\n")

    for tmpl, keyword, weight in results:
        if tmpl is None:
            continue
        bar = "█" * int(weight * 10) + "░" * (10 - int(weight * 10))
        match_info = f"匹配词: '{keyword}'" if keyword else "默认推荐"
        print(f"   {tmpl.id:<20} {bar} {weight:.0%}  | {match_info}")
        print(f"   {'':>20} {tmpl.description}")
        print()

    print("   提示: 使用 --template-preview <id> 查看模板详情")


class TemplateListCommand:
    name = "template-list"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "template_list", False)

    def run(self, args: argparse.Namespace) -> int:
        _cmd_template_list()
        return 0


class TemplatePreviewCommand:
    name = "template-preview"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "template_preview", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_template_preview(args.template_preview)
        return 0


class TemplateValidateCommand:
    name = "template-validate"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "template_validate", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_template_validate(args.template_validate)
        return 0


class TemplateRecommendCommand:
    name = "template-recommend"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "template_recommend", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_template_recommend(args.template_recommend)
        return 0


def register_templates(registry):
    registry.register(TemplateListCommand())
    registry.register(TemplatePreviewCommand())
    registry.register(TemplateValidateCommand())
    registry.register(TemplateRecommendCommand())
