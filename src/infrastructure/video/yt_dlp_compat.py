"""Compatibility patches for yt-dlp integrations used by this app."""

from __future__ import annotations

import http.cookiejar
import http.cookies
import hashlib
import os
import sys
import time
from pathlib import Path


def apply_yt_dlp_compat(url: str | None = None) -> None:
    """Apply narrow yt-dlp runtime patches before creating YoutubeDL."""
    if _is_bilibili_url(url):
        _patch_bilibili_412()


def set_bilibili_cookie_path(cookie_path: str | None) -> None:
    """Set or clear the optional Bilibili cookie file override."""
    if cookie_path:
        os.environ["VIDEO_NOTES_BILIBILI_COOKIES"] = cookie_path
    else:
        os.environ.pop("VIDEO_NOTES_BILIBILI_COOKIES", None)


def _is_bilibili_url(url: str | None) -> bool:
    return bool(url and "bilibili.com" in url.lower())


def _candidate_cookie_paths() -> list[Path]:
    # src/core/video/yt_dlp_compat.py → 3 级 parent 到达项目根目录
    project_root = Path(__file__).resolve().parents[3]
    roots = [Path.cwd(), project_root]
    if getattr(sys, "frozen", False):
        roots.insert(0, Path(sys.executable).resolve().parent)

    seen = set()
    candidates: list[Path] = []
    env_cookie_path = os.getenv("VIDEO_NOTES_BILIBILI_COOKIES")
    if env_cookie_path:
        candidates.append(Path(env_cookie_path).expanduser())

    for root in roots:
        for name in ("cookies.txt", "bilibili_cookies.txt"):
            candidates.append(root / name)
            candidates.append(root / "config" / name)

    paths: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


def _patch_bilibili_412() -> None:
    try:
        from yt_dlp.extractor import bilibili
        from yt_dlp.networking.exceptions import HTTPError
        from yt_dlp.utils import ExtractorError, jwt_decode_hs256, urlencode_postdata
    except Exception:
        return

    base = getattr(bilibili, "BilibiliBaseIE", None)
    if base is None or getattr(base, "_video_notes_ai_bili_412_patch", False):
        return

    original_download_webpage_handle = base._download_webpage_handle
    challenge_cookie = "X-BILI-SEC-TOKEN"
    cache_name = "bilibili_data"
    cache_key = "bili_sec_token"

    def _load_bili_cookies(self):
        if getattr(base, "_video_notes_ai_bili_cookies_loaded", False):
            return

        loaded = 0
        seen_cookies = set()
        for cookie_path in _candidate_cookie_paths():
            if not cookie_path.exists():
                continue
            try:
                jar = http.cookiejar.MozillaCookieJar(str(cookie_path))
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                continue

            for cookie in jar:
                if "bilibili.com" in cookie.domain:
                    key = (cookie.domain.lower(), cookie.name)
                    if key in seen_cookies:
                        continue
                    seen_cookies.add(key)
                    self._set_cookie(cookie.domain, cookie.name, cookie.value)
                    loaded += 1

        if loaded:
            base._video_notes_ai_bili_cookies_loaded = True

    def _bili_challenge_result(self, data, limit=5_000_000):
        try:
            if int(data.get("type")) != 1:
                return False
            final_hash = data.get("r")
            q = data.get("q")
            if not final_hash or not q:
                return None
        except Exception:
            return None

        for i in map(str, range(limit)):
            if hashlib.sha256((q + i).encode()).hexdigest() == final_hash:
                self.to_screen(f"Generated bilibili challenge result {i}")
                return i
        return None

    def _is_jwt_expired(self, token):
        try:
            return jwt_decode_hs256(token)["exp"] - time.time() < 300
        except Exception:
            return True

    def _get_and_set_bili_sec_token(self, token=None, use_cache=False):
        if token:
            if use_cache:
                self.cache.store(cache_name, cache_key, token)
            self._set_cookie("www.bilibili.com", challenge_cookie, token)
            return token

        if use_cache:
            cached = self.cache.load(cache_name, cache_key, default=None)
            if cached:
                cached_token = cached.split(",", 1)[-1]
                if not _is_jwt_expired(self, cached_token):
                    return cached
            return None

        bili_cookie = self._get_cookies("https://www.bilibili.com").get(challenge_cookie)
        if bili_cookie:
            return bili_cookie.value.split(",", 1)[-1]
        return None

    def _token_from_response(error):
        response = getattr(error.cause, "response", None)
        headers = getattr(response, "headers", None)
        if not headers:
            return None

        values = []
        if hasattr(headers, "get_all"):
            values.extend(headers.get_all("Set-Cookie") or [])
        value = headers.get("Set-Cookie")
        if value:
            values.append(value)

        for value in values:
            cookie = http.cookies.SimpleCookie()
            try:
                cookie.load(value)
            except Exception:
                continue
            if challenge_cookie in cookie:
                return cookie[challenge_cookie].value.split(",", 1)[-1]
        return None

    def _download_webpage_handle(self, url_or_request, video_id, note=None, headers=None, data=None, **kwargs):
        _load_bili_cookies(self)
        kwargs.setdefault("impersonate", "chrome")

        try:
            return original_download_webpage_handle(
                self, url_or_request, video_id, note, data=data, headers=headers, **kwargs)
        except ExtractorError as error:
            if not (isinstance(error.cause, HTTPError) and error.cause.status == 412):
                raise

            bili_token = (
                _get_and_set_bili_sec_token(self)
                or _get_and_set_bili_sec_token(self, _token_from_response(error))
            )
            if not bili_token:
                raise

            self.to_screen(f"[{video_id or ''}] Received a bilibili challenge")

            cached_token = _get_and_set_bili_sec_token(self, use_cache=True)
            if cached_token:
                self.to_screen("Using cached bili sec token")
                _get_and_set_bili_sec_token(self, cached_token)
                return original_download_webpage_handle(
                    self, url_or_request, video_id, note, data=data, headers=headers, **kwargs)

            challenge = self._download_json(
                "https://security.bilibili.com/th/captcha/cc/check",
                None,
                "Submitting bilibili challenge",
                errnote="Unable to solve bilibili challenge",
                data=urlencode_postdata({
                    "token": bili_token,
                    "result": _bili_challenge_result(self, jwt_decode_hs256(bili_token)),
                }),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            new_bili_token = challenge.get("message")
            if int(challenge.get("code")) != 0:
                raise ExtractorError(f"Failed to solve bilibili challenge: api says {new_bili_token}")

            _get_and_set_bili_sec_token(self, new_bili_token, use_cache=True)
            return original_download_webpage_handle(
                self, url_or_request, video_id, note, data=data, headers=headers, **kwargs)

    base._load_bili_cookies = _load_bili_cookies
    base.bili_challenge_result = _bili_challenge_result
    base._is_jwt_expired = _is_jwt_expired
    base._get_and_set_bili_sec_token = _get_and_set_bili_sec_token
    base._download_webpage_handle = _download_webpage_handle
    base._video_notes_ai_bili_412_patch = True