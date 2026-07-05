from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.api.event_journal import EventJournal
from src.application.services.job_queue import JobQueue, get_default_db_path
from src.application.services.request_snapshot import (
    pipeline_request_from_snapshot,
    pipeline_request_to_snapshot,
)
from src.application.services.task_supervisor import TaskSupervisor
from src.domain.job_state import JobState
from src.domain.types import PipelineRequest, PipelineResult


def _wait_for_status(queue: JobQueue, run_id: int, expected: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = queue.get_job(run_id)
        if job and job.status == expected:
            return job
        time.sleep(0.01)
    job = queue.get_job(run_id)
    raise AssertionError(f"job {run_id} status={job.status if job else None}, expected={expected}")


def test_request_snapshot_excludes_secrets_and_rehydrates_current_credentials(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "notes-main",
                        "type": "mimo",
                        "api_key": "sk-current-notes",
                        "base_url": "https://notes.example/v1",
                        "models": ["new-default"],
                    },
                    {
                        "name": "vision-main",
                        "type": "openai_compat",
                        "api_key": "sk-current-vision",
                        "base_url": "https://vision.example/v1",
                        "models": ["vision-default"],
                    },
                ],
                "bindings": {
                    "llm": {"provider": "notes-main", "model": "bound-notes"},
                    "vision": {"provider": "vision-main", "model": "bound-vision"},
                },
            }
        ),
        encoding="utf-8",
    )

    request = PipelineRequest(
        input="video.mp4",
        output_dir=str(tmp_path / "output"),
        whisper_model="small",
        gpt_model="snapshot-model",
        provider="mimo",
        api_key="sk-should-never-be-persisted",
        frame_mode="auto",
        max_frames=17,
        vision_enabled=True,
        vision_provider="openai_compat",
        vision_model="snapshot-vision",
        vision_api_key="sk-vision-never-persisted",
        bilibili_cookies="SESSDATA=secret",
    )

    snapshot = pipeline_request_to_snapshot(request)
    encoded = json.dumps(snapshot, ensure_ascii=False)
    assert "sk-should-never-be-persisted" not in encoded
    assert "sk-vision-never-persisted" not in encoded
    assert "SESSDATA=secret" not in encoded

    restored = pipeline_request_from_snapshot(snapshot, settings_path=str(settings_path))
    assert restored.whisper_model == "small"
    assert restored.gpt_model == "snapshot-model"
    assert restored.frame_mode == "auto"
    assert restored.max_frames == 17
    assert restored.vision_model == "snapshot-vision"
    assert restored.api_key == "sk-current-notes"
    assert restored.vision_api_key == "sk-current-vision"


def test_snapshot_credential_reference_survives_binding_change(tmp_path: Path):
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({
            "providers": [
                {"name": "original", "type": "mimo", "api_key": "sk-original", "models": ["m1"]},
                {"name": "new-active", "type": "dashscope", "api_key": "sk-new", "models": ["m2"]},
            ],
            "bindings": {"llm": {"provider": "new-active", "model": "m2"}},
        }),
        encoding="utf-8",
    )
    request = PipelineRequest(input="video.mp4", provider="mimo", gpt_model="snapshot-model")
    object.__setattr__(request, "_llm_profile_name", "original")
    snapshot = pipeline_request_to_snapshot(request)

    restored = pipeline_request_from_snapshot(snapshot, settings_path=str(settings_path))

    assert restored.provider == "mimo"
    assert restored.gpt_model == "snapshot-model"
    assert restored.api_key == "sk-original"
    assert getattr(restored, "_llm_profile_name") == "original"


def test_progress_snapshot_and_interrupted_state_survive_engine_restart(tmp_path: Path):
    output_dir = str(tmp_path / "output")
    db_path = get_default_db_path(output_dir)
    queue = JobQueue(db_path=db_path, output_dir=output_dir)
    snapshot = pipeline_request_to_snapshot(
        PipelineRequest(input="video.mp4", output_dir=output_dir, whisper_model="medium")
    )
    run_id = queue.enqueue("video.mp4", request_snapshot=snapshot)
    queue.update_stage(run_id, JobState.TRANSCRIBING, "正在转录", 43)

    restarted = JobQueue(db_path=db_path, output_dir=output_dir)
    assert restarted.reconcile_interrupted_jobs() == 1
    job = restarted.get_job(run_id)
    assert job is not None
    assert job.status == "interrupted"
    assert job.can_resume
    assert job.progress == 43
    assert job.progress_message == "正在转录"
    assert job.last_active_stage == "transcribing"
    assert job.request_snapshot["request"]["whisper_model"] == "medium"


def test_event_journal_shares_task_database(tmp_path: Path):
    output_dir = str(tmp_path / "output")
    db_path = get_default_db_path(output_dir)
    queue = JobQueue(db_path=db_path, output_dir=output_dir)
    run_id = queue.enqueue("video.mp4")

    journal = EventJournal(db_path)
    event_id = journal.append(run_id, "job.progress", {"progress": 12})
    assert event_id > 0
    events = journal.events_since(run_id)
    assert len(events) == 1
    assert events[0]["data"]["progress"] == 12


class _FakeOrchestrator:
    def __init__(self):
        self.requests: list[PipelineRequest] = []

    def run(self, request: PipelineRequest, **_kwargs) -> PipelineResult:
        self.requests.append(request)
        return PipelineResult(
            notes_path=os.path.join(request.output_dir, "notes.md"),
            transcript_path=os.path.join(request.output_dir, "transcript.json"),
            title=request.title or "test",
            input=request.input,
            elapsed_sec=0.01,
            frames_count=3,
            note_id=7,
        )


def test_supervisor_centralizes_start_resume_and_retry(tmp_path: Path):
    output_dir = str(tmp_path / "output")
    queue = JobQueue(db_path=get_default_db_path(output_dir), output_dir=output_dir)
    fake = _FakeOrchestrator()
    supervisor = TaskSupervisor(fake, queue)

    request = PipelineRequest(
        input="video.mp4",
        output_dir=output_dir,
        title="Original",
        whisper_model="small",
        frame_interval=11,
    )
    run_id = supervisor.start(request)
    completed = _wait_for_status(queue, run_id, "completed")
    assert completed.progress == 100
    assert fake.requests[0].whisper_model == "small"

    # Simulate a later failed run with an intact workspace and request snapshot.
    failed_id = queue.enqueue(
        "video-2.mp4",
        title="Resume Me",
        request_snapshot=pipeline_request_to_snapshot(
            PipelineRequest(
                input="video-2.mp4",
                output_dir=output_dir,
                title="Resume Me",
                whisper_model="medium",
                frame_interval=19,
            )
        ),
    )
    queue.update_stage(failed_id, JobState.TRANSCRIBING, "partial", 36)
    queue.fail(failed_id, "simulated crash")

    supervisor.resume(failed_id)
    resumed = _wait_for_status(queue, failed_id, "completed")
    assert resumed.attempt == 1
    assert fake.requests[-1].whisper_model == "medium"
    assert fake.requests[-1].frame_interval == 19

    # Retry creates a new lineage row rather than mutating history.
    retry_source = queue.enqueue(
        "video-3.mp4",
        request_snapshot=pipeline_request_to_snapshot(
            PipelineRequest(input="video-3.mp4", output_dir=output_dir)
        ),
    )
    queue.fail(retry_source, "bad input")
    retry_id = supervisor.retry(retry_source)
    retried = _wait_for_status(queue, retry_id, "completed")
    assert retried.parent_run_id == retry_source
    assert retried.attempt == 2
