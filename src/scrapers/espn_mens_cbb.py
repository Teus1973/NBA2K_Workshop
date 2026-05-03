"""
ESPN men's college basketball (core API) — fallback stats when Sports-Reference
has no matching ``cbb/players/…`` row (common with ``Jr.`` / suffix names).

Uses public JSON endpoints (no API key). Responses are cached under
``data/cache/espn/``.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.espn_mens_cbb")

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
LEAGUE_SLUG = "mens-college-basketball"
STATS_SEASON_TYPE = 2  # regular season

# Prospect seed strings like "BYU" / "UConn" vs ESPN team display names.
_SCHOOL_ABBR_HINTS: tuple[tuple[str, str], ...] = (
    ("byu", "brigham young"),
    ("uconn", "connecticut"),
    ("unc", "north carolina"),
    ("uva", "virginia"),
    ("umich", "michigan"),
    ("osu", "ohio state"),
    ("fsu", "florida state"),
    ("gt", "georgia tech"),
    ("vt", "virginia tech"),
    ("tcu", "texas christian"),
    ("smu", "southern methodist"),
)


def _school_match_hints(prospect_school: str) -> list[str]:
    raw = prospect_school.strip()
    low = raw.lower()
    out = [low]
    for abbr, full in _SCHOOL_ABBR_HINTS:
        if low == abbr:
            out.append(full)
            continue
        if low.startswith(abbr + " ") or low.endswith(" " + abbr):
            out.append(low.replace(abbr, full))
            out.append(full)
    seen: set[str] = set()
    hints: list[str] = []
    for h in out:
        h = re.sub(r"\s+", " ", h.strip().lower())
        if len(h) >= 2 and h not in seen:
            seen.add(h)
            hints.append(h)
    return hints


def school_matches(prospect_school: str | None, espn_team_display: str) -> bool:
    """Loose match: ``Alabama`` ↔ ``Alabama Crimson Tide``; ``BYU`` ↔ ``Brigham Young``."""
    if not prospect_school or not espn_team_display:
        return False
    t = re.sub(r"\s+", " ", espn_team_display.strip().lower())
    for p in _school_match_hints(prospect_school):
        if len(p) < 2:
            continue
        if p in t:
            return True
        first = p.split()[0]
        if len(first) >= 3 and first in t:
            return True
    return False


def espn_core_season_year(season_label: str) -> int:
    """Map ``2025-26`` → ``2026`` (ESPN ``seasons/{year}`` uses the second year)."""
    s = season_label.strip()
    if "-" in s:
        left, right = s.split("-", 1)
        left = left.strip()
        right = right.strip()
        if len(left) >= 4 and len(right) == 2:
            century = int(left[:2])
            return century * 100 + int(right)
        if len(left) >= 4 and len(right) >= 4:
            return int(right[:4])
    if len(s) >= 4 and s[:4].isdigit():
        return int(s[:4])
    return int(config.CURRENT_SEASON.split("-", 1)[0][:4]) + 1


def _https(ref: str) -> str:
    if ref.startswith("http://"):
        return "https://" + ref[len("http://") :]
    return ref


def search_players(query: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    q = quote(query.strip(), safe="")
    url = f"{SEARCH_URL}?query={q}&limit=25&type=player"
    try:
        data, _cache_hit = _http.fetch_json(
            url,
            scope_dir=config.CACHE_ESPN,
            cache_key=f"mcb-search-{q[:80]}",
            force_refresh=force_refresh,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("espn mcb search failed for %r: %s", query, exc)
        return []
    items = data.get("items") or []
    return [x for x in items if isinstance(x, dict)]


def fetch_json_ref(ref_url: str, *, cache_key: str,
                   force_refresh: bool = False) -> dict[str, Any] | None:
    url = _https(ref_url)
    try:
        data, _from_cache = _http.fetch_json(
            url,
            scope_dir=config.CACHE_ESPN,
            cache_key=cache_key,
            force_refresh=force_refresh,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("espn mcb ref fetch failed %s: %s", url[:90], exc)
        return None
    return data if isinstance(data, dict) else None


def flatten_statistics(stats_payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    splits = stats_payload.get("splits") or {}
    for cat in splits.get("categories") or []:
        for st in cat.get("stats") or []:
            name = st.get("name")
            if not name or "value" not in st:
                continue
            try:
                out[str(name)] = float(st["value"])
            except (TypeError, ValueError):
                continue
    return out


def statistics_url(league_slug: str, season_year: int, athlete_id: str) -> str:
    return (
        f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/"
        f"{league_slug}/seasons/{season_year}/types/{STATS_SEASON_TYPE}/"
        f"athletes/{athlete_id}/statistics/0?lang=en&region=us"
    )


def athlete_url(league_slug: str, season_year: int, athlete_id: str) -> str:
    return (
        f"https://sports.core.api.espn.com/v2/sports/basketball/leagues/"
        f"{league_slug}/seasons/{season_year}/athletes/{athlete_id}?lang=en&region=us"
    )


def fetch_statistics_document(
    league_slug: str,
    season_year: int,
    athlete_id: str,
    *,
    force_refresh: bool = False,
) -> dict[str, Any] | None:
    url = statistics_url(league_slug, season_year, athlete_id)
    try:
        data, _fc = _http.fetch_json(
            url,
            scope_dir=config.CACHE_ESPN,
            cache_key=f"{league_slug}-stats-{athlete_id}-{season_year}",
            force_refresh=force_refresh,
        )
    except Exception as exc:  # noqa: BLE001
        log.info("espn stats fetch failed %s: %s", url[:100], exc)
        return None
    return data if isinstance(data, dict) else None


def _search_name_variants(full_name: str) -> list[str]:
    """Try ``Jr.`` stripped — ESPN search occasionally misses suffix punctuation."""
    raw = full_name.strip()
    out = [raw]
    base = re.sub(
        r"\s*,?\s*\b(Jr\.?|Sr\.?|III|II|IV|V)\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()
    if base and base not in out:
        out.append(base)
    return list(dict.fromkeys(out))


def resolve_mens_cbb_athlete_id(
    full_name: str,
    school_or_team: str | None,
    season_year: int,
    *,
    force_refresh: bool = False,
) -> str | None:
    ncaa: list[dict[str, Any]] = []
    for qname in _search_name_variants(full_name):
        items = search_players(qname, force_refresh=force_refresh)
        ncaa = [it for it in items if it.get("league") == LEAGUE_SLUG]
        if ncaa:
            break
    if not ncaa:
        return None

    def team_name_for(aid: str) -> str | None:
        aj = fetch_json_ref(
            athlete_url(LEAGUE_SLUG, season_year, aid),
            cache_key=f"mcb-athlete-{aid}-{season_year}",
            force_refresh=force_refresh,
        )
        if not aj:
            return None
        ref = (aj.get("team") or {}).get("$ref")
        if not ref:
            return None
        tid_match = re.search(r"/teams/(\d+)", ref)
        tid = tid_match.group(1) if tid_match else "unknown"
        tj = fetch_json_ref(
            ref,
            cache_key=f"mcb-team-{tid}-{season_year}",
            force_refresh=force_refresh,
        )
        if not tj:
            return None
        return str(tj.get("displayName") or "")

    if len(ncaa) == 1:
        return str(ncaa[0].get("id") or "") or None

    for it in ncaa[:15]:
        aid = str(it.get("id") or "")
        if not aid:
            continue
        tname = team_name_for(aid)
        if tname and school_matches(school_or_team, tname):
            return aid
        if not tname:
            log.info(
                "espn mcb: no team display for athlete id %s while disambiguating %r",
                aid,
                full_name,
            )
    log.info(
        "espn mcb: no school match for %r (%r)",
        full_name,
        school_or_team,
    )
    return None


def statistics_to_prospect_stats(
    flat: dict[str, float],
    *,
    season_display: str,
    team_total_games: int | None = None,
) -> dict[str, Any]:
    gp = int(flat.get("gamesPlayed") or 0)
    if gp <= 0:
        return {}

    def _g(key: str) -> float | None:
        v = flat.get(key)
        return float(v) if v is not None else None

    fg_pct = _g("fieldGoalPct")
    fg3_pct = _g("threePointFieldGoalPct")
    ft_pct = _g("freeThrowPct")

    oreb = _g("avgOffensiveRebounds")
    dreb = _g("avgDefensiveRebounds")
    reb = _g("avgRebounds")
    if reb is None and oreb is not None and dreb is not None:
        reb = oreb + dreb

    ttg = int(team_total_games) if team_total_games is not None and team_total_games > 0 else gp

    out: dict[str, Any] = {
        "season": season_display,
        "gp": gp,
        "team_total_games": max(ttg, gp),
        "min": _g("avgMinutes"),
        "pts": _g("avgPoints"),
        "fgm": _g("avgFieldGoalsMade"),
        "fga": _g("avgFieldGoalsAttempted"),
        "fg_pct": fg_pct,
        "fg3m": _g("avgThreePointFieldGoalsMade"),
        "fg3a": _g("avgThreePointFieldGoalsAttempted"),
        "fg3_pct": fg3_pct,
        "ftm": _g("avgFreeThrowsMade"),
        "fta": _g("avgFreeThrowsAttempted"),
        "ft_pct": ft_pct,
        "oreb": oreb,
        "dreb": dreb,
        "reb": reb,
        "ast": _g("avgAssists"),
        "tov": _g("avgTurnovers"),
        "stl": _g("avgSteals"),
        "blk": _g("avgBlocks"),
        "_stats_source": "espn-mcb",
    }
    return out


def fetch_ncaa_stats_for_prospect(
    *,
    full_name: str,
    school_or_team: str | None,
    season_display: str | None = None,
    prospect_slug: str = "",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return a ``prospect_stats``-shaped dict or ``{}``.

    Requires ``school_or_team`` whenever the ESPN name search returns multiple
    NCAA hits (disambiguation).
    """
    season_display = season_display or config.CURRENT_SEASON
    year = espn_core_season_year(season_display)
    aid = resolve_mens_cbb_athlete_id(
        full_name,
        school_or_team,
        year,
        force_refresh=force_refresh,
    )
    if not aid:
        return {}

    for try_year in (year, year - 1):
        raw = fetch_statistics_document(
            LEAGUE_SLUG, try_year, aid, force_refresh=force_refresh)
        if not raw:
            continue
        flat = flatten_statistics(raw)
        row = statistics_to_prospect_stats(flat, season_display=season_display)
        if row.get("gp"):
            audit.log_event(
                action="espn_mcb_stats",
                entity_type="prospect",
                entity_slug=prospect_slug or aid,
                note=f"athlete={aid} season_year={try_year} gp={row.get('gp')}",
            )
            return row
    return {}
