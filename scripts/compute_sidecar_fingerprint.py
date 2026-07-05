from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def iter_inputs():
    for base in (ROOT / "src", ROOT / "templates"):
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            yield path
    for relative in (
        "requirements/sidecar.txt",
        "requirements/cuda.txt",
        "requirements/build.txt",
        "scripts/prepare_tauri_sidecar.ps1",
        "pyproject.toml",
    ):
        path = ROOT / relative
        if path.is_file():
            yield path


def main() -> None:
    digest = hashlib.sha256()
    for path in iter_inputs():
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\n")
    print(digest.hexdigest())


if __name__ == "__main__":
    main()
