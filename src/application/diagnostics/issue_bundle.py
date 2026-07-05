"""Issue / crash report bundle generator.

Collects diagnostic information into a timestamped zip for user feedback:
  - App version & platform info
  - Diagnostics report
  - Recent logs (sanitized)
  - Settings (sanitized, no API keys)
  - Recent job metadata
  - Build/test info (if available)

Usage:
    from src.application.diagnostics.issue_bundle import generate_issue_bundle

    bundle_path = generate_issue_bundle(output_dir="./output")
    print(f"Issue bundle created: {bundle_path}")
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import platform
import re
import sys
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Sanitization patterns ────────────────────────────────────────────

_SENSITIVE_KEYS = re.compile(
    r'(api[_-]?key|secret|password|token|authorization|auth)',
    re.IGNORECASE,
)

_SENSITIVE_PATTERNS = [
    # base64-encoded API keys (usually 40+ chars of base64)
    (re.compile(rb'[A-Za-z0-9+/=]{40,}'), b'[REDACTED]'),
    # Bearer tokens
    (re.compile(rb'Bearer\s+[A-Za-z0-9\-._~+/]+=*'), b'Bearer [REDACTED]'),
    # API keys in env-var style
    (re.compile(rb'[A-Z_]+API[_-]?KEY\s*=\s*[^\s]+'), b'API_KEY=[REDACTED]'),
    (re.compile(rb'[A-Z_]+SECRET\s*=\s*[^\s]+'), b'SECRET=[REDACTED]'),
    (re.compile(rb'[A-Z_]+TOKEN\s*=\s*[^\s]+'), b'TOKEN=[REDACTED]'),
    (re.compile(rb'[A-Z_]+PASSWORD\s*=\s*[^\s]+'), b'PASSWORD=[REDACTED]'),
    # Sk-prefixed keys
    (re.compile(rb'sk-[A-Za-z0-9]{32,}'), b'sk-[REDACTED]'),
    (re.compile(rb'sk-[A-Za-z0-9\-]{32,}'), b'sk-[REDACTED]'),
]


def sanitize_value(key: str, value: str) -> str:
    """Sanitize a settings value based on its key name."""
    if not isinstance(value, str):
        return value
    if _SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    # Also check if the value itself looks like a base64-encoded API key
    if len(value) > 40 and not value.startswith("/") and not value.startswith("."):
        try:
            decoded = base64.b64decode(value)
            if len(decoded) > 20:
                # Could be an encoded key
                return "[REDACTED]"
        except Exception:
            pass
    return value


def sanitize_settings(settings: dict) -> dict:
    """Return a copy of settings with sensitive values redacted."""
    safe: dict = {}
    for key, value in settings.items():
        if isinstance(value, dict):
            safe[key] = sanitize_settings(value)
        elif isinstance(value, str):
            safe[key] = sanitize_value(key, value)
        else:
            safe[key] = value
    return safe


def sanitize_text(text: str) -> str:
    """Sanitize a text block by removing API keys and tokens."""
    data = text.encode("utf-8", errors="replace")
    for pattern, replacement in _SENSITIVE_PATTERNS:
        data = pattern.sub(replacement, data)
    return data.decode("utf-8", errors="replace")


# ── Info collectors ──────────────────────────────────────────────────

def collect_version_info() -> dict:
    """Collect app version and platform information."""
    info = {
        "app_name": "video-notes-ai",
        "platform": platform.platform(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "machine": platform.machine(),
        "processor": platform.processor(),
    }

    try:
        from src.utils.app_version import get_version_info

        info.update(get_version_info())
    except Exception:
        info["app_version"] = "unknown"
        info["runtime_version"] = "unknown"

    return info


def collect_diagnostics_report() -> str:
    """Run environment diagnostics and return the text report."""
    try:
        from src.application.diagnostics import run_diagnostics
        report = run_diagnostics()
        return report.to_text()
    except Exception as exc:
        return f"Diagnostics failed: {exc}"


def collect_settings_sanitized() -> dict | None:
    """Collect sanitized settings."""
    try:
        from src.config.settings import load_settings, get_settings_path
        raw = load_settings(get_settings_path())
        if not raw:
            return None
        return sanitize_settings(raw)
    except Exception as exc:
        return {"error": str(exc)}


def collect_recent_logs(output_dir: str, max_lines: int = 500) -> str | None:
    """Collect recent log lines from the output directory."""
    log_dir = Path(output_dir) / "logs"
    if not log_dir.is_dir():
        return None

    log_files = sorted(log_dir.glob("*.log"), key=os.path.getmtime, reverse=True)
    if not log_files:
        return None

    lines: list[str] = []
    for lf in log_files[:3]:  # At most 3 recent log files
        try:
            content = lf.read_text(encoding="utf-8", errors="replace")
            file_lines = content.splitlines()
            # Take last N lines
            recent = file_lines[-max_lines:] if len(file_lines) > max_lines else file_lines
            lines.append(f"--- {lf.name} (last {len(recent)} lines) ---")
            lines.extend(recent)
        except Exception:
            pass

    if not lines:
        return None

    return sanitize_text("\n".join(lines))


def collect_recent_jobs(output_dir: str, limit: int = 20) -> list[dict] | None:
    """Collect metadata for recent jobs (excluding full content)."""
    try:
        from src.application.services.job_queue import JobQueue, get_default_db_path
        db_path = get_default_db_path(output_dir)
        if not os.path.isfile(db_path):
            return None

        queue = JobQueue(db_path, output_dir=output_dir)
        jobs = queue.list_jobs(limit=limit)
        if not jobs:
            return None

        result: list[dict] = []
        for j in jobs:
            result.append({
                "id": j.id,
                "job_id": j.job_id[:20] if j.job_id else "",
                "status": j.status,
                "stage": j.stage or "",
                "input": (j.input or "")[:100],
                "title": (j.title or "")[:100],
                "started_at": j.started_at or "",
                "completed_at": j.completed_at or "",
                "has_output": bool(j.output_path),
                "has_error": bool(j.error_message),
            })
        return result
    except Exception as exc:
        return [{"error": str(exc)}]


def collect_build_info() -> dict | None:
    """Collect build/test info if available."""
    info: dict = {}

    # Check for build marker files
    dist_dir = Path(__file__).resolve().parent.parent.parent.parent / "dist"
    if dist_dir.exists():
        builds = list(dist_dir.glob("video-notes-ai-*"))
        if builds:
            info["dist_builds"] = [b.name for b in builds[:5]]

    # Check for test baseline
    baseline = Path(__file__).resolve().parent.parent.parent.parent / "tests" / "BASELINE.md"
    if baseline.exists():
        info["baseline_exists"] = True

    # Check for .git info
    git_dir = Path(__file__).resolve().parent.parent.parent.parent / ".git"
    if git_dir.exists():
        info["git_repo"] = True

    return info if info else None


# ── Main bundle generator ────────────────────────────────────────────

def generate_issue_bundle(
    output_dir: str = "./output",
    bundle_dir: str | None = None,
) -> str:
    """Generate an issue bundle zip file.

    Args:
        output_dir: The app's output directory (for logs, DB, etc.)
        bundle_dir: Where to write the bundle zip. Defaults to output_dir.

    Returns:
        Path to the generated zip file.
    """
    if bundle_dir is None:
        bundle_dir = output_dir

    # Timestamped filename
    now = datetime.datetime.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    bundle_name = f"issue_bundle_{ts}.zip"
    bundle_path = os.path.join(bundle_dir, bundle_name)

    # Ensure output dir exists
    os.makedirs(bundle_dir, exist_ok=True)

    components: list[tuple[str, str]] = []

    # 1. Version info
    try:
        ver = collect_version_info()
        components.append(("version.json", json.dumps(ver, indent=2, ensure_ascii=False)))
    except Exception as exc:
        components.append(("version_error.txt", str(exc)))

    # 2. Diagnostics report
    try:
        diag_text = collect_diagnostics_report()
        components.append(("diagnostics.txt", diag_text))
    except Exception as exc:
        components.append(("diagnostics_error.txt", str(exc)))

    # 3. Sanitized settings
    try:
        settings = collect_settings_sanitized()
        if settings is not None:
            components.append(
                ("settings.json",
                 json.dumps(settings, indent=2, ensure_ascii=False)),
            )
    except Exception as exc:
        components.append(("settings_error.txt", str(exc)))

    # 4. Recent logs
    try:
        logs = collect_recent_logs(output_dir)
        if logs is not None:
            components.append(("recent_logs.txt", logs))
    except Exception as exc:
        components.append(("logs_error.txt", str(exc)))

    # 5. Recent job metadata
    try:
        jobs = collect_recent_jobs(output_dir)
        if jobs is not None:
            components.append(
                ("recent_jobs.json",
                 json.dumps(jobs, indent=2, ensure_ascii=False)),
            )
    except Exception as exc:
        components.append(("jobs_error.txt", str(exc)))

    # 6. Build info
    try:
        build_info = collect_build_info()
        if build_info is not None:
            components.append(
                ("build_info.json",
                 json.dumps(build_info, indent=2, ensure_ascii=False)),
            )
    except Exception as exc:
        components.append(("build_error.txt", str(exc)))

    # 7. Bundle manifest
    manifest = {
        "generated_at": now.isoformat(),
        "bundle_name": bundle_name,
        "files": [name for name, _ in components],
    }
    components.append(("bundle_manifest.json", json.dumps(manifest, indent=2)))

    # Write zip
    with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in components:
            zf.writestr(name, content)

    logger.info("Issue bundle created: %s (%d files)", bundle_path, len(components))
    return bundle_path
