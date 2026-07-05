"""Engine API 冒烟测试 — 直接测试 dispatcher + handlers，不经过 stdin/stdout。"""

import json
import sys
import os
import tempfile
from pathlib import Path

# 确保项目根在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_dispatcher():
    """创建已注册所有处理器的 dispatcher。"""
    from src.api.protocol import Dispatcher
    from src.api.handlers.system import create_system_handlers
    from src.api.handlers.process import create_process_handlers
    from src.api.handlers.settings import create_settings_handlers
    from src.api.handlers.diagnostics import create_diagnostics_handlers

    d = Dispatcher()
    d.register_all(create_system_handlers())
    d.register_all(create_process_handlers(None, None))
    d.register_all(create_settings_handlers())
    d.register_all(create_diagnostics_handlers())
    return d


def _capture_send(captures):
    """返回一个 fake send_response 来捕获结果。"""
    def fake(*args, **kwargs):
        if args:
            captures["id"] = args[0] if len(args) > 0 else None
            captures["result"] = args[1] if len(args) > 1 else kwargs.get("result")
            captures["error"] = args[2] if len(args) > 2 else kwargs.get("error")
        else:
            captures["id"] = kwargs.get("request_id") or kwargs.get("id")
            captures["result"] = kwargs.get("result")
            captures["error"] = kwargs.get("error")
    return fake


def test_protocol_version():
    from src.api.protocol import PROTOCOL_VERSION
    assert PROTOCOL_VERSION == 1


def test_dispatcher_ping():
    d = _make_dispatcher()
    captures = {}
    fake_send = _capture_send(captures)

    import src.api.protocol.dispatcher as dp
    _orig = dp.send_response
    dp.send_response = fake_send

    try:
        d.dispatch({"jsonrpc": "2.0", "protocol_version": 1, "id": 1, "method": "system.ping", "params": {}})
        assert captures.get("result") == "pong", f"Expected pong, got {captures.get('result')}"
        print("  [OK] system.ping returns pong")
    finally:
        dp.send_response = _orig


def test_dispatcher_system_info():
    d = _make_dispatcher()
    captures = {}
    fake_send = _capture_send(captures)

    import src.api.protocol.dispatcher as dp
    _orig = dp.send_response
    dp.send_response = fake_send

    try:
        d.dispatch({"jsonrpc": "2.0", "protocol_version": 1, "id": 2, "method": "system.info", "params": {}})
        info = captures.get("result")
        assert info is not None, "No result"
        assert "engine_version" in info, f"Missing engine_version in {info}"
        assert "python_version" in info, f"Missing python_version in {info}"
        print(f"  [OK] system.info returns version info: {info.get('engine_version')}")
    finally:
        dp.send_response = _orig


def test_dispatcher_method_not_found():
    d = _make_dispatcher()
    captures = {}
    fake_send = _capture_send(captures)

    import src.api.protocol.dispatcher as dp
    _orig = dp.send_response
    dp.send_response = fake_send

    try:
        d.dispatch({"jsonrpc": "2.0", "protocol_version": 1, "id": 3, "method": "nonexistent.method", "params": {}})
        assert captures.get("error") is not None, "Expected error"
        assert captures["error"]["code"] == "METHOD_NOT_FOUND", f"Wrong code: {captures['error']}"
        print("  [OK] nonexistent.method returns METHOD_NOT_FOUND")
    finally:
        dp.send_response = _orig


def test_dispatcher_incompatible_version():
    d = _make_dispatcher()
    captures = {}
    fake_send = _capture_send(captures)

    import src.api.protocol.dispatcher as dp
    import src.api.protocol.framing as fm
    _orig_send = dp.send_response
    _orig_send_fm = fm.send_response
    _orig_err = dp.send_error
    dp.send_response = fake_send
    fm.send_response = fake_send
    def capture_error(req_id, code="", message="", details=None, retryable=False):
        fake_send(req_id, error={"code": code, "message": message, "retryable": retryable})
    dp.send_error = capture_error

    try:
        d.dispatch({"jsonrpc": "2.0", "protocol_version": 999, "id": 4, "method": "system.ping", "params": {}})
        assert captures.get("error") is not None, "Expected error"
        assert captures["error"]["code"] == "INCOMPATIBLE_VERSION", f"Wrong code: {captures['error']}"
        print("  [OK] incompatible version returns INCOMPATIBLE_VERSION")
    finally:
        dp.send_response = _orig_send
        fm.send_response = _orig_send_fm
        dp.send_error = _orig_err


def test_framing_roundtrip():
    """Test Content-Length framing encode/decode."""
    from src.api.protocol.framing import write_frame
    import io

    test_msg = {"jsonrpc": "2.0", "id": 1, "method": "system.ping", "params": {}}

    import src.api.protocol.framing as fm
    captured = bytearray()

    def capture_write(data: bytes) -> None:
        captured.extend(data)

    _orig = fm._write_raw
    fm._write_raw = capture_write

    try:
        write_frame(test_msg)
        full = bytes(captured)
        assert len(full) > 0, "Nothing written"
        assert full.startswith(b"Content-Length:"), f"Bad header: {full[:50]}"
        print(f"  [OK] write_frame produces valid header ({len(full)} bytes)")
    finally:
        fm._write_raw = _orig


def test_event_journal():
    """Test EventJournal basic operations."""
    from src.api.event_journal import EventJournal
    import tempfile

    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "events.db")
        journal = EventJournal(db_path)

        journal.append(1, "job.created", {"title": "test"})
        journal.append(1, "job.started", {})
        journal.append(1, "job.progress", {"progress": 0.5})
        journal.append(2, "job.created", {"title": "test2"})

        events = journal.events_since(1, 0)
        assert len(events) == 3, f"Expected 3 events for job 1, got {len(events)}"
        assert events[0]["event_type"] == "job.created"

        events_since_1 = journal.events_since(1, 1)
        assert len(events_since_1) == 2, f"Expected 2 events since id 1, got {len(events_since_1)}"

        events_for_job = journal.events_for_job(2)
        assert len(events_for_job) == 1, f"Expected 1 event for job 2, got {len(events_for_job)}"

        del journal
        import gc; gc.collect()
        print("  [OK] EventJournal: append, events_since, events_for_job work correctly")
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_dto_job_start_request():
    from src.api.dto.jobs import JobStartRequest

    req = JobStartRequest(input="https://youtube.com/watch?v=test", whisper_model="large-v3")
    assert req.input == "https://youtube.com/watch?v=test"
    assert req.whisper_model == "large-v3"

    req_dict = req.model_dump(exclude_none=True)
    assert "input" in req_dict
    assert "whisper_model" in req_dict
    print("  [OK] JobStartRequest DTO works")


def test_dto_settings_response():
    from src.api.dto.settings import SettingsResponse, ProviderProfile

    provider = ProviderProfile(
        name="默认",
        provider="mimo",
        api_key_configured=True,
        api_key_preview="sk-****8fa2",
        base_url="",
        model="mimo-v2.5",
    )
    settings = SettingsResponse(
        output_dir="./output",
        whisper_model="large-v3",
        providers=[provider],
    )
    data = settings.model_dump()
    assert data["output_dir"] == "./output"
    assert len(data["providers"]) == 1
    assert data["providers"][0]["api_key_preview"] == "sk-****8fa2"
    print("  [OK] SettingsResponse DTO works (key masked)")


if __name__ == "__main__":
    print("Running Engine API smoke tests...")
    test_protocol_version()
    test_dispatcher_ping()
    test_dispatcher_system_info()
    test_dispatcher_method_not_found()
    test_dispatcher_incompatible_version()
    test_framing_roundtrip()
    test_event_journal()
    test_dto_job_start_request()
    test_dto_settings_response()
    print("\n[OK] All 9 smoke tests passed!")