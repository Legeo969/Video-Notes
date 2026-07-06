from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stage_runtime_payloads.py"


spec = importlib.util.spec_from_file_location("stage_runtime_payloads", SCRIPT)
assert spec is not None
payload_stager = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = payload_stager
spec.loader.exec_module(payload_stager)


def _write(path: Path, text: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


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


def test_stage_runtime_payloads_copies_python_runtime_payload(tmp_path: Path) -> None:
    _manifest(tmp_path, "base-engine", ["python.exe", "Lib/"])
    python_root = tmp_path / "python-runtime"
    _write(python_root / "python.exe")
    _write(python_root / "Lib" / "os.py")

    result = payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["base-engine"],
        python_root=python_root,
    )

    payload = tmp_path / "runtime" / "packages" / "base-engine"
    assert result[0]["component"] == "base-engine"
    assert (payload / "python.exe").is_file()
    assert (payload / "Lib" / "os.py").is_file()


def test_stage_runtime_payloads_copies_ffmpeg_payload(tmp_path: Path) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe", "ffprobe.exe"])
    ffmpeg_dir = tmp_path / "ffmpeg-bin"
    _write(ffmpeg_dir / "ffmpeg.exe")
    _write(ffmpeg_dir / "ffprobe.exe")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["ffmpeg-tools"],
        ffmpeg_dir=ffmpeg_dir,
    )

    payload = tmp_path / "runtime" / "packages" / "ffmpeg-tools"
    assert (payload / "ffmpeg.exe").is_file()
    assert (payload / "ffprobe.exe").is_file()


def test_stage_runtime_payloads_copies_site_packages_payload(tmp_path: Path) -> None:
    _manifest(tmp_path, "transcription-cpu", ["ctranslate2/", "faster_whisper/"])
    site_packages = tmp_path / "site-packages"
    _write(site_packages / "ctranslate2" / "__init__.py")
    _write(site_packages / "faster_whisper" / "__init__.py")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["transcription-cpu"],
        site_packages=site_packages,
    )

    payload = tmp_path / "runtime" / "packages" / "transcription-cpu"
    assert (payload / "ctranslate2" / "__init__.py").is_file()
    assert (payload / "faster_whisper" / "__init__.py").is_file()


def test_stage_runtime_payloads_uses_source_map(tmp_path: Path) -> None:
    _manifest(tmp_path, "ocr-cpu", ["paddle/"])
    source = tmp_path / "external-ocr"
    _write(source / "paddle" / "__init__.py")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["ocr-cpu"],
        source_map={"ocr-cpu": source},
    )

    assert (
        tmp_path / "runtime" / "packages" / "ocr-cpu" / "paddle" / "__init__.py"
    ).is_file()


def test_stage_runtime_payloads_refuses_to_overwrite_without_clean(tmp_path: Path) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe"])
    source = tmp_path / "ffmpeg-bin"
    _write(source / "ffmpeg.exe", "new")
    target = tmp_path / "runtime" / "packages" / "ffmpeg-tools"
    _write(target / "ffmpeg.exe", "old")

    with pytest.raises(FileExistsError):
        payload_stager.stage_runtime_payloads(
            tmp_path,
            components=["ffmpeg-tools"],
            ffmpeg_dir=source,
        )

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["ffmpeg-tools"],
        ffmpeg_dir=source,
        clean=True,
    )
    assert (target / "ffmpeg.exe").read_text(encoding="utf-8") == "new"


def test_stage_runtime_payloads_reports_missing_source_file(tmp_path: Path) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe", "ffprobe.exe"])
    source = tmp_path / "ffmpeg-bin"
    _write(source / "ffmpeg.exe")

    with pytest.raises(FileNotFoundError, match="ffprobe.exe"):
        payload_stager.stage_runtime_payloads(
            tmp_path,
            components=["ffmpeg-tools"],
            ffmpeg_dir=source,
        )


def test_stage_runtime_payloads_cli_json(tmp_path: Path, capsys) -> None:
    _manifest(tmp_path, "ffmpeg-tools", ["ffmpeg.exe"])
    source = tmp_path / "ffmpeg-bin"
    _write(source / "ffmpeg.exe")

    exit_code = payload_stager.main([
        "--root",
        str(tmp_path),
        "--component",
        "ffmpeg-tools",
        "--ffmpeg-dir",
        str(source),
        "--json",
    ])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["components"][0]["component"] == "ffmpeg-tools"


def test_stage_runtime_payloads_accepts_bom_source_map(tmp_path: Path) -> None:
    _manifest(tmp_path, "ocr-cpu", ["paddle/"])
    source = tmp_path / "external-ocr"
    _write(source / "paddle" / "__init__.py")
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        json.dumps({"ocr-cpu": str(source)}),
        encoding="utf-8-sig",
    )

    exit_code = payload_stager.main([
        "--root",
        str(tmp_path),
        "--component",
        "ocr-cpu",
        "--source-map",
        str(source_map),
    ])

    assert exit_code == 0
    assert (
        tmp_path / "runtime" / "packages" / "ocr-cpu" / "paddle" / "__init__.py"
    ).is_file()
