#!/usr/bin/env python3
"""Generate structured conformance report from the v0.2 fixture manifest.

Runs the Rust conformance_runner test (via cargo test) and produces a
machine-readable JSON report at conformance/v0.2/latest-report.json.

Usage:
    python scripts/conformance_report.py
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "conformance" / "v0.2" / "fixture-manifest.json"
RUST_MANIFEST = REPO_ROOT / "desktop" / "src-tauri" / "Cargo.toml"
OUTPUT_PATH = REPO_ROOT / "conformance" / "v0.2" / "latest-report.json"


def load_manifest() -> dict:
    with open(MANIFEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def run_rust_test(out: Path) -> bool:
    """Run the conformance_runner test and capture output."""
    result = subprocess.run(
        [
            "cargo", "test",
            "--locked",
            "--features", "compiler_v3",
            "--manifest-path", str(RUST_MANIFEST),
            "--test", "conformance_runner",
            "--", "--nocapture",
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    out.write_text(result.stdout + result.stderr)
    return result.returncode == 0


def parse_output(text: str) -> dict:
    """Parse the conformance runner output to extract per-fixture results."""
    report = {
        "schema_version": "0.1",
        "runner": "conformance_runner",
        "valid": [],
        "invalid": [],
        "trust_edge_cases": [],
        "cross_language": [],
    }
    section = None
    for line in text.splitlines():
        # Detect section headers
        if "═══ Valid fixtures ═══" in line:
            section = "valid"
        elif "═══ Invalid fixtures ═══" in line:
            section = "invalid"
        elif "═══ Trust-policy edge cases ═══" in line:
            section = "trust"
        elif "═══ Cross-language: canonical bytes ═══" in line:
            section = "cross_canonical"
        elif "═══ Cross-language: signature payloads ═══" in line:
            section = "cross_sig"
        elif "═══════════════════════════════════════════" in line:
            continue

        # Parse result lines
        if line.startswith("  ✅ ") or line.startswith("  ❌ "):
            passed = "✅" in line
            detail = line[4:].strip()
            entry = {"passed": passed, "detail": detail}

            if section == "valid":
                parts = detail.split(" — ", 1)
                path = parts[0] if len(parts) > 1 else detail
                entry["path"] = path
                if len(parts) > 1:
                    entry["result"] = parts[1]
                report["valid"].append(entry)
            elif section == "invalid":
                parts = detail.split(" — ", 1)
                path = parts[0] if len(parts) > 1 else detail
                entry["path"] = path
                if len(parts) > 1:
                    entry["result"] = parts[1]
                report["invalid"].append(entry)
            elif section in ("trust",):
                report["trust_edge_cases"].append(entry)
            elif section in ("cross_canonical", "cross_sig"):
                report["cross_language"].append(entry)

    # Extract summary line
    for line in text.splitlines():
        if "Total:" in line and "Passed:" in line:
            report["summary"] = line.strip()
        if "  Result:" in line:
            if "ALL PASSED" in line:
                report["overall"] = "passed"
            elif "FAILURES" in line:
                report["overall"] = "failed"

    return report


def write_report(report: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"Report written to {OUTPUT_PATH}")


def main() -> int:
    print(f"Loading manifest from {MANIFEST_PATH}")
    manifest = load_manifest()
    print(f"  Valid fixtures: {len(manifest['valid'])}")
    print(f"  Invalid fixtures: {len(manifest['invalid'])}")

    print("\nRunning Rust conformance_runner...")
    log_path = OUTPUT_PATH.with_suffix(".log")
    passed = run_rust_test(log_path)

    print(f"Parsing output from {log_path}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    report = parse_output(text)

    write_report(report)

    if report.get("overall") == "passed":
        print("Conformance: ALL PASSED")
        return 0
    else:
        print("Conformance: SOME FAILURES")
        print(report.get("summary", ""))
        return 1


if __name__ == "__main__":
    sys.exit(main())
