from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from src.infrastructure.transcription.whisper_cpp_backend import WhisperCppBackend


def test_whisper_cpp_backend_invokes_native_cli(tmp_path: Path, monkeypatch) -> None:
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    (model_dir / "ggml-small.bin").write_text("model", encoding="utf-8")
    audio = tmp_path / "audio.wav"
    audio.write_text("audio", encoding="utf-8")
    captured = {}

    monkeypatch.setattr(
        "src.infrastructure.transcription.whisper_cpp_backend.resolve_tool",
        lambda *args, **kwargs: "whisper-cli.exe",
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out_base = Path(cmd[cmd.index("-of") + 1])
        out_base.with_suffix(".txt").write_text("hello native", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "src.infrastructure.transcription.whisper_cpp_backend.subprocess.run",
        fake_run,
    )

    backend = WhisperCppBackend(model_size="small", model_dir=str(model_dir), n_threads=2)
    transcript = backend.transcribe(str(audio), language="en")

    assert transcript.text == "hello native"
    assert transcript.backend == "whisper_cpp"
    assert captured["cmd"][:5] == [
        "whisper-cli.exe",
        "-m",
        str(model_dir / "ggml-small.bin"),
        "-f",
        str(audio),
    ]
    assert "-otxt" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-l") + 1] == "en"
