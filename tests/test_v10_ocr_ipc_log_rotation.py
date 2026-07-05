from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import main as app_main
from src.application.diagnostics import crash_guard
from src.infrastructure.video import ocr_worker_cli
from src.infrastructure.video.ocr_isolated import IsolatedOCREngine, _PREFIX


def _write_log(path: Path, *, clean: bool, size: int = 0) -> None:
    body = "x" * size
    if clean:
        body += "\n=== clean process exit 2026-07-04T00:00:00 pid=999999 ===\n"
    path.write_text(body, encoding="utf-8")


def test_worker_protocol_escapes_non_gbk_characters(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(ocr_worker_cli.sys, "stdout", stream)
    ocr_worker_cli._send({"type": "result", "text": "中文 ✓"})
    wire = stream.getvalue()
    assert wire.startswith(_PREFIX)
    assert "✓" not in wire
    assert "\\u2713" in wire
    assert json.loads(wire[len(_PREFIX):])["text"] == "中文 ✓"


def test_ocr_worker_subprocess_forces_utf8_environment(monkeypatch):
    captured = {}

    class FakeProc:
        stdin = io.StringIO()
        stdout = io.StringIO(_PREFIX + '{"type":"ready","ok":true,"device":"gpu:0"}\n')
        stderr = io.StringIO()
        returncode = None

        def poll(self):
            return None

        def terminate(self):
            self.returncode = 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.returncode = -9

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    engine = IsolatedOCREngine(startup_timeout=1)
    monkeypatch.setattr("src.infrastructure.video.ocr_isolated.subprocess.Popen", fake_popen)
    monkeypatch.setattr("src.infrastructure.video.ocr_isolated.threading.Thread", MagicMock())
    # We only need to inspect Popen arguments; do not wait for the mocked reader.
    engine._start = IsolatedOCREngine._start.__get__(engine, IsolatedOCREngine)
    with patch.object(engine._messages, "get", side_effect=RuntimeError("stop")):
        try:
            engine._start("gpu:0")
        except RuntimeError:
            pass
    env = captured["env"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["VNA_DISABLE_SESSION_LOG"] == "1"
    assert captured.get("text") in (None, False)
    assert "encoding" not in captured


def test_log_retention_keeps_crash_evidence_longer(tmp_path, monkeypatch):
    now = time.time()
    clean_old = tmp_path / "session-20260101-000000-pid900001.log"
    crash_old = tmp_path / "session-20260101-000001-pid900002.log"
    crash_expired = tmp_path / "session-20260101-000002-pid900003.log"
    _write_log(clean_old, clean=True)
    _write_log(crash_old, clean=False)
    _write_log(crash_expired, clean=False)
    os.utime(clean_old, (now - 8 * 86400, now - 8 * 86400))
    os.utime(crash_old, (now - 8 * 86400, now - 8 * 86400))
    os.utime(crash_expired, (now - 31 * 86400, now - 31 * 86400))
    monkeypatch.setattr(crash_guard, "_is_process_running", lambda _pid: False)

    crash_guard.cleanup_old_logs(tmp_path, now=now)

    assert not clean_old.exists()
    assert crash_old.exists()
    assert not crash_expired.exists()


def test_log_count_and_size_caps_delete_oldest_inactive(tmp_path, monkeypatch):
    now = time.time()
    for index in range(6):
        path = tmp_path / f"session-20260704-00000{index}-pid91{index:04d}.log"
        _write_log(path, clean=True, size=100)
        os.utime(path, (now - (100 - index), now - (100 - index)))
    monkeypatch.setattr(crash_guard, "_is_process_running", lambda _pid: False)

    result = crash_guard.cleanup_old_logs(
        tmp_path, now=now, normal_retention_days=999, abnormal_retention_days=999,
        max_files=3, max_total_bytes=10_000,
    )

    assert result["remaining_files"] == 3
    assert len(list(tmp_path.glob("session-*.log"))) == 3


def test_last_stage_json_is_never_removed(tmp_path, monkeypatch):
    marker = tmp_path / "last-stage.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(crash_guard, "_is_process_running", lambda _pid: False)
    crash_guard.cleanup_old_logs(tmp_path, max_files=0, max_total_bytes=0)
    assert marker.exists()


def test_read_only_probes_do_not_install_session_logging():
    assert not app_main._should_install_crash_guard(["--check-ocr"])
    assert not app_main._should_install_crash_guard(["--template-list"])
    assert not app_main._should_install_crash_guard(["--doctor"])
    assert app_main._should_install_crash_guard([])
    assert app_main._should_install_crash_guard(["https://example.com/video"])
