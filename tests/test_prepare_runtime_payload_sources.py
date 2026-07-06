from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_runtime_payload_sources.ps1"
DOCS = [
    ROOT / "docs" / "VideoNotesAI-Final-Architecture-v3.md",
]


def test_prepare_runtime_payload_sources_script_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = [
        "payload-source-map.json",
        "yt-dlp.exe",
        "whisper-bin-x64.zip",
        "whisper-cli.exe",
        "whisper.dll",
        "tesseract.exe",
        "tessdata",
        "ffmpeg.exe",
        "ffprobe.exe",
        "stage_runtime_payloads.py",
        "verify_runtime_payloads.py",
        "StagePayloads",
        "componentArgs",
        "-SkipInstall",
        "-WhisperCppDir",
        "-TesseractDir",
    ]
    for token in required:
        assert token in text


def test_runtime_payload_source_preparation_is_documented() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)

    assert "prepare_runtime_payload_sources.ps1" in combined
    assert "payload-source-map.json" in combined
    assert "stage_runtime_payloads.py" in combined


def test_transcription_dependencies_are_not_in_base_runtime() -> None:
    base = (ROOT / "requirements" / "base.txt").read_text(encoding="utf-8")
    sidecar = (ROOT / "requirements" / "sidecar.txt").read_text(encoding="utf-8")

    assert "faster-whisper" not in base
    assert "ctranslate2" not in base
    assert "faster-whisper" not in sidecar
    assert "ctranslate2" not in sidecar
    assert "yt-dlp" not in base
    assert "yt-dlp" not in sidecar
