import os
import re
import sys
import argparse

from src.application.pipeline.video_pipeline import process_url, process_local
from src.application.pipeline.batch_pipeline import BatchJob
from src.application.services.job_queue import JobQueue, get_default_db_path
from src.infrastructure.video.yt_dlp_compat import set_bilibili_cookie_path
from src.application.vision import is_image, ImageProcessor
from src.application.notes.template_loader import get_template_registry
from src.app.cli.registry import CliCommand


def _is_url(input_str):
    return input_str.startswith("http://") or input_str.startswith("https://")


def _process_image(file_path, output_dir, gpt_model, api_key=None, base_url=None, title=None, vault_path=None):
    from src.application.llm import get_provider
    from src.utils.system import _safe_dirname

    provider = get_provider()
    if api_key and hasattr(provider, 'api_key'):
        provider.api_key = api_key
    if base_url and hasattr(provider, 'base_url'):
        provider.base_url = base_url

    processor = ImageProcessor(provider)
    print(f"🖼️  检测到图片，启动多模态分析...")
    result = processor.analyze(file_path, model=gpt_model)

    if not title:
        title = os.path.splitext(os.path.basename(file_path))[0]

    video_dir = os.path.join(output_dir, _safe_dirname(title))
    os.makedirs(video_dir, exist_ok=True)

    safe_name = re.sub(r'[\\/:*?"<>|]', '_', title)[:80] or "笔记"
    notes_path = os.path.join(video_dir, f"{safe_name}.md")
    with open(notes_path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        f.write(result["analysis"])
    print(f"📝 笔记已保存: {notes_path}")

    if vault_path is not None:
        from src.vault_writer import archive_to_obsidian
        archive_to_obsidian(notes_path, vault_path, title)

    return notes_path


def _resolve_template(template_id_or_path, process_kwargs):
    if template_id_or_path is None:
        return
    if template_id_or_path == "auto":
        process_kwargs["template_id"] = "auto"
        return
    try:
        registry = get_template_registry()
        if template_id_or_path in registry:
            process_kwargs["template_id"] = template_id_or_path
        elif os.path.isfile(template_id_or_path):
            process_kwargs["template"] = template_id_or_path
        else:
            available = ", ".join(registry.template_ids())
            print(f"❌ 未找到模板 '{template_id_or_path}'。可用模板：{available}", file=sys.stderr)
            print(f"   也可以传入模板文件路径（.md 文件）", file=sys.stderr)
            sys.exit(1)
    except Exception:
        if os.path.isfile(template_id_or_path):
            process_kwargs["template"] = template_id_or_path
        else:
            print(f"❌ 模板文件不存在: {template_id_or_path}", file=sys.stderr)
            sys.exit(1)


def _build_process_kwargs(args):
    legacy_style = getattr(args, "style", None)
    detail_level = getattr(args, "detail_level", None)
    effective_detail = detail_level or (
        legacy_style if legacy_style in {"concise", "detailed"} else None
    )
    style = {
        "concise": "简洁",
        "standard": None,
        "detailed": "详细",
    }.get(effective_detail)

    process_kwargs = dict(
        whisper_model=args.model,
        output_dir=args.output,
        language=args.lang,
        gpt_model=args.gpt_model,
        model_dir=getattr(args, 'model_dir', None),
        frame_interval=getattr(args, 'frame_interval', 30),
        frame_mode=getattr(args, 'frame_mode', 'auto'),
        max_frames=getattr(args, 'max_frames', 30),
    )

    if args.title and not args.batch_file:
        process_kwargs["title"] = args.title
    if args.api_key:
        process_kwargs["api_key"] = args.api_key
    if args.base_url:
        process_kwargs["base_url"] = args.base_url
    if args.obsidian_vault:
        process_kwargs["vault_path"] = args.obsidian_vault
    if legacy_style == "tutorial" and not args.template:
        process_kwargs["template_id"] = "coding_tutorial"
    elif legacy_style == "notes" and not args.template:
        process_kwargs["template_id"] = "study"
    if args.template:
        _resolve_template(args.template, process_kwargs)
    if args.subtitle_format != "none":
        process_kwargs["subtitle_format"] = args.subtitle_format
    process_kwargs["temperature"] = args.temperature
    process_kwargs["style"] = style
    if getattr(args, 'smart_summary', False):
        process_kwargs["smart_summary"] = True
    if getattr(args, 'ocr_enabled', False):
        process_kwargs["ocr_enabled"] = True
    process_kwargs["blocks"] = not getattr(args, 'no_blocks', False)
    process_kwargs["with_citations"] = args.with_citations

    plugin_manager = None
    if not getattr(args, 'no_plugins', False):
        try:
            from src.application.plugin import PluginManager
            plugin_manager = PluginManager()
        except Exception:
            pass
    if plugin_manager is not None:
        process_kwargs["plugin_manager"] = plugin_manager

    if args.collection_id:
        process_kwargs["collection_id"] = args.collection_id

    if args.resume:
        db_path = get_default_db_path(args.output)
        jq = JobQueue(db_path, output_dir=args.output)
        process_kwargs["resume_run_id"] = args.resume
        process_kwargs["job_queue"] = jq

        resumable = jq.get_resumable_stage(args.resume)
        if resumable is None:
            job = jq.get_job(args.resume)
            if job and job.status == "completed":
                print(f"ℹ️  任务 #{args.resume} 已完成，无需恢复")
                sys.exit(0)
            else:
                print(f"❌ 无法恢复任务 #{args.resume}，用 --job-list 检查状态")
                sys.exit(1)
        print(f"🔄 从「{resumable.label}」阶段恢复任务 #{args.resume}")

    return process_kwargs


class ProcessCommand:
    name = "process"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return bool(args.input) or bool(getattr(args, "batch_file", None))

    def run(self, args: argparse.Namespace) -> int:
        from dotenv import load_dotenv
        load_dotenv()
        if args.bilibili_cookies:
            set_bilibili_cookie_path(args.bilibili_cookies)

        if args.batch_file and args.input:
            print("input 参数与 --batch/--file-list 不可同时使用", file=sys.stderr)
            return 1

        process_kwargs = _build_process_kwargs(args)

        if args.batch_file:
            if not os.path.isfile(args.batch_file):
                print(f"❌ 批处理文件不存在: {args.batch_file}", file=sys.stderr)
                return 1

            with open(args.batch_file, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]

            if not lines:
                print("❌ 批处理文件为空", file=sys.stderr)
                return 1

            job = BatchJob()
            for line in lines:
                parts = line.split("|", 1)
                input_path = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else None
                job.add_item(input_path, title=title)

            def _dispatch(item_input, **kw):
                item_title = None
                for it in job.items:
                    if it.input == item_input:
                        item_title = it.title
                        break
                kw_copy = dict(kw)
                if item_title:
                    kw_copy["title"] = item_title
                if _is_url(item_input):
                    return process_url(url=item_input, **kw_copy)
                else:
                    return process_local(file_path=item_input, **kw_copy)

            print(f"🚀 批处理启动：共 {job.count} 个任务")
            job.run_all(_dispatch, **process_kwargs)
            print(f"\n{job.format_summary()}")
            return 0

        if not args.input:
            print("请提供 input 参数，或使用 --batch/--file-list 指定批处理文件", file=sys.stderr)
            return 1

        try:
            if is_image(args.input):
                notes_path = _process_image(
                    file_path=args.input,
                    output_dir=args.output,
                    gpt_model=args.gpt_model,
                    api_key=args.api_key,
                    base_url=args.base_url,
                    title=args.title,
                    vault_path=args.obsidian_vault,
                )
            elif _is_url(args.input):
                notes_path = process_url(url=args.input, **process_kwargs)
            else:
                notes_path = process_local(file_path=args.input, **process_kwargs)
            print(f"\n🎉 完成！笔记保存在: {notes_path}")
        except FileNotFoundError as e:
            print(f"\n❌ 文件错误: {e}", file=sys.stderr)
            return 1
        except RuntimeError as e:
            print(f"\n❌ 运行错误: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"\n❌ 未知错误: {e}", file=sys.stderr)
            return 1

        return 0


def register_process(registry):
    registry.register(ProcessCommand())
