"""Aggregate release acceptance checks into one report."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from verify_installed_runtime import verify_installed_runtime
from verify_release_gate import verify_repository
from verify_runtime_payloads import verify_runtime_payloads


@dataclass(frozen=True)
class AcceptanceCheck:
    name: str
    ok: bool
    skipped: bool
    details: dict[str, Any]


@dataclass(frozen=True)
class ReleaseAcceptanceReport:
    ok: bool
    checks: list[AcceptanceCheck]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }


def verify_release_acceptance(
    root: str | Path,
    *,
    payload_root: str | Path | None = None,
    payload_map: dict[str, str | Path] | None = None,
    installer: str | Path | None = None,
    app_dir: str | Path | None = None,
    app_exe: str | Path | None = None,
    skip_payloads: bool = False,
    skip_installer: bool = False,
    skip_installed_runtime: bool = False,
) -> ReleaseAcceptanceReport:
    """Run release acceptance checks that can execute on this machine."""
    repo = Path(root).expanduser().resolve()
    checks: list[AcceptanceCheck] = []

    preflight = verify_repository(repo, strict_packages=False)
    checks.append(AcceptanceCheck(
        name="release_preflight",
        ok=preflight.ok,
        skipped=False,
        details=preflight.to_dict(),
    ))

    if skip_payloads:
        checks.append(_skipped("runtime_payloads"))
    else:
        try:
            payloads = verify_runtime_payloads(
                repo,
                payload_root=payload_root,
                payload_map=payload_map,
            )
            checks.append(AcceptanceCheck(
                name="runtime_payloads",
                ok=payloads.ok,
                skipped=False,
                details=payloads.to_dict(),
            ))
        except Exception as exc:
            checks.append(_failed("runtime_payloads", exc))

    strict = verify_repository(repo, strict_packages=True)
    checks.append(AcceptanceCheck(
        name="strict_component_catalog",
        ok=strict.ok,
        skipped=False,
        details=strict.to_dict(),
    ))

    if skip_installer:
        checks.append(_skipped("installer_artifact"))
        resolved_installer = None
    else:
        resolved_installer = _resolve_installer(repo, installer)
        installer_details = {
            "installer": str(resolved_installer or ""),
            "exists": bool(resolved_installer and resolved_installer.is_file()),
        }
        checks.append(AcceptanceCheck(
            name="installer_artifact",
            ok=installer_details["exists"],
            skipped=False,
            details=installer_details,
        ))

    if skip_installed_runtime:
        checks.append(_skipped("installed_runtime_smoke"))
    else:
        should_run = app_dir is not None or app_exe is not None
        if should_run:
            installed = verify_installed_runtime(
                app_dir=app_dir,
                app_exe=app_exe,
                installer=resolved_installer,
            )
            checks.append(AcceptanceCheck(
                name="installed_runtime_smoke",
                ok=installed.ok,
                skipped=False,
                details=installed.to_dict(),
            ))
        else:
            checks.append(AcceptanceCheck(
                name="installed_runtime_smoke",
                ok=False,
                skipped=True,
                details={
                    "reason": "pass --app-dir or --app-exe after installing/unpacking the release",
                },
            ))

    return ReleaseAcceptanceReport(
        ok=all(check.ok for check in checks),
        checks=checks,
    )


def _resolve_installer(root: Path, installer: str | Path | None) -> Path | None:
    if installer is not None:
        return Path(installer).expanduser().resolve()
    bundle_dir = root / "desktop" / "src-tauri" / "target" / "release" / "bundle"
    if not bundle_dir.is_dir():
        return None
    candidates = sorted(
        path for path in bundle_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".exe", ".msi"}
    )
    return candidates[0] if candidates else None


def _failed(name: str, exc: Exception) -> AcceptanceCheck:
    return AcceptanceCheck(
        name=name,
        ok=False,
        skipped=False,
        details={"error": str(exc), "type": type(exc).__name__},
    )


def _skipped(name: str) -> AcceptanceCheck:
    return AcceptanceCheck(name=name, ok=False, skipped=True, details={})


def _load_payload_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload map must be a JSON object")
    result: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("payload map keys and values must be strings")
        result[key] = value
    return result


def _format_human(report: ReleaseAcceptanceReport) -> str:
    lines = ["Release acceptance: " + ("OK" if report.ok else "FAILED")]
    for check in report.checks:
        if check.skipped:
            status = "SKIPPED"
        else:
            status = "OK" if check.ok else "FAILED"
        lines.append(f"- {check.name}: {status}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--payload-root", type=Path)
    parser.add_argument("--payload-map", type=Path)
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--app-dir", type=Path)
    parser.add_argument("--app-exe", type=Path)
    parser.add_argument("--skip-payloads", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--skip-installed-runtime", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = verify_release_acceptance(
        args.root,
        payload_root=args.payload_root,
        payload_map=_load_payload_map(args.payload_map),
        installer=args.installer,
        app_dir=args.app_dir,
        app_exe=args.app_exe,
        skip_payloads=args.skip_payloads,
        skip_installer=args.skip_installer,
        skip_installed_runtime=args.skip_installed_runtime,
    )
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
