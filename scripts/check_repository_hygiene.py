#!/usr/bin/env python3
"""Fail when a source release contains generated output, unsafe placeholders, or broken metadata."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []

REQUIRED_FILES = {
    "VERSION",
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "CODE_OF_CONDUCT.md",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "desktop/package-lock.json",
    "desktop/src-tauri/Cargo.lock",
}
BANNED_DIR_NAMES = {
    "node_modules",
    "dist",
    "target",
    ".svelte-kit",
    "__pycache__",
    ".pytest_cache",
}
BANNED_SUFFIXES = {".log", ".tmp", ".bak", ".pyc", ".pyo"}
TEXT_SUFFIXES = {
    ".md", ".txt", ".toml", ".json", ".yml", ".yaml", ".rs", ".ts",
    ".js", ".svelte", ".css", ".html", ".py", ".ps1", ".nsh", ".gitignore",
    ".gitattributes", ".editorconfig", ".nvmrc",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "GitHub token": re.compile(r"\bgh[opusr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def fail(message: str) -> None:
    ERRORS.append(message)


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def repository_files() -> list[Path]:
    """Return the source files Git would place in a release commit.

    Local dependency trees and compiler output must not make a clean source
    checkout fail hygiene. In an exported tree without `.git`, fall back to
    scanning the export while excluding the same generated roots.
    """
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            path
            for path in ROOT.rglob("*")
            if path.is_file()
            and ".git" not in path.relative_to(ROOT).parts
            and not any(part in BANNED_DIR_NAMES for part in path.relative_to(ROOT).parts)
        )
    return sorted(
        ROOT / raw.decode("utf-8")
        for raw in output.split(b"\0")
        if raw
    )


for item in sorted(REQUIRED_FILES):
    if not (ROOT / item).is_file():
        fail(f"missing required file: {item}")

version = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").is_file() else ""
if not SEMVER_RE.fullmatch(version):
    fail(f"VERSION is not semantic version: {version!r}")

package_path = ROOT / "desktop/package.json"
package = json.loads(package_path.read_text(encoding="utf-8")) if package_path.is_file() else {}
if package.get("version") != version:
    fail("VERSION and desktop/package.json differ")

cargo_path = ROOT / "desktop/src-tauri/Cargo.toml"
cargo = cargo_path.read_text(encoding="utf-8") if cargo_path.is_file() else ""
cargo_version = re.search(r'^version\s*=\s*"([^"]+)"', cargo, re.MULTILINE)
if not cargo_version or cargo_version.group(1) != version:
    fail("VERSION and desktop/src-tauri/Cargo.toml differ")
if "github.com/example" in cargo:
    fail("Cargo.toml contains a placeholder repository URL")

tauri_path = ROOT / "desktop/src-tauri/tauri.conf.json"
if tauri_path.is_file():
    tauri = json.loads(tauri_path.read_text(encoding="utf-8"))
    if tauri.get("version") != version:
        fail("VERSION and tauri.conf.json differ")


source_files = repository_files()
casefold_paths: dict[str, str] = {}
for path in source_files:
    relative = rel(path)
    folded = relative.casefold()
    previous = casefold_paths.get(folded)
    if previous is not None and previous != relative:
        fail(f"case-insensitive path collision: {previous} <-> {relative}")
    else:
        casefold_paths[folded] = relative
    if not path.exists() and not path.is_symlink():
        fail(f"tracked source file is missing: {relative}")
        continue
    if path.is_symlink():
        fail(f"symbolic link is not allowed in source release: {relative}")
    if len(relative) > 220:
        fail(f"path is too long for a portable Windows source tree: {relative}")

reported_banned_dirs: set[str] = set()
for path in source_files:
    relative = rel(path)
    banned_dir = next((part for part in Path(relative).parts if part in BANNED_DIR_NAMES), None)
    if banned_dir is not None:
        prefix_parts = Path(relative).parts[: Path(relative).parts.index(banned_dir) + 1]
        prefix = Path(*prefix_parts).as_posix()
        if prefix not in reported_banned_dirs:
            fail(f"generated/cache directory committed: {prefix}/")
            reported_banned_dirs.add(prefix)
        continue
    if not path.is_file():
        continue
    if path.suffix.lower() in BANNED_SUFFIXES:
        fail(f"temporary/generated file committed: {relative}")
    if path.stat().st_size > 5 * 1024 * 1024:
        fail(f"unexpected source file larger than 5 MiB: {relative}")

    is_text = path.suffix.lower() in TEXT_SUFFIXES or path.name in {
        "VERSION", "LICENSE", "CHANGELOG", "README", "Cargo.lock", "package-lock.json"
    }
    if not is_text:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail(f"text file is not UTF-8: {relative}")
        continue
    if text and not text.endswith("\n"):
        fail(f"text file lacks final newline: {relative}")
    if "github.com/" + "example/video-notes-ai" in text:
        fail(f"placeholder repository URL: {relative}")
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            fail(f"possible {label} committed in {relative}")

    if path.suffix.lower() == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            fail(f"invalid JSON: {relative}: {exc}")
    elif path.suffix.lower() == ".toml" or path.name == "Cargo.lock":
        try:
            tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            fail(f"invalid TOML: {relative}: {exc}")

    if path.suffix.lower() == ".md":
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split()[0].strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                fail(f"Markdown link escapes repository: {relative} -> {target}")
                continue
            if not resolved.exists():
                fail(f"broken local Markdown link: {relative} -> {target}")

if (ROOT / "PRODUCT-COMPLETION-REPORT.md").exists():
    fail("release report belongs under docs/releases, not repository root")
if (ROOT / ".ignore").exists():
    fail("obsolete .ignore file is present")
if (ROOT / ".env").exists():
    fail("local .env file is present")
if ERRORS:
    print("Repository hygiene FAILED:")
    for error in sorted(set(ERRORS)):
        print(f"- {error}")
    sys.exit(1)

print("Repository hygiene passed")
print(f"- version: {version}")
print(f"- tracked files checked: {len(source_files)}")
print("- governance: specification and task based")
