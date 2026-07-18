#!/usr/bin/env python3
"""Create a deterministic, hygiene-checked source archive."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".build", "node_modules", "dist", "target", ".svelte-kit", "__pycache__"}


def zip_timestamp() -> tuple[int, int, int, int, int, int]:
    raw = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    if raw <= 0:
        return (1980, 1, 1, 0, 0, 0)
    value = datetime.fromtimestamp(raw, tz=timezone.utc)
    year = max(1980, min(2107, value.year))
    return (year, value.month, value.day, value.hour, value.minute, value.second)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Output ZIP path")
    args = parser.parse_args()

    subprocess.run([sys.executable, str(ROOT / "scripts/check_repository_hygiene.py")], check=True)
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output = args.output or ROOT / ".build" / "releases" / f"video-notes-ai-{version}-source.zip"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"video-notes-ai-{version}"
    timestamp = zip_timestamp()

    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached"], cwd=ROOT
    )
    files = [
        ROOT / raw.decode("utf-8")
        for raw in tracked.split(b"\0")
        if raw
        and (ROOT / raw.decode("utf-8")).is_file()
        and not any(part in EXCLUDED_PARTS for part in Path(raw.decode("utf-8")).parts)
        and (ROOT / raw.decode("utf-8")).resolve() != output
    ]

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(ROOT).as_posix()):
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{relative}", timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            executable = path.suffix == ".py" and path.parent.name == "scripts"
            mode = 0o755 if executable else 0o644
            info.external_attr = (mode & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"Created: {output}")
    print(f"Files: {len(files)}")
    print(f"SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
