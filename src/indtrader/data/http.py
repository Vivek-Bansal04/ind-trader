"""Shared plain-HTTP transport for the non-NSE-lib feeds (news RSS, macro calendar).

Round-5 live lesson: the MacBook's network (and NSE's WAF) rejects python-urllib's
default TLS fingerprint/UA — the news layer returned 100 items from the office box
and 0 from the laptop, which is exactly the "dead feed dressed as quiet" failure
this repo forbids. The confirmed-working recipe there is curl_cffi's Chrome
impersonation (the macro-calendar research used it), so: curl_cffi when installed,
plain urllib otherwise, and `transport()` tells the caller WHICH one ran — a
fetch result without its transport named is half a result.

Errors come back as strings, never exceptions: callers must be able to tell
"unreachable" from "empty" without a try/except dance.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
TIMEOUT_S = 15


def transport() -> str:
    """Which transport http_get will actually use ('curl_cffi' or 'urllib')."""
    try:
        import curl_cffi.requests  # noqa: F401
        return "curl_cffi"
    except ImportError:
        return "urllib"


def http_get(url: str, referer: str | None = None,
             accept: str = "*/*") -> tuple[bytes | None, str | None]:
    """GET -> (body, error). Exactly one side is set. curl_cffi with Chrome
    impersonation when present (the only transport proven through this WAF);
    urllib fallback so nothing crashes where curl_cffi isn't installed."""
    headers = {"User-Agent": UA, "Accept": accept,
               "Accept-Language": "en-IN,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    try:
        import curl_cffi.requests as cr
    except ImportError:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
                return r.read(), None
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError) as e:
            return None, f"{type(e).__name__}: {str(e)[:120]}"
    try:
        r = cr.get(url, headers=headers, impersonate="chrome", timeout=TIMEOUT_S)
    except Exception as e:  # curl_cffi raises its own CurlError family
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    return r.content, None


def get_json(url: str, referer: str | None = None) -> tuple[object | None, str | None]:
    """GET + json.loads -> (obj, error). Malformed JSON is an error string, not
    a None that could be mistaken for 'no data'."""
    body, err = http_get(url, referer=referer, accept="application/json, */*")
    if err:
        return None, err
    try:
        return json.loads(body.decode("utf-8", "replace")), None
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"JSON parse: {type(e).__name__}: {str(e)[:100]}"
