from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_installed_runtime.py"


spec = importlib.util.spec_from_file_location("verify_installed_runtime", SCRIPT)
assert spec is not None
runtime_verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = runtime_verifier
spec.loader.exec_module(runtime_verifier)


def _write_fake_sidecar(path: Path, *, require_dev_path: bool = False) -> None:
    check = (
        "import os\n"
        "if 'DEV_PYTHON_PATH' not in os.environ.get('PATH', ''):\n"
        "    print('missing dev path', file=sys.stderr)\n"
        "    sys.exit(7)\n"
        if require_dev_path else ""
    )
    path.write_text(
        (
            "import json, sys\n"
            f"{check}"
            "def read_frame():\n"
            "    content_length = None\n"
            "    while True:\n"
            "        line = sys.stdin.buffer.readline()\n"
            "        if not line:\n"
            "            return None\n"
            "        line = line.decode().rstrip('\\r\\n')\n"
            "        if not line:\n"
            "            break\n"
            "        if line.lower().startswith('content-length:'):\n"
            "            content_length = int(line.split(':', 1)[1].strip())\n"
            "    return json.loads(sys.stdin.buffer.read(content_length).decode())\n"
            "def write_frame(message):\n"
            "    body = json.dumps(message).encode()\n"
            "    sys.stdout.buffer.write(f'Content-Length: {len(body)}\\r\\n\\r\\n'.encode() + body)\n"
            "    sys.stdout.buffer.flush()\n"
            "write_frame({'jsonrpc':'2.0','protocol_version':1,'method':'engine.hello','params':{}})\n"
            "request = read_frame()\n"
            "if request:\n"
            "    write_frame({'jsonrpc':'2.0','protocol_version':1,'id':request.get('id'),'result':'pong'})\n"
        ),
        encoding="utf-8",
    )


def test_installed_runtime_verifier_pings_sidecar_with_sanitized_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_sidecar = tmp_path / "fake_sidecar.py"
    _write_fake_sidecar(fake_sidecar)
    monkeypatch.setenv("PYTHONPATH", "should-not-leak")
    monkeypatch.setenv("VIDEO_NOTES_ENGINE", "should-not-leak")

    report = runtime_verifier.verify_installed_runtime(
        sidecar_command=[sys.executable, str(fake_sidecar)],
    )

    assert report.ok, report.to_dict()


def test_installed_runtime_verifier_isolates_runtime_state(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    env = runtime_verifier._offline_env(state_dir=state_dir)

    assert env["VIDEO_NOTES_DATA_DIR"] == str(state_dir)
    assert env["VIDEO_NOTES_JOBS_DIR"] == str(state_dir / "jobs")
    assert env["VIDEO_NOTES_SETTINGS_PATH"] == str(state_dir / "settings.json")
    assert "PYTHONPATH" not in env
    assert "VIDEO_NOTES_ENGINE" not in env


def test_installed_runtime_verifier_detects_sidecar_depending_on_dev_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fake_sidecar = tmp_path / "path_dependent_sidecar.py"
    _write_fake_sidecar(fake_sidecar, require_dev_path=True)
    monkeypatch.setenv("PATH", "DEV_PYTHON_PATH")

    report = runtime_verifier.verify_installed_runtime(
        sidecar_command=[sys.executable, str(fake_sidecar)],
    )

    assert not report.ok
    assert {issue.code for issue in report.errors} == {"sidecar_exit_failed"}


def test_installed_runtime_verifier_locates_app_and_sidecar(tmp_path: Path) -> None:
    app_dir = tmp_path / "installed"
    sidecar_dir = app_dir / "binaries"
    sidecar_dir.mkdir(parents=True)
    app_exe = app_dir / "Video Notes AI.exe"
    sidecar = sidecar_dir / "python-engine.exe"
    installer = tmp_path / "Video Notes AI_1.5.0_x64-setup.exe"
    app_exe.write_text("app", encoding="utf-8")
    sidecar.write_text("sidecar", encoding="utf-8")
    installer.write_text("installer", encoding="utf-8")

    report = runtime_verifier.verify_installed_runtime(
        app_dir=app_dir,
        installer=installer,
        run_sidecar_ping=False,
    )

    assert report.ok, report.to_dict()
    assert report.app_exe == str(app_exe.resolve())
    assert report.sidecar == str(sidecar.resolve())
    assert report.installer == str(installer.resolve())


def test_installed_runtime_verifier_reports_missing_sidecar(tmp_path: Path) -> None:
    app_dir = tmp_path / "installed"
    app_dir.mkdir()
    (app_dir / "Video Notes AI.exe").write_text("app", encoding="utf-8")

    report = runtime_verifier.verify_installed_runtime(
        app_dir=app_dir,
        run_sidecar_ping=False,
    )

    assert not report.ok
    assert "sidecar_missing" in {issue.code for issue in report.errors}


def test_installed_runtime_verifier_cli_json(tmp_path: Path, capsys) -> None:
    app_dir = tmp_path / "installed"
    sidecar_dir = app_dir / "binaries"
    sidecar_dir.mkdir(parents=True)
    (app_dir / "Video Notes AI.exe").write_text("app", encoding="utf-8")
    (sidecar_dir / "python-engine.exe").write_text("sidecar", encoding="utf-8")

    exit_code = runtime_verifier.main([
        "--app-dir",
        str(app_dir),
        "--skip-sidecar-ping",
        "--json",
    ])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ok"] is True
