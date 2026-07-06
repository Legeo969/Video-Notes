"""Verify a release-candidate directory against RELEASE-MANIFEST.json."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CandidateIssue:
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class CandidateVerification:
    ok: bool
    candidate_dir: str
    checked: int
    errors: list[CandidateIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "candidate_dir": self.candidate_dir,
            "checked": self.checked,
            "errors": [asdict(issue) for issue in self.errors],
        }


def verify_release_candidate(candidate_dir: str | Path) -> CandidateVerification:
    root = Path(candidate_dir).expanduser().resolve()
    manifest_path = root / "RELEASE-MANIFEST.json"
    if not manifest_path.is_file():
        return CandidateVerification(
            False,
            str(root),
            0,
            [CandidateIssue("manifest_missing", "RELEASE-MANIFEST.json is missing", str(manifest_path))],
        )

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CandidateVerification(
            False,
            str(root),
            0,
            [CandidateIssue("manifest_invalid", f"cannot parse release manifest: {exc}", str(manifest_path))],
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return CandidateVerification(
            False,
            str(root),
            0,
            [CandidateIssue("artifacts_missing", "release manifest has no artifacts", str(manifest_path))],
        )

    errors: list[CandidateIssue] = []
    checked = 0
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append(CandidateIssue("artifact_invalid", "artifact entry is not an object"))
            continue
        raw_path = item.get("path")
        expected_size = item.get("bytes")
        expected_hash = item.get("sha256")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(CandidateIssue("artifact_path_missing", "artifact has no path"))
            continue
        artifact_path = _safe_artifact_path(root, raw_path)
        if artifact_path is None:
            errors.append(CandidateIssue("artifact_path_unsafe", "artifact path is unsafe", raw_path))
            continue
        if not artifact_path.is_file():
            errors.append(CandidateIssue("artifact_missing", "artifact file is missing", str(artifact_path)))
            continue
        checked += 1
        actual_size = artifact_path.stat().st_size
        if actual_size != expected_size:
            errors.append(CandidateIssue(
                "artifact_size_mismatch",
                f"expected {expected_size} bytes, found {actual_size}",
                str(artifact_path),
            ))
        actual_hash = _sha256(artifact_path)
        if actual_hash != expected_hash:
            errors.append(CandidateIssue(
                "artifact_hash_mismatch",
                f"expected {expected_hash}, found {actual_hash}",
                str(artifact_path),
            ))

    return CandidateVerification(not errors, str(root), checked, errors)


def _safe_artifact_path(root: Path, raw: str) -> Path | None:
    relative = Path(raw.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        return None
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_human(report: CandidateVerification) -> str:
    lines = [
        "Release candidate: " + ("OK" if report.ok else "FAILED"),
        f"Candidate: {report.candidate_dir}",
        f"Checked artifacts: {report.checked}",
    ]
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for issue in report.errors:
            suffix = f" ({issue.path})" if issue.path else ""
            lines.append(f"- [{issue.code}] {issue.message}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_release_candidate(args.candidate_dir)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
