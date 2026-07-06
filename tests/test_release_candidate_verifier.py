from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_release_candidate.py"


spec = importlib.util.spec_from_file_location("verify_release_candidate", SCRIPT)
assert spec is not None
candidate_verifier = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = candidate_verifier
spec.loader.exec_module(candidate_verifier)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(root: Path, path: str, artifact: Path) -> None:
    _write(
        root / "RELEASE-MANIFEST.json",
        json.dumps({
            "ok": True,
            "artifacts": [
                {
                    "name": artifact.name,
                    "path": path,
                    "bytes": artifact.stat().st_size,
                    "sha256": _sha256(artifact),
                }
            ],
        }),
    )


def test_verify_release_candidate_accepts_matching_hashes(tmp_path: Path) -> None:
    artifact = tmp_path / "components" / "base-engine.zip"
    _write(artifact, "payload")
    _write_manifest(tmp_path, "components/base-engine.zip", artifact)

    report = candidate_verifier.verify_release_candidate(tmp_path)

    assert report.ok, report.to_dict()
    assert report.checked == 1


def test_verify_release_candidate_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "components" / "base-engine.zip"
    _write(artifact, "payload")
    _write_manifest(tmp_path, "components/base-engine.zip", artifact)
    _write(artifact, "changed payload")

    report = candidate_verifier.verify_release_candidate(tmp_path)

    assert not report.ok
    assert {issue.code for issue in report.errors} == {
        "artifact_size_mismatch",
        "artifact_hash_mismatch",
    }


def test_verify_release_candidate_rejects_unsafe_paths(tmp_path: Path) -> None:
    _write(
        tmp_path / "RELEASE-MANIFEST.json",
        json.dumps({
            "ok": True,
            "artifacts": [
                {
                    "name": "bad",
                    "path": "../bad",
                    "bytes": 0,
                    "sha256": "",
                }
            ],
        }),
    )

    report = candidate_verifier.verify_release_candidate(tmp_path)

    assert not report.ok
    assert {issue.code for issue in report.errors} == {"artifact_path_unsafe"}


def test_verify_release_candidate_cli_json(tmp_path: Path, capsys) -> None:
    artifact = tmp_path / "installer" / "setup.exe"
    _write(artifact, "installer")
    _write_manifest(tmp_path, "installer/setup.exe", artifact)

    exit_code = candidate_verifier.main([str(tmp_path), "--json"])
    captured = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["ok"] is True
    assert captured["checked"] == 1
