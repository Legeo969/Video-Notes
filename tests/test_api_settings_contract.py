from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.api.handlers.diagnostics import create_diagnostics_handlers
from src.api.handlers.settings import create_settings_handlers


@pytest.fixture()
def settings_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Windows' expanduser() prefers USERPROFILE over HOME.  Use the product's
    # explicit settings-path override so this test can never read or mutate a
    # developer's real provider profiles/API keys on any platform.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv(
        "VIDEO_NOTES_SETTINGS_PATH",
        str(tmp_path / ".video-notes-ai" / "settings.json"),
    )
    return tmp_path


def _settings_file(home: Path) -> Path:
    return home / ".video-notes-ai" / "settings.json"


def test_tauri_settings_contract_round_trip(settings_home: Path):
    handlers = create_settings_handlers()

    assert handlers["settings.providers.create"]({
        "name": "Local",
        "provider": "custom",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen-test",
        "vision_model": "qwen-vl-test",
        "api_key": "sk-test-secret",
    }) is True

    profiles = handlers["settings.providers.list"]({})
    assert profiles[0]["provider"] == "openai_compat"
    assert profiles[0]["api_key_configured"] is True
    assert "sk-test-secret" not in json.dumps(profiles)

    assert handlers["settings.update"]({
        "patches": {
            "output_dir": "D:/notes",
            "whisper_model": "small",
            "whisper_model_dir": "D:/models",
            "ocr_enabled": True,
            "template": "study",
        }
    }) is True
    loaded = handlers["settings.get"]({})
    assert loaded["output_dir"] == "D:/notes"
    assert loaded["whisper_model_dir"] == "D:/models"
    assert loaded["model_dir"] == "D:/models"
    assert loaded["ocr_enabled"] is True
    assert loaded["template"] == "study"
    assert loaded["active_provider"] == "Local"
    assert loaded["bindings"]["llm"]["provider"] == "Local"

    assert handlers["settings.providers.update"]({
        "name": "Local",
        "model": "qwen-new",
        "vision_model": "qwen-vl-new",
    }) is True
    assert handlers["settings.providers.set_active"]({"name": "Local"}) is True
    loaded = handlers["settings.get"]({})
    assert loaded["bindings"]["llm"]["model"] == "qwen-new"
    assert loaded["bindings"]["vision"]["model"] == "qwen-vl-new"

    # Stored JSON remains valid after every atomic mutation.
    persisted = json.loads(_settings_file(settings_home).read_text(encoding="utf-8"))
    assert persisted["providers"][0]["api_key"] == "sk-test-secret"


def test_secret_aliases_and_delete(settings_home: Path):
    handlers = create_settings_handlers()
    handlers["settings.providers.add"]({"name": "P", "type": "mimo"})
    handlers["settings.secret.set"]({"provider": "P", "key": "sk-secret-value"})
    assert handlers["settings.providers.list"]({})[0]["api_key_configured"] is True
    handlers["settings.secret.delete"]({"provider": "P"})
    assert handlers["settings.providers.list"]({})[0]["api_key_configured"] is False
    handlers["settings.providers.remove"]({"name": "P"})
    assert handlers["settings.providers.list"]({}) == []


def test_template_and_model_contract(settings_home: Path):
    handlers = create_settings_handlers()
    templates = handlers["settings.templates.list"]({})
    assert len(templates) >= 8
    assert {"id", "name", "description", "path"} <= templates[0].keys()

    handlers["settings.update"]({"whisper_model": "large-v3"})
    models = handlers["settings.models.scan"]({})
    assert "large-v3" in models


def test_provider_test_uses_stored_secret_without_returning_it(settings_home: Path):
    handlers = create_settings_handlers()
    handlers["settings.providers.create"]({
        "name": "P",
        "provider": "openai_compat",
        "base_url": "https://example.test/v1",
        "model": "model",
        "api_key": "sk-secret-value",
    })

    response = type("Response", (), {
        "status": 200,
        "__enter__": lambda self: self,
        "__exit__": lambda self, *args: False,
    })()
    with patch("src.api.handlers.settings.urllib.request.urlopen", return_value=response) as urlopen:
        result = handlers["settings.providers.test"]({"name": "P"})
    assert result["success"] is True
    request = urlopen.call_args.args[0]
    assert request.headers["Authorization"] == "Bearer sk-secret-value"
    assert "sk-secret-value" not in json.dumps(result)


def test_diagnostics_contract_writes_safe_bundle(tmp_path: Path):
    handlers = create_diagnostics_handlers(output_dir=str(tmp_path))
    checks = handlers["doctor.run"]({})
    assert isinstance(checks, list)
    assert checks
    assert all(item["status"] in {"pass", "warn", "fail"} for item in checks)

    path = Path(handlers["diagnostics.bundle"]({"include_env": True}))
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "environment" not in payload
    assert "checks" in payload


def test_provider_models_uses_stored_secret_and_parses_openai_list(settings_home: Path):
    handlers = create_settings_handlers()
    handlers["settings.providers.create"]({
        "name": "P",
        "provider": "openai_compat",
        "base_url": "https://example.test/v1",
        "model": "saved-model",
        "api_key": "sk-secret-value",
    })

    payload = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode("utf-8")
    response = type("Response", (), {
        "status": 200,
        "read": lambda self: payload,
        "__enter__": lambda self: self,
        "__exit__": lambda self, *args: False,
    })()
    with patch("src.api.handlers.settings.urllib.request.urlopen", return_value=response) as urlopen:
        models = handlers["settings.providers.models"]({"name": "P"})

    assert models[:2] == ["model-a", "model-b"]
    assert "saved-model" in models
    request = urlopen.call_args.args[0]
    assert request.headers["Authorization"] == "Bearer sk-secret-value"


def test_local_whisper_models_are_normalized_and_selectable(settings_home: Path):
    handlers = create_settings_handlers()
    model_root = settings_home / "models"
    (model_root / "faster-whisper-large-v3-turbo").mkdir(parents=True)
    (model_root / "faster-whisper-tiny").mkdir()
    direct_medium = model_root / "medium"
    direct_medium.mkdir()
    (direct_medium / "config.json").write_text("{}", encoding="utf-8")

    handlers["settings.update"]({
        "whisper_model_dir": str(model_root),
        "whisper_model": "medium",
    })

    local = handlers["settings.models.local"]({})
    assert [item["id"] for item in local] == ["large-v3-turbo", "medium", "tiny"]
    paths = {item["id"]: item["path"] for item in local}
    assert paths["large-v3-turbo"].endswith("faster-whisper-large-v3-turbo")
    assert paths["tiny"].endswith("faster-whisper-tiny")
    assert paths["medium"].endswith("medium")
    # Compatibility endpoint still includes the configured model.
    assert "medium" in handlers["settings.models.scan"]({})
