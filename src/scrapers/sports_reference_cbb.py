"""
sports-reference.com/cbb college-basketball stats scraper.

URLs look like ``https://www.sports-reference.com/cbb/players/<slug>-1.html``.
We scrape the ``#players_per_game`` and ``#players_advanced`` tables and
translate them into rows for ``prospect_stats``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bs4 import BeautifulSoup

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.sports_reference_cbb")

BASE = "https://www.sports-reference.com/cbb/players"


def _parse_num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(v: Any) -> int | None:
    f = _parse_num(v)
    return int(f) if f is not None else None


def _parse_dob_from_info_text(t: str) -> str | None:
    """Parse ``Born: Mon dd, yyyy`` / abbreviated month from sports-reference
    CBB info box HTML ``get_text``."""
    m = re.search(
        r"Born[:\s]+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})",
        t,
        re.IGNORECASE,
    )
    if not m:
        return None
    s = m.group(1).strip()
    s = re.sub(r"\s+", " ", s)
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def slugify(full_name: str) -> str:
    """Generate a first-pass sports-reference slug.

    sports-reference uses ``firstname-lastname-N`` where N is a disambiguator.
    We return without the -N suffix; callers should try -1, -2 etc.
    """
    from . import twokratings as _tk
    return _tk.slugify_name(full_name)


def parse_player_page(html: str) -> dict[str, Any]:
    """Extract the current-season per-game stat row plus physicals."""
    soup = BeautifulSoup(html, "lxml")
    # sports-reference wraps some tables in HTML comments. Un-comment.
    html = html.replace("<!--", "").replace("-->", "")
    soup = BeautifulSoup(html, "lxml")

    out: dict[str, Any] = {}

    # Info box -- height / weight.
    info = soup.find("div", id="info") or soup.find("div", class_="players")
    if info is not None:
        t = info.get_text(" ", strip=True)
        dob = _parse_dob_from_info_text(t)
        if dob:
            out["date_of_birth"] = dob
        m_h = re.search(r"(\d)-(\d{1,2})\b", t)
        if m_h:
            out["height_in"] = int(m_h.group(1)) * 12 + int(m_h.group(2))
        m_w = re.search(r"(\d{3})\s*lb", t)
        if m_w:
            out["weight_lbs"] = float(m_w.group(1))

    # Per-game table: pick most recent season row.
    pg = soup.find("table", id=re.compile(r"players_per_game"))
    if pg is None:
        pg = soup.find("table", id=re.compile(r"per_game"))
    if pg is not None:
        rows = pg.find("tbody").find_all("tr") if pg.find("tbody") else []
        for r in reversed(rows):
            if r.get("class") and "thead" in r.get("class"):
                continue
            cells = {td.get("data-stat"): td.get_text(strip=True) for td in r.find_all("td")}
            if not cells:
                continue
            season = cells.get("year_id") or cells.get("season")
            out["season"] = season
            out["gp"] = _parse_int(cells.get("games"))
            out["min"] = _parse_num(cells.get("mp_per_g"))
            out["pts"] = _parse_num(cells.get("pts_per_g"))
            out["fgm"] = _parse_num(cells.get("fg_per_g"))
            out["fga"] = _parse_num(cells.get("fga_per_g"))
            out["fg_pct"] = _parse_num(cells.get("fg_pct"))
            out["fg3m"] = _parse_num(cells.get("fg3_per_g"))
            out["fg3a"] = _parse_num(cells.get("fg3a_per_g"))
            out["fg3_pct"] = _parse_num(cells.get("fg3_pct"))
            out["ftm"] = _parse_num(cells.get("ft_per_g"))
            out["fta"] = _parse_num(cells.get("fta_per_g"))
            out["ft_pct"] = _parse_num(cells.get("ft_pct"))
            out["oreb"] = _parse_num(cells.get("orb_per_g"))
            out["dreb"] = _parse_num(cells.get("drb_per_g"))
            out["reb"] = _parse_num(cells.get("trb_per_g"))
            out["ast"] = _parse_num(cells.get("ast_per_g"))
            out["tov"] = _parse_num(cells.get("tov_per_g"))
            out["stl"] = _parse_num(cells.get("stl_per_g"))
            out["blk"] = _parse_num(cells.get("blk_per_g"))
            out["pf"] = _parse_num(cells.get("pf_per_g"))
            break

    return out


def fetch_player(slug: str, *, suffix: int = 1,
                 force_refresh: bool = False) -> dict[str, Any]:
    """Fetch + parse ``/cbb/players/<slug>-<suffix>.html``.

    Returns an empty dict on 404 / parse failure.
    """
    url = f"{BASE}/{slug}-{suffix}.html"
    cache_key = f"{slug}-{suffix}"
    try:
        html, from_cache = _http.fetch_text(
            url,
            scope_dir=config.CACHE_CBB,
            cache_key=cache_key,
            suffix=".html",
            force_refresh=force_refresh,
        )
    except Exception as exc:
        log.info("sports-reference fetch failed for %s: %s", url, exc)
        return {}

    data = parse_player_page(html)
    audit.log_event(
        action="scrape_cbb",
        entity_type="prospect",
        entity_slug=slug,
        note=f"url={url}; cache={from_cache}; ok={bool(data)}",
    )
    return data


def bulk_scrape_ncaa_prospects(*, progress_cb=None) -> dict[str, int]:
    """Iterate every NCAA prospect and try to fetch their CBB player page.

    Tries slug suffixes 1..3 (sports-reference disambiguator). Idempotent and
    cached. Returns ``{total, ok, skipped}``.
    """
    from .. import db as _db

    conn = _db.connect()
    try:
        rows = conn.execute(
            "SELECT slug, full_name FROM prospects "
            "WHERE (league IS NULL OR league = 'ncaa') "
            "ORDER BY espn_rank IS NULL, espn_rank, full_name"
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
            sr_slug = slugify(name)
            stats: dict[str, Any] = {}
            for suffix in range(1, 6):
                stats = fetch_player(sr_slug, suffix=suffix)
                if stats:
                    break
            status = "ok" if stats else "no-data"
            if stats:
                dob = stats.pop("date_of_birth", None)
                if isinstance(dob, str) and dob.strip():
                    conn.execute(
                        """
                        UPDATE prospects
                        SET date_of_birth=COALESCE(?, date_of_birth)
                        WHERE slug=?
                        """,
                        (dob.strip()[: 10], slug),
                    )
                n = upsert_stats(conn, slug, stats)
                conn.commit()
                if n:
                    ok += 1
                else:
                    skipped += 1
                    status = "no-gp"
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
        action="scrape_cbb",
        entity_type="prospect",
        note=f"bulk ncaa: total={total} ok={ok} skipped={skipped}",
    )
    return {"total": total, "ok": ok, "skipped": skipped}


def enrich_missing_dates_of_birth(
    *,
    only_missing: bool = True,
    progress_cb: Any = None,
) -> dict[str, int]:
    """Fill ``prospects.date_of_birth`` from sports-reference CBB info boxes.

    Uses the same per-player URLs as the stat scrape (cached). Tries
    disambiguation suffixes 1-5. When ``only_missing`` is True, only rows
    with NULL ``date_of_birth`` are updated.
    """
    from .. import db as _db

    conn = _db.connect()
    try:
        if only_missing:
            rows = conn.execute(
                """
                SELECT slug, full_name, league, date_of_birth
                FROM prospects
                WHERE date_of_birth IS NULL
                  AND (league IS NULL OR league = 'ncaa')
                ORDER BY espn_rank IS NULL, espn_rank, full_name
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT slug, full_name, league, date_of_birth
                FROM prospects
                WHERE (league IS NULL OR league = 'ncaa')
                ORDER BY espn_rank IS NULL, espn_rank, full_name
                """
            ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    got = 0
    not_found = 0
    conn = _db.connect()
    try:
        for i, r in enumerate(rows, start=1):
            slug = r["slug"]
            name = r["full_name"]
            sr_slug = slugify(name)
            data: dict[str, Any] = {}
            for suffix in range(1, 6):
                data = fetch_player(sr_slug, suffix=suffix)
                if data and data.get("date_of_birth"):
                    break
            dob = (data or {}).get("date_of_birth")
            if isinstance(dob, str) and len(dob) >= 8:
                conn.execute(
                    "UPDATE prospects SET date_of_birth=? WHERE slug=?",
                    (dob[: 10], slug),
                )
                conn.commit()
                got += 1
            else:
                not_found += 1
            if progress_cb is not None:
                try:
                    progress_cb(
                        i, total, str(slug), "dob" if dob else "no-dob",
                    )
                except Exception:  # noqa: BLE001
                    pass
    finally:
        conn.close()

    audit.log_event(
        action="dob_enrich_cbb",
        entity_type="prospects",
        note=f"sr cbb: total={total} filled={got} not_found={not_found}",
    )
    return {"total": total, "filled": got, "not_found": not_found}


def upsert_stats(conn, slug: str, stats: dict[str, Any]) -> int:
    """Write scraped per-game stats into ``prospect_stats``."""
    if not stats or not stats.get("gp"):
        return 0
    season = stats.get("season") or config.CURRENT_SEASON
    row = {
        "slug": slug,
        "season": season,
        "league": config.LEAGUE_NCAA,
        "source": "sports-reference",
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
