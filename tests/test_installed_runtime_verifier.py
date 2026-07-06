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


def test_installed_runtime_verifier_locates_app_and_installer(tmp_path: Path) -> None:
    app_dir = tmp_path / "installed"
    app_dir.mkdir()
    app_exe = app_dir / "Video Notes AI.exe"
    installer = tmp_path / "Video Notes AI_1.5.0_x64-setup.exe"
    app_exe.write_text("app", encoding="utf-8")
    installer.write_text("installer", encoding="utf-8")

    report = runtime_verifier.verify_installed_runtime(
        app_dir=app_dir,
        installer=installer,
    )

    assert report.ok, report.to_dict()
    assert report.app_exe == str(app_exe.resolve())
    assert report.installer == str(installer.resolve())


def test_installed_runtime_verifier_reports_missing_app_exe(tmp_path: Path) -> None:
    app_dir = tmp_path / "installed"
    app_dir.mkdir()

    report = runtime_verifier.verify_installed_runtime(app_dir=app_dir)

    assert not report.ok
    assert "app_exe_missing" in {issue.code for issue in report.errors}


def test_installed_runtime_verifier_cli_json(tmp_path: Path, capsys) -> None:
    app_dir = tmp_path / "installed"
    app_dir.mkdir()
    (app_dir / "Video Notes AI.exe").write_text("app", encoding="utf-8")

    exit_code = runtime_verifier.main([
        "--app-dir",
        str(app_dir),
        "--json",
    ])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ok"] is True
    assert "sidecar" not in captured
