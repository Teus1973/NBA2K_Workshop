"""
International leagues scraper (Euroleague / ACB / LNB / NBL / NZNBL).

Primary source: **proballers.com** player pages (multi-league tables).

Fallback when proballers misses or blocks: **Basketball-Reference international**
(``/international/players/<slug>-N.html``) — totals table converted to per-game.

Euroleague-only seasons may still use **euroleague.net** where wired.

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
BBREF_INTL_PLAYERS = (
    "https://www.basketball-reference.com/international/players"
)


def slugify(full_name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)


def bbintl_slug_variants(full_name: str) -> list[str]:
    """BR international URLs use ``name-slug-1``, ``name-slug-2``, …."""
    base = slugify(full_name)
    stripped = re.sub(r"-(jr|sr|ii|iii|iv|v)$", "", base, flags=re.IGNORECASE)
    bases = list(dict.fromkeys([b for b in (base, stripped) if b]))
    out: list[str] = []
    seen: set[str] = set()
    for b in bases:
        for n in range(1, 6):
            slug = f"{b}-{n}"
            if slug not in seen:
                seen.add(slug)
                out.append(slug)
    return out


def _cell_by_stat(row_el: Any, stat: str) -> str | None:
    for tag in ("td", "th"):
        cell = row_el.find(tag, attrs={"data-stat": stat})
        if cell:
            return cell.get_text(strip=True)
    return None


def parse_bbintl_totals_per_game(
    html: str,
    *,
    season_display: str | None = None,
) -> dict[str, Any]:
    """Latest season row from BR international **Totals** table → per-game stats."""
    season_display = season_display or config.CURRENT_SEASON
    soup = BeautifulSoup(html, "lxml")
    table = soup.find(
        "table",
        id=lambda x: isinstance(x, str) and x.startswith("player-stats-totals-league"),
    )
    if table is None:
        return {}
    tbody = table.find("tbody")
    if tbody is None:
        return {}

    candidates: list[tuple[str, Any]] = []
    for tr in tbody.find_all("tr"):
        th = tr.find("th", attrs={"data-stat": "season"})
        if not th:
            continue
        season_txt = th.get_text(strip=True)
        if not season_txt or not re.match(r"^\d{4}-\d{2}$", season_txt):
            continue
        candidates.append((season_txt, tr))

    if not candidates:
        return {}

    chosen: tuple[str, Any] | None = None
    for season_txt, tr in candidates:
        if season_txt == season_display:
            chosen = (season_txt, tr)
            break
    if chosen is None:
        chosen = max(candidates, key=lambda x: x[0])

    season_txt, tr = chosen

    def ds(stat: str) -> str | None:
        return _cell_by_stat(tr, stat)

    gp = _parse_int(ds("g"))
    if gp is None or gp <= 0:
        return {}

    mp_tot = _parse_num(ds("mp"))
    if mp_tot is None:
        return {}

    def pg(total_key: str) -> float | None:
        v = _parse_num(ds(total_key))
        if v is None:
            return None
        return round(v / gp, 4)

    fg_pct = _parse_num(ds("fg_pct"))
    fg3_pct = _parse_num(ds("fg3_pct"))
    ft_pct = _parse_num(ds("ft_pct"))

    oreb = pg("orb")
    dreb = pg("drb")
    reb = pg("trb")
    if reb is None and oreb is not None and dreb is not None:
        reb = round(oreb + dreb, 4)

    return {
        "season": season_txt,
        "league": (ds("league") or "").strip() or None,
        "gp": gp,
        "min": round(mp_tot / gp, 4),
        "pts": pg("pts"),
        "fgm": pg("fg"),
        "fga": pg("fga"),
        "fg_pct": fg_pct,
        "fg3m": pg("fg3"),
        "fg3a": pg("fg3a"),
        "fg3_pct": fg3_pct,
        "ftm": pg("ft"),
        "fta": pg("fta"),
        "ft_pct": ft_pct,
        "oreb": oreb,
        "dreb": dreb,
        "reb": reb,
        "ast": pg("ast"),
        "tov": pg("tov"),
        "stl": pg("stl"),
        "blk": pg("blk"),
        "team_total_games": gp,
        "_stats_source": "basketball-reference-intl",
    }


def fetch_bbintl_player(slug: str, *, force_refresh: bool = False) -> dict[str, Any]:
    url = f"{BBREF_INTL_PLAYERS}/{slug}.html"
    try:
        html, from_cache = _http.fetch_text(
            url,
            scope_dir=config.CACHE_INTL,
            cache_key=f"bbintl-{slug}",
            suffix=".html",
            force_refresh=force_refresh,
        )
    except Exception as exc:
        log.info("basketball-reference intl fetch failed for %s: %s", url, exc)
        return {}

    data = parse_bbintl_totals_per_game(html)
    audit.log_event(
        action="scrape_international",
        entity_type="prospect",
        entity_slug=slug,
        note=f"bbref-intl {url}; cache={from_cache}; ok={bool(data)}",
    )
    return data


def proballers_slug_variants(full_name: str) -> list[str]:
    """Try a few URL slug shapes; proballers is picky about ``Jr.`` / dots."""
    base = slugify(full_name)
    variants = [base]
    stripped = re.sub(r"-(jr|sr|ii|iii|iv|v)$", "", base, flags=re.IGNORECASE)
    if stripped and stripped != base:
        variants.append(stripped)
    for v in list(variants):
        nodot = v.replace(".", "")
        if nodot != v:
            variants.append(nodot)
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


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
    }
    gp_i = out.get("gp")
    try:
        gi = int(gp_i) if gp_i is not None else 0
    except (TypeError, ValueError):
        gi = 0
    out["team_total_games"] = gi if gi > 0 else None
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
    src = stats.get("_stats_source") or "proballers"
    row = {
        "slug": slug,
        "season": season,
        "league": league,
        "source": src,
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


def bulk_scrape_international_prospects(
    *,
    progress_cb=None,
    force_refresh: bool = False,
) -> dict[str, int]:
    """Fetch stats for non-NCAA prospects (proballers, then Basketball-Reference intl).

    Best-effort: slug collisions may still need manual rows.
    """
    from .stat_normalize import apply_stat_normalizers
    from .. import db as _db

    conn = _db.connect()
    try:
        rows = conn.execute(
            """
            SELECT slug, full_name, league, pos FROM prospects
            WHERE league IS NOT NULL AND lower(league) != 'ncaa'
            ORDER BY espn_rank IS NULL, espn_rank, full_name
            """
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    ok = skipped = 0
    conn = _db.connect()
    try:
        for i, r in enumerate(rows, start=1):
            slug = r["slug"]
            name = r["full_name"]
            raw: dict[str, Any] = {}
            for cand in proballers_slug_variants(name):
                raw = fetch_proballers(cand, force_refresh=force_refresh)
                if raw.get("gp"):
                    raw["_stats_source"] = raw.get("_stats_source") or "proballers"
                    break
            if not raw.get("gp"):
                for cand in bbintl_slug_variants(name):
                    raw = fetch_bbintl_player(cand, force_refresh=force_refresh)
                    if raw.get("gp"):
                        break
            status = "no-data"
            if raw.get("gp"):
                apply_stat_normalizers(raw, pos=r["pos"])
                lg = league_for_prospect(r["league"])
                upsert_stats(conn, slug, lg, raw)
                conn.commit()
                ok += 1
                status = "ok" if raw.get("_stats_source") == "proballers" else "bbref-intl"
            else:
                skipped += 1
            if progress_cb is not None:
                try:
                    progress_cb(i, total, slug, status)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        conn.close()

    audit.log_event(
        action="scrape_international_bulk",
        entity_type="prospects",
        note=f"intl bulk (proballers+bbref): total={total} ok={ok} skipped={skipped}",
    )
    return {"total": total, "ok": ok, "skipped": skipped}
