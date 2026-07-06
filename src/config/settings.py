"""Settings path helpers for the existing GUI settings JSON file."""

import json
import os
import base64

from src.config.constants import DEFAULT_SETTINGS_DIRNAME, DEFAULT_SETTINGS_FILENAME


def get_default_export_dir() -> str:
    """Return a stable user-visible export directory.

    Desktop sidecars run with an AppData working directory, so a relative
    ``./output`` default would create invisible products under AppData.
    CLI callers can still pass ``--output ./output`` explicitly.
    """
    override = os.environ.get("VIDEO_NOTES_DEFAULT_OUTPUT_DIR", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    if os.name == "nt":
        home = os.path.expanduser("~")
        return os.path.join(home, "Documents", "Video Notes AI", "exports")
    return os.path.join(os.path.expanduser("~"), "Video Notes AI", "exports")


def get_settings_path() -> str:
    """Return the per-user settings JSON path.

    ``VIDEO_NOTES_SETTINGS_PATH`` is an explicit override for tests, portable
    builds and managed deployments.  It avoids relying on platform-specific
    ``HOME``/``USERPROFILE`` expansion rules while preserving the normal
    per-user location when no override is configured.
    """
    override = os.environ.get("VIDEO_NOTES_SETTINGS_PATH", "").strip()
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    return os.path.join(
        os.path.expanduser("~"),
        DEFAULT_SETTINGS_DIRNAME,
        DEFAULT_SETTINGS_FILENAME,
    )


def load_settings(settings_path: str) -> dict:
    """Load settings from a JSON file.

    Returns the parsed dict, or an empty dict if the file
    does not exist or cannot be parsed.
    """
    if not os.path.exists(settings_path):
        return {}
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def get_setting(key: str, default, settings_path: str):
    """Return a single setting value from the JSON file.

    Returns *default* when the key is absent or the file
    cannot be read.
    """
    data = load_settings(settings_path)
    return data.get(key, default)


def decode_legacy_secret(value) -> str:
    """Return raw secret text, decoding old base64-stored settings when needed."""
    if not value:
        return ""
    text = str(value)
    if text.startswith(("sk-", "sk_")):
        return text
    try:
        padded = text + "=" * ((4 - len(text) % 4) % 4)
        decoded = base64.b64decode(padded, validate=False).decode("utf-8")
    except Exception:
        return text
    if not decoded or any(ord(ch) < 32 and ch not in "\r\n\t" for ch in decoded):
        return text
    if not decoded.startswith(("sk-", "sk_")):
        return text
    if base64.b64encode(decoded.encode("utf-8")).decode("ascii").rstrip("=") == text.rstrip("="):
        return decoded
    return text


def get_env_api_key_for_provider(provider: str | None) -> str:
    """Return the first non-empty API key env var matching the provider."""
    name = (provider or "").strip().lower()
    custom_name = "\u81ea\u5b9a\u4e49"
    if name == "mimo":
        candidates = ("MIMO_API_KEY",)
    elif name in {"bailian", "dashscope"}:
        candidates = ("DASHSCOPE_API_KEY",)
    elif name in {custom_name, "custom", "openai_compat"}:
        candidates = ("OPENAI_API_KEY", "MIMO_API_KEY", "DASHSCOPE_API_KEY")
    else:
        candidates = ("MIMO_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")

    for key in candidates:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def resolve_provider_binding_from_settings(settings: dict, purpose: str = "llm") -> dict:
    """Resolve one configured provider binding into a runtime profile.

    New provider profiles/bindings are authoritative. Legacy flat fields are
    used only as a compatibility fallback. The returned mapping always contains
    ``name``, ``type``, ``model``, ``base_url`` and ``api_key``.
    """
    purpose = "vision" if purpose == "vision" else "llm"
    providers = settings.get("providers") or []
    bindings = settings.get("bindings") or {}
    binding = bindings.get(purpose) if isinstance(bindings, dict) else {}
    binding = binding if isinstance(binding, dict) else {}
    bound_name = str(binding.get("provider") or "").strip()
    bound_model = str(binding.get("model") or "").strip()

    selected = None
    if bound_name:
        for profile in providers:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name") or "").strip()
            if name.casefold() == bound_name.casefold():
                selected = profile
                break

    if selected is not None:
        provider_type = str(selected.get("type") or "自定义").strip() or "自定义"
        api_key = decode_legacy_secret(selected.get("api_key") or "").strip()
        if not api_key:
            api_key = get_env_api_key_for_provider(provider_type)
        return {
            "name": str(selected.get("name") or bound_name).strip(),
            "type": provider_type,
            "model": bound_model,
            "base_url": str(selected.get("base_url") or "").strip(),
            "api_key": api_key,
        }

    if purpose == "vision":
        provider_type = str(settings.get("vision_provider") or "").strip()
        model = (
            str(settings.get("vision_custom_model") or "").strip()
            if provider_type == "自定义"
            else str(settings.get("vision_model") or "").strip()
        )
        base_url = str(settings.get("vision_base_url") or "").strip()
        api_key = decode_legacy_secret(settings.get("vision_api_key") or "").strip()
    else:
        provider_type = str(settings.get("provider") or "").strip()
        model = (
            str(settings.get("custom_model") or "").strip()
            if provider_type == "自定义"
            else str(settings.get("ai_model") or "").strip()
        )
        base_url = str(settings.get("base_url") or "").strip()
        api_key = decode_legacy_secret(settings.get("api_key") or "").strip()

    # Old settings may have no binding at all. In that case, fall back to the
    # first complete provider profile instead of silently starting with no key.
    if not provider_type and not bound_name:
        for profile in providers:
            if not isinstance(profile, dict):
                continue
            name = str(profile.get("name") or "").strip()
            if not name:
                continue
            provider_type = str(profile.get("type") or "自定义").strip() or "自定义"
            profile_models = profile.get("models") or []
            model = str(profile_models[0] if profile_models else "").strip()
            base_url = str(profile.get("base_url") or "").strip()
            api_key = decode_legacy_secret(profile.get("api_key") or "").strip()
            bound_name = name
            break

    if not api_key:
        api_key = get_env_api_key_for_provider(provider_type)
    return {
        "name": bound_name,
        "type": provider_type,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    }


def resolve_api_key_from_settings(settings: dict) -> str | None:
    """Resolve the API key for the provider bound to note generation."""
    return resolve_provider_binding_from_settings(settings, "llm").get("api_key") or None


def resolve_vision_api_key_from_settings(settings: dict) -> str | None:
    """Resolve the API key for the provider bound to visual understanding."""
    return resolve_provider_binding_from_settings(settings, "vision").get("api_key") or None


def validate_ai_configuration(settings: dict) -> tuple[bool, str]:
    """Validate runtime AI bindings before media download/transcription starts."""
    llm = resolve_provider_binding_from_settings(settings, "llm")
    label = llm.get("name") or llm.get("type") or "生成笔记"
    if not llm.get("type"):
        return False, "请先在设置页选择用于“生成笔记”的供应商和模型。"
    if not llm.get("api_key"):
        return (
            False,
            f"供应商“{label}”尚未配置 API Key。\n"
            "请在设置页保存 API Key，并确认它已绑定到“生成笔记”用途。",
        )

    if settings.get("vision_enabled"):
        vision = resolve_provider_binding_from_settings(settings, "vision")
        vlabel = vision.get("name") or vision.get("type") or "视觉理解"
        if not vision.get("type"):
            return False, "已启用视觉理解，但尚未选择对应供应商和模型。"
        if not vision.get("api_key"):
            return (
                False,
                f"视觉供应商“{vlabel}”尚未配置 API Key。\n"
                "请在设置页保存 API Key，并确认它已绑定到“视觉理解”用途。",
            )
    return True, ""


def update_settings(
    data: dict,
    settings_path: str,
    remove_keys: list | None = None,
) -> None:
    """Merge *data* into an existing settings JSON file.

    Keys listed in *remove_keys* are deleted from the merged result
    before writing.  The file is created if it does not exist.
    """
    existing = load_settings(settings_path)
    existing.update(data)

    if remove_keys:
        for k in remove_keys:
            existing.pop(k, None)

    os.makedirs(os.path.dirname(settings_path), exist_ok=True)

    # 原子写入：先写临时文件，flush + fsync，再 os.replace
    tmp_path = settings_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, settings_path)
    except Exception:
        # 清理临时文件，避免残留
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
