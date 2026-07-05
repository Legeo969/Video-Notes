"""Test that conftest optional-dependency mocking tolerates DLL load failures (OSError)."""

import builtins
import sys
from unittest.mock import MagicMock


def test_optional_module_mocker_tolerates_oserror(monkeypatch):
    """_ensure_optional_module_mock should not propagate OSError (e.g. DLL load failed)."""
    import tests.conftest as conftest

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "broken_optional_dep":
            raise OSError("DLL load failed: The specified module could not be found")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    sys.modules.pop("broken_optional_dep", None)

    conftest._ensure_optional_module_mock("broken_optional_dep")

    assert "broken_optional_dep" in sys.modules
    assert isinstance(sys.modules["broken_optional_dep"], MagicMock)
