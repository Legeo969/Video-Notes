"""Verify installed/unpacked Windows release artifacts."""

from __future__ import annotations

import argparse
import json
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
    app_exe: str
    installer: str
    errors: list[RuntimeIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "app_exe": self.app_exe,
            "installer": self.installer,
            "errors": [asdict(issue) for issue in self.errors],
        }


def verify_installed_runtime(
    *,
    app_dir: str | Path | None = None,
    app_exe: str | Path | None = None,
    installer: str | Path | None = None,
) -> InstalledRuntimeReport:
    """Verify installed release artifacts for the native-engine desktop app."""
    errors: list[RuntimeIssue] = []
    resolved_app_dir = Path(app_dir).expanduser().resolve() if app_dir else None
    resolved_app_exe = _resolve_optional_file(app_exe, errors, "app_exe_missing")
    resolved_installer = _resolve_optional_file(installer, errors, "installer_missing")

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

    return InstalledRuntimeReport(
        ok=not errors,
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
    candidates = list(app_dir.glob("*.exe"))
    exact = [path for path in candidates if path.stem.lower() == "video notes ai"]
    if exact:
        return exact[0]
    return sorted(candidates)[0] if candidates else None


def _format_human(report: InstalledRuntimeReport) -> str:
    lines = ["Installed runtime: " + ("OK" if report.ok else "FAILED")]
    if report.app_exe:
        lines.append(f"App: {report.app_exe}")
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
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_installed_runtime(
        app_dir=args.app_dir,
        app_exe=args.app_exe,
        installer=args.installer,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
