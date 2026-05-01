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


def prospect_school_slug(school_or_team: str | None) -> str | None:
    """Normalize ``prospects.school_or_team`` for comparison with SR ``/cbb/schools/<slug>/``."""
    if school_or_team is None:
        return None
    s = str(school_or_team).strip().lower()
    if not s:
        return None
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or None


def pick_sr_player_stats(
    candidates: list[dict[str, Any]],
    school_or_team: str | None,
) -> dict[str, Any]:
    """Resolve SR ``firstname-lastname-N`` collisions using ``school_or_team``.

    Several unrelated players can share the same base slug; SR assigns ``-1``,
    ``-2``, … pages. When we have multiple stat payloads, prefer the row whose
    school matches our prospect; a unique candidate is accepted as-is.
    """
    if not candidates:
        return {}
    if len(candidates) == 1:
        return candidates[0]
    want = prospect_school_slug(school_or_team)
    if not want:
        return candidates[0]
    for c in candidates:
        got = (c.get("sr_school_slug") or "").strip().lower()
        if got == want:
            return c
    log.warning(
        "sr cbb: no school match for name collision want=%r got=%s",
        want,
        [x.get("sr_school_slug") for x in candidates],
    )
    return {}


def _cbb_team_slug_from_row(r: Any) -> str | None:
    team_td = r.find("td", {"data-stat": "team_name_abbr"})
    if team_td is None:
        return None
    link = team_td.find("a")
    href = (link.get("href") or "") if link is not None else ""
    m = re.search(r"/cbb/schools/([^/]+)/", href)
    if m:
        return m.group(1).lower()
    return prospect_school_slug(team_td.get_text(strip=True))


def _cbb_primary_table(soup: BeautifulSoup, base_id: str) -> Any:
    """Prefer exact ``id="players_*"``; SR also emits split ids like ``players_per_game.2026``."""
    t = soup.find("table", id=base_id)
    if t is not None:
        return t
    return soup.find("table", id=re.compile(rf"^{re.escape(base_id)}\.\d"))


def _cbb_latest_totals_splits(
    soup: BeautifulSoup,
    *,
    school_slug: str | None,
    prefer_school: bool,
) -> tuple[int | None, float | None, float | None, float | None]:
    """Latest non-career ``players_totals`` row: ``(gp, orb_tot, drb_tot, trb_tot)``."""
    tb = _cbb_primary_table(soup, "players_totals")
    if tb is None:
        return None, None, None, None
    body = tb.find("tbody")
    rows = body.find_all("tr") if body else []

    attempts: list[tuple[str | None, bool]]
    if prefer_school and school_slug:
        attempts = [(school_slug.strip().lower(), True), (None, False)]
    else:
        attempts = [(None, False)]

    for want_slug, _ in attempts:
        for row in reversed(rows):
            if row.get("class") and "thead" in row.get("class"):
                continue
            cells = {
                td.get("data-stat"): td.get_text(strip=True)
                for td in row.find_all("td")
            }
            if not cells:
                continue
            season_raw = cells.get("year_id") or cells.get("season") or ""
            if isinstance(season_raw, str) and "career" in season_raw.strip().lower():
                continue
            slug = _cbb_team_slug_from_row(row)
            if want_slug and slug and slug != want_slug:
                continue
            gp = _parse_int(cells.get("games"))
            if gp is None or gp <= 0:
                continue
            orb_t = _parse_num(cells.get("orb"))
            drb_t = _parse_num(cells.get("drb"))
            trb_t = _parse_num(cells.get("trb"))
            return gp, orb_t, drb_t, trb_t
    return None, None, None, None


def _cbb_merge_totals_splits(soup: BeautifulSoup, stats: dict[str, Any]) -> None:
    """Fill missing per-game ORB/DRB/TRB from season totals ÷ games."""
    school = (stats.get("sr_school_slug") or "").strip().lower() or None
    gp, orb_t, drb_t, trb_t = _cbb_latest_totals_splits(
        soup,
        school_slug=school,
        prefer_school=bool(school),
    )
    if gp is None or gp <= 0:
        return
    if stats.get("oreb") is None and orb_t is not None:
        stats["oreb"] = round(orb_t / gp, 4)
    if stats.get("dreb") is None and drb_t is not None:
        stats["dreb"] = round(drb_t / gp, 4)
    if stats.get("reb") is None and trb_t is not None:
        stats["reb"] = round(trb_t / gp, 4)


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
    pg = _cbb_primary_table(soup, "players_per_game")
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
            season_raw = cells.get("year_id") or cells.get("season") or ""
            if isinstance(season_raw, str) and "career" in season_raw.strip().lower():
                continue
            sr_school_slug = _cbb_team_slug_from_row(r)
            season = cells.get("year_id") or cells.get("season")
            out["season"] = season
            out["sr_school_slug"] = sr_school_slug
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
            # Rare layouts omit split rebounds on per-game rows; totals table fills them.
            if out.get("oreb") is None:
                out["oreb"] = _parse_num(cells.get("orb"))
            if out.get("dreb") is None:
                out["dreb"] = _parse_num(cells.get("drb"))
            if out.get("reb") is None:
                out["reb"] = _parse_num(cells.get("trb"))
            break

    _cbb_merge_totals_splits(soup, out)
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


def bulk_scrape_ncaa_prospects(
    *,
    progress_cb=None,
    force_refresh: bool = False,
) -> dict[str, int]:
    """Iterate every NCAA prospect and try to fetch their CBB player page.

    Tries suffixes ``-1`` … ``-5`` on the SR slug (same name → multiple pages).
    When several pages return stats, picks the row whose school matches
    ``prospects.school_or_team``. Idempotent and cached. Returns ``{total, ok, skipped}``.

    Set ``force_refresh=True`` to ignore on-disk HTML cache (fixes stale/partial pages).
    """
    from .stat_normalize import (
        apply_stat_normalizers,
        merge_missing_stat_fields,
        stats_need_supplemental_fill,
    )
    from .. import db as _db

    conn = _db.connect()
    try:
        rows = conn.execute(
            "SELECT slug, full_name, school_or_team, pos FROM prospects "
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
            candidates: list[dict[str, Any]] = []
            for suffix in range(1, 6):
                chunk = fetch_player(
                    sr_slug, suffix=suffix, force_refresh=force_refresh)
                if chunk.get("gp"):
                    candidates.append(chunk)
            stats = pick_sr_player_stats(candidates, r["school_or_team"])
            status = "ok" if stats else "no-data"
            if not stats and candidates:
                status = "school-mismatch"
            if not stats or not stats.get("gp"):
                from . import espn_mens_cbb as emcb

                espn_row = emcb.fetch_ncaa_stats_for_prospect(
                    full_name=name,
                    school_or_team=r["school_or_team"],
                    prospect_slug=slug,
                    force_refresh=force_refresh,
                )
                if espn_row.get("gp"):
                    stats = espn_row
                    status = "espn-mcb"
            elif stats.get("gp") and stats_need_supplemental_fill(
                stats, config.STAT_COLUMNS
            ):
                from . import espn_mens_cbb as emcb

                espn_row = emcb.fetch_ncaa_stats_for_prospect(
                    full_name=name,
                    school_or_team=r["school_or_team"],
                    prospect_slug=slug,
                    force_refresh=force_refresh,
                )
                if espn_row.get("gp") and merge_missing_stat_fields(
                    stats, espn_row, config.STAT_COLUMNS
                ):
                    stats["_stats_source"] = "sports-reference+espn-mcb"
                    status = "sr+espn"
            if stats:
                dob = stats.pop("date_of_birth", None)
                stats.pop("sr_school_slug", None)
                src = stats.pop("_stats_source", "sports-reference")
                apply_stat_normalizers(stats, pos=r["pos"])
                if isinstance(dob, str) and dob.strip():
                    conn.execute(
                        """
                        UPDATE prospects
                        SET date_of_birth=COALESCE(?, date_of_birth)
                        WHERE slug=?
                        """,
                        (dob.strip()[: 10], slug),
                    )
                n = upsert_stats(conn, slug, stats, source=src)
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
    """Fill ``prospects.date_of_birth``.

    1. **Sports-Reference CBB** info box (NCAA prospects only) — SR often omits
       ``Born:`` on current pages; kept for legacy cache / edge cases.
    2. **Wikidata P569** via English Wikipedia title search — primary source now.

    When ``only_missing`` is True, only rows with NULL ``date_of_birth`` are updated.
    """
    from . import wikidata as _wikidata
    from .. import db as _db

    conn = _db.connect()
    try:
        if only_missing:
            rows = conn.execute(
                """
                SELECT slug, full_name, league, date_of_birth, school_or_team
                FROM prospects
                WHERE date_of_birth IS NULL
                ORDER BY espn_rank IS NULL, espn_rank, full_name
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT slug, full_name, league, date_of_birth, school_or_team
                FROM prospects
                ORDER BY espn_rank IS NULL, espn_rank, full_name
                """
            ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    got = 0
    not_found = 0
    from_sr = 0
    from_wd = 0
    conn = _db.connect()
    try:
        for i, r in enumerate(rows, start=1):
            slug = r["slug"]
            name = r["full_name"]
            league = (r["league"] or "").strip().lower()
            dob: str | None = None
            status = "no-dob"

            if league in ("", "ncaa"):
                sr_slug = slugify(name)
                candidates: list[dict[str, Any]] = []
                for suffix in range(1, 6):
                    chunk = fetch_player(sr_slug, suffix=suffix)
                    if chunk:
                        candidates.append(chunk)
                data = pick_sr_player_stats(candidates, r["school_or_team"])
                dob = (data or {}).get("date_of_birth")
                if isinstance(dob, str) and len(dob) >= 8:
                    from_sr += 1
                    status = "dob-sr"

            if not dob:
                try:
                    wd = _wikidata.birth_date_iso_for_person(name)
                except Exception as exc:  # noqa: BLE001
                    log.info("wikidata dob lookup failed for %r: %s", name, exc)
                    wd = None
                if isinstance(wd, str) and len(wd) >= 8:
                    dob = wd[:10]
                    from_wd += 1
                    status = "dob-wikidata"

            if isinstance(dob, str) and len(dob) >= 8:
                conn.execute(
                    "UPDATE prospects SET date_of_birth=? WHERE slug=?",
                    (dob[:10], slug),
                )
                conn.commit()
                got += 1
            else:
                not_found += 1

            if progress_cb is not None:
                try:
                    progress_cb(i, total, str(slug), status)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        conn.close()

    audit.log_event(
        action="dob_enrich_cbb",
        entity_type="prospects",
        note=(
            f"total={total} filled={got} not_found={not_found} "
            f"from_sr={from_sr} from_wikidata={from_wd}"
        ),
    )
    return {
        "total": total,
        "filled": got,
        "not_found": not_found,
        "from_sr": from_sr,
        "from_wikidata": from_wd,
    }


def upsert_stats(
    conn,
    slug: str,
    stats: dict[str, Any],
    *,
    source: str = "sports-reference",
) -> int:
    """Write scraped per-game stats into ``prospect_stats``."""
    if not stats or not stats.get("gp"):
        return 0
    season = stats.get("season") or config.CURRENT_SEASON
    row = {
        "slug": slug,
        "season": season,
        "league": config.LEAGUE_NCAA,
        "source": source,
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
