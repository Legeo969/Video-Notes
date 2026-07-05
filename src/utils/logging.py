"""Logging helpers for CLI and GUI usage."""

import logging
import re
import sys
from collections.abc import Callable
from typing import Optional

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given name.

    Args:
        name: Logger name (typically module name).

    Returns:
        logging.Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    return logger


def get_gui_logger(name: str, callback: Optional[Callable[[str], None]] = None) -> logging.Logger:
    """Get a logger for GUI usage that forwards messages to callback.

    Args:
        name: Logger name.
        callback: Optional callback function(message: str) for GUI output.

    Returns:
        logging.Logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if callback:
        logger.handlers.clear()

        class CallbackHandler(logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                callback(msg)

        handler = CallbackHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.propagate = False

    return logger


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*.

    Args:
        text: Input string that may contain ANSI control codes.

    Returns:
        Clean string with all ANSI sequences removed.
    """
    return _ANSI_ESCAPE.sub("", text)


_SENSITIVE_URL_PARAMS = frozenset({"vd_source", "token", "sign", "api_key", "apikey", "secret", "access_token"})


def sanitize_url(url: str) -> str:
    """Remove sensitive or noisy query parameters from a URL.

    Strips parameters whose names (case-insensitive) appear in the
    *SENSITIVE_URL_PARAMS* set.  Fragments are preserved.

    Args:
        url: Full URL to sanitize.

    Returns:
        URL with matching query parameters removed.
    """
    if "?" not in url:
        return url

    base, _, query = url.partition("?")
    fragment = ""
    if "#" in query:
        query, _, fragment = query.partition("#")

    cleaned = []
    for part in query.split("&"):
        if "=" in part:
            key = part.split("=", 1)[0].lower()
            if key in _SENSITIVE_URL_PARAMS:
                continue
        cleaned.append(part)

    result = base + "?" + "&".join(cleaned)
    if fragment:
        result += "#" + fragment
    return result


_API_KEY_PATTERN = re.compile(r"(sk-)[A-Za-z0-9]{20,}")


def sanitize_api_key(text: str) -> str:
    """Replace long ``sk-`` prefixed API keys with a masked placeholder.

    Matches ``sk-`` followed by 20 or more alphanumeric characters
    and replaces the suffix with ``****``.

    Args:
        text: Input text that may contain API keys.

    Returns:
        Text with API key values masked.
    """
    return _API_KEY_PATTERN.sub(r"\1****", text)


def set_third_party_log_levels() -> None:
    """Raise log level to WARNING for verbose third-party libraries.

    Call once during application startup (e.g. from ``bootstrap.py``
    or ``main.py``) to suppress INFO/DEBUG noise from dependencies.
    """
    for name in ("faiss", "paddle", "paddlex", "urllib3", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)