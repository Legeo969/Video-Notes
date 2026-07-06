from __future__ import annotations

from pathlib import Path

from src.api.handlers.diagnostics import create_diagnostics_handlers
from src.application.services.job_queue import get_legacy_jobs_root


def test_storage_status_reports_split_paths(tmp_path: Path) -> None:
    handlers = create_diagnostics_handlers(output_dir=str(tmp_path / "exports"))

    status = handlers["storage.status"]({})

    assert status["export_dir"].endswith("exports")
    assert status["db_path"].endswith("state\\video_notes.db") or status["db_path"].endswith("state/video_notes.db")
    assert status["jobs_root"].endswith("jobs")
    assert status["legacy_jobs_root"].endswith(".jobs")


def test_storage_cleanup_orphans_sweeps_legacy_jobs(tmp_path: Path) -> None:
    legacy_orphan = Path(get_legacy_jobs_root()) / "resume-test"
    legacy_orphan.mkdir(parents=True)
    handlers = create_diagnostics_handlers(output_dir=str(tmp_path / "exports"))

    result = handlers["storage.cleanup_orphans"]({"min_age_hours": 0})

    assert result["removed"] == 1
    assert not legacy_orphan.exists()
