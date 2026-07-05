"""Video processing pipeline public API (lazy imports)."""
from __future__ import annotations


def __getattr__(name: str):
    if name in {"process_url", "process_local"}:
        from . import video_pipeline
        return getattr(video_pipeline, name)
    if name in {"process_url_with_logging", "process_local_with_logging"}:
        from . import enhanced_pipeline
        attr = "process_url" if name.startswith("process_url") else "process_local"
        return getattr(enhanced_pipeline, attr)
    if name == "BatchJob":
        from .batch_pipeline import BatchJob
        return BatchJob
    raise AttributeError(name)


__all__ = ["process_url", "process_local", "process_url_with_logging", "process_local_with_logging", "BatchJob"]
