from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.api.event_journal import EventJournal
from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import FileManifestStore, StageRunner
from src.application.pipeline.stages.resolve_media import ResolveMediaStage
from src.application.pipeline.stages.transcribe_stage import TranscribeStage
from src.application.pipeline.stages.base import StageResult
from src.application.speech import SpeechResult, SpeechSegment
from src.application.services.job_queue import (
    JobQueue,
    TaskCancelledError,
    get_default_db_path,
    get_default_jobs_root,
)
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
        transcription_backend="whisper_cpp",
        whisper_model="small",
        gpt_model="snapshot-model",
        provider="mimo",
        api_key="sk-should-never-be-persisted",
        frame_mode="auto",
        max_frames=17,
        ocr_backend="tesseract",
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
    assert restored.transcription_backend == "whisper_cpp"
    assert restored.gpt_model == "snapshot-model"
    assert restored.frame_mode == "auto"
    assert restored.max_frames == 17
    assert restored.ocr_backend == "tesseract"
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


def test_legacy_resume_does_not_infer_output_dir_from_appdata_jobs(tmp_path: Path):
    output_dir = str(tmp_path / "exports")
    queue = JobQueue(db_path=get_default_db_path(output_dir), output_dir=output_dir)
    run_id = queue.enqueue("video.mp4", job_id="legacy-no-snapshot")
    job = queue.get_job(run_id)
    assert job is not None
    assert str(job.job_dir).startswith(get_default_jobs_root())

    supervisor = TaskSupervisor(object(), queue)
    request = supervisor._request_for_job(job)

    assert request.output_dir == queue.output_dir


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


class _GateMediaResolver:
    def __init__(self, audio_path: Path, video_path: Path) -> None:
        self.audio_path = audio_path
        self.video_path = video_path
        self.calls = 0

    def resolve(self, request: PipelineRequest, *, job_dir: str):
        self.calls += 1
        return str(self.audio_path), str(self.video_path), []


class _GateTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_path: str, *, language=None, beam_size=5, vad_filter=False):
        self.calls += 1
        return SpeechResult(
            segments=[SpeechSegment(start=0.0, end=1.0, text="hello", language="en")],
            full_text="hello",
            language="en",
            elapsed=0.1,
        )


class _CrashOnceAfterTranscribeStage:
    id = "after_transcribe_gate"
    label = "Crash Gate"
    percent = 40

    def __init__(self) -> None:
        self.calls = 0

    def run(self, ctx: ProcessingContext, state: dict) -> StageResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated engine crash after whisper")
        notes_path = Path(ctx.job_dir) / "artifacts" / "notes.md"
        notes_path.write_text("# Notes\n", encoding="utf-8")
        return StageResult(outputs={"notes_path": str(notes_path)})


class _PauseOnceAfterTranscribeStage(_CrashOnceAfterTranscribeStage):
    def run(self, ctx: ProcessingContext, state: dict) -> StageResult:
        self.calls += 1
        if self.calls == 1:
            raise TaskCancelledError("pause after whisper", action="pause")
        notes_path = Path(ctx.job_dir) / "artifacts" / "notes.md"
        notes_path.write_text("# Notes\n", encoding="utf-8")
        return StageResult(outputs={"notes_path": str(notes_path)})


class _ResumeGateOrchestrator:
    def __init__(self, audio_path: Path, video_path: Path, *, pause: bool = False) -> None:
        self.media = _GateMediaResolver(audio_path, video_path)
        self.transcriber = _GateTranscriber()
        self.crash_stage = (
            _PauseOnceAfterTranscribeStage()
            if pause
            else _CrashOnceAfterTranscribeStage()
        )

    def run(
        self,
        request: PipelineRequest,
        *,
        resume_run_id: int,
        job_queue: JobQueue,
        cancel_token=None,
        force: bool = False,
    ) -> PipelineResult:
        job_dir = job_queue.get_job_dir(resume_run_id)
        assert job_dir
        ctx = ProcessingContext(
            request=request,
            job_dir=job_dir,
            job_id=Path(job_dir).name,
            resume_run_id=resume_run_id,
            force=force,
            cancel_token=cancel_token,
            emit_console_progress=False,
        )
        try:
            state = StageRunner(FileManifestStore()).run(
                ctx,
                [
                    ResolveMediaStage(self.media),
                    TranscribeStage(self.transcriber),
                    self.crash_stage,
                ],
            )
        except TaskCancelledError as exc:
            job_queue.finalize_stop(resume_run_id, getattr(exc, "action", "pause"))
            raise
        return PipelineResult(
            notes_path=state["notes_path"],
            transcript_path=str(Path(job_dir) / "artifacts" / "transcript.json"),
            title=request.title or "resume gate",
            input=request.input,
            elapsed_sec=0.1,
            frames_count=0,
            job_id=Path(job_dir).name,
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


def test_task_resume_after_crash_does_not_repeat_completed_whisper(tmp_path: Path):
    output_dir = str(tmp_path / "output")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "lecture.mp4"
    audio_path = media_dir / "lecture.wav"
    video_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")

    queue = JobQueue(db_path=get_default_db_path(output_dir), output_dir=output_dir)
    orchestrator = _ResumeGateOrchestrator(audio_path, video_path)
    supervisor = TaskSupervisor(orchestrator, queue)
    request = PipelineRequest(
        input=str(video_path),
        output_dir=output_dir,
        title="Crash Resume Gate",
    )

    run_id = supervisor.start(request)
    failed = _wait_for_status(queue, run_id, "failed")
    assert "simulated engine crash" in (failed.error_message or "")
    assert orchestrator.media.calls == 1
    assert orchestrator.transcriber.calls == 1

    supervisor.resume(run_id)
    completed = _wait_for_status(queue, run_id, "completed")

    assert completed.output_path
    assert orchestrator.media.calls == 1
    assert orchestrator.transcriber.calls == 1
    assert orchestrator.crash_stage.calls == 2


def test_task_resume_after_pause_does_not_repeat_completed_whisper(tmp_path: Path):
    output_dir = str(tmp_path / "output")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "lecture.mp4"
    audio_path = media_dir / "lecture.wav"
    video_path.write_bytes(b"video")
    audio_path.write_bytes(b"audio")

    queue = JobQueue(db_path=get_default_db_path(output_dir), output_dir=output_dir)
    orchestrator = _ResumeGateOrchestrator(audio_path, video_path, pause=True)
    supervisor = TaskSupervisor(orchestrator, queue)
    request = PipelineRequest(
        input=str(video_path),
        output_dir=output_dir,
        title="Pause Resume Gate",
    )

    run_id = supervisor.start(request)
    paused = _wait_for_status(queue, run_id, "paused")
    assert paused.can_resume
    assert orchestrator.media.calls == 1
    assert orchestrator.transcriber.calls == 1

    supervisor.resume(run_id)
    _wait_for_status(queue, run_id, "completed")

    assert orchestrator.media.calls == 1
    assert orchestrator.transcriber.calls == 1
    assert orchestrator.crash_stage.calls == 2
