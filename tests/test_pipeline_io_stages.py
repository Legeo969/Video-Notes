"""Tests for ResolveMediaStage, TranscribeStage, ExtractFramesStage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import StageRunner
from src.application.pipeline.stages.base import StageResult
from src.application.pipeline.stages.resolve_media import ResolveMediaStage
from src.application.pipeline.stages.transcribe_stage import TranscribeStage
from src.application.pipeline.stages.extract_frames_stage import ExtractFramesStage
from src.domain.types import PipelineRequest


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def ctx(temp_dir):
    return ProcessingContext(
        request=PipelineRequest(input="test.mp4", output_dir=temp_dir, whisper_model="base"),
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


# ── ResolveMediaStage ──


class TestResolveMediaStage:
    def test_stage_attributes(self):
        stage = ResolveMediaStage()
        assert stage.id == "resolve_media"
        assert stage.label == "解析输入源"
        assert stage.percent == 5

    def test_run_with_mocked_resolver(self, ctx, temp_dir):
        audio_path = os.path.join(temp_dir, "audio.wav")
        Path(audio_path).write_text("fake audio")
        video_path = os.path.join(temp_dir, "video.mp4")
        Path(video_path).write_text("fake video")

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = (audio_path, video_path, [audio_path, video_path])

        stage = ResolveMediaStage(media_resolver=mock_resolver)
        result = stage.run(ctx, {})
        assert isinstance(result, StageResult)
        assert "audio_path" in result.outputs
        assert "video_path" in result.outputs
        assert os.path.isfile(result.outputs["audio_path"])

    def test_raises_when_audio_missing(self, ctx, temp_dir):
        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = (None, None, [])
        stage = ResolveMediaStage(media_resolver=mock_resolver)
        with pytest.raises(RuntimeError, match="音频文件不可用"):
            stage.run(ctx, {})

    def test_copies_audio_to_job_dir(self, ctx, temp_dir):
        src_audio = os.path.join(temp_dir, "source", "audio.wav")
        os.makedirs(os.path.dirname(src_audio))
        Path(src_audio).write_text("audio content")

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = (src_audio, None, [src_audio])

        stage = ResolveMediaStage(media_resolver=mock_resolver)
        result = stage.run(ctx, {})
        art_audio = os.path.join(temp_dir, "artifacts", "audio.wav")
        assert os.path.isfile(art_audio)
        assert result.outputs["audio_path"] == art_audio

    def test_does_not_copy_when_already_in_job_dir(self, ctx, temp_dir):
        art_dir = os.path.join(temp_dir, "artifacts")
        os.makedirs(art_dir)
        art_audio = os.path.join(art_dir, "audio.wav")
        Path(art_audio).write_text("already there")

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = (art_audio, None, [])

        stage = ResolveMediaStage(media_resolver=mock_resolver)
        result = stage.run(ctx, {})
        assert result.outputs["audio_path"] == art_audio

    def test_appends_owned_files_to_ctx(self, ctx, temp_dir):
        src_audio = os.path.join(temp_dir, "audio.wav")
        Path(src_audio).write_text("content")

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = (src_audio, None, [src_audio, "/tmp/extra"])

        stage = ResolveMediaStage(media_resolver=mock_resolver)
        stage.run(ctx, {})
        assert "/tmp/extra" in ctx.owned_files
        assert src_audio in ctx.owned_files or os.path.join(temp_dir, "artifacts", "audio.wav") in ctx.owned_files


# ── TranscribeStage ──


class TestTranscribeStage:
    def test_stage_attributes(self):
        stage = TranscribeStage()
        assert stage.id == "transcribe"
        assert stage.label == "Whisper 转录"
        assert stage.percent == 15

    def test_run_with_mocked_transcriber(self, ctx, temp_dir):
        from src.application.speech import SpeechResult, SpeechSegment
        mock_result = SpeechResult(
            full_text="hello world",
            segments=[SpeechSegment(start=0, end=2, text="hello world")],
            language="en",
            elapsed=0.5,
        )
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = mock_result

        stage = TranscribeStage(transcriber=mock_transcriber)
        result = stage.run(ctx, {"audio_path": "/fake/audio.wav"})
        assert isinstance(result, StageResult)
        assert "speech_result" in result.outputs
        assert result.outputs["speech_result"].full_text == "hello world"

    def test_writes_transcript_json(self, ctx, temp_dir):
        from src.application.speech import SpeechResult, SpeechSegment
        mock_result = SpeechResult(
            full_text="test",
            segments=[SpeechSegment(start=0, end=1, text="test")],
            language="en",
            elapsed=0.1,
        )
        stage = TranscribeStage(transcriber=MagicMock(transcribe=MagicMock(return_value=mock_result)))
        stage.run(ctx, {"audio_path": "/fake/audio.wav"})
        transcript_path = os.path.join(temp_dir, "artifacts", "transcript.json")
        assert os.path.isfile(transcript_path)

    def test_uses_request_params(self, ctx):
        from src.application.speech import SpeechResult
        mock_transcriber = MagicMock()
        mock_transcriber.transcribe.return_value = SpeechResult()

        stage = TranscribeStage(transcriber=mock_transcriber)
        stage.run(ctx, {"audio_path": "/fake/audio.wav"})

        mock_transcriber.transcribe.assert_called_once()
        _, kwargs = mock_transcriber.transcribe.call_args
        assert kwargs.get("language") is None  # default
        assert kwargs.get("beam_size") == ctx.request.beam_size

    def test_returned_speech_result_in_state(self, ctx, temp_dir):
        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="abc", segments=[SpeechSegment(0, 1, "abc")], language="en", elapsed=1)
        stage = TranscribeStage(transcriber=MagicMock(transcribe=MagicMock(return_value=sr)))
        result = stage.run(ctx, {"audio_path": "/fake/audio.wav"})
        assert result.outputs["speech_result"].full_text == "abc"

    def test_via_stage_runner(self, ctx, mem_store, temp_dir):
        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="x", segments=[SpeechSegment(0, 1, "x")], language="en", elapsed=0.1)
        mock_t = MagicMock(transcribe=MagicMock(return_value=sr))

        runner = StageRunner(manifest_store=mem_store)
        result = runner.run(ctx, [TranscribeStage(transcriber=mock_t)],
                            initial_state={"audio_path": "/fake/audio.wav"})
        assert "speech_result" in result
        assert result["speech_result"].full_text == "x"

    def test_file_manifest_restores_speech_result(self, ctx, temp_dir):
        from src.application.pipeline.runner import FileManifestStore
        from src.application.speech import SpeechResult, SpeechSegment

        sr = SpeechResult(
            full_text="cached",
            segments=[SpeechSegment(0, 1, "cached", language="en")],
            language="en",
            elapsed=0.2,
        )
        mock_t = MagicMock(transcribe=MagicMock(return_value=sr))
        store = FileManifestStore()
        runner = StageRunner(manifest_store=store)

        runner.run(
            ctx,
            [TranscribeStage(transcriber=mock_t)],
            initial_state={"audio_path": "/fake/audio.wav"},
        )
        mock_t.transcribe.reset_mock()

        result = runner.run(
            ctx,
            [TranscribeStage(transcriber=mock_t)],
            initial_state={"audio_path": "/fake/audio.wav"},
        )

        mock_t.transcribe.assert_not_called()
        assert result["speech_result"].full_text == "cached"
        assert result["speech_result"].segments[0].text == "cached"


# ── ExtractFramesStage ──


class TestExtractFramesStage:
    def test_stage_attributes(self):
        stage = ExtractFramesStage()
        assert stage.id == "extract_frames"
        assert stage.label == "抽帧"
        assert stage.percent == 30

    def test_skips_when_no_video_path(self, ctx):
        stage = ExtractFramesStage()
        result = stage.run(ctx, {"video_path": None, "speech_result": MagicMock()})
        assert result.outputs["frames"] == []

    def test_skips_when_video_file_missing(self, ctx, temp_dir):
        stage = ExtractFramesStage()
        result = stage.run(ctx, {"video_path": "/nonexistent/video.mp4", "speech_result": MagicMock()})
        assert result.outputs["frames"] == []

    def test_calls_extract_frames_with_correct_args(self, ctx, temp_dir):
        video_path = os.path.join(temp_dir, "video.mp4")
        Path(video_path).write_text("fake video")

        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(
            full_text="test",
            segments=[SpeechSegment(start=0, end=1, text="test")],
        )
        ctx.request.frame_interval = 15
        ctx.request.frame_mode = "fixed"
        ctx.request.max_frames = 20

        frame_service = MagicMock()
        frame_service.extract.return_value = [
            {"path": "/frame1.jpg", "filename": "f1.jpg", "timestamp_sec": 0}
        ]
        stage = ExtractFramesStage(frame_service=frame_service)
        result = stage.run(ctx, {"video_path": video_path, "speech_result": sr})

        frame_service.extract.assert_called_once_with(
            video_path,
            os.path.join(ctx.job_dir, ".temp_frames"),
            interval_sec=15,
            mode="fixed",
            max_frames=20,
            transcript_segments=[{"start": 0, "end": 1, "text": "test"}],
            ocr_enabled=False,
            ocr_backend="tesseract",
        )
        assert len(result.outputs["frames"]) == 1

    def test_default_params_when_not_set_on_request(self, ctx, temp_dir):
        video_path = os.path.join(temp_dir, "video.mp4")
        Path(video_path).write_text("fake")

        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="t", segments=[SpeechSegment(0, 1, "t")])

        frame_service = MagicMock()
        frame_service.extract.return_value = []
        stage = ExtractFramesStage(frame_service=frame_service)
        stage.run(ctx, {"video_path": video_path, "speech_result": sr})
        frame_service.extract.assert_called_once()

    def test_runs_ocr_when_enabled(self, ctx, temp_dir):
        video_path = os.path.join(temp_dir, "video.mp4")
        Path(video_path).write_text("fake")

        from src.application.speech import SpeechResult, SpeechSegment
        sr = SpeechResult(full_text="t", segments=[SpeechSegment(0, 1, "t")])
        frames = [{"path": "/f.jpg", "filename": "f.jpg", "timestamp_sec": 0}]
        ctx.request.ocr_enabled = True

        frame_service = MagicMock()
        frame_service.extract.return_value = frames
        stage = ExtractFramesStage(frame_service=frame_service)
        result = stage.run(ctx, {"video_path": video_path, "speech_result": sr})

        frame_service.extract.assert_called_once()
        assert frame_service.extract.call_args.kwargs["ocr_enabled"] is True
        assert result.outputs["frames"] is frames

    def test_via_stage_runner(self, ctx, mem_store, temp_dir):
        video_path = os.path.join(temp_dir, "video.mp4")
        Path(video_path).write_text("fake")

        frame_service = MagicMock()
        frame_service.extract.return_value = [
            {"path": "/f.jpg", "filename": "f.jpg", "timestamp_sec": 0}
        ]
        runner = StageRunner(manifest_store=mem_store)
        result = runner.run(ctx, [ExtractFramesStage(frame_service=frame_service)], initial_state={
            "video_path": video_path,
            "speech_result": MagicMock(segments=[]),
        })
        assert "frames" in result
