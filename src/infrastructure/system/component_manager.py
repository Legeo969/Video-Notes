"""Runtime component manager with staged install and rollback."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SignatureVerifier = Callable[[Path, "ComponentManifest"], bool]


@dataclass
class ComponentManifest:
    """Component manifest shared by catalog and installed state."""

    component: str
    version: str
    platform: str
    engine_api: int
    description: str = ""
    sha256: str = ""
    signature: str = ""
    size_mb: int = 0
    package_sha256: str = ""
    requires: dict[str, str] = field(default_factory=dict)
    provides: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


class ComponentManager:
    """Install, verify, remove and rollback runtime components.

    Directory layout:
        runtime/
          manifests/                 catalog manifests
          components/
            <component>/              current component copy
            <component>-<version>/    immutable installed version copy
            .installed/<component>.json
            .staging/
            .rollback/
    """

    def __init__(
        self,
        base_dir: str | Path,
        *,
        signature_verifier: SignatureVerifier | None = None,
    ) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._signature_verifier = signature_verifier
        self._components_dir = self._base_dir / "components"
        self._catalog_dir = self._base_dir / "manifests"
        self._installed_dir = self._components_dir / ".installed"
        self._staging_dir = self._components_dir / ".staging"
        self._rollback_dir = self._components_dir / ".rollback"
        for path in (
            self._components_dir,
            self._catalog_dir,
            self._installed_dir,
            self._staging_dir,
            self._rollback_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    # ── manifests ─────────────────────────────────────────────

    def list_catalog(self) -> list[ComponentManifest]:
        """List available component manifests from runtime/manifests."""
        return self._read_manifests(self._catalog_dir)

    def list_components(self) -> list[ComponentManifest]:
        """List installed components."""
        return self._read_manifests(self._installed_dir)

    def get_catalog_component(self, name: str) -> ComponentManifest | None:
        for manifest in self.list_catalog():
            if manifest.component == name:
                return manifest
        return None

    def get_component(self, name: str) -> ComponentManifest | None:
        for manifest in self.list_components():
            if manifest.component == name:
                return manifest
        return None

    def read_manifest(self, path: str | Path) -> ComponentManifest:
        manifest_path = self._resolve_existing_file(path)
        return ComponentManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))

    def _read_manifests(self, directory: Path) -> list[ComponentManifest]:
        result: list[ComponentManifest] = []
        for path in sorted(directory.glob("*.json")):
            try:
                result.append(
                    ComponentManifest(**json.loads(path.read_text(encoding="utf-8")))
                )
            except Exception as exc:
                logger.warning("Invalid component manifest %s: %s", path, exc)
        return result

    # ── operations ────────────────────────────────────────────

    def install_component(
        self,
        manifest: ComponentManifest,
        source_dir: str | Path,
        *,
        require_signature: bool = False,
        package_path: str | Path | None = None,
    ) -> dict[str, Any]:
        """Install a component from a local package directory.

        The package is copied into a staging directory, verified, then promoted.
        If promotion fails, the previous current component and installed
        manifest are restored.
        """
        self._validate_component_name(manifest.component)
        self._verify_signature_requirement(
            manifest,
            Path(package_path).expanduser().resolve() if package_path else None,
            require_signature,
        )

        source = self._resolve_existing_dir(source_dir)
        install_id = f"{manifest.component}-{manifest.version}-{uuid.uuid4().hex}"
        stage = self._staging_dir / install_id
        target = self._components_dir / f"{manifest.component}-{manifest.version}"
        current = self._components_dir / manifest.component
        rollback = self._rollback_dir / install_id
        rollback_current = rollback / "current"
        rollback_manifest = rollback / "manifest.json"
        manifest_path = self._installed_dir / f"{manifest.component}.json"

        try:
            self._copy_package_files(source, stage, manifest.files)
            stage_check = self._verify_path(manifest, stage)
            if stage_check["status"] != "ok":
                raise ValueError(f"component verification failed: {stage_check}")

            rollback.mkdir(parents=True, exist_ok=True)
            if current.exists():
                shutil.move(str(current), str(rollback_current))
            if manifest_path.exists():
                shutil.copy2(manifest_path, rollback_manifest)

            if not target.exists():
                shutil.move(str(stage), str(target))
            else:
                shutil.rmtree(stage)
                target_check = self._verify_path(manifest, target)
                if target_check["status"] != "ok":
                    shutil.rmtree(target)
                    self._copy_package_files(source, target, manifest.files)

            shutil.copytree(target, current)
            self._write_installed_manifest(manifest)
            shutil.rmtree(rollback, ignore_errors=True)
            logger.info("Installed component %s %s", manifest.component, manifest.version)
            return {
                "ok": True,
                "component": manifest.component,
                "version": manifest.version,
                "status": "installed",
            }
        except Exception:
            self._restore_rollback(current, manifest_path, rollback_current, rollback_manifest)
            shutil.rmtree(stage, ignore_errors=True)
            logger.exception("Component install failed and rollback was attempted")
            raise

    def install_package(
        self,
        package_path: str | Path,
        *,
        expected_sha256: str = "",
        require_signature: bool = False,
    ) -> dict[str, Any]:
        """Install a zip component package.

        Package format:
          - manifest.json or component.json at archive root
          - component payload files referenced by manifest.files
        """
        package = self._resolve_existing_file(package_path)
        actual_digest = self._hash_file(package)
        expected_digest = expected_sha256.strip()
        if expected_digest:
            if actual_digest.lower() != expected_digest.lower():
                raise ValueError("component package sha256 mismatch")

        with tempfile.TemporaryDirectory(dir=self._staging_dir) as tmp:
            extracted = Path(tmp) / "package"
            extracted.mkdir()
            self._extract_zip_package(package, extracted)
            manifest_file = self._find_package_manifest(extracted)
            manifest = ComponentManifest(
                **json.loads(manifest_file.read_text(encoding="utf-8"))
            )
            manifest_digest = manifest.package_sha256.strip()
            if manifest_digest and manifest_digest.lower() != actual_digest.lower():
                raise ValueError("component package sha256 mismatch")
            if not manifest.package_sha256 and expected_digest:
                manifest.package_sha256 = expected_digest
            return self.install_component(
                manifest,
                extracted,
                require_signature=require_signature,
                package_path=package,
            )

    def remove_component(self, name: str) -> dict[str, Any]:
        """Remove current component files and installed manifest with rollback."""
        self._validate_component_name(name)
        current = self._components_dir / name
        manifest_path = self._installed_dir / f"{name}.json"
        if not current.exists() and not manifest_path.exists():
            return {"ok": True, "component": name, "status": "not_installed"}

        remove_id = f"{name}-remove-{uuid.uuid4().hex}"
        rollback = self._rollback_dir / remove_id
        rollback_current = rollback / "current"
        rollback_manifest = rollback / "manifest.json"
        try:
            rollback.mkdir(parents=True, exist_ok=True)
            if current.exists():
                shutil.move(str(current), str(rollback_current))
            if manifest_path.exists():
                shutil.move(str(manifest_path), str(rollback_manifest))
            shutil.rmtree(rollback, ignore_errors=True)
            logger.info("Removed component %s", name)
            return {"ok": True, "component": name, "status": "removed"}
        except Exception:
            self._restore_rollback(current, manifest_path, rollback_current, rollback_manifest)
            logger.exception("Component remove failed and rollback was attempted")
            raise

    def verify_component(self, name: str) -> dict[str, Any]:
        """Verify an installed component."""
        self._validate_component_name(name)
        manifest = self.get_component(name)
        if not manifest:
            return {"ok": False, "status": "not_installed", "component": name}

        current = self._components_dir / name
        result = self._verify_path(manifest, current)
        result["component"] = name
        result["version"] = manifest.version
        return result

    # ── internals ─────────────────────────────────────────────

    def catalog_status(self, manifest: ComponentManifest) -> dict[str, Any]:
        """Return list/verify status for a catalog manifest."""
        installed = self.get_component(manifest.component)
        current = self._components_dir / manifest.component
        missing = self._missing_files(manifest, current)
        return {
            "component": manifest.component,
            "version": manifest.version,
            "platform": manifest.platform,
            "engine_api": manifest.engine_api,
            "description": manifest.description,
            "manifest_path": str(self._catalog_dir / f"{manifest.component}.json"),
            "component_path": str(current),
            "installed": installed is not None and current.is_dir(),
            "installed_version": installed.version if installed else None,
            "status": "ok" if installed and not missing else "not_installed" if not installed else "missing_files",
            "missing_files": missing,
            "sha256": manifest.sha256,
            "package_sha256": manifest.package_sha256,
            "signature": manifest.signature,
            "provides": manifest.provides,
            "requires": manifest.requires,
        }

    def _write_installed_manifest(self, manifest: ComponentManifest) -> None:
        path = self._installed_dir / f"{manifest.component}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _copy_package_files(self, source: Path, destination: Path, files: list[str]) -> None:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        entries = files or [
            path.name for path in source.iterdir()
            if path.name not in {"manifest.json", "component.json"}
        ]
        for raw in entries:
            relative = self._safe_relative_path(raw)
            src = source / relative
            dst = destination / relative
            if not src.exists():
                raise FileNotFoundError(f"component file missing from package: {raw}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    def _verify_path(self, manifest: ComponentManifest, path: Path) -> dict[str, Any]:
        if not path.is_dir():
            return {"ok": False, "status": "not_installed", "missing_files": manifest.files}
        missing = self._missing_files(manifest, path)
        if missing:
            return {"ok": False, "status": "missing_files", "missing_files": missing}
        sha256_ok = None
        if manifest.sha256.strip():
            digest = self._hash_component(path, manifest.files)
            sha256_ok = digest == manifest.sha256
            if not sha256_ok:
                return {
                    "ok": False,
                    "status": "hash_mismatch",
                    "missing_files": [],
                    "sha256_ok": False,
                    "sha256": digest,
                }
        return {
            "ok": True,
            "status": "ok",
            "missing_files": [],
            "sha256_ok": sha256_ok,
        }

    def _missing_files(self, manifest: ComponentManifest, root: Path) -> list[str]:
        missing: list[str] = []
        for raw in manifest.files:
            relative = self._safe_relative_path(raw)
            if not (root / relative).exists():
                missing.append(raw)
        return missing

    def _hash_component(self, root: Path, files: list[str]) -> str:
        hasher = hashlib.sha256()
        paths: list[Path] = []
        if files:
            for raw in files:
                relative = self._safe_relative_path(raw)
                candidate = root / relative
                if candidate.is_file():
                    paths.append(candidate)
                elif candidate.is_dir():
                    paths.extend(path for path in candidate.rglob("*") if path.is_file())
        else:
            paths = [path for path in root.rglob("*") if path.is_file()]
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix().encode("utf-8")
            hasher.update(relative)
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
            hasher.update(b"\0")
        return hasher.hexdigest()

    @staticmethod
    def _hash_file(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _verify_signature_requirement(
        self,
        manifest: ComponentManifest,
        package_path: Path | None,
        require_signature: bool,
    ) -> None:
        if not require_signature:
            return
        if not manifest.signature.strip():
            raise ValueError("component package is unsigned")
        if package_path is None:
            raise ValueError("signature verification requires a package file")
        if self._signature_verifier is None:
            raise ValueError("component signature verifier is not configured")
        if not self._signature_verifier(package_path, manifest):
            raise ValueError("component signature verification failed")

    def _extract_zip_package(self, package: Path, destination: Path) -> None:
        try:
            with zipfile.ZipFile(package) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    relative = self._safe_relative_path(info.filename)
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        except zipfile.BadZipFile as exc:
            raise ValueError(f"invalid component package: {package}") from exc

    @staticmethod
    def _find_package_manifest(package_dir: Path) -> Path:
        for name in ("manifest.json", "component.json"):
            candidate = package_dir / name
            if candidate.is_file():
                return candidate
        raise FileNotFoundError("component package manifest is missing")

    def _restore_rollback(
        self,
        current: Path,
        manifest_path: Path,
        rollback_current: Path,
        rollback_manifest: Path,
    ) -> None:
        if rollback_current.exists() and current.exists():
            shutil.rmtree(current)
        if rollback_current.exists():
            shutil.move(str(rollback_current), str(current))
        if rollback_manifest.exists() and manifest_path.exists():
            manifest_path.unlink()
        if rollback_manifest.exists():
            shutil.move(str(rollback_manifest), str(manifest_path))

    def _resolve_existing_file(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _resolve_existing_dir(self, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise FileNotFoundError(path)
        return path

    @staticmethod
    def _validate_component_name(name: str) -> None:
        if not name or any(char in name for char in "\\/:*?\"<>|") or name in {".", ".."}:
            raise ValueError(f"invalid component name: {name!r}")

    @staticmethod
    def _safe_relative_path(raw: str) -> Path:
        clean = raw.strip().replace("\\", "/").rstrip("/")
        path = Path(clean)
        if not clean or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"invalid component file path: {raw!r}")
        return path
