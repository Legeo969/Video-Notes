"""Tests for ProcessingContext — centralized pipeline state container.

Tests cover:
- Initialization with all fields
- check_cancelled raises when CancellationToken is set
- check_cancelled is no-op when not cancelled
- set_stage calls progress callback with correct args
- set_stage handles None progress gracefully
- force flag is preserved
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.application.pipeline.context import ProcessingContext, ProgressCallback
from src.domain.types import PipelineRequest
from src.application.services.job_queue import CancellationToken, TaskCancelledError


class TestProcessingContextInit:
    """ProcessingContext 初始化"""

    def test_all_fields(self):
        """所有字段正确初始化。"""
        request = PipelineRequest(input="test.mp4")
        token = CancellationToken()
        cb_calls = []

        def progress(stage: str, msg: str, pct: int):
            cb_calls.append((stage, msg, pct))

        ctx = ProcessingContext(
            request=request,
            job_dir="/tmp/jobs/abc123",
            job_id="abc123",
            resume_run_id=42,
            force=True,
            owned_files=["/tmp/a.wav", "/tmp/b.mp4"],
            progress=progress,
            cancel_token=token,
        )

        assert ctx.request is request
        assert ctx.job_dir == "/tmp/jobs/abc123"
        assert ctx.job_id == "abc123"
        assert ctx.resume_run_id == 42
        assert ctx.force is True
        assert ctx.owned_files == ["/tmp/a.wav", "/tmp/b.mp4"]
        assert ctx.progress is progress
        assert ctx.cancel_token is token

    def test_defaults(self):
        """可选字段有合理默认值。"""
        request = PipelineRequest(input="test.mp4")
        ctx = ProcessingContext(
            request=request,
            job_dir="/tmp/jobs/abc123",
            job_id="abc123",
            owned_files=[],
        )
        assert ctx.resume_run_id is None
        assert ctx.force is False
        assert ctx.progress is None
        assert ctx.cancel_token is None

    def test_force_flag_preserved(self):
        """force==True 被正确保存和读取。"""
        ctx_true = ProcessingContext(
            request=PipelineRequest(input="x"),
            job_dir="/tmp/d",
            job_id="x",
            owned_files=[],
            force=True,
        )
        ctx_false = ProcessingContext(
            request=PipelineRequest(input="x"),
            job_dir="/tmp/d",
            job_id="x",
            owned_files=[],
            force=False,
        )
        assert ctx_true.force is True
        assert ctx_false.force is False


class TestProcessingContextCheckCancelled:
    """check_cancelled 方法"""

    def test_raises_when_cancelled(self):
        """取消后调用 check_cancelled 应抛出 TaskCancelledError。"""
        token = CancellationToken()
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            cancel_token=token,
        )
        token.cancel()
        with pytest.raises(TaskCancelledError):
            ctx.check_cancelled()

    def test_noop_when_not_cancelled(self):
        """未取消时 check_cancelled 应无异常。"""
        token = CancellationToken()
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            cancel_token=token,
        )
        # Should not raise
        ctx.check_cancelled()

    def test_noop_when_no_token(self):
        """cancel_token 为 None 时 check_cancelled 应无异常。"""
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            cancel_token=None,
        )
        ctx.check_cancelled()


class TestProcessingContextSetStage:
    """set_stage 方法"""

    def test_calls_progress_callback(self, capsys):
        """set_stage 调用 progress callback。"""
        captured: list[tuple[str, str, int]] = []

        def progress(stage: str, msg: str, pct: int):
            captured.append((stage, msg, pct))

        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            progress=progress,
        )
        ctx.set_stage("resolving", "解析输入源…", 5)

        assert len(captured) == 1
        assert captured[0] == ("resolving", "解析输入源…", 5)

    def test_prints_output(self, capsys):
        """set_stage 打印格式化的阶段信息。"""
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
        )
        ctx.set_stage("transcribing", "Whisper 转录中…", 15)

        captured = capsys.readouterr()
        assert "transcribing" in captured.out
        assert "Whisper 转录中…" in captured.out
        assert "15" in captured.out or "=" in captured.out

    def test_handles_none_progress_gracefully(self, capsys):
        """progress 为 None 时 set_stage 不崩溃（只打印）。"""
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            progress=None,
        )
        # Should not raise
        ctx.set_stage("generating_notes", "生成笔记…", 50)
        captured = capsys.readouterr()
        assert "generating_notes" in captured.out

    def test_multiple_calls(self, capsys):
        """连续多次 set_stage 全部触发回调。"""
        calls: list[tuple[str, str, int]] = []

        def progress(stage: str, msg: str, pct: int):
            calls.append((stage, msg, pct))

        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
            progress=progress,
        )
        ctx.set_stage("resolving", "解析中…", 5)
        ctx.set_stage("transcribing", "转录中…", 30)
        ctx.set_stage("generating_notes", "生成笔记…", 80)

        assert len(calls) == 3
        assert calls[0] == ("resolving", "解析中…", 5)
        assert calls[1] == ("transcribing", "转录中…", 30)
        assert calls[2] == ("generating_notes", "生成笔记…", 80)


class TestProcessingContextOwnedFiles:
    """owned_files 可变列表"""

    def test_mutable(self):
        """owned_files 可追加。"""
        ctx = ProcessingContext(
            request=PipelineRequest(input="test.mp4"),
            job_dir="/tmp/jobs/x",
            job_id="x",
            owned_files=[],
        )
        ctx.owned_files.append("/tmp/new.wav")
        assert "/tmp/new.wav" in ctx.owned_files
        assert len(ctx.owned_files) == 1
