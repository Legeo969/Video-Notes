"""StageRunner — execute a list of PipelineStage with resume/cancel support.

Usage:
    store = FileManifestStore()
    runner = StageRunner(manifest_store=store)
    result = runner.run(ctx, [TranscribeStage(), MapNotesStage(), ...])
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Protocol

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import PipelineStage, StageResult


logger = logging.getLogger(__name__)


class ManifestStore(Protocol):
    """Abstract interface for persisting stage completion state."""

    def is_completed(
        self,
        job_dir: str,
        stage_id: str,
        expected_input_hash: str = "",
        *,
        ignore_input_hash: bool = False,
    ) -> bool:
        ...

    def load_outputs(self, job_dir: str, stage_id: str) -> dict[str, Any]:
        ...

    def save_completed(
        self,
        job_dir: str,
        stage_id: str,
        artifact_files: list[str],
        input_hash: str = "",
        outputs: dict[str, Any] | None = None,
    ) -> None:
        ...


_MANIFEST_SUBDIR = os.path.join("artifacts", "_manifest")


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _fingerprint_value(value: Any) -> Any:
    """Return a deterministic, JSON-safe representation for cache validation.

    Existing files include size and mtime so a manifest cannot restore outputs
    after its upstream media was replaced in place.  API keys and other request
    values are never written to disk; only the final SHA-256 is stored.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, Path)):
        text = str(value)
        if os.path.isfile(text):
            try:
                stat = os.stat(text)
                return {
                    "path": os.path.abspath(text),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            except OSError:
                pass
        return text
    if is_dataclass(value):
        return _fingerprint_value(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_fingerprint_value(item) for item in value]
    if isinstance(value, set):
        items = [_fingerprint_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=True, default=str))
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        public = {
            key: item for key, item in attrs.items()
            if not str(key).startswith("_") and not callable(item)
        }
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "attrs": _fingerprint_value(public),
        }
    return {
        "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "repr": repr(value),
    }


def _stage_input_hash(
    ctx: ProcessingContext, stage: PipelineStage, state: dict[str, Any]
) -> str:
    """Hash only the inputs that can affect this stage.

    Built-in stages may expose ``cache_inputs(ctx, state)``.  This prevents an
    unrelated setting (for example an LLM API key) from invalidating completed
    media extraction or Whisper transcription.  Third-party stages without the
    hook retain the original whole-request/whole-state cache contract.
    """
    cache_inputs = getattr(stage, "cache_inputs", None)
    if callable(cache_inputs):
        payload = {
            "schema": 2,
            "stage": stage.id,
            "inputs": _fingerprint_value(cache_inputs(ctx, state)),
        }
    else:
        payload = {
            "schema": 1,
            "stage": stage.id,
            "request": _fingerprint_value(ctx.request),
            "state": _fingerprint_value(state),
        }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FileManifestStore:
    """Stores stage manifests as JSON files under artifacts/_manifest/."""

    _REQUIRED_FILES: dict[str, tuple[str, ...]] = {
        "resolve_media": ("artifacts/audio.wav",),
        "transcribe": ("artifacts/transcript.json",),
    }

    def is_completed(
        self,
        job_dir: str,
        stage_id: str,
        expected_input_hash: str = "",
        *,
        ignore_input_hash: bool = False,
    ) -> bool:
        """Validate manifest JSON, cache signature and stage-owned files.

        ``ignore_input_hash`` is used by explicit continuation.  A completed,
        intact stage is then authoritative even if settings changed after the
        pause/failure.  Users who want changed settings applied to completed
        stages must use the separate "从头重跑" action.
        """
        path = self._manifest_path(job_dir, stage_id)
        if not os.path.isfile(path):
            old_path = os.path.join(job_dir, f"_manifest_{stage_id}.json")
            if not os.path.isfile(old_path):
                return False
            path = old_path
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            return False
        if data.get("status") != "completed":
            return False
        stored_hash = str(data.get("input_hash") or "")
        # Empty hashes belong to legacy workspaces and remain readable for
        # backward compatibility. Every newly written V13 manifest has a hash.
        if (
            not ignore_input_hash
            and expected_input_hash
            and stored_hash
            and stored_hash != expected_input_hash
        ):
            return False

        # Strict artifact validation is enabled for built-in stages.  Unknown
        # plugin/test stages retain the historical manifest-only contract.
        files = list(self._REQUIRED_FILES.get(stage_id, ()))
        if stage_id in self._REQUIRED_FILES or stage_id == "write_artifacts":
            files.extend(data.get("artifact_files") or [])
        for item in files:
            candidate = str(item)
            full = candidate if os.path.isabs(candidate) else os.path.join(job_dir, candidate)
            if not os.path.isfile(full) or os.path.getsize(full) <= 0:
                return False

        outputs = data.get("outputs") or {}

        # Media restore must not return paths that disappeared from the workspace.
        if stage_id == "resolve_media":
            audio_path = outputs.get("audio_path")
            if not audio_path or not os.path.isfile(str(audio_path)) or os.path.getsize(str(audio_path)) <= 0:
                return False
            video_path = outputs.get("video_path")
            if video_path and (
                not os.path.isfile(str(video_path)) or os.path.getsize(str(video_path)) <= 0
            ):
                return False

        # Frame metadata is only reusable while every referenced frame still exists.
        if stage_id == "extract_frames":
            for frame in outputs.get("frames") or []:
                if not isinstance(frame, dict):
                    return False
                frame_path = frame.get("path")
                if not frame_path or not os.path.isfile(str(frame_path)) or os.path.getsize(str(frame_path)) <= 0:
                    return False

        # Final-output paths are stored in outputs for write_artifacts.
        if stage_id == "write_artifacts":
            for key in ("transcript_path", "notes_path"):
                full = outputs.get(key)
                if not full or not os.path.isfile(str(full)) or os.path.getsize(str(full)) <= 0:
                    return False
        return True

    def load_outputs(self, job_dir: str, stage_id: str) -> dict[str, Any]:
        path = self._manifest_path(job_dir, stage_id)
        if not os.path.isfile(path):
            # 向后兼容: 检查旧路径 {job_dir}/_manifest_{stage_id}.json
            old_path = os.path.join(job_dir, f"_manifest_{stage_id}.json")
            if os.path.isfile(old_path):
                path = old_path
            else:
                raise FileNotFoundError(f"No manifest for stage '{stage_id}' at {path}")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return dict(data.get("outputs", {}))

    def save_completed(
        self,
        job_dir: str,
        stage_id: str,
        artifact_files: list[str],
        input_hash: str = "",
        outputs: dict[str, Any] | None = None,
    ) -> None:
        manifest_dir = os.path.join(job_dir, _MANIFEST_SUBDIR)
        os.makedirs(manifest_dir, exist_ok=True)
        path = os.path.join(manifest_dir, f"{stage_id}.json")
        data = {
            "stage": stage_id,
            "status": "completed",
            "artifact_files": list(artifact_files),
            "input_hash": input_hash,
            "outputs": _json_safe(dict(outputs)) if outputs else {},
        }
        tmp = f"{path}.tmp-{uuid.uuid4().hex}"
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    @staticmethod
    def _manifest_path(job_dir: str, stage_id: str) -> str:
        return os.path.join(job_dir, _MANIFEST_SUBDIR, f"{stage_id}.json")


class StageRunner:
    """Executes a list of PipelineStage with resume and cancellation support.

    For each stage:
    1. Check cancellation token.
    2. Set progress via ProcessingContext.set_stage().
    3. If not forced and manifest says completed, load & apply cached outputs.
    4. Otherwise run the stage, save manifest, update state.
    """

    def __init__(self, manifest_store: ManifestStore):
        self.manifest_store = manifest_store

    def run(
        self,
        ctx: ProcessingContext,
        stages: list[PipelineStage],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute stages sequentially, returning accumulated state."""
        state = dict(initial_state) if initial_state else {}

        for stage in stages:
            ctx.check_cancelled()
            ctx.set_stage(stage.id, stage.label, stage.percent)
            try:
                from src.application.diagnostics.crash_guard import record_stage
                record_stage(ctx.job_id, stage.id, "starting", label=stage.label, percent=stage.percent)
            except Exception:
                pass

            input_hash = _stage_input_hash(ctx, stage, state)
            try:
                completed = self.manifest_store.is_completed(
                    ctx.job_dir,
                    stage.id,
                    expected_input_hash=input_hash,
                    ignore_input_hash=ctx.resume_run_id is not None,
                )
            except TypeError:
                # Compatibility for third-party/test manifest stores that still
                # implement the pre-V13 two-argument protocol.
                completed = self.manifest_store.is_completed(ctx.job_dir, stage.id)
            if not ctx.force and completed:
                cached = self.manifest_store.load_outputs(ctx.job_dir, stage.id)
                restore = getattr(stage, "restore_outputs", None)
                if callable(restore):
                    cached = restore(cached)
                state.update(cached)
                logger.info("♻️ 断点复用：%s（跳过重复执行）", stage.label)
                try:
                    from src.application.diagnostics.crash_guard import record_stage
                    record_stage(ctx.job_id, stage.id, "restored")
                except Exception:
                    pass
                continue

            try:
                result: StageResult = stage.run(ctx, state)
                state.update(result.outputs)
                self.manifest_store.save_completed(
                    ctx.job_dir,
                    stage.id,
                    artifact_files=result.artifact_files,
                    input_hash=result.input_hash or input_hash,
                    outputs=result.outputs,
                )
                try:
                    from src.application.diagnostics.crash_guard import record_stage
                    record_stage(ctx.job_id, stage.id, "completed")
                except Exception:
                    pass
            except BaseException as exc:
                try:
                    from src.application.diagnostics.crash_guard import record_stage
                    record_stage(ctx.job_id, stage.id, "failed", error=repr(exc))
                except Exception:
                    pass
                raise

        return state
