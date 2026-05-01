"""
Scouting-report ingestion + keyword-based rating modulation.

1. **Blurbs** + **Ollama** synthesis — play style, pros, cons, and a **physical
   traits** block, plus 0–1 ``*_01`` features (:class:`ScoutingSynthesis`).
   Those features are stored on ``prospects`` and :mod:`formulas.apply` nudges
   physical 2K ratings when combine numbers are not present. Text sources
   include ESPN cache, optional Wikipedia, optional DuckDuckGo, and Ollama
   (set ``NBA2K_WORKSHOP_USE_OLLAMA=0`` to disable LLM calls).

2. **Keyword modulation** — ``modulate_ratings`` applies ``data/scouting_keywords.yaml``
   and logs each hit to ``audit_log``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Iterable

import requests
import yaml
from bs4 import BeautifulSoup

from .. import audit, config
from ..logger import get_logger
from . import _http
from . import espn_bigboard

log = get_logger("scrapers.scouting")

_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
_WIKI_MIN_INTERVAL_SEC = 0.55
_WIKI_LOCK = threading.Lock()
_WIKI_LAST_MONO = 0.0

_OLLAMA_REACH_CACHE: tuple[float, bool] | None = None
_OLLAMA_REACH_TTL_SEC = 30.0

KEYWORDS_PATH = config.DATA_DIR / "scouting_keywords.yaml"


# ---------------------------------------------------------------------------
@dataclass
class ScoutingSynthesis:
    """AI scouting output: game summary + physical narrative + 0–1 feature hints."""

    scouting_text: str
    physical_text: str
    features: dict[str, float] = field(default_factory=dict)
    """Keys: ``strength_01``, ``leaping_01``, ``athleticism_01``, ``stamina_01``."""

    @property
    def full_text(self) -> str:
        parts = [p for p in (self.scouting_text.strip(), self.physical_text.strip()) if p]
        return "\n\n".join(parts)


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
        "espn.com",
        "nba.com",
        "nbadraft.net",
        "theringer.com",
        "sports-reference.com",
        "247sports.com",
        "basketball-reference.com",
        "theathletic.com",
        "hoopshype.com",
        "on3.com",
        "sports.yahoo.com",
        "si.com",
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


def fetch_ddg_prospect_blurbs(
    full_name: str,
    *,
    pos: str | None = None,
    school: str | None = None,
    league: str | None = None,
    max_blurbs: int = 10,
) -> list[str]:
    """Multiple targeted DDG queries (school, position, level) to reduce generic hits."""
    name = (full_name or "").strip()
    if len(name) < 2:
        return []
    pos_s = (pos or "").strip()
    school_s = (school or "").strip()
    league_s = (league or "").strip()
    queries = [
        f"{name} {school_s} NBA draft vertical athleticism speed explosiveness".strip(),
        f"{name} {pos_s} {school_s} lateral quickness leaping first step".strip(),
        f"{name} {school_s} NBA draft prospect analysis strengths weaknesses".strip(),
        f"{name} {pos_s} {league_s or 'basketball'} draft scouting profile".strip(),
        f"{name} NBA draft scouting report 2026 {pos_s} athletic",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if len(q) < 8:
            continue
        for b in fetch_ddg_blurbs(q, max_results=3):
            key = b[:100].lower()
            if not b or key in seen:
                continue
            seen.add(key)
            out.append(b)
            if len(out) >= max_blurbs:
                return out
    return out


def format_listing_for_scouting(row: Mapping[str, Any]) -> str:
    """Compact verified facts for the model (avoids 'unknown' when DB has name/rank/pos/size)."""
    lines: list[str] = []
    name = (str(row.get("full_name") or "")).strip() or "Prospect"
    lines.append(f"Name: {name}")
    rnk = row.get("espn_rank")
    if rnk is not None and str(rnk).strip() != "":
        try:
            lines.append(f"ESPN big-board rank: {int(rnk)}")
        except (TypeError, ValueError):
            lines.append(f"ESPN big-board rank: {rnk}")
    pos = (str(row.get("pos") or "")).strip()
    if pos:
        lines.append(f"Position: {pos}")
    school = (str(row.get("school_or_team") or "")).strip()
    if school:
        lines.append(f"School / team: {school}")
    le = (str(row.get("league") or "")).strip()
    if le:
        lines.append(f"League: {le}")
    for label, col in (
        ("Listed height", "height_in"),
        ("Listed weight (lb)", "weight_lbs"),
        ("Listed wingspan", "wingspan_in"),
    ):
        val = row.get(col)
        if val is None or str(val).strip() == "":
            continue
        if col == "weight_lbs":
            try:
                lines.append(f"{label}: {int(float(val))}")
            except (TypeError, ValueError):
                pass
        else:
            try:
                w = float(val)
                ft = int(w // 12)
                inch = int(round(w - ft * 12))
                lines.append(f"{label}: {ft}'{inch}\"")
            except (TypeError, ValueError):
                pass
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _names_for_blurb(slug: str, full_name: str | None) -> list[str]:
    names: list[str] = []
    if full_name and str(full_name).strip():
        names.append(str(full_name).strip())
    name_parts = slug.replace("-", " ").split()
    if len(name_parts) >= 2:
        tt = " ".join(p.capitalize() for p in name_parts)
        if not names or names[0].lower() != tt.lower():
            names.append(tt)
    return names


def _name_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def format_big_board_line(p: espn_bigboard.Prospect) -> str:
    """Single ranked line as shown on the ESPN print big board."""
    s = f"{p.rank}. {p.full_name}"
    if p.pos:
        s += f", {p.pos}"
    if p.school_or_team:
        s += f", {p.school_or_team}"
    return s


def big_board_line_from_html(html: str, slug: str, full_name: str | None) -> str | None:
    """Return the one **player-specific** line from parsed big-board HTML, or ``None``."""
    prospects = espn_bigboard.parse_bigboard_html(html)
    key = _name_key(full_name or "")
    skey = _name_key(slug.replace("-", " "))
    for p in prospects:
        if key and _name_key(p.full_name) == key:
            return format_big_board_line(p)
        if skey and _name_key(p.full_name) == skey:
            return format_big_board_line(p)
    return None


def _wikipedia_user_agent() -> str:
    """Identify this app for Wikimedia rate limits (avoid generic python-requests)."""
    ua = (config.USER_AGENT or "").strip()
    if not ua or "python-requests" in ua.lower():
        ua = "NBA2K-Workshop/1.0"
    return f"{ua} (local NBA 2K draft workshop; Wikipedia extracts for scouting blurbs)"


def _wikipedia_throttle() -> None:
    """Space out API hits — burst requests trigger HTTP 429."""
    global _WIKI_LAST_MONO
    with _WIKI_LOCK:
        now = time.monotonic()
        gap = now - _WIKI_LAST_MONO
        wait = _WIKI_MIN_INTERVAL_SEC - gap
        if wait > 0:
            time.sleep(wait)
        _WIKI_LAST_MONO = time.monotonic()


def _wikipedia_api_get(
    params: dict[str, Any],
    *,
    timeout: float = 15.0,
) -> dict[str, Any] | None:
    """GET the Wikipedia JSON API with throttling and retries on 429 / 5xx."""
    headers = {"User-Agent": _wikipedia_user_agent()}
    last_log: str | None = None
    for attempt in range(1, 7):
        _wikipedia_throttle()
        try:
            r = requests.get(
                _WIKIPEDIA_API,
                params=params,
                timeout=timeout,
                headers=headers,
            )
        except requests.RequestException as exc:
            last_log = str(exc)
            log.info(
                "Wikipedia API request failed (attempt %s/%s): %s",
                attempt,
                7,
                exc,
            )
            time.sleep(min(30.0, 0.6 * attempt))
            continue

        if r.status_code == 429:
            raw_ra = r.headers.get("Retry-After")
            try:
                wait_s = float(raw_ra) if raw_ra is not None else 2.0 ** min(attempt, 5)
            except ValueError:
                wait_s = 2.0 ** min(attempt, 5)
            wait_s = max(wait_s, 1.0)
            wait_s = min(wait_s, 120.0)
            log.warning(
                "Wikipedia API rate limited (429); sleeping %.1fs before retry",
                wait_s,
            )
            time.sleep(wait_s)
            continue

        if r.status_code >= 500:
            log.info(
                "Wikipedia API HTTP %s; retry in %.1fs",
                r.status_code,
                0.5 * attempt,
            )
            time.sleep(0.5 * attempt)
            continue

        if r.status_code != 200:
            log.info(
                "Wikipedia API HTTP %s for action=%r",
                r.status_code,
                params.get("action"),
            )
            return None

        try:
            return r.json()
        except ValueError as exc:
            last_log = str(exc)
            log.info("Wikipedia API invalid JSON: %s", exc)
            time.sleep(0.4 * attempt)

    if last_log:
        log.info("Wikipedia API exhausted retries (%s)", last_log)
    return None


def _is_article_boilerplate(block: str) -> bool:
    """True for shared article intros, not per-prospect scouting."""
    low = block.lower()
    needles = (
        "for the first time this cycle",
        "there's a change at no",
        "there is a change at no",
        "best available",
        "we're taking stock",
        "nba draft big board",
        "this week's",
    )
    return any(n in low for n in needles) and len(block) > 120


def _wikipedia_extract_intro(title: str) -> str | None:
    data = _wikipedia_api_get(
        {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "titles": title,
        },
        timeout=15.0,
    )
    if not data:
        return None
    q = (data.get("query") or {}).get("pages") or {}
    for _pid, page in q.items():
        ex = page.get("extract")
        if ex and str(ex).strip():
            t = re.sub(r"\s+", " ", str(ex).strip())
            if len(t) > 40:
                return t[:2000] if len(t) > 2000 else t
    return None


def wikipedia_intro(full_name: str) -> str | None:
    """First paragraph of the best-matching en.wikipedia.org article (if any)."""
    q = (full_name or "").strip()
    if len(q) < 3:
        return None
    for search in (f"{q} American basketball", q):
        data = _wikipedia_api_get(
            {
                "action": "opensearch",
                "search": search,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            timeout=12.0,
        )
        if not data:
            log.info("Wikipedia opensearch for %r returned no data", search)
            continue
        if len(data) < 2 or not data[1]:
            continue
        title = data[1][0]
        intro = _wikipedia_extract_intro(title)
        if intro:
            return intro
    return None


def extract_espn_blurb(
    slug: str,
    full_name: str | None = None,
) -> str | None:
    """Pull a **per-player** line for ``full_name`` from cached ESPN ``*.html``.

    Prefer the ranked list line (``1. Name, POS, School``) from
    :func:`espn_bigboard.parse_bigboard_html` — the same as the main big-board
    scraper. That avoids the shared *article* lede that names the No.1 pick but
    applies to the whole page.

    If no list line is found, we fall back to a short name-containing block, but
    we drop long paragraphs that look like global article boilerplate.
    """
    if not config.CACHE_ESPN.is_dir():
        return None
    names_to_try = _names_for_blurb(slug, full_name)
    if not names_to_try:
        return None

    for fp in config.CACHE_ESPN.glob("*.html"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tlow = text.lower()
        if not any(n and n.lower() in tlow for n in names_to_try):
            continue
        line = big_board_line_from_html(text, slug, full_name)
        if line:
            return line

    best: str | None = None
    for fp in config.CACHE_ESPN.glob("*.html"):
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tlow = text.lower()
        for nm in names_to_try:
            if not nm or nm.lower() not in tlow:
                continue
            esc = re.escape(nm)
            for m in re.finditer(
                r"([^<>]{30,500}?" + esc + r"[^<>]{5,500}?\.)",
                text, re.IGNORECASE,
            ):
                blurb = m.group(1).strip()
                if _is_article_boilerplate(blurb):
                    continue
                if best is None or len(blurb) > len(best):
                    best = blurb
        try:
            soup = BeautifulSoup(text, "lxml")
        except Exception:  # noqa: BLE001
            soup = BeautifulSoup(text, "html.parser")
        for nm in names_to_try:
            if not nm or nm.lower() not in tlow:
                continue
            for tag in soup.find_all(["p", "li", "td", "h2", "h3", "h4"]):
                block = tag.get_text(" ", strip=True)
                if not block or nm.lower() not in block.lower():
                    continue
                if len(block) < 10 or len(block) > 3000:
                    continue
                if _is_article_boilerplate(block):
                    continue
                if best is None or len(block) > len(best):
                    best = block
    return best


def ollama_server_reachable() -> bool:
    """True if something is listening on ``config.OLLAMA_HOST`` (fast probe)."""
    global _OLLAMA_REACH_CACHE
    if not config.USE_OLLAMA:
        return False
    now = time.monotonic()
    if _OLLAMA_REACH_CACHE is not None:
        ts, ok = _OLLAMA_REACH_CACHE
        if now - ts < _OLLAMA_REACH_TTL_SEC:
            return ok
    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/tags"
    try:
        r = requests.get(url, timeout=2.5)
        ok = r.status_code == 200
    except (OSError, requests.RequestException):
        ok = False
    _OLLAMA_REACH_CACHE = (now, ok)
    return ok


_PHYSICAL_JSON_KEYS = (
    "strength_01", "leaping_01", "athleticism_01", "stamina_01",
)


def _extract_scouting_json_obj(raw: str) -> dict[str, float] | None:
    tag = "SCOUTING_PHYSICAL_JSON"
    if tag not in raw and tag.lower() not in raw.lower():
        return None
    i = raw.lower().rfind("scouting_physical_json")
    if i < 0:
        return None
    j = raw.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(raw)):
        if raw[k] == "{":
            depth += 1
        elif raw[k] == "}":
            depth -= 1
            if depth == 0:
                try:
                    blob = json.loads(raw[j : k + 1])
                except (TypeError, ValueError):
                    return None
                if not isinstance(blob, dict):
                    return None
                out: dict[str, float] = {}
                for key in _PHYSICAL_JSON_KEYS:
                    if key not in blob:
                        continue
                    try:
                        v = float(blob[key])
                    except (TypeError, ValueError):
                        continue
                    out[key] = max(0.0, min(1.0, v))
                return out or None
    return None


def split_scouting_synthesis_text(raw: str) -> tuple[str, str, dict[str, float]]:
    """Split model output into game summary, physical blurb, and JSON features."""
    features = _extract_scouting_json_obj(raw) or {}
    i = re.search(r"SCOUTING_PHYSICAL_JSON", raw, re.IGNORECASE)
    pre = raw[: i.start()] if i else raw
    phys_match = re.search(
        r"(?is)Physical traits[^:]*:\s*(.*?)(?=\n\s*SCOUTING_PHYSICAL_JSON|\Z)",
        pre,
    )
    if phys_match:
        physical_text = phys_match.group(1).strip()
        head = re.sub(
            r"(?is)Physical traits[^:]*:.*$",
            "",
            pre,
        ).strip()
    else:
        physical_text, head = "", pre.strip()
    if len(head) < 5:
        head = pre.strip()
    return head, physical_text, features


def synthesize_scouting_with_ollama(
    player_name: str,
    context: str,
    *,
    listing: str | None = None,
) -> ScoutingSynthesis | None:
    """Rewrite ``context`` into scouting notes + physical summary + 0–1 features.

    Respects :data:`config.USE_OLLAMA`, :data:`config.OLLAMA_HOST`, and
    :data:`config.OLLAMA_MODEL`. Returns ``None`` if disabled, on HTTP errors,
    or on empty/invalid responses. Call :func:`ollama_server_reachable` first
    to avoid slow failures when nothing is running.
    """
    if not config.USE_OLLAMA:
        return None
    list_block = (listing or "").strip()
    ctx = (context or "").strip()
    if list_block and ctx:
        combined_for_len = f"{list_block}\n{ctx}"
    else:
        combined_for_len = list_block or ctx
    if len(combined_for_len) < 8:
        return None
    if ctx and list_block:
        user_blob = (
            f"{list_block}\n\n---\n"
            f"Source text (ESPN / web; may be short):\n{ctx[:8000]}\n"
        )
    elif list_block:
        user_blob = f"{list_block}\n(Additional source text was minimal.)\n"
    else:
        user_blob = f"Source text:\n{ctx[:8000]}\n"
    name = (player_name or "This prospect").strip() or "This prospect"
    url = f"{config.OLLAMA_HOST.rstrip('/')}/api/chat"
    body = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write short NBA draft scouting notes. Use the **Workshop listing** (if "
                    "given) and any source text. Do not hedge with 'unknown', 'undisclosed', "
                    "'TBD', or 'insufficient data' as filler sentences. When a fact is in the "
                    "listing (rank, school, size, position), treat it as confirmed. If outside "
                    "scouting is thin, infer *reasonable* guard/big archetype language from "
                    "position and size, and one sentence that sources are limited. "
                    "The JSON line must be present exactly as specified."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Player: {name}\n\n"
                    f"{user_blob}\n"
                    "Reply in plain text only, under 220 words, with exactly these "
                    "headings on their own lines (no markdown, no # symbols):\n"
                    "Play style:\n"
                    "Pros:\n"
                    "Cons:\n"
                    "Physical traits:\n"
                    "Write 4–6 short lines. You must include: (1) one line that starts with "
                    "the words 'Athletic profile:' and states plainly whether the player is "
                    "typically described as a plus, above-average, average, below-average, or limited "
                    "athlete (explosiveness, quickness) vs draft peers, based on the sources; "
                    "(2) strength; (3) leaping/vertical; (4) speed/lateral quickness; "
                    "(5) conditioning/motor; (6) frame if relevant. If sources conflict, say so briefly.\n"
                    "After that, on its own line, print exactly this tag:\n"
                    "SCOUTING_PHYSICAL_JSON\n"
                    "Then a single line of JSON (no other text) with four numbers 0.0 to 1.0:\n"
                    '{"strength_01":0.0,"leaping_01":0.0,'
                    '"athleticism_01":0.0,"stamina_01":0.0}\n'
                    "Use decimals (e.g. 0.55). Calibrate: 0.5 = average for draft-level prospects. "
                    "If a trait is not evidenced in the text, use ~0.45–0.55, not 0.0. "
                    "0.8+ = elite in that area only if sources support it."
                ),
            },
        ],
    }
    try:
        r = requests.post(url, json=body, timeout=120)
        r.raise_for_status()
        data = r.json() if r.content else {}
    except (OSError, requests.RequestException, ValueError) as exc:  # noqa: BLE001
        log.warning("Ollama scouting synthesis failed: %s", exc)
        return None
    msg = (data.get("message") or {}) if isinstance(data, dict) else {}
    text = (msg.get("content") or "").strip()
    if not text or len(text) < 20:
        return None
    text = text[:5000] if len(text) > 5000 else text
    head, physical, feats = split_scouting_synthesis_text(text)
    if not head and not physical:
        return None
    return ScoutingSynthesis(
        scouting_text=head or text,
        physical_text=physical,
        features=feats,
    )


def collect_prospect_blurbs(
    slug: str,
    full_name: str,
    *,
    use_wikipedia: bool = False,
    use_duckduckgo: bool = False,
    pos: str | None = None,
    school: str | None = None,
    league: str | None = None,
) -> tuple[list[str], str]:
    """Gather blurbs and a short source label for the Scouting tab."""
    blurbs: list[str] = []
    parts: list[str] = []
    espn = extract_espn_blurb(slug, full_name=full_name or None)
    if espn:
        blurbs.append(espn)
        parts.append("ESPN cache")
    if use_wikipedia and full_name:
        w = wikipedia_intro(full_name)
        if w:
            blurbs.append(w)
            parts.append("Wikipedia")
    if use_duckduckgo and full_name:
        d = fetch_ddg_prospect_blurbs(
            full_name,
            pos=pos,
            school=school,
            league=league,
        )
        for b in d:
            if b and b not in blurbs:
                blurbs.append(b)
        if d:
            parts.append("Web (DDG)")
    label = " + ".join(parts) if parts else "—"
    return blurbs, label


# ---------------------------------------------------------------------------
def fetch_blurbs(
    slug: str,
    full_name: str,
    *,
    use_duckduckgo: bool = False,
    use_wikipedia: bool = False,
) -> list[str]:
    """Backward-compatible blurb list (no source label)."""
    blurbs, _ = collect_prospect_blurbs(
        slug,
        full_name,
        use_wikipedia=use_wikipedia,
        use_duckduckgo=use_duckduckgo,
    )
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
