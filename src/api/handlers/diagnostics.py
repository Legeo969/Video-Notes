"""Runtime diagnostics handlers.

Diagnostics are deliberately lazy: optional heavy libraries are imported only
when the user explicitly runs the doctor.  Exported bundles contain a safe
allow-list of environment facts and never include API keys or the full process
environment.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.api.protocol.errors import InternalError, InvalidParams
from src.config.settings import get_settings_path
from src.infrastructure.system.component_downloader import download_component_package
from src.infrastructure.system.component_manager import (
    ComponentManager,
    ComponentManifest,
    SignatureVerifier,
)
from src.infrastructure.system.signing import create_release_signature_verifier
from src.utils.external_tools import resolve_tool
from src.utils.runtime_components import activate_runtime_components, installed_component_paths

logger = logging.getLogger(__name__)
YTDLP_EXE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
WHISPER_CPP_WINDOWS_URL = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip"


def _result(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _module_available(name: str) -> bool:
    """Return module availability even when a test stub has no __spec__."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _download_executable(url: str, target: Path, *, max_bytes: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            with tmp.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("download exceeds maximum size")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _download_whisper_cpp_tools(
    url: str,
    target_dir: Path,
    files: list[str],
    *,
    max_bytes: int,
) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / "whisper-bin-x64.zip"
    _download_executable(url, zip_path, max_bytes=max_bytes)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = {item.filename.replace("\\", "/"): item for item in archive.infolist()}
            for filename in files:
                member = names.get(f"Release/{filename}") or names.get(filename)
                if member is None:
                    raise FileNotFoundError(f"whisper.cpp archive missing {filename}")
                output = target_dir / filename
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, output.open("wb") as dest:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        dest.write(chunk)
    finally:
        zip_path.unlink(missing_ok=True)


def create_diagnostics_handlers(
    output_dir: str = "./output",
    runtime_dir: str | None = None,
    signature_verifier: SignatureVerifier | None = None,
) -> dict[str, Any]:
    """Create doctor.run and diagnostics.bundle handlers."""
    project_root = Path(__file__).resolve().parents[3]
    runtime_root = Path(runtime_dir).expanduser().resolve() if runtime_dir else project_root / "runtime"
    verifier = signature_verifier or create_release_signature_verifier()
    component_manager = ComponentManager(
        runtime_root,
        signature_verifier=verifier,
    )

    def _check_python() -> dict[str, str]:
        return _result("Python", "pass", f"{platform.python_version()} ({sys.executable})")

    def _check_ffmpeg() -> dict[str, str]:
        executable = resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg")
        if not executable:
            return _result("FFmpeg", "fail", "未在 PATH 中找到 ffmpeg")
        try:
            completed = subprocess.run(
                [executable, "-version"],
                capture_output=True,
                timeout=10,
                check=False,
            )
            line = completed.stdout.decode("utf-8", errors="replace").splitlines()
            if completed.returncode == 0:
                return _result("FFmpeg", "pass", line[0] if line else executable)
            return _result("FFmpeg", "fail", f"退出码 {completed.returncode}")
        except Exception as exc:
            return _result("FFmpeg", "fail", str(exc))

    def _check_ytdlp() -> dict[str, str]:
        executable = resolve_tool("yt-dlp", components=["download-tools"], provides="download")
        if not executable:
            return _result("yt-dlp", "fail", "未找到 yt-dlp.exe，请安装 download-tools 插件")
        try:
            completed = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if completed.returncode == 0:
                return _result("yt-dlp", "pass", completed.stdout.strip() or executable)
            return _result("yt-dlp", "fail", f"退出码 {completed.returncode}")
        except Exception as exc:
            return _result("yt-dlp", "fail", str(exc))

    def _check_cuda() -> dict[str, str]:
        activate_runtime_components(
            components=["transcription-cuda", "transcription-cpu"],
            provides="transcription",
            runtime_root=runtime_root,
        )
        if not _module_available("ctranslate2"):
            return _result("CUDA", "warn", "未安装 ctranslate2，将无法使用 faster-whisper")
        try:
            import ctranslate2

            count = int(ctranslate2.get_cuda_device_count())
            if count > 0:
                return _result("CUDA", "pass", f"检测到 {count} 个 CUDA 设备")
            return _result("CUDA", "warn", "未检测到 CUDA 设备，将使用 CPU")
        except Exception as exc:
            return _result("CUDA", "warn", str(exc))

    def _check_whisper() -> dict[str, str]:
        components = installed_component_paths(
            components=["transcription-cuda", "transcription-cpu"],
            provides="transcription",
            runtime_root=runtime_root,
        )
        activate_runtime_components(
            components=["transcription-cuda", "transcription-cpu"],
            provides="transcription",
            runtime_root=runtime_root,
        )
        if not _module_available("faster_whisper"):
            return _result("faster-whisper", "fail", "未安装转写组件")
        if components:
            names = ", ".join(path.name for path in components)
            return _result("faster-whisper", "pass", f"已安装转写组件: {names}")
        return _result("faster-whisper", "pass", "Python 模块可导入")

    def _check_whisper_cpp() -> dict[str, str]:
        executable = (
            resolve_tool(
                "whisper-cli",
                components=["whisper-cpp-tools"],
                provides="transcription-native",
            )
            or resolve_tool(
                "main",
                components=["whisper-cpp-tools"],
                provides="transcription-native",
            )
        )
        if executable:
            return _result("whisper.cpp", "pass", executable)
        return _result("whisper.cpp", "warn", "未安装 whisper-cpp-tools")

    def _check_ocr() -> dict[str, str]:
        # OCR is optional.  Report capability without importing model weights.
        tesseract = resolve_tool(
            "tesseract",
            components=["tesseract-ocr-tools"],
            provides="ocr-native",
        )
        if tesseract:
            return _result("OCR", "pass", f"Tesseract native: {tesseract}")
        ocr_components = installed_component_paths(provides="ocr", runtime_root=runtime_root)
        activate_runtime_components(provides="ocr", runtime_root=runtime_root)
        candidates = ["paddleocr", "easyocr", "rapidocr_onnxruntime"]
        available = [name for name in candidates if _module_available(name)]
        if available:
            return _result("OCR", "pass", "可用后端：" + ", ".join(available))
        if ocr_components:
            return _result("OCR", "warn", "OCR 组件已安装，但 Python 模块未通过校验")
        return _result("OCR", "warn", "未检测到可选 OCR 后端")

    def _check_settings() -> dict[str, str]:
        path = Path(get_settings_path())
        if not path.is_file():
            return _result("设置文件", "warn", f"尚未创建：{path}")
        try:
            json.loads(path.read_text(encoding="utf-8"))
            return _result("设置文件", "pass", str(path))
        except Exception as exc:
            return _result("设置文件", "fail", f"JSON 无法读取：{exc}")

    def _check_output_dir() -> dict[str, str]:
        path = Path(output_dir).expanduser().resolve()
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return _result("输出目录", "pass", str(path))
        except Exception as exc:
            return _result("输出目录", "fail", str(exc))

    def _run_checks() -> list[dict[str, str]]:
        return [
            _check_python(),
            _check_ffmpeg(),
            _check_ytdlp(),
            _check_cuda(),
            _check_whisper(),
            _check_whisper_cpp(),
            _check_ocr(),
            _check_settings(),
            _check_output_dir(),
        ]

    def handle_doctor_run(params: dict[str, Any]) -> list[dict[str, str]]:
        return _run_checks()

    def handle_bundle(params: dict[str, Any]) -> str:
        checks = _run_checks()
        bundle = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "python_executable": sys.executable,
            "checks": checks,
            "summary": {
                "pass": sum(item["status"] == "pass" for item in checks),
                "warn": sum(item["status"] == "warn" for item in checks),
                "fail": sum(item["status"] == "fail" for item in checks),
            },
        }
        target_dir = Path(output_dir).expanduser().resolve() / "diagnostics"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = target_dir / f"video-notes-diagnostics-{timestamp}.json"
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(bundle, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            return str(target)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            logger.exception("Failed to create diagnostics bundle")
            raise InternalError(str(exc)) from exc

    def _read_component_manifests() -> list[dict[str, Any]]:
        return [
            component_manager.catalog_status(manifest)
            for manifest in component_manager.list_catalog()
        ]

    def handle_components_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        return _read_component_manifests()

    def handle_components_verify(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("component") or params.get("name") or "").strip()
        catalog = _read_component_manifests()
        selected = [item for item in catalog if not name or item["component"] == name]
        if name and not selected:
            raise InvalidParams(f"component not found: {name}")

        results = []
        for item in selected:
            if item["installed"]:
                results.append(component_manager.verify_component(item["component"]))
            else:
                results.append({
                    "component": item["component"],
                    "ok": False,
                    "status": "not_installed",
                    "missing_files": item["missing_files"],
                    "sha256_ok": None,
                })
        return {
            "ok": all(result["ok"] for result in results),
            "components": results,
        }

    def _manifest_from_params(params: dict[str, Any]) -> ComponentManifest:
        manifest_path = str(params.get("manifest_path") or "").strip()
        if manifest_path:
            return component_manager.read_manifest(manifest_path)
        name = str(params.get("component") or params.get("name") or "").strip()
        if not name:
            raise InvalidParams("component is required")
        manifest = component_manager.get_catalog_component(name)
        if manifest is None:
            raise InvalidParams(f"component not found: {name}")
        return manifest

    def handle_components_install(params: dict[str, Any]) -> dict[str, Any]:
        package_url = str(params.get("package_url") or params.get("url") or "").strip()
        if package_url:
            sha256 = str(params.get("sha256") or "").strip()
            try:
                package_path = download_component_package(
                    package_url,
                    runtime_root / "components" / ".downloads",
                    expected_sha256=sha256,
                    max_bytes=int(params.get("max_bytes") or 2 * 1024 * 1024 * 1024),
                )
                return component_manager.install_package(
                    package_path,
                    expected_sha256=sha256,
                    require_signature=bool(params.get("require_signature", True)),
                )
            except (
                FileNotFoundError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                raise InvalidParams(str(exc)) from exc

        package_path = str(params.get("package_path") or "").strip()
        if package_path:
            try:
                return component_manager.install_package(
                    package_path,
                    expected_sha256=str(params.get("sha256") or "").strip(),
                    require_signature=bool(params.get("require_signature", False)),
                )
            except (
                FileNotFoundError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ) as exc:
                raise InvalidParams(str(exc)) from exc

        source = str(
            params.get("source_dir")
            or params.get("package_dir")
            or params.get("path")
            or ""
        ).strip()
        manifest_path = str(params.get("manifest_path") or "").strip()
        if not source and manifest_path:
            source = str(Path(manifest_path).expanduser().resolve().parent)
        try:
            manifest = _manifest_from_params(params)
            if not source:
                bundled_source = runtime_root / "packages" / manifest.component
                if bundled_source.is_dir():
                    source = str(bundled_source)
            if not source and manifest.component == "download-tools":
                download_url = str(params.get("download_url") or YTDLP_EXE_URL).strip()
                with tempfile.TemporaryDirectory(dir=runtime_root / "components") as temp_dir:
                    temp_source = Path(temp_dir)
                    _download_executable(
                        download_url,
                        temp_source / "yt-dlp.exe",
                        max_bytes=int(params.get("max_bytes") or 100 * 1024 * 1024),
                    )
                    return component_manager.install_component(
                        manifest,
                        temp_source,
                        require_signature=False,
                    )
            if not source and manifest.component == "whisper-cpp-tools":
                download_url = str(params.get("download_url") or WHISPER_CPP_WINDOWS_URL).strip()
                with tempfile.TemporaryDirectory(dir=runtime_root / "components") as temp_dir:
                    temp_source = Path(temp_dir)
                    _download_whisper_cpp_tools(
                        download_url,
                        temp_source,
                        manifest.files,
                        max_bytes=int(params.get("max_bytes") or 300 * 1024 * 1024),
                    )
                    return component_manager.install_component(
                        manifest,
                        temp_source,
                        require_signature=False,
                    )
            if not source and manifest.component == "tesseract-ocr-tools":
                tesseract = resolve_tool("tesseract")
                if tesseract:
                    candidate = Path(tesseract).resolve().parent
                    if (candidate / "tessdata").is_dir():
                        source = str(candidate)
            if not source:
                raise InvalidParams("source_dir is required for component install")
            return component_manager.install_component(
                manifest,
                source,
                require_signature=bool(params.get("require_signature", False)),
            )
        except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_components_remove(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("component") or params.get("name") or "").strip()
        if not name:
            raise InvalidParams("component is required")
        try:
            return component_manager.remove_component(name)
        except ValueError as exc:
            raise InvalidParams(str(exc)) from exc

    def handle_logs_tail(params: dict[str, Any]) -> list[str]:
        limit = max(1, min(1000, int(params.get("limit", 200) or 200)))
        logs_dir = Path(output_dir).expanduser().resolve() / "logs"
        name = str(params.get("file") or params.get("name") or "engine.log").strip()
        if not name:
            name = "engine.log"
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise InvalidParams("log file must be a relative filename")
        path = (logs_dir / candidate).resolve()
        if logs_dir not in path.parents and path != logs_dir:
            raise InvalidParams("log file must stay inside logs directory")
        if not path.is_file():
            return []
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError as exc:
            raise InternalError(str(exc)) from exc

    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for item in path.rglob("*"):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
        return total

    def _dir_count(path: Path) -> dict[str, int]:
        if not path.is_dir():
            return {"dirs": 0, "files": 0}
        dirs = 0
        files = 0
        for item in path.rglob("*"):
            try:
                if item.is_dir():
                    dirs += 1
                elif item.is_file():
                    files += 1
            except OSError:
                continue
        return {"dirs": dirs, "files": files}

    def _storage_queue():
        from src.application.services.job_queue import JobQueue, get_default_db_path

        db_path = get_default_db_path(output_dir)
        return JobQueue(db_path=db_path, output_dir=output_dir)

    def handle_storage_status(params: dict[str, Any]) -> dict[str, Any]:
        from src.application.services.job_queue import (
            get_default_db_path,
            get_default_jobs_root,
            get_default_state_dir,
            get_legacy_jobs_root,
        )
        from src.config.settings import load_settings

        settings = load_settings(get_settings_path())
        export_dir = Path(output_dir).expanduser().resolve()
        state_dir = Path(get_default_state_dir()).expanduser().resolve()
        jobs_root = Path(get_default_jobs_root()).expanduser().resolve()
        legacy_jobs_root = Path(get_legacy_jobs_root()).expanduser().resolve()
        db_path = Path(get_default_db_path(output_dir)).expanduser().resolve()
        vault_path = str(settings.get("vault_path") or "")
        vault = Path(vault_path).expanduser().resolve() if vault_path else None
        return {
            "export_dir": str(export_dir),
            "state_dir": str(state_dir),
            "db_path": str(db_path),
            "jobs_root": str(jobs_root),
            "legacy_jobs_root": str(legacy_jobs_root),
            "vault_path": str(vault) if vault else "",
            "sizes": {
                "export_bytes": _dir_size(export_dir),
                "state_bytes": _dir_size(state_dir),
                "jobs_bytes": _dir_size(jobs_root),
                "legacy_jobs_bytes": _dir_size(legacy_jobs_root),
                "db_bytes": _dir_size(db_path),
                "vault_bytes": 0,
            },
            "counts": {
                "jobs": _dir_count(jobs_root),
                "legacy_jobs": _dir_count(legacy_jobs_root),
            },
        }

    def handle_storage_cleanup_orphans(params: dict[str, Any]) -> dict[str, int]:
        from src.application.services.job_queue import get_default_jobs_root, get_legacy_jobs_root

        min_age = float(params.get("min_age_hours", 0) or 0)
        queue = _storage_queue()
        current = queue.cleanup_orphans(min_age_hours=min_age)
        legacy = 0
        legacy_root = get_legacy_jobs_root()
        if os.path.abspath(legacy_root) != os.path.abspath(get_default_jobs_root()):
            legacy = queue.cleanup_orphans(min_age_hours=min_age, jobs_root=legacy_root)
        return {"removed": current + legacy, "current": current, "legacy": legacy}

    def handle_storage_cleanup_completed(params: dict[str, Any]) -> dict[str, int]:
        removed = _storage_queue().cleanup_completed_workspaces()
        return {"removed": removed}

    return {
        "doctor.run": handle_doctor_run,
        "diagnostics.bundle": handle_bundle,
        "logs.tail": handle_logs_tail,
        "storage.status": handle_storage_status,
        "storage.cleanup_orphans": handle_storage_cleanup_orphans,
        "storage.cleanup_completed": handle_storage_cleanup_completed,
        "components.list": handle_components_list,
        "components.verify": handle_components_verify,
        "components.install": handle_components_install,
        "components.remove": handle_components_remove,
    }
