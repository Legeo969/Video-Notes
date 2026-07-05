"""CLI package — backward-compat re-exports only.

Command modules imported lazily to avoid pulling heavy deps (dotenv,
ctranslate2, etc.) at package init time.
"""

import importlib

from src.app.cli.main import main


_BACKWARD_COMPAT_SYMBOLS = {
    "_cmd_doctor": "src.app.cli.commands.diagnostics",
    "_cmd_issue_bundle": "src.app.cli.commands.diagnostics",
    "_cmd_template_list": "src.app.cli.commands.templates",
    "_get_job_collections": "src.app.cli.commands.collections",
    "_get_provenance_status_for_display": "src.app.cli.commands.provenance",
}

__all__ = [*list(_BACKWARD_COMPAT_SYMBOLS), "main"]


def __getattr__(name):
    if name in _BACKWARD_COMPAT_SYMBOLS:
        mod = importlib.import_module(_BACKWARD_COMPAT_SYMBOLS[name])
        return getattr(mod, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
