"""Utilities for normalizing provider profiles stored in settings.

The GUI keeps provider profiles as user-editable dictionaries.  Older builds could
silently append an empty auto-named profile whenever settings were saved.  These
helpers keep that migration logic independent from Qt so it is easy to test.
"""

from __future__ import annotations

import re
from typing import Any

_AUTO_PLACEHOLDER_RE = re.compile(r"^新供应商-\d+$")


def _clean_models(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        model = str(item or "").strip()
        key = model.casefold()
        if model and key not in seen:
            seen.add(key)
            result.append(model)
    return result


def _clean_binding(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"provider": "", "model": ""}
    return {
        "provider": str(value.get("provider") or "").strip(),
        "model": str(value.get("model") or "").strip(),
    }


def normalize_provider_settings(
    providers: Any,
    bindings: Any,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], bool]:
    """Normalize providers/bindings and remove empty phantom profiles.

    Returns ``(providers, bindings, changed)``.

    A profile is considered a phantom only when all of the following are true:
    - its name matches the old automatically generated form ``新供应商-N``;
    - it has no URL, API key or models;
    - no binding references it.

    Duplicate names are merged case-insensitively while preserving the first
    display name and any non-empty data found in later copies.
    """

    raw_bindings = bindings if isinstance(bindings, dict) else {}
    cleaned_bindings: dict[str, dict[str, str]] = {
        "llm": _clean_binding(raw_bindings.get("llm")),
        "vision": _clean_binding(raw_bindings.get("vision")),
    }
    referenced = {
        item["provider"]
        for item in cleaned_bindings.values()
        if item.get("provider")
    }

    raw_providers = providers if isinstance(providers, list) else []
    cleaned: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}

    for raw in raw_providers:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        base_url = str(raw.get("base_url") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        models = _clean_models(raw.get("models"))
        ptype = str(raw.get("type") or "自定义").strip() or "自定义"

        is_phantom = (
            bool(_AUTO_PLACEHOLDER_RE.fullmatch(name))
            and not base_url
            and not api_key
            and not models
            and name not in referenced
        )
        if is_phantom:
            continue

        entry = {
            "name": name,
            "type": ptype,
            "base_url": base_url,
            "api_key": api_key,
            "models": models,
        }
        key = name.casefold()
        if key not in index_by_key:
            index_by_key[key] = len(cleaned)
            cleaned.append(entry)
            continue

        # Merge duplicate rows without discarding configured values.
        existing = cleaned[index_by_key[key]]
        if not existing.get("base_url") and base_url:
            existing["base_url"] = base_url
        if not existing.get("api_key") and api_key:
            existing["api_key"] = api_key
        if existing.get("type") in {"", "自定义"} and ptype not in {"", "自定义"}:
            existing["type"] = ptype
        known = {str(m).casefold() for m in existing.get("models", [])}
        for model in models:
            if model.casefold() not in known:
                existing.setdefault("models", []).append(model)
                known.add(model.casefold())

    canonical_names = {p["name"].casefold(): p["name"] for p in cleaned}
    for binding in cleaned_bindings.values():
        provider_name = binding.get("provider", "")
        if not provider_name:
            continue
        canonical = canonical_names.get(provider_name.casefold())
        if canonical:
            binding["provider"] = canonical
        else:
            binding["provider"] = ""
            binding["model"] = ""

    # Avoid writing empty binding records back when the source did not contain them.
    cleaned_bindings = {
        key: value
        for key, value in cleaned_bindings.items()
        if value.get("provider") or value.get("model")
    }

    changed = cleaned != raw_providers or cleaned_bindings != raw_bindings
    return cleaned, cleaned_bindings, changed
