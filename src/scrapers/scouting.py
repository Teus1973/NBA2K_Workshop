"""
Scouting-report ingestion + keyword-based rating modulation.

Two moving parts:

1. ``fetch_blurbs(prospect)`` -- given a prospect slug / name, return a list of
   scouting-report paragraphs. Primary source is the ESPN big-board HTML we
   already cache; optional DuckDuckGo search (``duckduckgo-search`` package)
   pulls 2-3 more blurbs per prospect from espn.com / nbadraft.net /
   theringer.com / sports-reference.com.

2. ``modulate_ratings(prospect, ratings, blurbs)`` -- apply the keyword table
   at ``data/scouting_keywords.yaml`` to bump/nerf specific 2K attributes.
   Every keyword hit writes a row to ``audit_log`` so the user can see which
   phrase changed which rating.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.scouting")


KEYWORDS_PATH = config.DATA_DIR / "scouting_keywords.yaml"


# ---------------------------------------------------------------------------
@dataclass
class KeywordRule:
    phrase: str
    deltas: dict[str, int]


def load_keyword_rules(path: Path | None = None) -> list[KeywordRule]:
    p = path or KEYWORDS_PATH
    if not p.is_file():
        log.warning("scouting keywords file missing: %s", p)
        return []
    blob = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rules: list[KeywordRule] = []
    for entry in blob.get("keywords", []):
        phrase = str(entry.get("phrase", "")).strip().lower()
        raw_deltas = entry.get("deltas") or {}
        if not phrase or not raw_deltas:
            continue
        try:
            deltas = {str(k): int(v) for k, v in raw_deltas.items()}
        except (TypeError, ValueError):
            log.warning("bad deltas in keyword %r", phrase)
            continue
        rules.append(KeywordRule(phrase=phrase, deltas=deltas))
    return rules


# ---------------------------------------------------------------------------
def fetch_ddg_blurbs(query: str, *, max_results: int = 3) -> list[str]:
    """Optional DuckDuckGo-search fallback for free-text blurbs.

    Requires the ``duckduckgo-search`` package. Returns ``[]`` if the package
    isn't installed or the search fails. Restricted to a few trusted domains.
    """
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError:
        log.info("duckduckgo-search not installed; skipping DDG scouting fetch")
        return []
    allowed_domains = (
        "espn.com", "nbadraft.net", "theringer.com",
        "sports-reference.com", "247sports.com",
    )
    out: list[str] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results * 3):
                url = r.get("href") or r.get("url") or ""
                body = r.get("body") or ""
                if any(d in url for d in allowed_domains) and body:
                    out.append(body.strip())
                if len(out) >= max_results:
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("DDG search for %r failed: %s", query, exc)
    return out


def extract_espn_blurb(slug: str) -> str | None:
    """Pull a prospect blurb from the cached ESPN big-board HTML.

    Searches every cached ESPN HTML under ``data/cache/espn/`` for a sentence
    containing the prospect's full name with enough prose (>60 chars) to be
    worth using. Returns the longest match found, or ``None``.
    """
    if not config.CACHE_ESPN.is_dir():
        return None
    name_parts = slug.replace("-", " ").split()
    if len(name_parts) < 2:
        return None
    full_name = " ".join(p.capitalize() for p in name_parts)
    best: str | None = None
    for fp in config.CACHE_ESPN.glob("*.html"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if full_name.lower() not in text.lower():
            continue
        for m in re.finditer(
            r"([^<>]{60,400}?" + re.escape(full_name) + r"[^<>]{10,400}?\.)",
            text, re.IGNORECASE,
        ):
            blurb = m.group(1).strip()
            if best is None or len(blurb) > len(best):
                best = blurb
    return best


# ---------------------------------------------------------------------------
def fetch_blurbs(slug: str, full_name: str, *,
                 use_duckduckgo: bool = False) -> list[str]:
    blurbs: list[str] = []
    espn = extract_espn_blurb(slug)
    if espn:
        blurbs.append(espn)
    if use_duckduckgo:
        blurbs.extend(fetch_ddg_blurbs(f"{full_name} NBA draft scouting report"))
    return blurbs


# ---------------------------------------------------------------------------
def modulate_ratings(
    slug: str,
    ratings: dict[str, int],
    blurbs: Iterable[str],
    *,
    rules: list[KeywordRule] | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Apply keyword-based deltas to ``ratings`` in place.

    Returns the mutated ratings + a list of audit events (one per keyword hit).
    Each event is a dict ready to pass to :func:`audit.log_batch`.
    """
    rules = rules or load_keyword_rules()
    text = " ".join(blurbs).lower()
    events: list[dict[str, Any]] = []
    if not text or not rules:
        return ratings, events

    for rule in rules:
        if rule.phrase not in text:
            continue
        for attr, delta in rule.deltas.items():
            if attr not in ratings:
                continue
            before = int(ratings[attr])
            after = max(25, min(99, before + int(delta)))
            if after == before:
                continue
            ratings[attr] = after
            events.append({
                "action": "rating_recalc",
                "entity_type": "prospect",
                "entity_slug": slug,
                "field": attr,
                "before": before,
                "after": after,
                "note": f"scouting keyword '{rule.phrase}' ({delta:+d})",
            })
    if events:
        audit.log_batch(events)
    return ratings, events
