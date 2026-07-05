"""Stable, secret-safe serialization for resumable pipeline requests.

A resumable job must restore the exact non-secret configuration that created it.
Raw credentials are deliberately excluded from SQLite and are re-hydrated from
user settings or environment variables when the worker starts again.
"""

from __future__ import annotations

from typing import Any, Mapping

from src.config.settings import (
    decode_legacy_secret,
    get_env_api_key_for_provider,
    get_settings_path,
    load_settings,
    resolve_provider_binding_from_settings,
)
from src.domain.types import PipelineRequest

SNAPSHOT_SCHEMA_VERSION = 1

# Keep this explicit. Adding a new PipelineRequest field should require a
# deliberate compatibility decision instead of being silently persisted.
_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "input",
    "collection_id",
    "output_dir",
    "title",
    "subtitle_format",
    "vault_path",
    "export_mode",
    "artifact_layout",
    "whisper_model",
    "model_dir",
    "language",
    "beam_size",
    "vad_filter",
    "whisper_device",
    "whisper_compute_type",
    "gpt_model",
    "base_url",
    "provider",
    "template",
    "template_id",
    "temperature",
    "style",
    "smart_summary",
    "map_max_workers",
    "frame_interval",
    "frame_mode",
    "max_frames",
    "vision_enabled",
    "vision_provider",
    "vision_model",
    "vision_base_url",
    "ocr_enabled",
)

# These values must never be written to the task database or event journal.
_SECRET_FIELDS = {"api_key", "vision_api_key", "bilibili_cookies"}


def pipeline_request_to_snapshot(request: PipelineRequest) -> dict[str, Any]:
    """Return a versioned, JSON-serializable snapshot without credentials."""
    values: dict[str, Any] = {}
    for name in _SNAPSHOT_FIELDS:
        values[name] = getattr(request, name)
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "request": values,
        "credential_refs": {
            "llm_profile": str(getattr(request, "_llm_profile_name", "") or ""),
            "vision_profile": str(getattr(request, "_vision_profile_name", "") or ""),
        },
        "secret_policy": "named_profile_or_environment",
    }


def sanitize_request_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Remove credentials from arbitrary request params before journaling."""
    return {
        str(key): value
        for key, value in params.items()
        if str(key) not in _SECRET_FIELDS
    }


def _request_values(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    version = int(snapshot.get("schema_version", 1))
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported request snapshot schema: {version} "
            f"(expected {SNAPSHOT_SCHEMA_VERSION})"
        )
    raw = snapshot.get("request", snapshot)
    if not isinstance(raw, Mapping):
        raise ValueError("Invalid request snapshot: request must be an object")
    values = {str(k): v for k, v in raw.items() if str(k) in _SNAPSHOT_FIELDS}
    if not str(values.get("input") or "").strip():
        raise ValueError("Invalid request snapshot: input is missing")
    return values




def _credential_profile(
    settings: Mapping[str, Any],
    profile_name: str,
    provider_type: str | None,
    fallback_purpose: str,
) -> dict[str, Any]:
    """Resolve credentials from the same named profile used by the original job.

    The profile name is non-secret and can safely be persisted.  Older snapshots
    have no reference, so they fall back to the currently bound profile.
    """
    wanted = str(profile_name or "").strip().casefold()
    if wanted:
        for raw in settings.get("providers") or []:
            if not isinstance(raw, Mapping):
                continue
            name = str(raw.get("name") or "").strip()
            if name.casefold() != wanted:
                continue
            ptype = str(raw.get("type") or provider_type or "").strip()
            key = decode_legacy_secret(raw.get("api_key") or "").strip()
            if not key:
                key = get_env_api_key_for_provider(ptype)
            models = raw.get("models") or []
            return {
                "name": name,
                "type": ptype,
                "model": str(models[0] if models else "").strip(),
                "base_url": str(raw.get("base_url") or "").strip(),
                "api_key": key,
            }
    return resolve_provider_binding_from_settings(dict(settings), fallback_purpose)

def pipeline_request_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    settings_path: str | None = None,
) -> PipelineRequest:
    """Restore a PipelineRequest and resolve credentials at execution time.

    Provider/model/base URL from the snapshot remain authoritative. Credentials
    are fetched from the current bound profile or environment, so they can be
    rotated without mutating historical job rows.
    """
    values = _request_values(snapshot)
    settings = load_settings(settings_path or get_settings_path())

    refs = snapshot.get("credential_refs") or {}
    if not isinstance(refs, Mapping):
        refs = {}
    llm = _credential_profile(
        settings,
        str(refs.get("llm_profile") or ""),
        values.get("provider"),
        "llm",
    )
    vision = _credential_profile(
        settings,
        str(refs.get("vision_profile") or ""),
        values.get("vision_provider"),
        "vision",
    )

    if not values.get("provider"):
        values["provider"] = llm.get("type") or None
    if not values.get("gpt_model"):
        values["gpt_model"] = llm.get("model") or "mimo-v2.5"
    if not values.get("base_url"):
        values["base_url"] = llm.get("base_url") or None
    values["api_key"] = llm.get("api_key") or None

    if values.get("vision_enabled"):
        if not values.get("vision_provider"):
            values["vision_provider"] = vision.get("type") or None
        if not values.get("vision_model"):
            values["vision_model"] = vision.get("model") or None
        if not values.get("vision_base_url"):
            values["vision_base_url"] = vision.get("base_url") or None
        values["vision_api_key"] = vision.get("api_key") or None

    request = PipelineRequest(**values)
    object.__setattr__(request, "_llm_profile_name", str(llm.get("name") or ""))
    object.__setattr__(request, "_vision_profile_name", str(vision.get("name") or ""))
    return request
