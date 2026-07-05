"""Settings and provider-profile JSON-RPC handlers.

The desktop UI and the processing runtime share the same persisted settings
file.  All mutations go through :func:`update_settings`, which performs an
atomic replace, so a crash cannot leave a half-written JSON document.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.api.protocol.errors import InternalError, InvalidParams
from src.application.notes.template_loader import get_template_registry
from src.config.provider_profiles import normalize_provider_settings
from src.config.settings import (
    decode_legacy_secret,
    get_settings_path,
    load_settings,
    update_settings,
)

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDER_ALIASES = {
    "mimo": "mimo",
    "xiaomi": "mimo",
    "dashscope": "dashscope",
    "bailian": "dashscope",
    "openai_compat": "openai_compat",
    "openai-compatible": "openai_compat",
    "openai compatible": "openai_compat",
    "openai": "openai_compat",
    "custom": "openai_compat",
    "自定义": "openai_compat",
    "google_gemini": "google_gemini",
    "google": "google_gemini",
    "gemini": "google_gemini",
    "anthropic_messages": "anthropic_messages",
    "anthropic": "anthropic_messages",
    "claude": "anthropic_messages",
    "openai_responses": "openai_responses",
    "responses": "openai_responses",
    "chatgpt_codex": "chatgpt_codex",
    "codex": "chatgpt_codex",
}

_DEFAULT_BASE_URLS = {
    "mimo": "https://token-plan-cn.xiaomimimo.com/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "openai_compat": "https://api.openai.com/v1",
    "google_gemini": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic_messages": "https://api.anthropic.com/v1",
    "openai_responses": "https://api.openai.com/v1",
}


def _get_settings_path() -> str:
    """Compatibility wrapper around the canonical settings-path resolver."""
    return get_settings_path()


def _mask_api_key(key: str) -> str:
    """Return a non-reversible preview; never return the complete key."""
    key = str(key or "").strip()
    if not key:
        return ""
    if len(key) < 8:
        return "****"
    return key[:5] + "****" + key[-4:]


def _normalise_provider_type(value: Any) -> str:
    raw = str(value or "openai_compat").strip()
    return _SUPPORTED_PROVIDER_ALIASES.get(raw.casefold(), raw)


def _clean_models(*values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for candidate in candidates:
            model = str(candidate or "").strip()
            key = model.casefold()
            if model and key not in seen:
                seen.add(key)
                result.append(model)
    return result


def _safe_raw_settings(path: str) -> dict[str, Any]:
    raw = load_settings(path)
    if not isinstance(raw, dict):
        return {}
    providers, bindings, changed = normalize_provider_settings(
        raw.get("providers"), raw.get("bindings")
    )
    if changed:
        raw["providers"] = providers
        raw["bindings"] = bindings
        update_settings({"providers": providers, "bindings": bindings}, path)
    return raw


def _find_provider(raw: dict[str, Any], name: str) -> tuple[int, dict[str, Any]]:
    for index, profile in enumerate(raw.get("providers") or []):
        if not isinstance(profile, dict):
            continue
        if str(profile.get("name") or "").strip().casefold() == name.casefold():
            return index, profile
    raise InvalidParams(f"Provider '{name}' does not exist")


def _build_provider_profile(profile: dict[str, Any], active_name: str = "") -> dict[str, Any]:
    name = str(profile.get("name") or "").strip() or "未命名"
    provider_type = _normalise_provider_type(profile.get("type") or profile.get("provider"))
    api_key_raw = decode_legacy_secret(profile.get("api_key", ""))
    models = _clean_models(profile.get("models"), profile.get("model"))
    model = str(profile.get("model") or (models[0] if models else "")).strip()
    vision_model = str(
        profile.get("vision_model") or (models[1] if len(models) > 1 else model)
    ).strip()
    return {
        "name": name,
        "provider": provider_type,
        "type": provider_type,
        "api_key_configured": bool(api_key_raw),
        "api_key_preview": _mask_api_key(api_key_raw),
        "base_url": str(profile.get("base_url") or "").strip(),
        "model": model,
        "vision_model": vision_model,
        "models": models,
        "active": name.casefold() == active_name.casefold() if active_name else False,
    }


def _normalise_whisper_model_id(name: str) -> str:
    """Return the model_size value accepted by ``_resolve_model``.

    Local directories are commonly named ``faster-whisper-medium`` while the
    pipeline expects ``medium``.  Hugging Face cache entries use
    ``models--Systran--faster-whisper-medium``.  Normalising in one place keeps
    scan results selectable and prevents paths such as
    ``faster-whisper-faster-whisper-medium`` at runtime.
    """
    value = str(name or "").strip().replace("\\", "/")
    if not value:
        return ""
    value = value.rstrip("/").rsplit("/", 1)[-1]
    for prefix in (
        "models--Systran--faster-whisper-",
        "Systran--faster-whisper-",
        "faster-whisper-",
    ):
        if value.lower().startswith(prefix.lower()):
            value = value[len(prefix):]
            break
    return value.strip()


def _discover_local_whisper_models(raw: dict[str, Any]) -> list[dict[str, str]]:
    """Return only models that the current local resolver can actually open."""
    configured_dir = str(
        raw.get("whisper_model_dir") or raw.get("model_dir") or ""
    ).strip()
    roots: list[tuple[Path, str]] = []
    if configured_dir:
        roots.append((Path(configured_dir).expanduser(), "configured"))

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        roots.append((Path(local_app_data) / "VideoCaptioner" / "AppData" / "models", "videocaptioner"))
    roots.append((Path.home() / "faster-whisper", "home"))

    discovered: dict[str, dict[str, str]] = {}
    seen_roots: set[str] = set()
    for root, source in roots:
        root_key = str(root.resolve(strict=False)).casefold()
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue
                prefixed = entry.name.lower().startswith("faster-whisper-")
                direct_model = any((entry / marker).exists() for marker in ("model.bin", "config.json"))
                if not prefixed and not direct_model:
                    continue
                model_id = _normalise_whisper_model_id(entry.name)
                if not model_id:
                    continue
                discovered[model_id] = {
                    "id": model_id,
                    "path": str(entry.resolve()),
                    "source": source,
                }
        except OSError:
            logger.debug("Unable to scan model directory %s", root, exc_info=True)

    return [discovered[key] for key in sorted(discovered)]


def _scan_whisper_models(raw: dict[str, Any]) -> list[str]:
    """Compatibility scan used by older clients.

    It includes the configured value so existing API consumers keep seeing the
    same contract.  New desktop code uses ``settings.models.local`` to show
    only models that are truly selectable.
    """
    discovered = {item["id"] for item in _discover_local_whisper_models(raw)}
    configured = _normalise_whisper_model_id(str(raw.get("whisper_model") or ""))
    if configured:
        discovered.add(configured)
    return sorted(discovered)


def _provider_models_url(provider_type: str, base_url: str, key: str) -> tuple[str, dict[str, str]]:
    if provider_type == "google_gemini":
        sep = "&" if "?" in base_url else "?"
        return f"{base_url.rstrip('/')}/models{sep}key={key}", {"Accept": "application/json"}
    if provider_type == "anthropic_messages":
        return f"{base_url.rstrip('/')}/models", {"x-api-key": key, "anthropic-version": "2023-06-01", "Accept": "application/json"}
    return f"{base_url.rstrip('/')}/models", {"Authorization": f"Bearer {key}", "Accept": "application/json"}


def _extract_model_ids(provider_type: str, payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw_items = payload.get("data", payload.get("models", []))
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    discovered: list[str] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if isinstance(item, str):
            model = item
        elif isinstance(item, dict):
            model = str(item.get("id") or item.get("name") or "")
        else:
            model = ""
        if provider_type == "google_gemini" and model.startswith("models/"):
            model = model.split("/", 1)[1]
        if model:
            discovered.append(model)
    return discovered


def _test_provider_connection(profile: dict[str, Any]) -> dict[str, Any]:
    provider_type = _normalise_provider_type(profile.get("type") or profile.get("provider"))
    key = decode_legacy_secret(profile.get("api_key") or "").strip()
    if not key:
        return {"success": False, "message": "尚未配置 API Key"}

    base_url = str(profile.get("base_url") or "").strip()
    if not base_url:
        base_url = _DEFAULT_BASE_URLS.get(provider_type, "")
    if not base_url:
        return {"success": False, "message": "必须配置 Base URL"}
    if provider_type == "chatgpt_codex":
        return {"success": False, "message": "ChatGPT Codex (Plus/Pro) 不是可直接调用的 API 端点，不能用于自动笔记任务"}

    url, headers = _provider_models_url(provider_type, base_url, key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status = int(getattr(response, "status", 200))
            if 200 <= status < 300:
                return {"success": True, "message": f"连接成功（HTTP {status}）"}
            return {"success": False, "message": f"服务返回 HTTP {status}"}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            message = "认证失败，请检查 API Key"
        elif exc.code == 404:
            # Some OpenAI-compatible services do not expose /models. A reachable
            # 404 still proves DNS/TLS/routing are healthy; report it honestly.
            message = "服务可达，但未提供 /models 接口（HTTP 404）"
        else:
            message = f"服务返回 HTTP {exc.code}"
        return {"success": False, "message": message}
    except urllib.error.URLError as exc:
        return {"success": False, "message": f"无法连接：{exc.reason}"}
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("Provider connection test failed")
        return {"success": False, "message": str(exc)}



def _discover_provider_models(profile: dict[str, Any]) -> list[str]:
    """Fetch model identifiers from an OpenAI-compatible ``/models`` endpoint.

    The API key is used only for the request and is never returned to the UI.
    Services that do not expose ``/models`` raise a user-facing error so the
    desktop can keep offering manual model entry.
    """
    provider_type = _normalise_provider_type(profile.get("type") or profile.get("provider"))
    key = decode_legacy_secret(profile.get("api_key") or "").strip()
    if not key:
        raise InvalidParams("请先配置 API Key，再读取模型列表")

    base_url = str(profile.get("base_url") or "").strip()
    if not base_url:
        base_url = _DEFAULT_BASE_URLS.get(provider_type, "")
    if not base_url:
        raise InvalidParams("必须配置 Base URL")
    if provider_type == "chatgpt_codex":
        raise InvalidParams("ChatGPT Codex (Plus/Pro) 不是可直接调用的 API 端点，不能读取模型列表")

    url, headers = _provider_models_url(provider_type, base_url, key)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise InvalidParams("认证失败，请检查 API Key") from exc
        if exc.code == 404:
            raise InvalidParams("该服务未提供 /models 接口，请手动输入模型 ID") from exc
        raise InternalError(f"读取模型列表失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise InternalError(f"无法连接模型服务：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise InternalError("模型服务返回了无法解析的 JSON") from exc
    except Exception as exc:  # pragma: no cover - defensive boundary
        logger.exception("Provider model discovery failed")
        raise InternalError(str(exc)) from exc

    discovered = _extract_model_ids(provider_type, payload)
    result = _clean_models(discovered, profile.get("models"), profile.get("model"), profile.get("vision_model"))
    if not result:
        raise InvalidParams("服务已连接，但没有返回可用模型；请手动输入模型 ID")
    return result

def create_settings_handlers() -> dict[str, Any]:
    """Create settings.* handlers used by both legacy and Tauri clients."""

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        active_name = str(raw.get("active_provider") or "").strip()
        providers = [
            _build_provider_profile(profile, active_name)
            for profile in raw.get("providers", [])
            if isinstance(profile, dict)
        ]
        bindings: dict[str, dict[str, Any]] = {}
        for purpose, binding in (raw.get("bindings") or {}).items():
            if isinstance(binding, dict):
                bindings[purpose] = {
                    "provider": binding.get("provider"),
                    "model": binding.get("model"),
                }

        template_id = str(raw.get("template_id") or raw.get("template") or "default")
        model_dir = str(raw.get("whisper_model_dir") or raw.get("model_dir") or "")
        return {
            "output_dir": raw.get("output_dir", "./output"),
            "whisper_model": raw.get("whisper_model", "large-v3"),
            "whisper_model_dir": model_dir,
            "model_dir": model_dir,
            "whisper_device": raw.get("whisper_device", "auto"),
            "whisper_compute_type": raw.get("whisper_compute_type", "auto"),
            "language": raw.get("language", ""),
            "frame_interval": raw.get("frame_interval", 30),
            "frame_mode": raw.get("frame_mode", "fixed"),
            "max_frames": raw.get("max_frames", 30),
            "ocr_enabled": bool(raw.get("ocr_enabled", False)),
            "vision_enabled": bool(raw.get("vision_enabled", False)),
            "template": template_id,
            "template_id": template_id,
            "detail_level": raw.get("detail_level", "standard"),
            "vault_path": raw.get("vault_path", ""),
            "export_mode": raw.get("export_mode", "markdown"),
            "active_provider": active_name,
            "providers": providers,
            "bindings": bindings,
            # Legacy flat fields remain readable during migration.
            "provider": raw.get("provider"),
            "ai_model": raw.get("ai_model"),
            "base_url": raw.get("base_url"),
            "vision_provider": raw.get("vision_provider"),
            "vision_model": raw.get("vision_model"),
            "vision_base_url": raw.get("vision_base_url"),
            "subtitle_format": raw.get("subtitle_format", "none"),
            "bilibili_cookie_file": raw.get("bilibili_cookie_file", raw.get("bilibili_cookies", "")),
        }

    def handle_update(params: dict[str, Any]) -> bool:
        path = _get_settings_path()
        # New UI sends {patches:{...}}; older clients send fields directly.
        patches = params.get("patches", params)
        if not isinstance(patches, dict):
            raise InvalidParams("patches must be an object")
        allowed = {
            "output_dir", "whisper_model", "whisper_model_dir", "model_dir",
            "whisper_device", "whisper_compute_type",
            "language", "frame_interval", "frame_mode", "max_frames",
            "ocr_enabled", "vision_enabled", "template", "template_id",
            "detail_level", "vault_path", "export_mode",
            "provider", "ai_model", "base_url", "vision_provider",
            "vision_model", "vision_base_url", "subtitle_format",
            "bilibili_cookie_file", "bilibili_cookies",
        }
        updates = {key: value for key, value in patches.items() if key in allowed}
        if "template" in updates and "template_id" not in updates:
            updates["template_id"] = updates["template"]
        if "whisper_model_dir" in updates and "model_dir" not in updates:
            updates["model_dir"] = updates["whisper_model_dir"]
        try:
            update_settings(updates, path)
            return True
        except Exception as exc:
            logger.exception("Failed to update settings")
            raise InternalError(str(exc)) from exc

    def handle_secret_set(params: dict[str, Any]) -> bool:
        provider_name = str(params.get("provider") or "").strip()
        api_key = str(params.get("api_key") or params.get("key") or "").strip()
        if not provider_name:
            raise InvalidParams("provider is required")
        if not api_key:
            raise InvalidParams("api_key is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _, profile = _find_provider(raw, provider_name)
        profile["api_key"] = api_key
        update_settings({"providers": raw.get("providers", [])}, path)
        return True

    def handle_secret_delete(params: dict[str, Any]) -> bool:
        provider_name = str(params.get("provider") or "").strip()
        if not provider_name:
            raise InvalidParams("provider is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _, profile = _find_provider(raw, provider_name)
        profile.pop("api_key", None)
        update_settings({"providers": raw.get("providers", [])}, path)
        return True

    def handle_providers_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        raw = _safe_raw_settings(_get_settings_path())
        active_name = str(raw.get("active_provider") or "")
        return [
            _build_provider_profile(profile, active_name)
            for profile in raw.get("providers", [])
            if isinstance(profile, dict)
        ]

    def handle_providers_create(params: dict[str, Any]) -> bool:
        name = str(params.get("name") or "").strip()
        if not name:
            raise InvalidParams("name is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        try:
            _find_provider(raw, name)
        except InvalidParams:
            pass
        else:
            raise InvalidParams(f"Provider '{name}' already exists")

        provider_type = _normalise_provider_type(params.get("provider") or params.get("type"))
        models = _clean_models(params.get("models"), params.get("model"), params.get("vision_model"))
        entry: dict[str, Any] = {
            "name": name,
            "type": provider_type,
            "base_url": str(params.get("base_url") or "").strip(),
            "models": models,
            "model": str(params.get("model") or (models[0] if models else "")).strip(),
            "vision_model": str(params.get("vision_model") or "").strip(),
        }
        api_key = str(params.get("api_key") or "").strip()
        if api_key:
            entry["api_key"] = api_key
        providers = list(raw.get("providers") or [])
        providers.append(entry)
        updates: dict[str, Any] = {"providers": providers}
        if not str(raw.get("active_provider") or "").strip():
            updates["active_provider"] = name
            updates["bindings"] = {
                **(raw.get("bindings") or {}),
                "llm": {"provider": name, "model": entry["model"]},
            }
        update_settings(updates, path)
        return True

    def handle_providers_update(params: dict[str, Any]) -> bool:
        name = str(params.get("name") or "").strip()
        if not name:
            raise InvalidParams("name is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _, profile = _find_provider(raw, name)
        if "provider" in params or "type" in params:
            profile["type"] = _normalise_provider_type(
                params.get("provider") or params.get("type")
            )
        if "base_url" in params:
            profile["base_url"] = str(params.get("base_url") or "").strip()
        current_models = profile.get("models") or []
        model = str(params.get("model") or profile.get("model") or (current_models[0] if current_models else "")).strip()
        vision_model = str(params.get("vision_model") or profile.get("vision_model") or "").strip()
        profile["model"] = model
        profile["vision_model"] = vision_model
        profile["models"] = _clean_models(model, vision_model, current_models)

        bindings = dict(raw.get("bindings") or {})
        for purpose, selected_model in (("llm", model), ("vision", vision_model or model)):
            binding = bindings.get(purpose)
            if isinstance(binding, dict) and str(binding.get("provider") or "").casefold() == name.casefold():
                bindings[purpose] = {"provider": name, "model": selected_model}
        update_settings({"providers": raw.get("providers", []), "bindings": bindings}, path)
        return True

    def handle_providers_delete(params: dict[str, Any]) -> bool:
        name = str(params.get("name") or "").strip()
        if not name:
            raise InvalidParams("name is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _find_provider(raw, name)  # fail loudly when the UI is stale
        providers = [
            profile for profile in raw.get("providers", [])
            if not (
                isinstance(profile, dict)
                and str(profile.get("name") or "").strip().casefold() == name.casefold()
            )
        ]
        bindings = {
            purpose: binding
            for purpose, binding in (raw.get("bindings") or {}).items()
            if not (
                isinstance(binding, dict)
                and str(binding.get("provider") or "").strip().casefold() == name.casefold()
            )
        }
        active_name = str(raw.get("active_provider") or "").strip()
        replacement = ""
        if active_name.casefold() == name.casefold() and providers:
            replacement = str(providers[0].get("name") or "").strip()
            model = str((providers[0].get("models") or [""])[0] or "").strip()
            bindings["llm"] = {"provider": replacement, "model": model}
        update_settings(
            {"providers": providers, "bindings": bindings, "active_provider": replacement if active_name.casefold() == name.casefold() else active_name},
            path,
        )
        return True

    def handle_set_active(params: dict[str, Any]) -> bool:
        name = str(params.get("name") or "").strip()
        if not name:
            raise InvalidParams("name is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _, profile = _find_provider(raw, name)
        models = _clean_models(profile.get("models"), profile.get("model"))
        model = str(profile.get("model") or (models[0] if models else "")).strip()
        vision_model = str(profile.get("vision_model") or (models[1] if len(models) > 1 else model)).strip()
        bindings = dict(raw.get("bindings") or {})
        bindings["llm"] = {"provider": name, "model": model}
        if vision_model:
            bindings["vision"] = {"provider": name, "model": vision_model}
        update_settings({"active_provider": name, "bindings": bindings}, path)
        return True

    def handle_provider_test(params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "").strip()
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        if name:
            _, stored = _find_provider(raw, name)
            profile = dict(stored)
        else:
            profile = {}
        # Permit unsaved form fields to override non-secret connection metadata.
        for key in ("provider", "type", "base_url", "model"):
            if key in params and params[key] is not None:
                profile[key] = params[key]
        return _test_provider_connection(profile)


    def handle_provider_models(params: dict[str, Any]) -> list[str]:
        name = str(params.get("name") or "").strip()
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        if name:
            _, stored = _find_provider(raw, name)
            profile = dict(stored)
        else:
            profile = {}
        for key in ("provider", "type", "base_url", "model", "vision_model"):
            if key in params and params[key] is not None:
                profile[key] = params[key]
        api_key = str(params.get("api_key") or "").strip()
        if api_key:
            profile["api_key"] = api_key
        return _discover_provider_models(profile)

    def handle_templates_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            templates = get_template_registry().list_templates()
        except Exception as exc:
            logger.exception("Failed to load templates")
            raise InternalError(str(exc)) from exc
        return [
            {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "path": f"builtin://{template.id}",
            }
            for template in templates
        ]

    def handle_models_scan(params: dict[str, Any]) -> list[str]:
        return _scan_whisper_models(_safe_raw_settings(_get_settings_path()))

    def handle_models_local(params: dict[str, Any]) -> list[dict[str, str]]:
        return _discover_local_whisper_models(_safe_raw_settings(_get_settings_path()))

    def handle_bindings_set(params: dict[str, Any]) -> bool:
        purpose = str(params.get("purpose") or "").strip()
        if purpose not in ("llm", "vision"):
            raise InvalidParams("purpose must be 'llm' or 'vision'")
        provider = str(params.get("provider") or "").strip()
        if not provider:
            raise InvalidParams("provider is required")
        path = _get_settings_path()
        raw = _safe_raw_settings(path)
        _find_provider(raw, provider)
        bindings = dict(raw.get("bindings") or {})
        bindings[purpose] = {
            "provider": provider,
            "model": str(params.get("model") or "").strip(),
        }
        update_settings({"bindings": bindings}, path)
        return True

    return {
        "settings.get": handle_get,
        "settings.update": handle_update,
        "settings.secret.set": handle_secret_set,
        "settings.secret.delete": handle_secret_delete,
        "settings.providers.list": handle_providers_list,
        "settings.providers.create": handle_providers_create,
        "settings.providers.add": handle_providers_create,  # legacy alias
        "settings.providers.update": handle_providers_update,
        "settings.providers.delete": handle_providers_delete,
        "settings.providers.remove": handle_providers_delete,  # legacy alias
        "settings.providers.set_active": handle_set_active,
        "settings.providers.test": handle_provider_test,
        "settings.providers.models": handle_provider_models,
        "settings.templates.list": handle_templates_list,
        "settings.models.scan": handle_models_scan,
        "settings.models.local": handle_models_local,
        "settings.bindings.set": handle_bindings_set,
    }
