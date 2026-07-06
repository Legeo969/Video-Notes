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


def test_stage_runtime_payloads_copies_whisper_cpp_payload(tmp_path: Path) -> None:
    _manifest(tmp_path, "whisper-cpp-tools", ["whisper-cli.exe", "whisper.dll"])
    source = tmp_path / "runtime" / "packages" / "whisper-cpp-tools"
    _write(source / "whisper-cli.exe")
    _write(source / "whisper.dll")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["whisper-cpp-tools"],
        clean=True,
    )

    payload = tmp_path / "runtime" / "packages" / "whisper-cpp-tools"
    assert (payload / "whisper-cli.exe").is_file()
    assert (payload / "whisper.dll").is_file()


def test_stage_runtime_payloads_copies_tesseract_payload(tmp_path: Path) -> None:
    _manifest(tmp_path, "tesseract-ocr-tools", ["tesseract.exe", "tessdata/"])
    source = tmp_path / "runtime" / "packages" / "tesseract-ocr-tools"
    _write(source / "tesseract.exe")
    _write(source / "tessdata" / "eng.traineddata")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["tesseract-ocr-tools"],
        clean=True,
    )

    payload = tmp_path / "runtime" / "packages" / "tesseract-ocr-tools"
    assert (payload / "tesseract.exe").is_file()
    assert (payload / "tessdata" / "eng.traineddata").is_file()


def test_stage_runtime_payloads_uses_source_map(tmp_path: Path) -> None:
    _manifest(tmp_path, "download-tools", ["yt-dlp.exe"])
    source = tmp_path / "external-download-tools"
    _write(source / "yt-dlp.exe")

    payload_stager.stage_runtime_payloads(
        tmp_path,
        components=["download-tools"],
        source_map={"download-tools": source},
    )

    assert (tmp_path / "runtime" / "packages" / "download-tools" / "yt-dlp.exe").is_file()


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
    _manifest(tmp_path, "download-tools", ["yt-dlp.exe"])
    source = tmp_path / "external-download-tools"
    _write(source / "yt-dlp.exe")
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        json.dumps({"download-tools": str(source)}),
        encoding="utf-8-sig",
    )

    exit_code = payload_stager.main([
        "--root",
        str(tmp_path),
        "--component",
        "download-tools",
        "--source-map",
        str(source_map),
    ])

    assert exit_code == 0
    assert (tmp_path / "runtime" / "packages" / "download-tools" / "yt-dlp.exe").is_file()
