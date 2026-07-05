import os
import sys
import argparse

from src.app.cli.registry import CliCommand


def _get_provenance_status_for_display(db_path, job_id):
    from src.application.provenance.indexer import ProvenanceIndexer
    indexer = ProvenanceIndexer(db_path)
    return indexer.check_provenance_status(job_id)


def _cmd_reindex_job(db_path, output_dir, run_id):
    from src.application.services.job_queue import JobQueue
    from src.application.provenance.indexer import ProvenanceIndexer

    jq = JobQueue(db_path, output_dir=output_dir)
    job = jq.get_job(run_id)
    if not job:
        print(f"❌ 未找到任务 #{run_id}")
        return
    if not job.job_dir:
        print(f"❌ 任务 #{run_id} 没有工作目录，无法重建索引")
        return

    print(f"🔄 正在为任务 #{run_id}「{job.title or job.input}」重建 provenance 索引…")
    indexer = ProvenanceIndexer(db_path)
    result = indexer.index_job(
        job.job_id,
        job_dir=job.job_dir,
        source_uri=job.input,
        title=job.title,
    )

    if result.success:
        print(f"✅ 重建完成：{result.segments_count} 分段 / {result.frames_count} 帧 / "
              f"{result.blocks_count} 知识块 / {result.sources_count} 来源链接")
    else:
        print(f"❌ 重建失败：{result.warnings[0]}" if result.warnings else "未知错误")


def _cmd_reindex_all(db_path, output_dir):
    from src.application.services.job_queue import JobQueue
    from src.application.provenance.indexer import ProvenanceIndexer

    jq = JobQueue(db_path, output_dir=output_dir)
    jobs = jq.list_jobs(limit=500, status="completed")
    if not jobs:
        print("📭 没有已完成的的任务")
        return

    print(f"🔄 正在为 {len(jobs)} 个已完成任务重建 provenance 索引…\n")
    indexer = ProvenanceIndexer(db_path)
    success_count = 0
    fail_count = 0

    for job in jobs:
        if not job.job_dir:
            print(f"  ⏭️  任务 #{job.id}: 无工作目录，跳过")
            fail_count += 1
            continue
        result = indexer.index_job(
            job.job_id,
            job_dir=job.job_dir,
            source_uri=job.input,
            title=job.title,
        )
        if result.success:
            print(f"  ✅ #{job.id}「{job.title or job.input[:40]}」: "
                  f"{result.segments_count}段/{result.frames_count}帧/"
                  f"{result.blocks_count}块/{result.sources_count}来源")
            success_count += 1
        else:
            print(f"  ❌ #{job.id}: {result.warnings}")
            fail_count += 1

    print(f"\n📊 完成: {success_count} 成功 / {fail_count} 失败 / {len(jobs)} 总计")


def _cmd_citation_preview(db_path, output_dir, run_id):
    from src.application.services.job_queue import JobQueue
    from src.application.provenance.indexer import ProvenanceIndexer
    from src.application.provenance.renderer import CitationRenderer

    jq = JobQueue(db_path, output_dir=output_dir)
    job = jq.get_job(run_id)
    if not job:
        print(f"❌ 未找到任务 #{run_id}")
        return
    if not job.job_dir:
        print(f"❌ 任务 #{run_id} 没有工作目录")
        return

    indexer = ProvenanceIndexer(db_path)
    status = indexer.check_provenance_status(job.job_id)
    if not status["indexed"]:
        print(f"⚠️  任务 #{run_id} 尚未建立 provenance 索引，请先运行 --reindex-job {run_id}")
        return

    print(f"📎 任务 #{run_id}「{job.title or job.input}」来源引用预览：\n")
    renderer = CitationRenderer(db_path)
    result = renderer.render(job_id=job.job_id)
    if result:
        print(result)
    else:
        print("(无引用内容)")


class ReindexJobCommand:
    name = "reindex-job"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "reindex_job", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        from src.application.services.job_queue import get_default_db_path
        db_path = get_default_db_path(args.output)
        _cmd_reindex_job(db_path, args.output, args.reindex_job)
        return 0


class ReindexAllCommand:
    name = "reindex-all"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "reindex_all", False)

    def run(self, args: argparse.Namespace) -> int:
        from src.application.services.job_queue import get_default_db_path
        db_path = get_default_db_path(args.output)
        _cmd_reindex_all(db_path, args.output)
        return 0


class CitationPreviewCommand:
    name = "citation-preview"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "citation_preview", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        from src.application.services.job_queue import get_default_db_path
        db_path = get_default_db_path(args.output)
        _cmd_citation_preview(db_path, args.output, args.citation_preview)
        return 0


def register_provenance(registry):
    registry.register(ReindexJobCommand())
    registry.register(ReindexAllCommand())
    registry.register(CitationPreviewCommand())
