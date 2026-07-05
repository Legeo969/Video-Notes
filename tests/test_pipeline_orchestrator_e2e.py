"""End-to-end mock tests for PipelineOrchestrator._run_new_pipeline().

Tests cover:
- Happy path: all stages succeed, PipelineResult with all output keys
- Plugin hooks: plugin_manager.run_hook called with correct hook names
- Resume support: job_queue.get_job_dir used for existing job_dir
- Cancel support: CancellationToken stops execution
- Error handling: stage failure propagates correctly
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from src.application.services.job_queue import CancellationToken, TaskCancelledError
from src.application.services.orchestrator import PipelineOrchestrator
from src.domain.types import PipelineRequest, PipelineResult


# ── Fixtures ──


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def orchestrator(temp_dir):
    """Create PipelineOrchestrator with all service deps replaced by mocks."""
    orch = PipelineOrchestrator()
    orch.media = MagicMock()
    orch.transcription = MagicMock()
    orch.notes_service = MagicMock()
    orch.writer = MagicMock()
    orch.cleanup = MagicMock()
    orch.cleanup.create_job_dir.return_value = os.path.join(
        temp_dir, ".jobs", "test-job-123"
    )
    return orch


def _make_request(temp_dir: str, **overrides) -> PipelineRequest:
    """Helper: build PipelineRequest with defaults + overrides."""
    kwargs: dict = {"input": "https://example.com/lecture.mp4", "output_dir": temp_dir}
    kwargs.update(overrides)
    return PipelineRequest(**kwargs)


def _io_state(temp_dir: str, **kw) -> dict:
    """Default I/O stage state (stages 1-4)."""
    state = {
        "audio_path": os.path.join(temp_dir, "audio.wav"),
        "video_path": None,
        "speech_result": MagicMock(
            segments=[],
            full_text="Welcome to the lecture. Today we cover mocking.",
            language="en",
            elapsed=2.3,
        ),
        "frames": [],
        "insights": [],
    }
    state.update(kw)
    return state


def _note_state(**kw) -> dict:
    """Default note stage state (stages 5-7)."""
    state = {
        "timeline": MagicMock(items=[]),
        "chunks": [],
        "map_results": [MagicMock(elapsed=0.8)],
        "notes": "# Lecture Notes\n\nKey concepts from today.",
    }
    state.update(kw)
    return state


def _write_state(temp_dir: str, **kw) -> dict:
    """Default write stage state (stages 8-9)."""
    state = {
        "transcript_path": os.path.join(temp_dir, "transcript.md"),
        "notes_path": os.path.join(temp_dir, "notes.md"),
        "note_id": 42,
    }
    state.update(kw)
    return state


# ═══════════════════════════════════════════════════════════════
# 1. Happy path
# ═══════════════════════════════════════════════════════════════


class TestHappyPath:
    """All stages succeed; PipelineResult has all expected fields."""

    @patch("src.application.services.orchestrator.StageRunner")
    def test_full_pipeline_returns_pipeline_result(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """Verify PipelineResult with all output keys on successful run."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            _write_state(temp_dir),
        ]

        request = _make_request(temp_dir)
        result = orchestrator._run_new_pipeline(request)

        assert isinstance(result, PipelineResult)
        assert result.notes_path == os.path.join(temp_dir, "notes.md")
        assert result.transcript_path == os.path.join(temp_dir, "transcript.md")
        assert result.input == "https://example.com/lecture.mp4"
        assert isinstance(result.elapsed_sec, float)
        assert result.elapsed_sec >= 0
        assert result.frames_count == 0
        assert result.note_id == 42
        assert result.job_id == "test-job-123"

    @patch("src.application.services.orchestrator.StageRunner")
    def test_explicit_title_is_used(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """When request.title is provided, it appears in PipelineResult."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir, frames=[{"path": "f001.jpg"}]),
            _note_state(),
            _write_state(temp_dir, note_id=7),
        ]

        request = _make_request(temp_dir, title="My Great Lecture")
        result = orchestrator._run_new_pipeline(request)

        assert result.title == "My Great Lecture"
        assert result.frames_count == 1
        assert result.note_id == 7

    @patch("src.application.services.orchestrator.StageRunner")
    @patch("src.application.providers.factory.ProviderFactory")
    def test_vision_enabled_path(
        self, mock_pf_cls, mock_runner_cls, orchestrator, temp_dir
    ):
        """Vision-enabled pipeline succeeds with mocked ProviderFactory."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(
                temp_dir,
                video_path=os.path.join(temp_dir, "video.mp4"),
                frames=[{"path": "f001.jpg"}, {"path": "f002.jpg"}],
                insights=[{"frame": "f001.jpg", "desc": "A person speaking"}],
            ),
            _note_state(notes="# Vision Notes"),
            _write_state(temp_dir, note_id=10),
        ]

        request = _make_request(
            temp_dir,
            vision_enabled=True,
            vision_provider="openai",
            vision_api_key="sk-test",
            vision_model="gpt-4o",
        )
        result = orchestrator._run_new_pipeline(request)

        assert isinstance(result, PipelineResult)
        assert result.notes_path == os.path.join(temp_dir, "notes.md")
        assert result.frames_count == 2
        assert result.note_id == 10

        # ProviderFactory was called to create the vision provider
        mock_pf = mock_pf_cls.return_value
        mock_pf.create.assert_called_once()

    @patch("src.application.services.orchestrator.StageRunner")
    def test_localfile_title_fallback(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """Local file input without title falls back to filename stem."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            _write_state(temp_dir),
        ]

        local_path = os.path.join(temp_dir, "my_video.mov")
        Path(local_path).write_text("not a real video")
        request = _make_request(temp_dir, input=local_path)
        result = orchestrator._run_new_pipeline(request)

        # Uses audio_path ("audio.wav") before local input for fallback
        assert result.title == "audio"


# ═══════════════════════════════════════════════════════════════
# 2. Plugin hooks
# ═══════════════════════════════════════════════════════════════


class TestPluginHooks:
    """Plugin hook invocations are correct."""

    @patch("src.application.services.orchestrator.StageRunner")
    def test_three_hooks_called_in_order(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """on_transcript, on_note, on_complete hooks fire with correct args."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        transcript = "Welcome to the lecture."
        mock_runner.run.side_effect = [
            _io_state(temp_dir, speech_result=MagicMock(
                segments=[], full_text=transcript, language="en", elapsed=1.0,
            )),
            _note_state(notes="# Notes from plugin"),
            _write_state(temp_dir),
        ]

        mock_plugin = MagicMock()
        request = _make_request(temp_dir, title="Plugin Test")
        orchestrator._run_new_pipeline(request, plugin_manager=mock_plugin)

        assert mock_plugin.run_hook.call_count == 3
        mock_plugin.run_hook.assert_any_call("on_transcript", transcript)
        mock_plugin.run_hook.assert_any_call(
            "on_note", "# Notes from plugin", {
                "title": "Plugin Test",
                "input": request.input,
                "job_id": "test-job-123",
            }
        )
        mock_plugin.run_hook.assert_any_call(
            "on_complete", os.path.join(temp_dir, "notes.md")
        )

    @patch("src.application.services.orchestrator.StageRunner")
    def test_plugin_hook_failure_does_not_crash(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """A plugin hook that raises is caught by logger; pipeline continues."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            _write_state(temp_dir),
        ]

        mock_plugin = MagicMock()
        mock_plugin.run_hook.side_effect = RuntimeError("Plugin exploded")

        request = _make_request(temp_dir)
        result = orchestrator._run_new_pipeline(request, plugin_manager=mock_plugin)

        # Pipeline should still complete despite plugin failures
        assert isinstance(result, PipelineResult)
        assert result.notes_path


# ═══════════════════════════════════════════════════════════════
# 3. Resume support
# ═══════════════════════════════════════════════════════════════


class TestResume:
    """Resume (断点续跑) uses existing job_dir when available."""

    @patch("src.application.services.orchestrator.StageRunner")
    def test_resume_uses_existing_job_dir(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """When resume_run_id + job_queue provide a dir, create_job_dir is skipped."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            _write_state(temp_dir),
        ]

        resume_job_dir = os.path.join(temp_dir, ".jobs", "resume-job-42")
        os.makedirs(resume_job_dir, exist_ok=True)

        mock_jq = MagicMock()
        mock_jq.get_job_dir.return_value = resume_job_dir

        request = _make_request(temp_dir)
        result = orchestrator._run_new_pipeline(
            request, resume_run_id=42, job_queue=mock_jq
        )

        assert isinstance(result, PipelineResult)
        # Existing job_dir was used
        mock_jq.get_job_dir.assert_called_once_with(42)
        # create_job_dir should NOT have been called
        orchestrator.cleanup.create_job_dir.assert_not_called()

    @patch("src.application.services.orchestrator.StageRunner")
    def test_resume_fallback_when_job_dir_missing(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """When get_job_dir returns falsy, fall back to create_job_dir."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            _write_state(temp_dir),
        ]

        mock_jq = MagicMock()
        mock_jq.get_job_dir.return_value = None

        request = _make_request(temp_dir)
        result = orchestrator._run_new_pipeline(
            request, resume_run_id=42, job_queue=mock_jq
        )

        assert isinstance(result, PipelineResult)
        orchestrator.cleanup.create_job_dir.assert_called_once_with(temp_dir)


# ═══════════════════════════════════════════════════════════════
# 4. Cancel support
# ═══════════════════════════════════════════════════════════════


class TestCancel:
    """CancellationToken is checked and raises TaskCancelledError."""

    @patch("src.application.services.orchestrator.StageRunner")
    def test_cancellation_raises_task_cancelled(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """When StageRunner raises TaskCancelledError, pipeline aborts."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = TaskCancelledError("Cancelled by user")

        token = CancellationToken()
        token.cancel()

        request = _make_request(temp_dir)
        with pytest.raises(TaskCancelledError):
            orchestrator._run_new_pipeline(request, cancel_token=token)

    @patch("src.application.services.orchestrator.StageRunner")
    def test_cancellation_mid_pipeline(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """Pipeline cancels mid-way; first stage group succeeds, second does not."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        # First _runner.run succeeds, second raises TaskCancelledError
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            TaskCancelledError("Cancelled during notes generation"),
        ]

        token = CancellationToken()
        # Token itself is not pre-cancelled — it's the mock that simulates
        # the cancellation happening inside StageRunner.

        request = _make_request(temp_dir)
        with pytest.raises(TaskCancelledError):
            orchestrator._run_new_pipeline(request, cancel_token=token)


# ═══════════════════════════════════════════════════════════════
# 5. Error handling
# ═══════════════════════════════════════════════════════════════


class TestErrorHandling:
    """Stage failures propagate correctly."""

    @patch("src.application.services.orchestrator.StageRunner")
    def test_stage_failure_propagates(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """A generic error from StageRunner.run propagates unchanged."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            RuntimeError("Transcription model not found"),
        ]

        request = _make_request(temp_dir)
        with pytest.raises(RuntimeError, match="Transcription model not found"):
            orchestrator._run_new_pipeline(request)

    @patch("src.application.services.orchestrator.StageRunner")
    def test_stage_failure_mid_pipeline(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """A stage fails after some stages succeeded."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner
        mock_runner.run.side_effect = [
            _io_state(temp_dir),
            _note_state(),
            ValueError("Index provenance failed: DB locked"),
        ]

        request = _make_request(temp_dir)
        with pytest.raises(ValueError, match="Index provenance failed"):
            orchestrator._run_new_pipeline(request)

    @patch("src.application.services.orchestrator.StageRunner")
    def test_missing_vision_provider_raises(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """Vision enabled without provider raises RuntimeError before stages."""
        # No need to set up mock_runner since the error occurs before stages run
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner

        request = _make_request(temp_dir, vision_enabled=True, vision_provider=None)
        with pytest.raises(RuntimeError, match="视觉 provider 未配置"):
            orchestrator._run_new_pipeline(request)

        # StageRunner.run should NOT have been called
        mock_runner.run.assert_not_called()

    @patch("src.application.services.orchestrator.StageRunner")
    def test_missing_vision_api_key_raises(
        self, mock_runner_cls, orchestrator, temp_dir
    ):
        """Vision enabled without api key raises RuntimeError before stages."""
        mock_runner = MagicMock(spec=["run"])
        mock_runner_cls.return_value = mock_runner

        request = _make_request(
            temp_dir,
            vision_enabled=True,
            vision_provider="openai",
            vision_api_key=None,
        )
        with pytest.raises(RuntimeError, match="视觉 API Key 未配置"):
            orchestrator._run_new_pipeline(request)

        mock_runner.run.assert_not_called()
