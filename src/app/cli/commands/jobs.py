import os
import argparse

from src.app.cli.registry import CliCommand


def _cmd_job_list(output_dir):
    from src.application.services.job_queue import JobQueue, get_default_db_path

    db_path = get_default_db_path(output_dir)
    jq = JobQueue(db_path, output_dir=output_dir)
    jobs = jq.list_jobs(limit=50)
    if not jobs:
        print("📭 暂无任务记录")
        return
    print(f"\n📋 任务历史（最近 {len(jobs)} 条）:\n")
    print(f"{'ID':>5} | {'状态':<8} | {'阶段':<20} | {'来源':<6} | {'标题':<32} | {'时间'}")
    print("-" * 100)
    for j in jobs:
        title = (j.title or j.input)[:30]
        stage_label = j.state.label[:18] if j.state else "?"
        status_icon = {"completed": "✅", "failed": "❌", "running": "🔄", "cancelled": "⏹️"}.get(j.status, "❓")
        from .provenance import _get_provenance_status_for_display
        pp_icon = " "
        try:
            pv = _get_provenance_status_for_display(db_path, j.job_id)
            if pv["is_citation_ready"]:
                pp_icon = "📎"
            elif pv["indexed"]:
                pp_icon = "📄"
        except Exception:
            pass
        time_str = j.started_at or ""
        print(f"{j.id:>5} | {status_icon} {j.status:<6} | {stage_label:<18} | {pp_icon:<6} | {title:<30} | {time_str}")
    print(f"\n  📎=引用就绪  📄=已索引  (空)=未索引")


def _cmd_job_status(run_id, output_dir):
    from src.application.services.job_queue import JobQueue, get_default_db_path
    from .collections import _get_job_collections

    db_path = get_default_db_path(output_dir)
    jq = JobQueue(db_path, output_dir=output_dir)
    try:
        run_id_int = int(run_id)
        job = jq.get_job(run_id_int)
        if not job:
            print(f"❌ 未找到任务 #{run_id_int}，用 --job-list 查看可用任务")
            return
        print(f"\n📋 任务 #{job.id} 详情:\n")
        print(f"  状态:     {job.state.label}")
        print(f"  输入:     {job.input}")
        print(f"  标题:     {job.title or '(无)'}")
        print(f"  开始时间: {job.started_at}")
        print(f"  结束时间: {job.completed_at or '进行中…'}")
        print(f"  耗时:     {job.elapsed_sec:.1f}s" if job.elapsed_sec else "  耗时:     N/A")
        print(f"  产物:     {job.output_path or '(无)'}")
        if job.error_message:
            print(f"  错误:     {job.error_message}")
        print(f"  工作目录: {job.job_dir or '(无)'}")

        from .provenance import _get_provenance_status_for_display
        pv = _get_provenance_status_for_display(db_path, job.job_id)
        if pv["indexed"]:
            print(f"  来源索引: ✅ 已索引 ({pv['segments']}段/{pv['frames']}帧/{pv['total_sources']}来源)")
            print(f"  引用就绪: {'✅ 是' if pv['is_citation_ready'] else '⚠️ 否'}")
        else:
            print(f"  来源索引: ⚠️ 未索引（运行 --reindex-job {run_id_int} 建立）")

        if job.job_dir:
            val_path = os.path.join(job.job_dir, "artifacts", "template_validation.json")
            if os.path.isfile(val_path):
                try:
                    import json
                    with open(val_path, "r", encoding="utf-8") as f:
                        tv = json.load(f)
                    tmpl_id = tv.get("template_id", "?")
                    wc = tv.get("warning_count", 0)
                    ec = sum(1 for w in tv.get("warnings", []) if w.get("level") == "error")
                    if tv.get("valid", True):
                        if wc == 0:
                            print(f"  模板校验: ✅ {tmpl_id}（无警告）")
                        else:
                            print(f"  模板校验: ⚠️  {tmpl_id}（{wc} 个警告）")
                    else:
                        print(f"  模板校验: ❌ {tmpl_id}（{ec} 个错误, {wc - ec} 个警告）")
                except Exception:
                    pass

        try:
            coll_names = _get_job_collections(db_path, str(job.job_id))
            if coll_names:
                print(f"  所属集合:")
                for cn in coll_names:
                    print(f"    - {cn}")
        except Exception:
            pass

    except ValueError:
        print(f"❌ 无效任务 ID: {run_id}，应为整数")


class JobListCommand:
    name = "job-list"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "job_list", False)

    def run(self, args: argparse.Namespace) -> int:
        _cmd_job_list(args.output)
        return 0


class JobStatusCommand:
    name = "job-status"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "job_status", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_job_status(args.job_status, args.output)
        return 0


def register_jobs(registry):
    registry.register(JobListCommand())
    registry.register(JobStatusCommand())
