from __future__ import annotations

from src.application.speech import SpeechTranscriber


def test_speech_transcriber_uses_injected_gateway() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.calls = []

        def transcribe_with_segments(self, audio_path, **kwargs):
            self.calls.append((audio_path, kwargs))
            return (
                "hello",
                [{"start": 0.0, "end": 1.0, "text": "hello", "language": "en"}],
            )

    gateway = FakeGateway()
    transcriber = SpeechTranscriber(
        model_size="base",
        model_dir="/models",
        device="cpu",
        compute_type="int8",
        gateway=gateway,
    )

    result = transcriber.transcribe("audio.wav", language="en", beam_size=3, vad_filter=True)

    assert result.full_text == "hello"
    assert result.language == "en"
    assert result.segments[0].text == "hello"
    assert gateway.calls == [
        (
            "audio.wav",
            {
                "model_size": "base",
                "language": "en",
                "model_dir": "/models",
                "beam_size": 3,
                "device": "cpu",
                "compute_type": "int8",
                "vad_filter": True,
            },
        )
    ]
