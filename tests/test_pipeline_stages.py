"""Tests for FuseTimelineStage, MapNotesStage, ReduceNotesStage."""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock, PropertyMock

import pytest

from src.application.fusion import FusionEngine
from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import StageRunner
from src.application.pipeline.stages.base import StageResult
from src.application.pipeline.stages.fuse_timeline import FuseTimelineStage
from src.application.pipeline.stages.map_notes import MapNotesStage
from src.application.pipeline.stages.reduce_notes import ReduceNotesStage
from src.domain.types import PipelineRequest


@pytest.fixture
def ctx():
    return ProcessingContext(
        request=PipelineRequest(
            input="test",
            provider="mimo",
            api_key="test-key",
            gpt_model="gpt-4o",
        ),
        job_dir=tempfile.mkdtemp(),
        job_id="test-job",
    )


@pytest.fixture
def mock_provider():
    p = MagicMock()
    p.model = "gpt-4o"
    return p


@pytest.fixture
def speech_result():
    from src.application.speech import SpeechResult, SpeechSegment
    return SpeechResult(
        full_text="Hello world. This is a test. Goodbye.",
        segments=[
            SpeechSegment(start=0.0, end=2.0, text="Hello world."),
            SpeechSegment(start=2.5, end=4.0, text="This is a test."),
            SpeechSegment(start=4.5, end=6.0, text="Goodbye."),
        ],
        language="en",
        elapsed=1.0,
    )


@pytest.fixture
def insights():
    return []


@pytest.fixture
def mem_manifest_store():
    from src.application.pipeline.runner import ManifestStore
    from typing import Any

    class _Mem(ManifestStore):
        def __init__(self):
            self._data: dict = {}
        def is_completed(self, *a, **kw): return False
        def load_outputs(self, *a, **kw): return {}
        def save_completed(self, *a, **kw): pass

    return _Mem()


# ── FuseTimelineStage ──


class TestFuseTimelineStage:
    def test_stage_attributes(self):
        stage = FuseTimelineStage()
        assert stage.id == "fuse_timeline"
        assert stage.label == "融合转录+视觉"
        assert stage.percent == 60

    def test_run_returns_stage_result(self, ctx, speech_result, insights):
        stage = FuseTimelineStage()
        result = stage.run(ctx, {
            "speech_result": speech_result,
            "insights": insights,
        })
        assert isinstance(result, StageResult)
        assert "timeline" in result.outputs
        assert "chunks" in result.outputs

    def test_fuse_creates_timeline_with_items(self, ctx, speech_result, insights):
        stage = FuseTimelineStage()
        result = stage.run(ctx, {
            "speech_result": speech_result,
            "insights": insights,
        })
        timeline = result.outputs["timeline"]
        assert len(timeline.items) > 0
        assert timeline.duration > 0

    def test_fuse_creates_chunks(self, ctx, speech_result, insights):
        stage = FuseTimelineStage()
        result = stage.run(ctx, {
            "speech_result": speech_result,
            "insights": insights,
        })
        chunks = result.outputs["chunks"]
        assert len(chunks) > 0
        assert "index" in chunks[0]
        assert "start" in chunks[0]
        assert "end" in chunks[0]
        assert "transcript" in chunks[0]

    def test_fuse_without_insights(self, ctx, speech_result):
        stage = FuseTimelineStage()
        result = stage.run(ctx, {
            "speech_result": speech_result,
            "insights": [],
        })
        assert "timeline" in result.outputs
        assert "chunks" in result.outputs

    def test_fuse_via_stage_runner(self, ctx, speech_result, insights, mem_manifest_store):
        runner = StageRunner(manifest_store=mem_manifest_store)
        result = runner.run(ctx, [FuseTimelineStage()], initial_state={
            "speech_result": speech_result,
            "insights": insights,
        })
        assert "timeline" in result
        assert "chunks" in result


# ── MapNotesStage ──


class TestMapNotesStage:
    def test_stage_attributes(self):
        stage = MapNotesStage()
        assert stage.id == "map_notes"
        assert stage.label == "MAP 并行摘要"
        assert stage.percent == 70

    def test_run_with_injected_provider(self, ctx, mock_provider):
        from src.application.llm.map_stage import MapResult
        mock_provider.chat = MagicMock(return_value="""{
            "segment_summary": "test summary",
            "key_points": ["point1"],
            "technical_details": "",
            "visual_references": [],
            "questions_answered": [],
            "difficulty_level": "beginner"
        }""")

        stage = MapNotesStage(provider=mock_provider, model="gpt-4o")
        chunks = [{"index": 0, "start": 0, "end": 5, "transcript": "test"}]
        result = stage.run(ctx, {"chunks": chunks})
        assert isinstance(result, StageResult)
        assert "map_results" in result.outputs
        assert len(result.outputs["map_results"]) == 1

    def test_run_creates_provider_from_context_when_not_injected(self, ctx):
        stage = MapNotesStage(model="gpt-4o")
        chunks = [{"index": 0, "start": 0, "end": 5, "transcript": "test"}]
        with pytest.raises(Exception):
            stage.run(ctx, {"chunks": chunks})

    def test_raises_on_all_failures(self, ctx, mock_provider):
        mock_provider.chat = MagicMock(return_value="not json")

        stage = MapNotesStage(provider=mock_provider, model="gpt-4o")
        chunks = [{"index": 0, "start": 0, "end": 5, "transcript": "test"}]
        with pytest.raises(RuntimeError, match="所有 LLM 调用失败"):
            stage.run(ctx, {"chunks": chunks})

    def test_empty_chunks(self, ctx, mock_provider):
        stage = MapNotesStage(provider=mock_provider, model="gpt-4o")
        result = stage.run(ctx, {"chunks": []})
        assert result.outputs["map_results"] == []

    def test_map_via_stage_runner(self, ctx, mock_provider, mem_manifest_store):
        mock_provider.chat = MagicMock(return_value="""{
            "segment_summary": "test",
            "key_points": [],
            "technical_details": "",
            "visual_references": [],
            "questions_answered": [],
            "difficulty_level": "beginner"
        }""")

        runner = StageRunner(manifest_store=mem_manifest_store)
        result = runner.run(ctx, [MapNotesStage(provider=mock_provider, model="gpt-4o")], initial_state={
            "chunks": [{"index": 0, "start": 0, "end": 5, "transcript": "test"}],
        })
        assert "map_results" in result


# ── ReduceNotesStage ──


class TestReduceNotesStage:
    def test_stage_attributes(self):
        stage = ReduceNotesStage()
        assert stage.id == "reduce_notes"
        assert stage.label == "REDUCE 生成最终笔记"
        assert stage.percent == 85

    def test_run_with_injected_provider(self, ctx, mock_provider):
        mock_provider.chat = MagicMock(return_value="# Final Notes\n\nContent here.")

        stage = ReduceNotesStage(provider=mock_provider, model="gpt-4o")
        from src.application.llm.map_stage import MapResult
        map_results = [MapResult(index=0, start=0, end=5, summary="test", key_points=[], technical_details="", visual_references=[], difficulty="beginner")]
        from src.application.fusion import Timeline, TimelineItem
        timeline = Timeline(items=[TimelineItem(timestamp=0, text="hello", visual="")], chapters=[], duration=10.0)

        result = stage.run(ctx, {
            "map_results": map_results,
            "timeline": timeline,
        })
        assert isinstance(result, StageResult)
        assert "notes" in result.outputs
        assert len(result.outputs["notes"]) > 0

    def test_run_uses_request_title_when_available(self, ctx, mock_provider):
        ctx.request.title = "Custom Title"
        mock_provider.chat = MagicMock(return_value="# Custom Title\n\nContent.")

        stage = ReduceNotesStage(provider=mock_provider, model="gpt-4o")
        from src.application.llm.map_stage import MapResult
        from src.application.fusion import Timeline, TimelineItem
        result = stage.run(ctx, {
            "map_results": [MapResult(index=0, start=0, end=5, summary="test", key_points=[], technical_details="", visual_references=[], difficulty="beginner")],
            "timeline": Timeline(items=[TimelineItem(timestamp=0, text="hello", visual="")], chapters=[], duration=10.0),
        })
        assert "notes" in result.outputs

    def test_empty_map_results(self, ctx, mock_provider):
        stage = ReduceNotesStage(provider=mock_provider, model="gpt-4o")
        from src.application.fusion import Timeline, TimelineItem
        result = stage.run(ctx, {
            "map_results": [],
            "timeline": Timeline(items=[TimelineItem(timestamp=0, text="hello", visual="")], chapters=[], duration=10.0),
        })
        assert result.outputs["notes"] == ""

    def test_reduce_via_stage_runner(self, ctx, mock_provider, mem_manifest_store):
        mock_provider.chat = MagicMock(return_value="# Notes\n\nContent.")

        from src.application.llm.map_stage import MapResult
        from src.application.fusion import Timeline, TimelineItem

        runner = StageRunner(manifest_store=mem_manifest_store)
        result = runner.run(ctx, [ReduceNotesStage(provider=mock_provider, model="gpt-4o")], initial_state={
            "map_results": [MapResult(index=0, start=0, end=5, summary="test", key_points=[], technical_details="", visual_references=[], difficulty="beginner")],
            "timeline": Timeline(items=[TimelineItem(timestamp=0, text="hello", visual="")], chapters=[], duration=10.0),
        })
        assert "notes" in result


# ── ReduceStage (direct) ──


class TestHierarchicalReduce:
    """Tests for ReduceStage hierarchical reduce logic."""

    def make_map_results(self, count: int):
        from src.application.llm.map_stage import MapResult
        return [
            MapResult(
                index=i, start=float(i), end=float(i + 1),
                summary=f"test{i}", key_points=[f"kp{i}_a", f"kp{i}_b"],
                technical_details="", visual_references=[], difficulty="beginner",
            )
            for i in range(count)
        ]

    def test_single_group_bypasses_hierarchy(self):
        """≤ GROUP_SIZE MAP results → single reduce call (no hierarchy)."""
        from src.application.llm.reduce_stage import ReduceStage, GROUP_SIZE

        provider = MagicMock()
        provider.chat = MagicMock(return_value="# Notes\n\nContent.")
        stage = ReduceStage(provider, "gpt-4o")

        results = self.make_map_results(min(5, GROUP_SIZE))
        stage.execute(results, title="test", duration=10, source="test.mp4")

        # Single reduce call only (no group reduce calls)
        assert provider.chat.call_count == 1

    def test_large_input_splits_into_groups(self):
        """> GROUP_SIZE MAP results → multiple group reduce + one final reduce."""
        from src.application.llm.reduce_stage import ReduceStage, GROUP_SIZE

        provider = MagicMock()
        provider.chat = MagicMock(return_value="# Group Result\n\nContent.")
        stage = ReduceStage(provider, "gpt-4o")

        results = self.make_map_results(20)
        result = stage.execute(results, title="test", duration=10, source="test.mp4")

        # 20 results / GROUP_SIZE=8 = 3 groups → 3 group calls + 1 final = 4 calls
        expected_calls = (20 + GROUP_SIZE - 1) // GROUP_SIZE + 1  # groups + 1 final
        assert provider.chat.call_count == expected_calls
        assert len(result.markdown) > 0

    def test_group_failure_skips_group(self):
        """One group failing should not break the whole reduce."""
        from src.application.llm.reduce_stage import ReduceStage, GROUP_SIZE

        call_count = [0]

        def mock_chat(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # first group fails
                raise Exception("group fail")
            return "# Surviving result\n\nContent."

        provider = MagicMock()
        provider.chat = MagicMock(side_effect=mock_chat)
        stage = ReduceStage(provider, "gpt-4o")

        results = self.make_map_results(20)
        result = stage.execute(results, title="test", duration=10, source="test.mp4")

        # Should still produce something (from surviving groups)
        assert len(result.markdown) > 0

    def test_all_groups_fail(self):
        """All groups failing → error result."""
        from src.application.llm.reduce_stage import ReduceStage, GROUP_SIZE

        provider = MagicMock()
        provider.chat = MagicMock(side_effect=Exception("always fail"))
        stage = ReduceStage(provider, "gpt-4o")

        results = self.make_map_results(20)
        result = stage.execute(results, title="test", duration=10, source="test.mp4")

        assert "所有分组 REDUCE 均失败" in result.markdown
        assert result.error == "all groups failed"

    def test_single_group_after_failures(self):
        """Only one group succeeds → return its result directly (no final reduce)."""
        from src.application.llm.reduce_stage import ReduceStage, GROUP_SIZE

        call_count = [0]

        def mock_chat(**kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:  # first 2 groups fail
                raise Exception("group fail")
            return "# Solo survivor\n\nContent."

        provider = MagicMock()
        provider.chat = MagicMock(side_effect=mock_chat)
        stage = ReduceStage(provider, "gpt-4o")

        results = self.make_map_results(20)
        result = stage.execute(results, title="test", duration=10, source="test.mp4")

        # Only one group succeeded → no final reduce, result is direct group output
        assert "# Solo survivor" in result.markdown
        # call_count = 3 total: 3 groups (2 failures, 1 success) → 3 calls, no final
        assert call_count[0] == 3
