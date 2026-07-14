#!/usr/bin/env python3
"""Batch process Unreal for VFX tutorial subtitles into the semantic quality corpus.

Scans /d/Tutorial/Unreal for VFX/ for SRT subtitle files, parses each into
segments, merges short gaps, and creates ground-truth annotation files plus
matching system-output bundles in the v0.1 quality corpus.

Usage:
    PYTHONIOENCODING=utf-8 python scripts/batch_process_unreal_videos.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Paths ----------------------------------------------------------------

TUTORIAL_ROOT = Path(r"D:\Tutorial\Unreal for VFX")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_DIR = PROJECT_ROOT / "conformance/quality/v0.1/annotations"
SYSTEM_OUTPUT_DIR = PROJECT_ROOT / "conformance/quality/v0.1/system-outputs"
MANIFEST_PATH = PROJECT_ROOT / "conformance/quality/v0.1/semantic-manifest.json"

ANNOTATION_DIR.mkdir(parents=True, exist_ok=True)
SYSTEM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NOW_ISO = "2026-07-15T00:00:00Z"


# --- SRT parsing ----------------------------------------------------------


def _parse_srt_timestamp(ts: str) -> int:
    """Convert SRT timestamp *HH:MM:SS,mmm* or *HH:MM:SS.mmm* to microseconds."""
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})", ts)
    if not m:
        raise ValueError(f"Invalid SRT timestamp: {ts!r}")
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return (h * 3600 + mi * 60 + s) * 1_000_000 + ms * 1000


def parse_srt(filepath: Path) -> list[dict]:
    """Parse an SRT file into a list of *{start_us, end_us, text}* entries."""
    raw = filepath.read_text(encoding="utf-8-sig")
    entries: list[dict] = []

    # SRT blocks are separated by one or more blank lines
    blocks = re.split(r"\n\s*\n", raw.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # Skip optional sequence-number line
        idx = 0
        if lines[0].strip().isdigit():
            idx = 1

        if idx >= len(lines):
            continue

        # Parse timestamp line
        tm = re.match(
            r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})",
            lines[idx],
        )
        if not tm:
            continue

        start_us = _parse_srt_timestamp(tm.group(1))
        end_us = _parse_srt_timestamp(tm.group(2))
        text = " ".join(lines[idx + 1 :]).strip()

        entries.append({"start_us": start_us, "end_us": end_us, "text": text})

    return entries


# --- Segment merging ------------------------------------------------------


def merge_segments(entries: list[dict], max_gap_us: int = 3_000_000) -> list[dict]:
    """Merge entries whose inter-segment gap is ≤ *max_gap_us*."""
    if not entries:
        return []

    merged: list[dict] = []
    cur = dict(entries[0])  # copy

    for entry in entries[1:]:
        gap = entry["start_us"] - cur["end_us"]
        if 0 <= gap <= max_gap_us:
            # Extend the current segment
            cur["end_us"] = entry["end_us"]
            cur["text"] += " " + entry["text"]
        else:
            merged.append(cur)
            cur = dict(entry)

    merged.append(cur)
    return merged


# --- Slug generation ------------------------------------------------------


def compute_slug(filename_stem: str) -> str:
    """Derive a URL-safe slug from a video filename stem.

    Strips the leading number prefix, lowercases, and replaces non-alphanumeric
    runs with a single hyphen.

    Examples
    --------
    "1 Welcome to Unreal Fundamentals"  → "welcome-to-unreal-fundamentals"
    "01 Intro - The Dark Knight Environment" → "intro-the-dark-knight-environment"
    """
    # Strip leading digits and separators (e.g. "1 ", "01 ", "01 - ", "08 ")
    stem = re.sub(r"^\d+\s*[-.]?\s*", "", filename_stem, count=1).strip()
    slug = stem.lower()
    # Remove characters that are not letters, digits, spaces or hyphens
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug.strip("-")


def _slug_for_srt(srt_path: Path) -> str:
    return compute_slug(srt_path.stem)


# --- Output construction --------------------------------------------------


def build_annotation(
    media_path: str, segments: list[dict]
) -> dict:
    """Build the ground-truth annotation dict for a single video."""
    items = []
    for i, seg in enumerate(segments, 1):
        items.append(
            {
                "item_id": f"gt_ev_{i:03d}",
                "kind": "quote",
                "type": "concept_explanation",
                "text": seg["text"],
                "modality": "audio",
                "anchor": {
                    "start_us": seg["start_us"],
                    "end_us": seg["end_us"],
                    "confidence": 1.0,
                },
                "speaker": "instructor",
            }
        )

    return {
        "annotation_version": "0.1.0",
        "media_path": media_path,
        "annotator": "subtitle-batch",
        "annotation_date": NOW_ISO,
        "items": items,
        "claims": [],
        "gaps": [],
        "conflicts": [],
    }


def build_system_output(media_path: str, slug: str, segments: list[dict]) -> dict:
    """Build the system-output bundle (legacy v2.1 format) for a single video."""
    total_duration = segments[-1]["end_us"] / 1_000_000 if segments else 0.0
    source_title = Path(media_path).with_suffix("").as_posix()

    evidences = []
    for i, seg in enumerate(segments, 1):
        evidences.append(
            {
                "id": f"ev_{i:03d}",
                "content": seg["text"],
                "timestamp_start_sec": seg["start_us"] / 1_000_000,
                "timestamp_end_sec": seg["end_us"] / 1_000_000,
                "evidence_type": "concept",
                "speaker": "instructor",
                "confidence": 1.0,
            }
        )

    return {
        "ir_schema_version": 2,
        "capsule_id": f"{slug}_capsule",
        "source_hash": f"{slug}_hash",
        "source_title": source_title,
        "version": 1,
        "total_duration": total_duration,
        "processed_at": NOW_ISO,
        "model_used": "subtitle-batch",
        "evidences": evidences,
    }


# --- Per-file processing --------------------------------------------------


def process_one_srt(srt_path: Path) -> tuple[str, str, list[dict]] | None:
    """Process a single SRT file.

    Returns *(media_path, slug, segments)* or *None* if the file produces no
    valid evidence items.
    """
    try:
        rel = srt_path.relative_to(TUTORIAL_ROOT)
    except ValueError:
        return None

    # The media file lives alongside the SRT with a .mp4 extension
    media_path = str(rel.parent / (rel.stem + ".mp4"))

    entries = parse_srt(srt_path)
    if not entries:
        return None

    # Filter out individual SRT entries that are too short
    entries = [
        e
        for e in entries
        if (e["end_us"] - e["start_us"]) >= 500_000 and len(e["text"]) >= 15
    ]
    if not entries:
        return None

    segments = merge_segments(entries)
    slug = _slug_for_srt(srt_path)

    return media_path, slug, segments


# --- Main -----------------------------------------------------------------


def main() -> int:
    srt_files = sorted(TUTORIAL_ROOT.rglob("*.srt"))
    print(f"SRT files found: {len(srt_files)}")
    print()

    created = []  # list of (slug, item_count)
    skipped: list[tuple[str, str]] = []
    seen_slugs: set[str] = set()

    for srt_path in srt_files:
        result = process_one_srt(srt_path)
        if result is None:
            skipped.append((srt_path.name, "no valid segments"))
            continue

        media_path, slug, segments = result

        # Guard against accidental slug collisions
        if slug in seen_slugs:
            # Append a suffix so files don't overwrite each other
            parent_name = srt_path.parent.name
            suffix = re.sub(r"[^a-z0-9]", "-", parent_name.lower()).strip("-")
            slug = f"{slug}-from-{suffix}"
        seen_slugs.add(slug)

        basename = f"{slug}.json"

        # --- Write annotation ---
        annotation = build_annotation(media_path, segments)
        (ANNOTATION_DIR / basename).write_text(
            json.dumps(annotation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # --- Write system output ---
        sys_out = build_system_output(media_path, slug, segments)
        (SYSTEM_OUTPUT_DIR / basename).write_text(
            json.dumps(sys_out, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        created.append((basename, len(segments)))
        print(f"  [{srt_path.parent.name}] {basename} ({len(segments)} items)")

    # --- Summary ---
    print()
    print(f"Annotations created:    {len(created)}")
    for name, n in created:
        print(f"  - {name} ({n} items)")

    if skipped:
        print(f"\nSkipped: {len(skipped)}")
        for name, reason in skipped:
            print(f"  - {name}: {reason}")

    # --- Update manifest ---
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    else:
        manifest = {
            "benchmark_version": "0.1.0",
            "kind": "semantic-evaluation-manifest",
            "cases": [],
            "semantic_minimums": {"evidence_precision": 0.5, "evidence_recall": 0.5},
        }

    existing_cases = set(manifest.get("cases", []))
    new_cases: list[str] = []
    for name, _ in created:
        case_path = f"conformance/quality/v0.1/system-outputs/{name}"
        if case_path not in existing_cases:
            new_cases.append(case_path)
            existing_cases.add(case_path)

    manifest["cases"] = manifest.get("cases", []) + new_cases
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\nManifest updated: {len(new_cases)} new case(s) added "
          f"(total: {len(manifest['cases'])})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
