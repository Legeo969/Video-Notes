"""Verify an installed/unpacked Windows runtime can start offline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeIssue:
    code: str
    message: str
    path: str = ""


@dataclass(frozen=True)
class InstalledRuntimeReport:
    ok: bool
    sidecar: str
    app_exe: str
    installer: str
    errors: list[RuntimeIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "sidecar": self.sidecar,
            "app_exe": self.app_exe,
            "installer": self.installer,
            "errors": [asdict(issue) for issue in self.errors],
        }


def verify_installed_runtime(
    *,
    app_dir: str | Path | None = None,
    app_exe: str | Path | None = None,
    installer: str | Path | None = None,
    sidecar: str | Path | None = None,
    sidecar_command: list[str] | None = None,
    timeout: float = 15.0,
    run_sidecar_ping: bool = True,
) -> InstalledRuntimeReport:
    """Verify installed release artifacts and the bundled sidecar smoke path."""
    errors: list[RuntimeIssue] = []
    resolved_app_dir = Path(app_dir).expanduser().resolve() if app_dir else None
    resolved_app_exe = _resolve_optional_file(app_exe, errors, "app_exe_missing")
    resolved_installer = _resolve_optional_file(installer, errors, "installer_missing")
    resolved_sidecar = _resolve_optional_file(sidecar, errors, "sidecar_missing")

    if resolved_installer and resolved_installer.suffix.lower() not in {".exe", ".msi"}:
        errors.append(RuntimeIssue(
            "installer_extension",
            "installer must be an .exe or .msi artifact",
            str(resolved_installer),
        ))

    if resolved_app_dir:
        if not resolved_app_dir.is_dir():
            errors.append(RuntimeIssue("app_dir_missing", "app directory is missing", str(resolved_app_dir)))
        else:
            if resolved_app_exe is None:
                resolved_app_exe = _find_app_exe(resolved_app_dir)
                if resolved_app_exe is None:
                    errors.append(RuntimeIssue(
                        "app_exe_missing",
                        "could not locate app executable under app directory",
                        str(resolved_app_dir),
                    ))
            if resolved_sidecar is None and sidecar_command is None:
                resolved_sidecar = _find_sidecar(resolved_app_dir)
                if resolved_sidecar is None:
                    errors.append(RuntimeIssue(
                        "sidecar_missing",
                        "could not locate bundled python-engine sidecar",
                        str(resolved_app_dir),
                    ))

    command = sidecar_command
    if command is None and resolved_sidecar is not None:
        command = [str(resolved_sidecar)]

    if run_sidecar_ping:
        if command is None:
            errors.append(RuntimeIssue("sidecar_missing", "sidecar command is required for offline smoke"))
        else:
            ping_error = _ping_sidecar(command, timeout=timeout)
            if ping_error is not None:
                errors.append(ping_error)

    return InstalledRuntimeReport(
        ok=not errors,
        sidecar=str(resolved_sidecar or ""),
        app_exe=str(resolved_app_exe or ""),
        installer=str(resolved_installer or ""),
        errors=errors,
    )


def _resolve_optional_file(
    value: str | Path | None,
    errors: list[RuntimeIssue],
    code: str,
) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        errors.append(RuntimeIssue(code, "file is missing", str(path)))
        return None
    return path


def _find_app_exe(app_dir: Path) -> Path | None:
    candidates = [
        path for path in app_dir.glob("*.exe")
        if not path.name.lower().startswith("python-engine")
    ]
    exact = [path for path in candidates if path.stem.lower() == "video notes ai"]
    if exact:
        return exact[0]
    return sorted(candidates)[0] if candidates else None


def _find_sidecar(app_dir: Path) -> Path | None:
    names = [
        "python-engine.exe",
        "binaries/python-engine.exe",
        "resources/binaries/python-engine.exe",
    ]
    for name in names:
        candidate = app_dir / name
        if candidate.is_file():
            return candidate
    matches = sorted(app_dir.rglob("python-engine*.exe"))
    return matches[0] if matches else None


def _ping_sidecar(command: list[str], *, timeout: float) -> RuntimeIssue | None:
    request = {
        "jsonrpc": "2.0",
        "protocol_version": 1,
        "id": 1,
        "method": "system.ping",
        "params": {},
    }
    body = json.dumps(request).encode("utf-8")
    frame = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body
    full_command = list(command) + ["--stdio"]

    with tempfile.TemporaryDirectory(prefix="video-notes-runtime-smoke-") as cwd:
        state_dir = Path(cwd) / "state"
        try:
            completed = subprocess.run(
                full_command,
                input=frame,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=_offline_env(state_dir=state_dir),
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            return RuntimeIssue("sidecar_spawn_failed", str(exc), command[0])
        except subprocess.TimeoutExpired:
            return RuntimeIssue("sidecar_timeout", f"sidecar did not respond within {timeout} seconds", command[0])

    if completed.returncode != 0:
        return RuntimeIssue(
            "sidecar_exit_failed",
            completed.stderr.decode("utf-8", errors="replace")[-1000:],
            command[0],
        )

    try:
        frames = _parse_frames(completed.stdout)
    except ValueError as exc:
        return RuntimeIssue("sidecar_protocol_error", str(exc), command[0])

    for message in frames:
        if message.get("id") == 1 and message.get("result") == "pong":
            return None
    return RuntimeIssue("sidecar_ping_failed", "system.ping response was not received", command[0])


def _offline_env(*, state_dir: Path | None = None) -> dict[str, str]:
    keep = {
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "USERPROFILE",
        "USERNAME",
        "COMSPEC",
    }
    env = {key.upper(): value for key, value in os.environ.items() if key.upper() in keep}
    system_root = env.get("SYSTEMROOT") or env.get("WINDIR")
    if system_root:
        env["PATH"] = str(Path(system_root) / "System32")
    else:
        env["PATH"] = ""
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "VIDEO_NOTES_ENGINE",
        "VIDEO_NOTES_ENGINE_CWD",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        env.pop(key, None)
    if state_dir is not None:
        env["VIDEO_NOTES_DATA_DIR"] = str(state_dir)
        env["VIDEO_NOTES_JOBS_DIR"] = str(state_dir / "jobs")
        env["VIDEO_NOTES_SETTINGS_PATH"] = str(state_dir / "settings.json")
    return env


def _parse_frames(data: bytes) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    offset = 0
    while offset < len(data):
        header_end = data.find(b"\r\n\r\n", offset)
        if header_end < 0:
            trailing = data[offset:].strip()
            if trailing:
                raise ValueError("stdout contains non-framed trailing data")
            break
        header = data[offset:header_end].decode("utf-8", errors="replace")
        content_length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())
                break
        if content_length is None:
            raise ValueError("missing Content-Length header")
        body_start = header_end + 4
        body_end = body_start + content_length
        if body_end > len(data):
            raise ValueError("incomplete frame body")
        frames.append(json.loads(data[body_start:body_end].decode("utf-8")))
        offset = body_end
    return frames


def _format_human(report: InstalledRuntimeReport) -> str:
    lines = ["Installed runtime: " + ("OK" if report.ok else "FAILED")]
    if report.app_exe:
        lines.append(f"App: {report.app_exe}")
    if report.sidecar:
        lines.append(f"Sidecar: {report.sidecar}")
    if report.installer:
        lines.append(f"Installer: {report.installer}")
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for issue in report.errors:
            suffix = f" ({issue.path})" if issue.path else ""
            lines.append(f"- [{issue.code}] {issue.message}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path)
    parser.add_argument("--app-exe", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--skip-sidecar-ping", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_installed_runtime(
        app_dir=args.app_dir,
        app_exe=args.app_exe,
        installer=args.installer,
        sidecar=args.sidecar,
        timeout=args.timeout,
        run_sidecar_ping=not args.skip_sidecar_ping,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
