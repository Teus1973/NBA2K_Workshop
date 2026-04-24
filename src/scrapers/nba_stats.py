"""
NBA regular-season + playoff stats via nba_api.

Wraps ``LeagueDashPlayerStats``, ``LeagueDashPlayerBioStats``, and
``CommonPlayerInfo`` with retry/backoff and local JSON caching.

All columns are normalised to our snake_case schema
(``nba_stats_season`` and ``nba_players``).
"""

from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
from nba_api.stats.endpoints import (
    commonplayerinfo,
    leaguedashplayerbiostats,
    leaguedashplayerstats,
)

from .. import audit, config
from ..logger import get_logger

log = get_logger("scrapers.nba_stats")


# ---------------------------------------------------------------------------
# Retry wrapper (shared with nba_combine; duplicated here to avoid a cycle).
# ---------------------------------------------------------------------------
def _retry(call, *, name: str, max_attempts: int = 4, base_delay: float = 1.5):
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:
            last_exc = exc
            wait = base_delay * (2 ** (attempt - 1))
            log.warning("nba_api %s failed (attempt %d/%d): %s; sleep %.1fs",
                        name, attempt, max_attempts, exc, wait)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# League-wide dashboards
# ---------------------------------------------------------------------------
def fetch_season_totals(
    season: str = config.CURRENT_SEASON,
    season_type: str = "Regular Season",
    *,
    per_mode: str = "Totals",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch LeagueDashPlayerStats totals for the given season.

    ``season`` is NBA notation ("2025-26"). ``season_type`` is one of
    "Regular Season", "Playoffs", "Pre Season", "All Star".
    """
    slug = season.replace("-", "") + "_" + season_type.replace(" ", "").lower()
    cache = config.CACHE_NBA / f"dash_{slug}_{per_mode.lower()}.json"
    if cache.is_file() and not force_refresh:
        return pd.DataFrame(json.loads(cache.read_text(encoding="utf-8")))

    def call():
        ep = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star=season_type,
            per_mode_detailed=per_mode,
        )
        return ep.get_data_frames()[0]

    df = _retry(call, name="LeagueDashPlayerStats")
    cache.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_stats", entity_type="nba_player",
                    note=f"{season} {season_type} {per_mode}: {len(df)} rows")
    return df


def fetch_bio_stats(
    season: str = config.CURRENT_SEASON,
    season_type: str = "Regular Season",
    *,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch LeagueDashPlayerBioStats (height/weight/age/college/country)."""
    slug = season.replace("-", "") + "_" + season_type.replace(" ", "").lower()
    cache = config.CACHE_NBA / f"bio_{slug}.json"
    if cache.is_file() and not force_refresh:
        return pd.DataFrame(json.loads(cache.read_text(encoding="utf-8")))

    def call():
        ep = leaguedashplayerbiostats.LeagueDashPlayerBioStats(
            season=season, season_type_all_star=season_type,
        )
        return ep.get_data_frames()[0]

    df = _retry(call, name="LeagueDashPlayerBioStats")
    cache.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_stats", entity_type="nba_player",
                    note=f"bio {season}: {len(df)} rows")
    return df


def fetch_player_info(player_id: int, *, force_refresh: bool = False,
                      ttl_seconds: int = 30 * 24 * 3600) -> dict[str, Any]:
    """CommonPlayerInfo for a single player_id. Cached 30d by default."""
    cache = config.CACHE_NBA / f"player_{player_id}.json"
    if cache.is_file() and not force_refresh:
        age = time.time() - cache.stat().st_mtime
        if age < ttl_seconds:
            return json.loads(cache.read_text(encoding="utf-8"))

    def call():
        ep = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
        return ep.get_data_frames()[0].iloc[0].to_dict()

    info = _retry(call, name="CommonPlayerInfo")
    cache.write_text(json.dumps(info, default=str), encoding="utf-8")
    return info


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _height_in_from_nba_height(h: Any) -> float | None:
    """Parse NBA 'H_FT-H_IN' style height ("6-9") into total inches."""
    if h is None:
        return None
    s = str(h).strip()
    if "-" in s:
        try:
            ft, inch = s.split("-")
            return float(int(ft) * 12 + int(inch))
        except (ValueError, AttributeError):
            return None
    try:
        return float(s)
    except ValueError:
        return None


def season_stats_rows(
    totals_df: pd.DataFrame, season: str, season_type: str = "Regular",
) -> list[dict[str, Any]]:
    """Translate a LeagueDashPlayerStats frame to ``nba_stats_season`` rows.

    Produces per-game derived stats where the source gives only totals
    (GP -> per-game PTS etc.).
    """
    rows: list[dict[str, Any]] = []
    for _, r in totals_df.iterrows():
        gp = int(r.get("GP", 0) or 0)
        if gp <= 0:
            continue
        pts = _f(r.get("PTS"))
        minutes = _f(r.get("MIN"))
        per_game = (lambda x: (x / gp) if (x is not None and gp) else None)
        rows.append({
            "player_id": int(r["PLAYER_ID"]),
            "season": season,
            "season_type": season_type,
            "gp": gp,
            "min": per_game(minutes),
            "pts": per_game(pts),
            "fgm": per_game(_f(r.get("FGM"))),
            "fga": per_game(_f(r.get("FGA"))),
            "fg_pct": _f(r.get("FG_PCT")),
            "fg3m": per_game(_f(r.get("FG3M"))),
            "fg3a": per_game(_f(r.get("FG3A"))),
            "fg3_pct": _f(r.get("FG3_PCT")),
            "ftm": per_game(_f(r.get("FTM"))),
            "fta": per_game(_f(r.get("FTA"))),
            "ft_pct": _f(r.get("FT_PCT")),
            "oreb": per_game(_f(r.get("OREB"))),
            "dreb": per_game(_f(r.get("DREB"))),
            "reb": per_game(_f(r.get("REB"))),
            "ast": per_game(_f(r.get("AST"))),
            "tov": per_game(_f(r.get("TOV"))),
            "stl": per_game(_f(r.get("STL"))),
            "blk": per_game(_f(r.get("BLK"))),
            "pf": per_game(_f(r.get("PF"))),
            "source": "nba_api",
        })
    return rows


def bio_rows(bio_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Translate LeagueDashPlayerBioStats to ``nba_players`` rows."""
    from . import twokratings as _tk
    rows: list[dict[str, Any]] = []
    for _, r in bio_df.iterrows():
        full = str(r.get("PLAYER_NAME") or "").strip()
        parts = full.split(" ", 1)
        first = parts[0] if parts else None
        last = parts[1] if len(parts) > 1 else None
        age = _f(r.get("AGE"))
        rows.append({
            "player_id": int(r["PLAYER_ID"]),
            "slug": _tk.slugify_name(full) if full else None,
            "first_name": first,
            "last_name": last,
            "full_name": full,
            "team": str(r.get("TEAM_ABBREVIATION") or "") or None,
            "pos": None,
            "height_in": _height_in_from_nba_height(r.get("PLAYER_HEIGHT_INCHES")
                                                    or r.get("PLAYER_HEIGHT")),
            "weight_lbs": _f(r.get("PLAYER_WEIGHT")),
            "wingspan_in": None,
            "age": age,
            "birthdate": None,
        })
    return rows


# ---------------------------------------------------------------------------
# DB writer
# ---------------------------------------------------------------------------
def upsert_players(conn, bio_rows_list: list[dict[str, Any]]) -> int:
    cur = conn.cursor()
    for r in bio_rows_list:
        cur.execute(
            """
            INSERT INTO nba_players
                (player_id, slug, first_name, last_name, full_name, team, pos,
                 height_in, weight_lbs, wingspan_in, age, birthdate)
            VALUES (:player_id, :slug, :first_name, :last_name, :full_name,
                    :team, :pos, :height_in, :weight_lbs, :wingspan_in,
                    :age, :birthdate)
            ON CONFLICT(player_id) DO UPDATE SET
                slug      =COALESCE(excluded.slug, nba_players.slug),
                first_name=excluded.first_name,
                last_name =excluded.last_name,
                full_name =excluded.full_name,
                team      =excluded.team,
                pos       =COALESCE(excluded.pos, nba_players.pos),
                height_in =COALESCE(excluded.height_in, nba_players.height_in),
                weight_lbs=COALESCE(excluded.weight_lbs, nba_players.weight_lbs),
                age       =excluded.age,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            r,
        )
    return len(bio_rows_list)


def upsert_stats(conn, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    cur = conn.cursor()
    cols = ["player_id", "season", "season_type", *config.STAT_COLUMNS, "source"]
    placeholders = ",".join(f":{c}" for c in cols)
    on_conf = ",\n".join(f"{c}=excluded.{c}" for c in cols if c not in (
        "player_id", "season", "season_type"))
    sql = f"""
        INSERT INTO nba_stats_season ({",".join(cols)})
        VALUES ({placeholders})
        ON CONFLICT(player_id, season, season_type) DO UPDATE SET
            {on_conf},
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
    """
    for r in rows:
        cur.execute(sql, r)
    return len(rows)
