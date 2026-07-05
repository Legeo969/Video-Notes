"""Tests for WriteArtifactsStage, IndexProvenanceStage."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import StageRunner
from src.application.pipeline.stages.base import StageResult
from src.application.pipeline.stages.write_artifacts import WriteArtifactsStage
from src.application.pipeline.stages.index_provenance import IndexProvenanceStage
from src.domain.types import PipelineRequest


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def ctx(temp_dir):
    return ProcessingContext(
        request=PipelineRequest(input="test.mp4", output_dir=temp_dir, title="Test Title"),
        job_dir=temp_dir,
        job_id="test-job",
    )


@pytest.fixture
def mem_store():
    from src.application.pipeline.runner import ManifestStore
    class _Mem(ManifestStore):
        def is_completed(self, *a, **kw): return False
        def load_outputs(self, *a, **kw): return {}
        def save_completed(self, *a, **kw): pass
    return _Mem()


# ── WriteArtifactsStage ──


class TestWriteArtifactsStage:
    def test_stage_attributes(self):
        stage = WriteArtifactsStage()
        assert stage.id == "write_artifacts"
        assert stage.label == "写入产物"
        assert stage.percent == 90

    def test_run_calls_writer_with_correct_args(self, ctx):
        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="hello", segments=[SpeechSegment(0, 1, "hello")])
        state = {
            "speech_result": sr,
            "notes": "# Notes",
            "frames": [],
            "insights": [],
        }
        mock_writer = MagicMock()
        mock_writer.write.return_value = ("/out/transcript.txt", "/out/notes.md")

        stage = WriteArtifactsStage(writer=mock_writer)
        result = stage.run(ctx, state)

        mock_writer.write.assert_called_once()
        args = mock_writer.write.call_args
        assert args[0][0] is ctx.request
        assert args[0][1] == "hello"
        assert args[0][2] == "# Notes"
        assert args[0][4] == []
        assert "insights" in args[1]

    def test_returns_paths_in_state(self, ctx):
        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="t", segments=[SpeechSegment(0, 1, "t")])
        mock_writer = MagicMock()
        mock_writer.write.return_value = ("/p/transcript.txt", "/p/notes.md")

        stage = WriteArtifactsStage(writer=mock_writer)
        result = stage.run(ctx, {"speech_result": sr, "notes": "n", "frames": [], "insights": []})
        assert result.outputs["transcript_path"] == "/p/transcript.txt"
        assert result.outputs["notes_path"] == "/p/notes.md"

    def test_via_stage_runner(self, ctx, mem_store):
        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="t", segments=[SpeechSegment(0, 1, "t")])
        mock_writer = MagicMock()
        mock_writer.write.return_value = ("/t.txt", "/n.md")

        runner = StageRunner(manifest_store=mem_store)
        result = runner.run(ctx, [WriteArtifactsStage(writer=mock_writer)],
                            initial_state={"speech_result": sr, "notes": "n", "frames": [], "insights": []})
        assert result["transcript_path"] == "/t.txt"
        assert result["notes_path"] == "/n.md"


# ── IndexProvenanceStage ──


class TestIndexProvenanceStage:
    def test_stage_attributes(self):
        stage = IndexProvenanceStage()
        assert stage.id == "index_provenance"
        assert stage.label == "Provenance 索引"
        assert stage.percent == 97

    def test_calls_provenance_indexer(self, ctx):
        mock_indexer_cls = MagicMock()
        mock_instance = MagicMock()
        mock_indexer_cls.return_value = mock_instance

        stage = IndexProvenanceStage(provenance_indexer_cls=mock_indexer_cls)
        stage.run(ctx, {})

        mock_indexer_cls.assert_called_once()
        mock_instance.index_job.assert_called_once_with(
            "test-job",
            job_dir=ctx.job_dir,
            source_type="local",
            source_uri="test.mp4",
            title="Test Title",
        )

    def test_non_fatal_on_failure(self, ctx):
        mock_indexer_cls = MagicMock()
        mock_indexer_cls.side_effect = ImportError("no module")

        stage = IndexProvenanceStage(provenance_indexer_cls=mock_indexer_cls)
        result = stage.run(ctx, {})
        # should not raise, returns empty outputs
        assert isinstance(result, StageResult)

    def test_url_source_type_detected(self, ctx):
        ctx.request.input = "https://example.com/video"
        mock_indexer_cls = MagicMock()
        mock_instance = MagicMock()
        mock_indexer_cls.return_value = mock_instance

        stage = IndexProvenanceStage(provenance_indexer_cls=mock_indexer_cls)
        stage.run(ctx, {})

        args = mock_instance.index_job.call_args
        assert args[1]["source_type"] == "url"
