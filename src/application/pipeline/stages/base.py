"""PipelineStage protocol and StageResult dataclass.

Usage:
    @dataclass
    class MyStage:
        id = "my_stage"
        label = "My Stage"
        percent = 50

        def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
            ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.application.pipeline.context import ProcessingContext


@dataclass
class StageResult:
    """output of a pipeline stage."""
    outputs: dict[str, Any] = field(default_factory=dict)
    artifact_files: list[str] = field(default_factory=list)
    input_hash: str = ""


@runtime_checkable
class PipelineStage(Protocol):
    """Contract that each pipeline stage must satisfy."""

    id: str
    label: str
    percent: int

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        ...
