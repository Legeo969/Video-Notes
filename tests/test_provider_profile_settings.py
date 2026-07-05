from __future__ import annotations

from pathlib import Path

from src.config.provider_profiles import normalize_provider_settings


def test_removes_unbound_empty_auto_named_profile():
    providers = [
        {
            "name": "阿里云百炼",
            "type": "自定义",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "secret",
            "models": ["deepseek-v4-flash"],
        },
        {
            "name": "新供应商-2",
            "type": "自定义",
            "base_url": "",
            "api_key": "",
            "models": [],
        },
    ]
    bindings = {
        "llm": {"provider": "阿里云百炼", "model": "deepseek-v4-flash"},
        "vision": {"provider": "阿里云百炼", "model": "qwen-vl"},
    }

    cleaned, cleaned_bindings, changed = normalize_provider_settings(providers, bindings)

    assert [item["name"] for item in cleaned] == ["阿里云百炼"]
    assert cleaned_bindings == bindings
    assert changed is True


def test_keeps_configured_auto_named_profile():
    providers = [{
        "name": "新供应商-2",
        "type": "自定义",
        "base_url": "http://127.0.0.1:8080/v1",
        "api_key": "",
        "models": ["local-model"],
    }]

    cleaned, _bindings, changed = normalize_provider_settings(providers, {})

    assert cleaned[0]["name"] == "新供应商-2"
    assert cleaned[0]["base_url"] == "http://127.0.0.1:8080/v1"
    assert changed is False


def test_keeps_referenced_empty_profile_to_avoid_breaking_binding():
    providers = [{
        "name": "新供应商-2",
        "type": "自定义",
        "base_url": "",
        "api_key": "",
        "models": [],
    }]
    bindings = {"llm": {"provider": "新供应商-2", "model": "manual-model"}}

    cleaned, cleaned_bindings, _changed = normalize_provider_settings(providers, bindings)

    assert [item["name"] for item in cleaned] == ["新供应商-2"]
    assert cleaned_bindings["llm"]["provider"] == "新供应商-2"


def test_merges_duplicate_names_and_preserves_data():
    providers = [
        {
            "name": "阿里云百炼",
            "type": "自定义",
            "base_url": "https://example.test/v1",
            "api_key": "",
            "models": ["model-a"],
        },
        {
            "name": "阿里云百炼",
            "type": "自定义",
            "base_url": "",
            "api_key": "secret",
            "models": ["model-a", "model-b"],
        },
    ]

    cleaned, _bindings, changed = normalize_provider_settings(providers, {})

    assert len(cleaned) == 1
    assert cleaned[0]["api_key"] == "secret"
    assert cleaned[0]["models"] == ["model-a", "model-b"]
    assert changed is True


def test_missing_provider_clears_stale_binding():
    cleaned, bindings, changed = normalize_provider_settings(
        [],
        {"llm": {"provider": "不存在", "model": "model"}},
    )

    assert cleaned == []
    assert bindings == {}
    assert changed is True


def test_settings_save_does_not_implicitly_append_profile():
    source = Path("src/gui/pages/settings_page.py").read_text(encoding="utf-8")
    save_body = source.split("    def _save_settings(self):", 1)[1].split(
        "    def _load_settings(self):", 1
    )[0]

    assert "self._commit_selected_profile()" in save_body
    assert "self._save_edit_to_providers()" not in save_body
