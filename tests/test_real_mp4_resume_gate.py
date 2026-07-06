from __future__ import annotations

import subprocess
from pathlib import Path

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import FileManifestStore, StageRunner
from src.application.pipeline.stages.base import StageResult
from src.application.pipeline.stages.resolve_media import ResolveMediaStage
from src.application.pipeline.stages.transcribe_stage import TranscribeStage
from src.application.services.job_queue import (
    JobQueue,
    TaskCancelledError,
    get_default_db_path,
)
from src.application.services.media_resolver import MediaResolver
from src.application.services.task_supervisor import TaskSupervisor
from src.application.speech import SpeechResult, SpeechSegment
from src.domain.types import PipelineRequest, PipelineResult
from tests.helpers.markers import requires_ffmpeg


def _make_test_mp4(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=5:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


class _CountingTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio_path: str, *, language=None, beam_size=5, vad_filter=False):
        self.calls += 1
        assert Path(audio_path).is_file()
        return SpeechResult(
            segments=[SpeechSegment(start=0.0, end=1.0, text="tone", language="en")],
            full_text="tone",
            language="en",
            elapsed=0.1,
        )


class _StopOnceStage:
    id = "real_mp4_gate"
    label = "Real MP4 Gate"
    percent = 40

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def run(self, ctx: ProcessingContext, state: dict) -> StageResult:
        self.calls += 1
        if self.calls == 1:
            if self.mode == "pause":
                raise TaskCancelledError("pause after real mp4 whisper", action="pause")
            raise RuntimeError("crash after real mp4 whisper")
        notes_path = Path(ctx.job_dir) / "artifacts" / "notes.md"
        notes_path.write_text("# Notes\n", encoding="utf-8")
        return StageResult(outputs={"notes_path": str(notes_path)})


class _RealMp4ResumeGateOrchestrator:
    def __init__(self, mode: str) -> None:
        self.media = MediaResolver()
        self.transcriber = _CountingTranscriber()
        self.stop_stage = _StopOnceStage(mode)

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
                    self.stop_stage,
                ],
            )
        except TaskCancelledError as exc:
            job_queue.finalize_stop(resume_run_id, getattr(exc, "action", "pause"))
            raise
        return PipelineResult(
            notes_path=state["notes_path"],
            transcript_path=str(Path(job_dir) / "artifacts" / "transcript.json"),
            title=request.title or "real mp4 gate",
            input=request.input,
            elapsed_sec=0.1,
            frames_count=0,
            job_id=Path(job_dir).name,
        )


def _run_gate(tmp_path: Path, mode: str) -> _RealMp4ResumeGateOrchestrator:
    output_dir = str(tmp_path / "output")
    mp4_path = tmp_path / "sample.mp4"
    _make_test_mp4(mp4_path)

    queue = JobQueue(db_path=get_default_db_path(output_dir), output_dir=output_dir)
    orchestrator = _RealMp4ResumeGateOrchestrator(mode)
    supervisor = TaskSupervisor(orchestrator, queue)
    request = PipelineRequest(
        input=str(mp4_path),
        output_dir=output_dir,
        title=f"Real MP4 {mode}",
        frame_mode="disabled",
    )
    run_id = supervisor.start(request)

    expected = "paused" if mode == "pause" else "failed"
    _wait_for_status(queue, run_id, expected)
    assert orchestrator.transcriber.calls == 1

    supervisor.resume(run_id)
    completed = _wait_for_status(queue, run_id, "completed")
    assert completed.output_path
    assert orchestrator.transcriber.calls == 1
    assert orchestrator.stop_stage.calls == 2
    return orchestrator


def _wait_for_status(queue: JobQueue, run_id: int, expected: str):
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        job = queue.get_job(run_id)
        if job and job.status == expected:
            return job
        time.sleep(0.01)
    job = queue.get_job(run_id)
    raise AssertionError(f"job {run_id} status={job.status if job else None}, expected={expected}")


@requires_ffmpeg
def test_real_mp4_crash_resume_does_not_repeat_whisper(tmp_path: Path) -> None:
    _run_gate(tmp_path, "crash")


@requires_ffmpeg
def test_real_mp4_pause_resume_does_not_repeat_whisper(tmp_path: Path) -> None:
    _run_gate(tmp_path, "pause")

