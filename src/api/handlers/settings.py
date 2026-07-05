"""settings.* RPC 处理器

使用 ~/.video-notes-ai/settings.json 进行持久化存储。
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any

from src.config.constants import DEFAULT_SETTINGS_DIRNAME, DEFAULT_SETTINGS_FILENAME
from src.config.settings import load_settings, update_settings, decode_legacy_secret
from src.api.protocol.errors import InvalidParams, InternalError

logger = logging.getLogger(__name__)


def _get_settings_path() -> str:
    return os.path.join(
        os.path.expanduser("~"),
        DEFAULT_SETTINGS_DIRNAME,
        DEFAULT_SETTINGS_FILENAME,
    )


def _mask_api_key(key: str) -> str:
    """脱敏 API Key，仅保留前缀和后4位。"""
    if not key or len(key) < 8:
        return ""
    return key[:5] + "****" + key[-4:]


def _build_provider_profile(profile: dict) -> dict[str, Any]:
    """从 settings 中的 provider profile dict 构建安全响应。"""
    name = str(profile.get("name", "")).strip()
    provider_type = str(profile.get("type", "custom")).strip()
    api_key_raw = decode_legacy_secret(profile.get("api_key", ""))
    return {
        "name": name or "未命名",
        "provider": provider_type,
        "api_key_configured": bool(api_key_raw),
        "api_key_preview": _mask_api_key(api_key_raw) if api_key_raw else "",
        "base_url": str(profile.get("base_url") or "").strip() or None,
        "model": str(profile.get("models", [None])[0]).strip() if profile.get("models") else None,
        "models": profile.get("models", []),
    }


def create_settings_handlers() -> dict[str, Any]:
    """创建 settings.* 方法处理器字典。"""

    def handle_get(params: dict[str, Any]) -> dict[str, Any]:
        """settings.get — 读取完整设置。"""
        path = _get_settings_path()
        raw = load_settings(path)

        # 构建 providers 列表
        providers_raw = raw.get("providers", [])
        providers = [_build_provider_profile(p) for p in providers_raw]

        # 构建 bindings
        bindings_raw = raw.get("bindings", {})
        bindings = {}
        for purpose, binding in bindings_raw.items():
            if isinstance(binding, dict):
                bindings[purpose] = {
                    "provider": binding.get("provider"),
                    "model": binding.get("model"),
                }

        return {
            "output_dir": raw.get("output_dir", "./output"),
            "whisper_model": raw.get("whisper_model", "large-v3"),
            "providers": providers,
            "bindings": bindings,
            # 旧格式扁平字段（向后兼容）
            "provider": raw.get("provider"),
            "ai_model": raw.get("ai_model"),
            "base_url": raw.get("base_url"),
            "vision_enabled": raw.get("vision_enabled", False),
            "vision_provider": raw.get("vision_provider"),
            "vision_model": raw.get("vision_model"),
            "vision_base_url": raw.get("vision_base_url"),
            "subtitle_format": raw.get("subtitle_format", "none"),
        }

    def handle_update(params: dict[str, Any]) -> bool:
        """settings.update — 更新设置。"""
        path = _get_settings_path()

        # 只更新传入的字段
        updates = {}
        for key in (
            "output_dir", "whisper_model",
            "provider", "ai_model", "base_url",
            "vision_enabled", "vision_provider", "vision_model", "vision_base_url",
            "subtitle_format",
        ):
            if key in params:
                updates[key] = params[key]

        try:
            update_settings(updates, path)
            return True
        except Exception as e:
            logger.exception("Failed to update settings")
            raise InternalError(str(e))

    def handle_secret_set(params: dict[str, Any]) -> bool:
        """settings.secret.set — 设置 API Key。"""
        provider_name = params.get("provider", "").strip()
        api_key = params.get("api_key", "").strip()
        if not provider_name:
            raise InvalidParams("provider is required")
        if not api_key:
            raise InvalidParams("api_key is required")

        path = _get_settings_path()
        raw = load_settings(path)
        providers = raw.get("providers", [])
        found = False
        for p in providers:
            if isinstance(p, dict) and p.get("name", "").strip() == provider_name:
                p["api_key"] = api_key
                found = True
                break
        if not found:
            # 如果 provider 不存在，追加
            providers.append({"name": provider_name, "api_key": api_key})
        raw["providers"] = providers

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.exception("Failed to set API key")
            raise InternalError(str(e))

    def handle_secret_delete(params: dict[str, Any]) -> bool:
        """settings.secret.delete — 删除 API Key。"""
        provider_name = params.get("provider", "").strip()
        if not provider_name:
            raise InvalidParams("provider is required")

        path = _get_settings_path()
        raw = load_settings(path)
        providers = raw.get("providers", [])
        for p in providers:
            if isinstance(p, dict) and p.get("name", "").strip() == provider_name:
                p.pop("api_key", None)
                break

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.exception("Failed to delete API key")
            raise InternalError(str(e))

    def handle_providers_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        """settings.providers.list — 列出所有供应商。"""
        path = _get_settings_path()
        raw = load_settings(path)
        providers_raw = raw.get("providers", [])
        return [_build_provider_profile(p) for p in providers_raw]

    def handle_providers_add(params: dict[str, Any]) -> bool:
        """settings.providers.add — 添加供应商。"""
        name = params.get("name", "").strip()
        if not name:
            raise InvalidParams("name is required")

        path = _get_settings_path()
        raw = load_settings(path)
        providers = raw.setdefault("providers", [])

        # 检查是否已存在
        for p in providers:
            if isinstance(p, dict) and p.get("name", "").strip() == name:
                raise InvalidParams(f"Provider '{name}' already exists")

        entry = {
            "name": name,
            "type": params.get("type", "custom"),
        }
        if "api_key" in params:
            entry["api_key"] = params["api_key"]
        if "base_url" in params:
            entry["base_url"] = params["base_url"]
        if "models" in params:
            entry["models"] = params["models"]

        providers.append(entry)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.exception("Failed to add provider")
            raise InternalError(str(e))

    def handle_providers_remove(params: dict[str, Any]) -> bool:
        """settings.providers.remove — 删除供应商。"""
        name = params.get("name", "").strip()
        if not name:
            raise InvalidParams("name is required")

        path = _get_settings_path()
        raw = load_settings(path)
        providers = raw.get("providers", [])
        raw["providers"] = [
            p for p in providers
            if not (isinstance(p, dict) and p.get("name", "").strip() == name)
        ]

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.exception("Failed to remove provider")
            raise InternalError(str(e))

    def handle_templates_list(params: dict[str, Any]) -> list[dict[str, Any]]:
        """settings.templates.list — 列出可用笔记模板。"""
        template_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "templates")
        template_dir = os.path.abspath(template_dir)

        if not os.path.isdir(template_dir):
            return []

        templates = []
        for fname in sorted(os.listdir(template_dir)):
            if not fname.endswith((".yaml", ".yml")):
                continue
            fpath = os.path.join(template_dir, fname)
            try:
                import yaml
                with open(fpath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    templates.append({
                        "id": data.get("id", fname),
                        "name": data.get("name", fname),
                        "description": data.get("description", ""),
                        "file": fname,
                    })
            except Exception as e:
                logger.warning("Failed to load template %s: %s", fname, e)
                templates.append({
                    "id": fname,
                    "name": fname,
                    "description": "",
                    "file": fname,
                })
        return templates

    def handle_bindings_set(params: dict[str, Any]) -> bool:
        """settings.bindings.set — 设置用途绑定。"""
        purpose = params.get("purpose", "").strip()
        if not purpose:
            raise InvalidParams("purpose is required (llm|vision)")
        if purpose not in ("llm", "vision"):
            raise InvalidParams("purpose must be 'llm' or 'vision'")

        path = _get_settings_path()
        raw = load_settings(path)
        bindings = raw.setdefault("bindings", {})
        bindings[purpose] = {
            "provider": params.get("provider"),
            "model": params.get("model"),
        }

        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.exception("Failed to set binding")
            raise InternalError(str(e))

    return {
        "settings.get": handle_get,
        "settings.update": handle_update,
        "settings.secret.set": handle_secret_set,
        "settings.secret.delete": handle_secret_delete,
        "settings.providers.list": handle_providers_list,
        "settings.providers.add": handle_providers_add,
        "settings.providers.remove": handle_providers_remove,
        "settings.templates.list": handle_templates_list,
        "settings.bindings.set": handle_bindings_set,
    }
