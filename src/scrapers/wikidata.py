"""Structured birth dates from Wikidata (Sports-Reference CBB no longer exposes DOB in HTML)."""

from __future__ import annotations

import re
import threading
import time
from typing import Any

import requests

from .. import config
from ..logger import get_logger

log = get_logger("scrapers.wikidata")

_WP_API = "https://en.wikipedia.org/w/api.php"
_WD_API = "https://www.wikidata.org/w/api.php"

_LOCK = threading.Lock()
_LAST_MONO = 0.0
_MIN_INTERVAL_SEC = 0.55


def _throttle() -> None:
    global _LAST_MONO
    with _LOCK:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SEC - (now - _LAST_MONO)
        if wait > 0:
            time.sleep(wait)
        _LAST_MONO = time.monotonic()


def _ua() -> str:
    base = (config.USER_AGENT or "").strip()
    if not base or "python-requests" in base.lower():
        base = "NBA2K-Workshop/1.0"
    return f"{base} (NBA2K Workshop; Wikidata DOB lookup)"


def _wp_opensearch_first_title(query: str) -> str | None:
    _throttle()
    try:
        r = requests.get(
            _WP_API,
            params={
                "action": "opensearch",
                "search": query,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            timeout=12.0,
            headers={"User-Agent": _ua()},
        )
    except requests.RequestException as exc:
        log.info("wikidata: opensearch failed for %r: %s", query, exc)
        return None
    if r.status_code != 200:
        log.info("wikidata: opensearch HTTP %s", r.status_code)
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if len(data) < 2 or not data[1]:
        return None
    title = data[1][0]
    return str(title).strip() if title else None


def _wikidata_time_to_iso(val: dict[str, Any]) -> str | None:
    raw_t = val.get("time") or ""
    if isinstance(raw_t, str) and raw_t.startswith("+"):
        raw_t = raw_t[1:]
    prec = int(val.get("precision") or 11)
    if prec >= 11:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", raw_t)
        return m.group(1) if m else None
    if prec == 10:
        # Month precision: Wikidata uses ``YYYY-MM-00`` or ``YYYY-MM-01`` — one group if we
        # match ``YYYY-MM`` only.
        m = re.match(r"(\d{4})-(\d{2})", raw_t)
        if m:
            return f"{m.group(1)}-{m.group(2)}-01"
        return None
    if prec == 9:
        m = re.match(r"(\d{4})", raw_t)
        return f"{m.group(1)}-07-01" if m else None
    return None


def birth_date_iso_from_enwiki_title(title: str) -> str | None:
    """Resolve ``title`` on English Wikipedia → Wikidata entity → ``P569`` ISO date."""
    title = title.strip()
    if len(title) < 2:
        return None
    _throttle()
    try:
        r = requests.get(
            _WD_API,
            params={
                "action": "wbgetentities",
                "sites": "enwiki",
                "titles": title,
                "format": "json",
                "props": "claims",
            },
            timeout=15.0,
            headers={"User-Agent": _ua()},
        )
    except requests.RequestException as exc:
        log.info("wikidata: wbgetentities failed for %r: %s", title, exc)
        return None
    if r.status_code != 200:
        log.info("wikidata: wbgetentities HTTP %s", r.status_code)
        return None
    try:
        payload = r.json()
    except ValueError:
        return None
    entities = payload.get("entities") or {}
    for ent in entities.values():
        if not isinstance(ent, dict):
            continue
        claims = ent.get("claims") or {}
        p569 = claims.get("P569") or []
        if not p569:
            continue
        mainsnak = (p569[0] or {}).get("mainsnak") or {}
        if mainsnak.get("snaktype") != "value":
            continue
        datavalue = mainsnak.get("datavalue") or {}
        if datavalue.get("type") != "time":
            continue
        inner = datavalue.get("value") or {}
        iso = _wikidata_time_to_iso(inner if isinstance(inner, dict) else {})
        if iso:
            return iso
    return None


def birth_date_iso_for_person(full_name: str) -> str | None:
    """Best-effort birth date for ``full_name`` via Wikipedia search → Wikidata."""
    q = (full_name or "").strip()
    if len(q) < 3:
        return None
    for query in (f"{q} basketball", q):
        title = _wp_opensearch_first_title(query)
        if not title:
            continue
        bd = birth_date_iso_from_enwiki_title(title)
        if bd:
            return bd
    return None
