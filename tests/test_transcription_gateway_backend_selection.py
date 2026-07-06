from __future__ import annotations

from src.infrastructure.transcription.gateway import InfrastructureTranscriptionGateway


def test_gateway_routes_to_selected_backend(monkeypatch) -> None:
    calls = {}

    class FakeBackend:
        def transcribe(self, audio_path: str, **kwargs):
            calls["audio_path"] = audio_path
            calls["kwargs"] = kwargs
            return type(
                "Transcript",
                (),
                {
                    "text": "hello",
                    "segments": [],
                    "language": "en",
                },
            )()

    def fake_get_backend(name: str, **kwargs):
        calls["backend"] = name
        calls["init"] = kwargs
        return FakeBackend()

    monkeypatch.setattr(
        "src.infrastructure.transcription.gateway.get_backend",
        fake_get_backend,
    )

    text, segments = InfrastructureTranscriptionGateway().transcribe_with_segments(
        "audio.wav",
        backend="whisper_cpp",
        model_size="small",
        model_dir="D:/models",
        language="en",
    )

    assert calls["backend"] == "whisper_cpp"
    assert calls["init"]["model_size"] == "small"
    assert calls["kwargs"]["model_dir"] == "D:/models"
    assert text == "hello"
    assert segments == [{"start": 0.0, "end": 0.0, "text": "hello", "language": "en"}]
