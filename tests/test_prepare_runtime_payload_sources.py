from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_runtime_payload_sources.ps1"
DOCS = [
    ROOT / "docs" / "Runtime-Component-Package.md",
    ROOT / "docs" / "Windows-Release-Acceptance.md",
]


def test_prepare_runtime_payload_sources_script_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    required = [
        "payload-source-map.json",
        "python3.dll",
        "python$($pythonInfo.version_nodot).dll",
        "Copy-PythonStdlib",
        "site-packages",
        "requirements\\sidecar.txt",
        "requirements\\cuda.txt",
        "requirements\\ocr-cpu.txt",
        "requirements\\ocr-gpu.txt",
        "ffmpeg.exe",
        "ffprobe.exe",
        "stage_runtime_payloads.py",
        "verify_runtime_payloads.py",
        "StagePayloads",
        "-SkipInstall",
    ]
    for token in required:
        assert token in text


def test_runtime_payload_source_preparation_is_documented() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)

    assert "prepare_runtime_payload_sources.ps1" in combined
    assert "payload-source-map.json" in combined
    assert "stage_runtime_payloads.py" in combined
