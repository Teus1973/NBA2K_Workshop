"""
Shared polite HTTP client for every scraper module.

Features:
- Per-host rate limiting (token bucket based on ``config.SCRAPE_RPS``).
- Disk cache of raw responses under ``data/cache/<scope>/<key>.html``.
- Single retry with exponential backoff on 429/5xx.
- Consistent User-Agent from ``config.USER_AGENT``.

Other scrapers use :func:`fetch_text` or :func:`fetch_json` and never touch
``requests`` directly.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    import primp  # type: ignore
    _HAS_PRIMP = True
except ImportError:  # pragma: no cover
    _HAS_PRIMP = False

from .. import config
from ..logger import get_logger

log = get_logger("scrapers.http")

# Primp is a browser-TLS-fingerprint HTTP client (ships with duckduckgo-search).
# Many sports/ratings sites (e.g. 2kratings.com) sit behind Cloudflare which
# rejects vanilla ``requests`` User-Agent strings. primp impersonates Chrome
# end-to-end so our personal-use scrapes succeed. We fall back to ``requests``
# when primp is unavailable.
_primp_client: Any = None


def _get_primp_client() -> Any:
    global _primp_client
    if _primp_client is None and _HAS_PRIMP:
        try:
            # "chrome" (no version) is a supported tag in primp 1.x and
            # makes primp pick a recent Chrome profile automatically.
            _primp_client = primp.Client(impersonate="chrome", verify=True)  # type: ignore
        except Exception as exc:  # pragma: no cover
            log.warning("primp client init failed (%s); falling back to requests", exc)
            _primp_client = False  # sentinel: tried and failed
    return _primp_client if _primp_client else None

_rate_locks: dict[str, threading.Lock] = {}
_rate_last: dict[str, float] = {}
_global_lock = threading.Lock()


def _rate_limit(host: str) -> None:
    """Block until we're allowed to hit ``host`` again (per-host throttle)."""
    if config.SCRAPE_RPS <= 0:
        return
    with _global_lock:
        if host not in _rate_locks:
            _rate_locks[host] = threading.Lock()
    lock = _rate_locks[host]
    min_interval = 1.0 / float(config.SCRAPE_RPS)
    with lock:
        last = _rate_last.get(host, 0.0)
        elapsed = time.time() - last
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _rate_last[host] = time.time()


def _cache_path(scope_dir: Path, key: str, suffix: str) -> Path:
    scope_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)[:160]
    return scope_dir / f"{safe}{suffix}"


def _cache_fresh(path: Path, ttl: int) -> bool:
    if not path.is_file():
        return False
    if ttl <= 0:
        return True
    age = time.time() - path.stat().st_mtime
    return age < ttl


def fetch_text(
    url: str,
    *,
    scope_dir: Path,
    cache_key: str,
    suffix: str = ".html",
    ttl: int | None = None,
    headers: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> tuple[str, bool]:
    """GET ``url`` and return ``(text, from_cache)``.

    - ``scope_dir``: directory under which the response is cached.
    - ``cache_key``: stable slug (no extension).
    - ``ttl``: seconds; defaults to ``config.CACHE_TTL_SECONDS``. 0 = infinite.
    - ``force_refresh``: bypass cache (still writes back).
    """
    ttl_eff = config.CACHE_TTL_SECONDS if ttl is None else ttl
    path = _cache_path(scope_dir, cache_key, suffix)
    if not force_refresh and _cache_fresh(path, ttl_eff):
        return path.read_text(encoding="utf-8", errors="replace"), True

    host = urlparse(url).netloc or "unknown"
    _rate_limit(host)

    # Browser-like default headers. Many sports/ratings sites reject the
    # default ``python-requests/...`` UA outright. The UA can be overridden in
    # ``.env`` via NBA2K_WORKSHOP_USER_AGENT; otherwise we ship a recent
    # Chrome UA string. Rate limiting + cache keep us polite.
    ua = config.USER_AGENT
    if "python-requests" in ua.lower() or ua.startswith("NBA2K-Workshop"):
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if headers:
        h.update(headers)

    def _non_retryable_client_error(exc: BaseException) -> bool:
        """True if this failure should not be backed off and retried."""
        resp = getattr(exc, "response", None)
        code = getattr(resp, "status_code", None)
        if code is not None and 400 <= int(code) < 500:
            return True
        msg = str(exc)
        if " returned HTTP 4" in msg:
            return True
        low = msg.lower()
        if " 404 " in f" {low} " or low.endswith(" 404"):
            return True
        if " 403 " in f" {low} " or low.endswith(" 403"):
            return True
        return False

    client = _get_primp_client()
    last_exc: Exception | None = None
    # Only pass extra headers to primp if the caller explicitly requested some
    # -- primp's impersonation sets the UA and sec-fetch-* headers itself and
    # overriding them breaks the Chrome fingerprint.
    primp_extra = headers or None
    for attempt in (1, 2, 3):
        try:
            if client is not None:
                if primp_extra:
                    resp = client.get(url, headers=primp_extra,
                                      timeout=config.HTTP_TIMEOUT)
                else:
                    resp = client.get(url, timeout=config.HTTP_TIMEOUT)
                status = resp.status_code
                text = resp.text
            else:
                resp = requests.get(url, headers=h, timeout=config.HTTP_TIMEOUT)
                status = resp.status_code
                text = resp.text
            if status == 200 and text:
                try:
                    path.write_text(text, encoding="utf-8")
                except OSError as exc:
                    log.warning("cache write failed for %s: %s", path, exc)
                return text, False
            if status in (429, 500, 502, 503, 504):
                backoff = (2 ** attempt) + random.random()
                log.warning("fetch %s returned %s; backing off %.1fs",
                            url, status, backoff)
                time.sleep(backoff)
                continue
            if 400 <= status < 500:
                raise RuntimeError(f"fetch {url!r} returned HTTP {status}")
            if client is None:
                resp.raise_for_status()
            else:
                raise RuntimeError(f"fetch {url!r} returned HTTP {status}")
        except requests.RequestException as exc:
            last_exc = exc
            if _non_retryable_client_error(exc):
                log.warning("fetch %s failed (%s); not retrying (client error)",
                            url, exc)
                raise
            backoff = (2 ** attempt) + random.random()
            log.warning("fetch %s failed (%s); retry in %.1fs", url, exc, backoff)
            time.sleep(backoff)
        except RuntimeError as exc:
            last_exc = exc
            if _non_retryable_client_error(exc):
                log.warning("fetch %s failed (%s); not retrying (client error)",
                            url, exc)
                raise
            backoff = (2 ** attempt) + random.random()
            log.warning("fetch %s failed (%s); retry in %.1fs", url, exc, backoff)
            time.sleep(backoff)
        except Exception as exc:
            last_exc = exc
            if _non_retryable_client_error(exc):
                log.warning("fetch %s failed (%s); not retrying (client error)",
                            url, exc)
                raise
            backoff = (2 ** attempt) + random.random()
            log.warning("fetch %s failed (%s); retry in %.1fs", url, exc, backoff)
            time.sleep(backoff)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"fetch {url!r} failed after retries")


def fetch_json(
    url: str,
    *,
    scope_dir: Path,
    cache_key: str,
    ttl: int | None = None,
    headers: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> tuple[Any, bool]:
    """Like :func:`fetch_text` but JSON-decodes the body."""
    text, from_cache = fetch_text(
        url,
        scope_dir=scope_dir,
        cache_key=cache_key,
        suffix=".json",
        ttl=ttl,
        headers=headers,
        force_refresh=force_refresh,
    )
    return json.loads(text), from_cache
