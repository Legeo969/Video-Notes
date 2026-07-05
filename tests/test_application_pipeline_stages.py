"""Tests for new-style application pipeline stages (VisionStage, ExtractFramesStage)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult
from src.application.pipeline.stages.vision_stage import VisionStage
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


# ── VisionStage ──


class TestVisionStage:
    """VisionStage: multi-modal LLM analysis of extracted video frames."""

    def test_vision_stage_attributes(self):
        """Class-level attributes must match pipeline contract."""
        assert VisionStage.id == "vision_analysis"
        assert VisionStage.label == "视觉理解分析"
        assert VisionStage.percent == 45

    def test_vision_stage_analyzes_frames(self, ctx):
        """With a vision provider and frames, analyzes frames and returns filtered insights."""
        from src.application.vision.frame_understanding import (
            FrameInsight,
            FrameUnderstandingService,
        )

        mock_frames = [
            {"path": "/tmp/f1.jpg", "filename": "f1.jpg", "timestamp_sec": 0.0},
            {"path": "/tmp/f2.jpg", "filename": "f2.jpg", "timestamp_sec": 10.0},
        ]

        mock_insights = [
            FrameInsight(
                timestamp=0.0, image_path="/tmp/f1.jpg",
                visual_summary="code snippet", visual_importance="shows key logic",
                importance_score=0.92, related_topic="implementation",
                transcript_relation="illustrates",
            ),
            FrameInsight(
                timestamp=10.0, image_path="/tmp/f2.jpg",
                visual_summary="architecture diagram", visual_importance="explains design",
                importance_score=0.85, related_topic="system design",
                transcript_relation="visualizes",
            ),
        ]

        speech_mock = MagicMock()
        seg1 = MagicMock(start=0.0, text="introduction")
        seg2 = MagicMock(start=8.0, text="architecture overview")
        speech_mock.segments = [seg1, seg2]

        with patch.object(FrameUnderstandingService, "analyze_frames", return_value=mock_insights):
            stage = VisionStage(vision_provider=MagicMock())
            result = stage.run(ctx, {"frames": mock_frames, "speech_result": speech_mock})

        assert isinstance(result, StageResult)
        assert "insights" in result.outputs
        # Both insights have importance >= 0.65, so both pass filter_important
        assert len(result.outputs["insights"]) == 2
        assert result.outputs["insights"][0].image_path == "/tmp/f1.jpg"
        assert result.outputs["insights"][1].image_path == "/tmp/f2.jpg"

    def test_vision_stage_analyzes_with_speech_context(self, ctx):
        """Speech segments are converted to dict format and passed to analyze_frames."""
        from src.application.vision.frame_understanding import (
            FrameInsight,
            FrameUnderstandingService,
        )

        mock_frames = [
            {"path": "/tmp/f1.jpg", "filename": "f1.jpg", "timestamp_sec": 0.0},
        ]

        mock_insight = FrameInsight(
            timestamp=0.0, image_path="/tmp/f1.jpg",
            visual_summary="title slide", visual_importance="introduces topic",
            importance_score=0.70, related_topic="overview",
            transcript_relation="aligns",
        )

        speech_mock = MagicMock()
        seg = MagicMock(start=0.0, text="welcome to the tutorial")
        speech_mock.segments = [seg]

        with patch.object(FrameUnderstandingService, "analyze_frames", return_value=[mock_insight]) as mock_method:
            stage = VisionStage(vision_provider=MagicMock())
            result = stage.run(ctx, {"frames": mock_frames, "speech_result": speech_mock})

            # Verify transcript_segments were converted correctly
            _call_kwargs = mock_method.call_args[1]
            assert "transcript_segments" in _call_kwargs
            assert _call_kwargs["transcript_segments"] == [
                {"t": 0.0, "text": "welcome to the tutorial"},
            ]

        assert len(result.outputs["insights"]) == 1

    def test_vision_stage_skips_when_no_provider(self, ctx):
        """Without a vision provider, returns empty insights regardless of frames."""
        mock_frames = [
            {"path": "/tmp/f1.jpg", "filename": "f1.jpg", "timestamp_sec": 0.0},
        ]

        stage = VisionStage(vision_provider=None)
        result = stage.run(ctx, {"frames": mock_frames, "speech_result": None})

        assert isinstance(result, StageResult)
        assert result.outputs["insights"] == []

    def test_vision_stage_skips_when_no_frames(self, ctx):
        """Without frames in state, returns empty insights even with a provider."""
        stage = VisionStage(vision_provider=MagicMock())
        result = stage.run(ctx, {"frames": [], "speech_result": None})

        assert isinstance(result, StageResult)
        assert result.outputs["insights"] == []

    def test_vision_stage_skips_when_frames_missing_from_state(self, ctx):
        """When state has no 'frames' key, treats as empty and skips."""
        stage = VisionStage(vision_provider=MagicMock())
        result = stage.run(ctx, {"speech_result": None})

        assert isinstance(result, StageResult)
        assert result.outputs["insights"] == []


# ── ExtractFramesStage ──


class TestExtractFramesStage:
    """ExtractFramesStage: key-frame extraction from video files."""

    def test_extract_frames_stage_attributes(self):
        """Class-level attributes must match pipeline contract."""
        assert ExtractFramesStage.id == "extract_frames"
        assert ExtractFramesStage.label == "抽帧"
        assert ExtractFramesStage.percent == 30

    def test_extract_frames_skips_when_no_video_path(self, ctx):
        """No video_path in state returns empty frames list."""
        stage = ExtractFramesStage()
        result = stage.run(ctx, {"video_path": None, "speech_result": MagicMock()})

        assert isinstance(result, StageResult)
        assert result.outputs["frames"] == []

    def test_extract_frames_skips_when_video_file_missing(self, ctx, temp_dir):
        """Non-existent video file returns empty frames list."""
        stage = ExtractFramesStage()
        result = stage.run(
            ctx,
            {"video_path": "/nonexistent/video.mp4", "speech_result": MagicMock()},
        )

        assert result.outputs["frames"] == []
