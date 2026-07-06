from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_runtime_payloads.py"


spec = importlib.util.spec_from_file_location("verify_runtime_payloads", SCRIPT)
assert spec is not None
payload_verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = payload_verifier
spec.loader.exec_module(payload_verifier)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write(path: Path, text: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manifest(root: Path, component: str, files: list[str]) -> None:
    _write_json(
        root / "runtime" / "manifests" / f"{component}.json",
        {
            "component": component,
            "version": "1.5.0",
            "platform": "windows-x86_64",
            "engine_api": 1,
            "files": files,
        },
    )


def test_runtime_payload_verifier_accepts_complete_payloads(tmp_path: Path) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe", "ffprobe.exe"])
    _write(tmp_path / "runtime" / "packages" / "ffmpeg-tools" / "ffmpeg.exe")
    _write(tmp_path / "runtime" / "packages" / "ffmpeg-tools" / "ffprobe.exe")

    report = payload_verifier.verify_runtime_payloads(tmp_path)

    assert report.ok
    assert report.components[0].missing_files == []


def test_runtime_payload_verifier_reports_missing_payload_directory(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, "ocr-cpu", ["paddle/", "paddleocr/"])

    report = payload_verifier.verify_runtime_payloads(tmp_path)

    assert not report.ok
    assert report.components[0].missing_files == ["paddle/", "paddleocr/"]


def test_runtime_payload_verifier_reports_missing_manifest_files(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, "base-engine", ["python.exe", "Lib/"])
    _write(tmp_path / "runtime" / "packages" / "base-engine" / "python.exe")

    report = payload_verifier.verify_runtime_payloads(tmp_path)

    assert not report.ok
    assert report.components[0].missing_files == ["Lib/"]


def test_runtime_payload_verifier_uses_payload_map(tmp_path: Path) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe"])
    external = tmp_path / "external" / "ffmpeg"
    _write(external / "ffmpeg.exe")

    report = payload_verifier.verify_runtime_payloads(
        tmp_path,
        payload_map={"ffmpeg-tools": external},
    )

    assert report.ok
    assert report.components[0].payload_dir == str(external.resolve())


def test_runtime_payload_verifier_rejects_unsafe_manifest_path(
    tmp_path: Path,
) -> None:
    _manifest(tmp_path, "bad", ["../outside.exe"])

    with pytest.raises(ValueError, match="invalid component file path"):
        payload_verifier.verify_runtime_payloads(tmp_path)


def test_runtime_payload_verifier_cli_json_reports_failure(
    tmp_path: Path,
    capsys,
) -> None:
    _manifest(tmp_path, "ocr-gpu", ["paddle/"])

    exit_code = payload_verifier.main(["--root", str(tmp_path), "--json"])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert captured["ok"] is False
    assert captured["components"][0]["missing_files"] == ["paddle/"]
