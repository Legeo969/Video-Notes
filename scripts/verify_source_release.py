#!/usr/bin/env python3
"""Source-level release gates that do not require a Rust toolchain."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []

subprocess.run([sys.executable, str(ROOT / "scripts/check_repository_hygiene.py")], check=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def read(path: str) -> str:
    target = ROOT / path
    require(target.is_file(), f"missing required file: {path}")
    return target.read_text(encoding="utf-8") if target.is_file() else ""


release_version = read("VERSION").strip()
package = json.loads(read("desktop/package.json") or "{}")
package_version = str(package.get("version", ""))
cargo = read("desktop/src-tauri/Cargo.toml")
tauri = json.loads(read("desktop/src-tauri/tauri.conf.json") or "{}")
cargo_version = re.search(r'^version\s*=\s*"([^"]+)"', cargo, re.MULTILINE)
require(bool(cargo_version), "Cargo.toml package version missing")
require(package_version == release_version, f"VERSION/package version mismatch: {release_version} != {package_version}")
require(cargo_version and cargo_version.group(1) == package_version, "npm/Cargo versions differ")
require(str(tauri.get("version", "")) == package_version, "npm/Tauri versions differ")

cargo_lower = cargo.lower()
for banned in ("whisper", "tesseract", "leptonica", "speech-to-text"):
    require(banned not in cargo_lower, f"banned Cargo dependency present: {banned}")

manifests = sorted(path.name for path in (ROOT / "runtime/manifests").glob("*.json"))
require(
    manifests == ["download-tools.json", "ffmpeg-tools.json", "mpv-tools.json"],
    f"unexpected runtime manifests: {manifests}",
)

engine = read("desktop/src-tauri/src/compile/engine.rs")
sampler = read("desktop/src-tauri/src/compile/sampler.rs")
storage = read("desktop/src-tauri/src/compile/storage.rs")
client = read("desktop/src-tauri/src/compile/client.rs")
renderer = read("desktop/src-tauri/src/compile/renderer.rs")
native = read("desktop/src-tauri/src/native_engine/mod.rs")

checks = {
    "backend physical time anchors": "probe_media" in sampler and "frame_index_map" in sampler,
    "metadata-only audio discovery": "has_audio" in sampler and "extract_audio" not in sampler,
    "explicit provider video capability": "accepts_video" in client and "video_input" in native,
    "bounded JSON handling": "MAX_RAW_BYTES" in read("desktop/src-tauri/src/compile/repair.rs"),
    "immutable version reservation": "reserve_next_version" in storage and "create_new(true)" in storage,
    "Evidence version propagation": "evidence.version = version" in storage,
    "Capsule replay metadata": "video_notes_source_hash" in renderer and "compile.list_versions" in native,
    "safe checkpoint cancellation": "ProcessControl" in sampler and "on_process_started" in engine,
    "URL host allowlist": "ALLOWED" in native and "validate_public_media_url" in native,
    "deterministic Provider failures": "is_retryable_status" in client and "BAD_REQUEST" in client,
    "direct local playback": "notes_video_playback" in native and "mpv" in native,
}
for label, ok in checks.items():
    require(ok, f"missing release contract: {label}")

# Parse every Rust source/test/build script. These dependencies are pinned in
# requirements-dev.txt so CI must not silently skip the parser gate.
try:
    from tree_sitter import Language, Parser
    import tree_sitter_rust
except Exception as error:
    require(False, f"Rust syntax parser dependencies unavailable: {error}")
else:
    parser = Parser(Language(tree_sitter_rust.language()))
    rust_files = list((ROOT / "desktop/src-tauri/src").rglob("*.rs"))
    rust_files.extend((ROOT / "desktop/src-tauri/tests").rglob("*.rs"))
    rust_files.append(ROOT / "desktop/src-tauri/build.rs")
    for path in sorted(set(rust_files)):
        tree = parser.parse(path.read_bytes())
        require(not tree.root_node.has_error, f"Rust syntax parse error: {path.relative_to(ROOT)}")

if ERRORS:
    print("Source release verification FAILED:")
    for error in ERRORS:
        print(f"- {error}")
    sys.exit(1)

print("Source release verification passed")
print(f"- version: {package_version}")
print(f"- runtime manifests: {', '.join(manifests)}")
print(f"- verified contracts: {len(checks)}")
