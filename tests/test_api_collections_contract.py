from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.api.handlers.collections import create_collections_handlers
from src.api.protocol.errors import InvalidParams
from src.application.services.job_queue import JobQueue, get_default_db_path
from src.application.services.task_supervisor import TaskSupervisor
from src.domain.types import PipelineResult


def test_collection_handlers_keep_database_open_for_request(tmp_path: Path) -> None:
    """Regression: temporary context manager must not close DB before use."""
    output_dir = str(tmp_path / "notes")
    handlers = create_collections_handlers(output_dir=output_dir)

    assert handlers["collection.list"]({}) == []

    created = handlers["collection.create"]({
        "title": "Regression Collection",
        "collection_type": "course",
    })
    assert created["name"] == "Regression Collection"

    rows = handlers["collection.list"]({})
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["item_count"] == 0

    detail = handlers["collection.get"]({"collection_id": created["id"]})
    assert detail["name"] == "Regression Collection"
    assert detail["item_count"] == 0

    assert handlers["collection.list_items"]({
        "collection_id": created["id"],
    }) == []

    assert handlers["collection.delete"]({"collection_id": created["id"]}) is True
    assert handlers["collection.list"]({}) == []


def test_collection_handlers_cover_tauri_page_contract(tmp_path: Path) -> None:
    output_dir = str(tmp_path / "notes")
    handlers = create_collections_handlers(output_dir=output_dir)

    created = handlers["collection.create"]({
        "name": "Tauri Collection",
        "items": ["first.mp4", "https://example.com/second"],
    })
    collection_id = created["id"]

    detail = handlers["collection.get"]({"id": collection_id})
    assert detail["name"] == "Tauri Collection"
    assert [item["input"] for item in detail["items"]] == [
        "first.mp4",
        "https://example.com/second",
    ]

    added = handlers["collection.add_items"]({
        "id": collection_id,
        "items": ["third.wav"],
    })
    assert added[0]["input"] == "third.wav"

    assert handlers["collection.remove_items"]({
        "id": collection_id,
        "item_ids": [added[0]["id"]],
    }) is True

    detail = handlers["collection.get"]({"id": collection_id})
    assert [item["input"] for item in detail["items"]] == [
        "first.mp4",
        "https://example.com/second",
    ]

    updated = handlers["collection.update"]({
        "id": collection_id,
        "name": "Renamed Collection",
        "description": "updated",
    })
    assert updated["name"] == "Renamed Collection"
    assert updated["description"] == "updated"

    exported = handlers["collection.export"]({"id": collection_id})
    assert Path(exported["path"]).name == collection_id

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "01_intro.mp4").write_text("video")
    imported = handlers["collection.import_folder"]({"path": str(media_dir)})
    assert imported["count"] == 1

    with pytest.raises(InvalidParams):
        handlers["collection.batch_process"]({"id": collection_id})


def test_collection_batch_process_creates_real_task_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output_dir = str(tmp_path / "notes")
    monkeypatch.setenv("VIDEO_NOTES_JOBS_DIR", str(tmp_path / "jobs"))
    job_queue = JobQueue(
        db_path=get_default_db_path(output_dir),
        output_dir=output_dir,
    )

    class FastOrchestrator:
        def run(self, request, **_kwargs):
            return PipelineResult(
                notes_path=str(tmp_path / f"{request.title}.md"),
                transcript_path=str(tmp_path / f"{request.title}.json"),
                title=request.title or "untitled",
                input=request.input,
            )

    supervisor = TaskSupervisor(FastOrchestrator(), job_queue)
    handlers = create_collections_handlers(
        output_dir=output_dir,
        job_queue=job_queue,
        supervisor=supervisor,
    )
    created = handlers["collection.create"]({
        "name": "Batch Collection",
        "items": ["first.mp4", "second.mp4"],
    })

    result = handlers["collection.batch_process"]({"id": created["id"], "opts": {}})

    assert result["batch_job_id"].startswith("batch-")
    assert result["count"] == 2
    assert len(result["run_ids"]) == 2
    assert len(result["job_ids"]) == 2
    for run_id in result["run_ids"]:
        assert job_queue.get_job(run_id) is not None

    detail = handlers["collection.get"]({"id": created["id"]})
    assert [item["job_id"] for item in detail["items"]] == result["job_ids"]

    for _ in range(50):
        if not supervisor.active_run_ids():
            break
        time.sleep(0.02)
    assert supervisor.active_run_ids() == []
