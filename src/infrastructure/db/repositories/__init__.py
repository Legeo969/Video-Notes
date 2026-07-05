"""Database repositories (lazy exports)."""
from __future__ import annotations


def __getattr__(name: str):
    mapping = {
        "NoteRepository": ("note_repository", "NoteRepository"),
        "JobRepository": ("job_repository", "JobRepository"),
        "ProvenanceRepository": ("provenance_repository", "ProvenanceRepository"),
        "CollectionRepository": ("collection_repository", "CollectionRepository"),
    }
    if name not in mapping:
        raise AttributeError(name)
    module, attr = mapping[name]
    return getattr(__import__(f"{__name__}.{module}", fromlist=[attr]), attr)


__all__ = ["NoteRepository", "JobRepository", "ProvenanceRepository", "CollectionRepository"]
