"""
International leagues scraper (Euroleague / ACB / LNB / NBL / NZNBL).

For v1 we rely primarily on **proballers.com** player pages, which consolidate
multiple-league per-game stats into a single HTML table. Fallbacks to
**euroleague.net** official pages for Euroleague-only seasons.

Covers the prospects flagged in the plan's section 3.1:
    Karim Lopez (NZNBL), Hannes Steinbach (ACB/DE), Sergio de Larrea (ACB),
    Dash Daniels (NBL), Adam Atamna (LNB), Michael Ruzic (ACB),
    Mouhamed Faye (LNB), Luigi Suigo (LKL), Dame Sarr (ACB).

All scrape functions are best-effort: they return ``{}`` on a failed fetch so
the calibration and prospects pages can still render with missing stats.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.international")

PROBALLERS_BASE = "https://www.proballers.com"


def slugify(full_name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def _parse_num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().rstrip("%")
    if not s or s == "-":
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def _parse_int(v: Any) -> int | None:
    f = _parse_num(v)
    return int(f) if f is not None else None


def parse_proballers_page(html: str) -> dict[str, Any]:
    """Pull the current-season row from a proballers player page.

    The page has a ``<table class="table-stats ...">`` where each row is a
    season. We pick the latest season row and normalise to our stat schema.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_=re.compile(r"table-stats"))
    if table is None:
        return {}
    tbody = table.find("tbody")
    if tbody is None:
        return {}
    rows = tbody.find_all("tr")
    if not rows:
        return {}
    # Latest season is usually the first row.
    cells = rows[0].find_all("td")
    if not cells:
        return {}
    headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    text_cells = [c.get_text(strip=True) for c in cells]

    def col(names: list[str]) -> str | None:
        for n in names:
            n = n.lower()
            if n in headers:
                idx = headers.index(n)
                if idx < len(text_cells):
                    return text_cells[idx]
        return None

    out = {
        "season": col(["season", "saison"]),
        "league": col(["league", "ligue"]),
        "gp":  _parse_int(col(["gp", "games", "mj"])),
        "min": _parse_num(col(["min", "mpg", "mn"])),
        "pts": _parse_num(col(["pts", "points", "ppg"])),
        "fgm": _parse_num(col(["fgm"])),
        "fga": _parse_num(col(["fga"])),
        "fg_pct": _parse_num(col(["fg%", "fg_pct"])),
        "fg3m": _parse_num(col(["3pm", "3p"])),
        "fg3a": _parse_num(col(["3pa"])),
        "fg3_pct": _parse_num(col(["3p%", "3pt%"])),
        "ftm": _parse_num(col(["ftm", "ft"])),
        "fta": _parse_num(col(["fta"])),
        "ft_pct": _parse_num(col(["ft%"])),
        "oreb": _parse_num(col(["oreb", "orb"])),
        "dreb": _parse_num(col(["dreb", "drb"])),
        "reb": _parse_num(col(["reb", "trb", "rpg"])),
        "ast": _parse_num(col(["ast", "apg"])),
        "tov": _parse_num(col(["tov", "to"])),
        "stl": _parse_num(col(["stl", "spg"])),
        "blk": _parse_num(col(["blk", "bpg"])),
        "pf": _parse_num(col(["pf"])),
    }
    return out


def fetch_proballers(slug: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """Try proballers.com/basketball/player/<slug>."""
    url = f"{PROBALLERS_BASE}/basketball/player/{slug}"
    try:
        html, from_cache = _http.fetch_text(
            url,
            scope_dir=config.CACHE_INTL,
            cache_key=f"proballers-{slug}",
            suffix=".html",
            force_refresh=force_refresh,
        )
    except Exception as exc:
        log.info("proballers fetch failed for %s: %s", url, exc)
        return {}

    data = parse_proballers_page(html)
    audit.log_event(
        action="scrape_international",
        entity_type="prospect",
        entity_slug=slug,
        note=f"proballers {url}; cache={from_cache}; ok={bool(data)}",
    )
    return data


def league_for_prospect(league_hint: str | None) -> str:
    """Normalise a free-text league name into our canonical league tag."""
    if not league_hint:
        return config.LEAGUE_OTHER
    s = league_hint.strip().lower()
    if "euroleague" in s or "aba" in s or "acb" in s or "spain" in s:
        return config.LEAGUE_EUROLEAGUE
    if "nbl" in s:
        return config.LEAGUE_NBL
    if "nznbl" in s or "new zealand" in s:
        return config.LEAGUE_NZNBL
    if "g-league" in s or "gleague" in s or "ignite" in s:
        return config.LEAGUE_GLEAGUE
    if "high school" in s or s == "hs":
        return config.LEAGUE_HS
    return config.LEAGUE_OTHER


def upsert_stats(conn, slug: str, league: str, stats: dict[str, Any]) -> int:
    """Write scraped stats into ``prospect_stats``."""
    if not stats or not stats.get("gp"):
        return 0
    season = stats.get("season") or config.CURRENT_SEASON
    row = {
        "slug": slug,
        "season": season,
        "league": league,
        "source": "proballers",
    }
    for c in config.STAT_COLUMNS:
        row[c] = stats.get(c)
    cols = ["slug", "season", "league", *config.STAT_COLUMNS, "source"]
    placeholders = ",".join(f":{c}" for c in cols)
    on_conf = ",\n".join(f"{c}=excluded.{c}" for c in cols if c not in (
        "slug", "season", "league"))
    conn.execute(
        f"""
        INSERT INTO prospect_stats ({",".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(slug, season, league) DO UPDATE SET
            {on_conf},
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        row,
    )
    return 1
