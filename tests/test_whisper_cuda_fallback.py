from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.infrastructure.transcription import whisper_engine


class _Segment:
    start = 0.0
    end = 1.0
    text = "hello"


class _CudaFailingModel:
    def transcribe(self, *args, **kwargs):
        def segments():
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield _Segment()

        return segments(), SimpleNamespace(language="en")


class _CpuModel:
    def transcribe(self, *args, **kwargs):
        return iter([_Segment()]), SimpleNamespace(language="en")


def _patch_runtime(monkeypatch, calls: list[tuple[str, str]]) -> None:
    monkeypatch.setattr(whisper_engine, "_resolve_model", lambda *args, **kwargs: "model-path")
    monkeypatch.setattr(whisper_engine.ctranslate2, "get_cuda_device_count", lambda: 1)
    monkeypatch.setattr(
        whisper_engine.ctranslate2,
        "get_supported_compute_types",
        lambda device: {"float16"} if device == "cuda" else {"int8"},
    )

    def fake_get_cached_model(model_path: str, device: str, compute_type: str):
        calls.append((device, compute_type))
        return _CudaFailingModel() if device == "cuda" else _CpuModel()

    fake_get_cached_model.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(whisper_engine, "_get_cached_model", fake_get_cached_model)


def test_auto_cuda_runtime_error_during_segment_iteration_falls_back_to_cpu(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    _patch_runtime(monkeypatch, calls)

    text, segments = whisper_engine.transcribe_with_segments("audio.wav", device="auto", compute_type="auto")

    assert text == "hello"
    assert segments[0]["text"] == "hello"
    assert calls == [("cuda", "float16"), ("cpu", "int8")]


def test_explicit_cuda_runtime_error_does_not_fallback(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    _patch_runtime(monkeypatch, calls)

    with pytest.raises(RuntimeError, match="cublas64_12"):
        whisper_engine.transcribe_with_segments("audio.wav", device="cuda", compute_type="float16")

    assert calls == [("cuda", "float16")]
