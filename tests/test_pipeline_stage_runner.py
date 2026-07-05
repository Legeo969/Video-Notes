"""Tests for PipelineStage protocol, StageResult, ManifestStore, StageRunner."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import PipelineStage, StageResult
from src.application.pipeline.runner import FileManifestStore, ManifestStore, StageRunner
from src.application.services.job_queue import TaskCancelledError
from src.domain.types import PipelineRequest


# ── Fixtures ──


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def manifest_store():
    return FileManifestStore()


@pytest.fixture
def ctx(temp_dir):
    return ProcessingContext(
        request=PipelineRequest(input="test"),
        job_dir=temp_dir,
        job_id="test-job",
    )


# ── StageResult ──


class TestStageResult:
    def test_default_values(self):
        result = StageResult()
        assert result.outputs == {}
        assert result.artifact_files == []
        assert result.input_hash == ""

    def test_custom_values(self):
        result = StageResult(
            outputs={"key": "value"},
            artifact_files=["a.json"],
            input_hash="abc123",
        )
        assert result.outputs == {"key": "value"}
        assert result.artifact_files == ["a.json"]
        assert result.input_hash == "abc123"

    def test_mutable_outputs(self):
        result = StageResult()
        result.outputs["key"] = "value"
        assert result.outputs["key"] == "value"


# ── PipelineStage Protocol ──


class TestPipelineStageProtocol:
    def test_simple_stage_satisfies_protocol(self):
        @dataclass
        class SimpleStage:
            id: str = "simple"
            label: str = "Simple"
            percent: int = 50

            def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
                return StageResult(outputs={"result": "done"})

        stage = SimpleStage()
        assert isinstance(stage, PipelineStage)
        assert stage.id == "simple"
        assert stage.label == "Simple"
        assert stage.percent == 50

    def test_stage_passes_state_and_returns_output(self, ctx):
        class EchoStage:
            id = "echo"
            label = "Echo"
            percent = 30

            def run(self, ctx, state):
                return StageResult(
                    outputs={"echo": state.get("input", "")},
                    artifact_files=["echo.json"],
                    input_hash="hash1",
                )

        stage = EchoStage()
        result = stage.run(ctx, {"input": "hello"})
        assert result.outputs == {"echo": "hello"}
        assert result.artifact_files == ["echo.json"]

    def test_stage_receives_processing_context(self, ctx):
        class ContextCheckStage:
            id = "check"
            label = "Check"
            percent = 10

            def run(self, ctx, state):
                return StageResult(
                    outputs={
                        "job_id": ctx.job_id,
                        "input": ctx.request.input,
                    }
                )

        stage = ContextCheckStage()
        result = stage.run(ctx, {})
        assert result.outputs["job_id"] == "test-job"
        assert result.outputs["input"] == "test"


# ── ManifestStore ──


class TestFileManifestStore:
    def test_is_completed_returns_false_for_missing(self, manifest_store, temp_dir):
        assert manifest_store.is_completed(temp_dir, "nonexistent") is False

    def test_save_and_check_completed(self, manifest_store, temp_dir):
        manifest_store.save_completed(temp_dir, "stage1", ["out.json"], input_hash="h1")
        assert manifest_store.is_completed(temp_dir, "stage1") is True

    def test_load_outputs_after_save(self, manifest_store, temp_dir):
        manifest_store.save_completed(
            temp_dir, "stage1",
            artifact_files=["out.json"],
            input_hash="h1",
            outputs={"key": "value"},
        )
        loaded = manifest_store.load_outputs(temp_dir, "stage1")
        assert loaded == {"key": "value"}

    def test_load_outputs_returns_empty_dict_when_no_outputs(self, manifest_store, temp_dir):
        manifest_store.save_completed(temp_dir, "stage1", ["out.json"], input_hash="h1")
        loaded = manifest_store.load_outputs(temp_dir, "stage1")
        assert loaded == {}

    def test_load_outputs_raises_for_missing(self, manifest_store, temp_dir):
        with pytest.raises(FileNotFoundError):
            manifest_store.load_outputs(temp_dir, "nonexistent")

    def test_manifest_path_default(self, manifest_store, temp_dir):
        manifest_store.save_completed(temp_dir, "stage1", [], input_hash="h1")
        path = os.path.join(temp_dir, "artifacts", "_manifest", "stage1.json")
        assert os.path.isfile(path)

    def test_uses_new_path(self, manifest_store, temp_dir):
        manifest_store.save_completed(temp_dir, "stage1", [], input_hash="h1")
        new_path = os.path.join(temp_dir, "artifacts", "_manifest", "stage1.json")
        old_path = os.path.join(temp_dir, "_manifest_stage1.json")
        assert os.path.isfile(new_path)
        assert not os.path.isfile(old_path)


# ── StageRunner ──


class TestStageRunner:
    def test_runs_stages_in_order(self, ctx):
        calls = []

        class StageA:
            id = "A"
            label = "Stage A"
            percent = 25
            def run(self, ctx, state):
                calls.append("A")
                return StageResult(outputs={"a": 1})

        class StageB:
            id = "B"
            label = "Stage B"
            percent = 50
            def run(self, ctx, state):
                calls.append("B")
                return StageResult(outputs={"b": 2})

        runner = StageRunner(manifest_store=_MemManifestStore())
        result = runner.run(ctx, [StageA(), StageB()])
        assert calls == ["A", "B"]
        assert result == {"a": 1, "b": 2}

    def test_skips_completed_stages(self, ctx):
        class StageA:
            id = "A"
            label = "Stage A"
            percent = 25
            def run(self, ctx, state):
                return StageResult(outputs={"a": 1})

        store = _MemManifestStore()
        store.save_completed(ctx.job_dir, "A", artifact_files=[], input_hash="h1", outputs={"a": 1})

        runner = StageRunner(manifest_store=store)
        result = runner.run(ctx, [StageA()])
        assert result == {"a": 1}

    def test_force_reruns_completed_stages(self, ctx):
        calls = []

        class StageA:
            id = "A"
            label = "Stage A"
            percent = 25
            def run(self, ctx, state):
                calls.append("A")
                return StageResult(outputs={"a": 2})

        store = _MemManifestStore()
        store.save_completed(ctx.job_dir, "A", artifact_files=[], input_hash="h1", outputs={"a": 1})
        ctx.force = True

        runner = StageRunner(manifest_store=store)
        result = runner.run(ctx, [StageA()])
        assert calls == ["A"]
        assert result == {"a": 2}

    def test_state_propagates_between_stages(self, ctx):
        class AdderStage:
            id = "adder"
            label = "Adder"
            percent = 50
            def run(self, ctx, state):
                return StageResult(outputs={"sum": state.get("base", 0) + 1})

        runner = StageRunner(manifest_store=_MemManifestStore())
        result = runner.run(ctx, [AdderStage(), AdderStage(), AdderStage()], initial_state={"base": 10})
        assert result["sum"] == 11

    def test_stops_on_cancellation(self, ctx):
        calls = []

        class StageA:
            id = "A"
            label = "Stage A"
            percent = 25
            def run(self, ctx, state):
                calls.append("A")
                return StageResult(outputs={})

        from src.application.services.job_queue import CancellationToken
        token = CancellationToken()
        token.cancel()
        ctx.cancel_token = token

        runner = StageRunner(manifest_store=_MemManifestStore())
        with pytest.raises(TaskCancelledError):
            runner.run(ctx, [StageA()])


# ── In-memory manifest store for tests ──


class _MemManifestStore(ManifestStore):
    def __init__(self):
        self._data: dict[str, dict] = {}

    def is_completed(self, job_dir: str, stage_id: str) -> bool:
        return stage_id in self._data

    def load_outputs(self, job_dir: str, stage_id: str) -> dict[str, Any]:
        if stage_id not in self._data:
            raise FileNotFoundError(stage_id)
        return dict(self._data[stage_id].get("outputs", {}))

    def save_completed(
        self,
        job_dir: str,
        stage_id: str,
        artifact_files: list[str],
        input_hash: str = "",
        outputs: dict[str, Any] | None = None,
    ) -> None:
        self._data[stage_id] = {
            "artifact_files": list(artifact_files),
            "input_hash": input_hash,
            "outputs": dict(outputs) if outputs else {},
        }
