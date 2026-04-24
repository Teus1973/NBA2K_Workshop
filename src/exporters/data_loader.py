"""
Shared data loaders for exporters + UI tabs.

Each function returns a ``pandas.DataFrame`` ready for display / export.
Keeps the SQL in one place so the Streamlit tabs, Excel writer, and
Google-Sheets writer all show identical data.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from .. import config, db
from ..formatting import height_in_to_ft_str, normalize_full_name
from ..logger import get_logger
from ..scrapers import twokratings as _twok

log = get_logger("exporters.data_loader")


# ---------------------------------------------------------------------------
def nba_roster_match_sets(conn: sqlite3.Connection) -> tuple[set[str], set[str]]:
    """Slugs + normalized full names for current ``nba_players`` rows."""
    slugs: set[str] = set()
    names: set[str] = set()
    for row in conn.execute("SELECT slug, full_name FROM nba_players"):
        if row["slug"]:
            slugs.add(row["slug"])
        if row["full_name"]:
            slugs.add(_twok.slugify_name(row["full_name"]))
            names.add(normalize_full_name(row["full_name"]))
    return slugs, names


def is_prospect_on_nba_roster(
    slug: str | None,
    full_name: str | None,
    nba_slugs: set[str],
    nba_names: set[str],
) -> bool:
    if slug and slug in nba_slugs:
        return True
    fn = normalize_full_name(full_name or "")
    return bool(fn and fn in nba_names)


# ---------------------------------------------------------------------------
def _coalesce_nba_physicals(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer NBA bio values; fill gaps from combine anthro (wingspan often missing in API)."""
    if df.empty:
        return df
    if not any(
        c in df.columns
        for c in ("wingspan_in_combine_m", "weight_lbs_combine_m", "height_w_shoes_in")
    ):
        return df
    out = df.copy()
    if "wingspan_in_combine_m" in out.columns:
        out["wingspan_in"] = out["wingspan_in"].fillna(
            out["wingspan_in_combine_m"])
    if "weight_lbs_combine_m" in out.columns:
        out["weight_lbs"] = out["weight_lbs"].fillna(out["weight_lbs_combine_m"])
    if "height_w_shoes_in" in out.columns:
        out["height_in"] = out["height_in"].fillna(out["height_w_shoes_in"])
    return out


def _coalesce_prospect_physicals(df: pd.DataFrame) -> pd.DataFrame:
    """Fill listing-height / weight / wingspan from prospect combine merge."""
    if df.empty:
        return df
    if not any(
        c in df.columns
        for c in ("wingspan_in_combine_m", "weight_lbs_combine_m", "height_w_shoes_in")
    ):
        return df
    out = df.copy()
    if "wingspan_in_combine_m" in out.columns:
        out["wingspan_in"] = out["wingspan_in"].fillna(
            out["wingspan_in_combine_m"])
    if "weight_lbs_combine_m" in out.columns:
        out["weight_lbs"] = out["weight_lbs"].fillna(out["weight_lbs_combine_m"])
    if "height_w_shoes_in" in out.columns:
        out["height_in"] = out["height_in"].fillna(out["height_w_shoes_in"])
    return out


# ---------------------------------------------------------------------------
def _maybe_conn(conn: sqlite3.Connection | None) -> tuple[sqlite3.Connection, bool]:
    if conn is not None:
        return conn, False
    return db.connect(), True


def load_reference_df(
    season: str = config.CURRENT_SEASON,
    season_type: str = "Regular",
    *,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Reference sheet: one row per NBA player with ratings + stats + combine."""
    conn, own = _maybe_conn(conn)
    try:
        players = pd.read_sql_query("SELECT * FROM nba_players", conn)
        ratings = pd.read_sql_query("SELECT * FROM nba_ratings_2k26", conn)
        stats = pd.read_sql_query(
            "SELECT * FROM nba_stats_season WHERE season=? AND season_type=?",
            conn,
            params=[season, season_type],
        )
        comb_m = pd.read_sql_query("SELECT * FROM combine_measurements", conn)
        comb_d = pd.read_sql_query("SELECT * FROM combine_drills", conn)
    finally:
        if own:
            conn.close()

    if players.empty:
        return pd.DataFrame()

    df = players.merge(ratings, on="player_id", how="left",
                       suffixes=("", "_rating"))
    if not stats.empty:
        df = df.merge(stats, on="player_id", how="left",
                      suffixes=("", "_stats"))

    # Keep most-recent combine row per player.
    if not comb_m.empty:
        comb_m["_pid"] = comb_m["subject_key"].str.replace("nba:", "", regex=False)
        comb_m = comb_m[comb_m["_pid"].str.isdigit()].copy()
        comb_m["player_id"] = comb_m["_pid"].astype(int)
        comb_m = comb_m.sort_values("year").drop_duplicates(
            "player_id", keep="last")
        df = df.merge(
            comb_m.drop(columns=["_pid", "subject_key"], errors="ignore"),
            on="player_id", how="left", suffixes=("", "_combine_m"))
    if not comb_d.empty:
        comb_d["_pid"] = comb_d["subject_key"].str.replace("nba:", "", regex=False)
        comb_d = comb_d[comb_d["_pid"].str.isdigit()].copy()
        comb_d["player_id"] = comb_d["_pid"].astype(int)
        comb_d = comb_d.sort_values("year").drop_duplicates(
            "player_id", keep="last")
        df = df.merge(
            comb_d.drop(columns=["_pid", "subject_key"], errors="ignore"),
            on="player_id", how="left", suffixes=("", "_combine_d"))

    df = _coalesce_nba_physicals(df)

    df = df.copy()
    df["height_ft"] = df["height_in"].apply(height_in_to_ft_str)

    # Preferred column ordering for the UI.
    front = [
        "player_id", "last_name", "first_name", "team", "pos", "age",
        "height_in", "height_ft", "weight_lbs", "wingspan_in",
    ]
    rating_cols = [c for c in config.RATING_ATTRIBUTES if c in df.columns]
    stat_cols = [c for c in config.STAT_COLUMNS if c in df.columns]
    combine_cols = [c for c in (
        "height_wo_shoes_in", "height_w_shoes_in", "std_reach_in",
        "body_fat_pct", "lane_agility_sec", "shuttle_sec",
        "three_quarter_sprint_sec", "standing_vert_in", "max_vert_in",
        "bench_reps", "c_speed_2k", "c_speed_with_ball_2k",
        "c_vertical_2k", "c_agility_2k",
    ) if c in df.columns]
    order = [c for c in front if c in df.columns] + rating_cols + stat_cols + combine_cols
    rest = [c for c in df.columns if c not in order]
    return df[order + rest]


# ---------------------------------------------------------------------------
def load_prospects_df(
    *,
    conn: sqlite3.Connection | None = None,
    latest_season_only: bool = True,
    exclude_current_nba: bool = True,
) -> pd.DataFrame:
    """Prospects sheet: one row per prospect with stats + combine + computed ratings.

    When ``exclude_current_nba`` is True, rows matching a current ``nba_players``
    slug or full name are dropped (ESPN boards sometimes list NBA veterans).
    """
    conn, own = _maybe_conn(conn)
    try:
        nba_slugs: set[str] = set()
        nba_names: set[str] = set()
        if exclude_current_nba:
            nba_slugs, nba_names = nba_roster_match_sets(conn)

        prospects = pd.read_sql_query("SELECT * FROM prospects", conn)
        if prospects.empty:
            return pd.DataFrame()
        if exclude_current_nba and (nba_slugs or nba_names):
            mask = ~prospects.apply(
                lambda r: is_prospect_on_nba_roster(
                    r.get("slug"), r.get("full_name"),
                    nba_slugs, nba_names),
                axis=1,
            )
            prospects = prospects[mask].copy()
            if prospects.empty:
                return pd.DataFrame()
        stats = pd.read_sql_query("SELECT * FROM prospect_stats", conn)
        comb_m = pd.read_sql_query(
            "SELECT * FROM combine_measurements "
            "WHERE subject_key LIKE 'prospect:%'", conn)
        comb_d = pd.read_sql_query(
            "SELECT * FROM combine_drills "
            "WHERE subject_key LIKE 'prospect:%'", conn)
        computed = pd.read_sql_query(
            "SELECT * FROM prospect_ratings_computed", conn)
    finally:
        if own:
            conn.close()

    df = prospects.copy()
    if not stats.empty:
        if latest_season_only:
            stats = stats.sort_values("season").drop_duplicates(
                "slug", keep="last")
        df = df.merge(stats, on="slug", how="left", suffixes=("", "_stats"))
    if not comb_m.empty:
        comb_m["slug"] = comb_m["subject_key"].str.replace(
            "prospect:", "", regex=False)
        comb_m = comb_m.drop(columns=["subject_key"])
        comb_m = comb_m.sort_values("year").drop_duplicates("slug", keep="last")
        df = df.merge(comb_m, on="slug", how="left", suffixes=("", "_combine_m"))
    if not comb_d.empty:
        comb_d["slug"] = comb_d["subject_key"].str.replace(
            "prospect:", "", regex=False)
        comb_d = comb_d.drop(columns=["subject_key"])
        comb_d = comb_d.sort_values("year").drop_duplicates("slug", keep="last")
        df = df.merge(comb_d, on="slug", how="left", suffixes=("", "_combine_d"))
    if not computed.empty:
        df = df.merge(computed, on="slug", how="left",
                      suffixes=("", "_rating"))

    df = _coalesce_prospect_physicals(df)
    h_disp = df["height_in"]
    if "height_wo_shoes_in" in df.columns:
        h_disp = h_disp.fillna(df["height_wo_shoes_in"])
    df = df.copy()
    df["height_ft"] = h_disp.apply(height_in_to_ft_str)

    front = [
        "espn_rank", "slug", "last_name", "first_name", "pos",
        "school_or_team", "league", "age", "height_in", "height_ft",
        "weight_lbs", "wingspan_in", "status",
    ]
    rating_cols = [c for c in config.RATING_ATTRIBUTES if c in df.columns]
    stat_cols = [c for c in config.STAT_COLUMNS if c in df.columns]
    combine_cols = [c for c in (
        "height_wo_shoes_in", "height_w_shoes_in", "std_reach_in",
        "body_fat_pct", "lane_agility_sec", "shuttle_sec",
        "three_quarter_sprint_sec", "standing_vert_in", "max_vert_in",
        "bench_reps", "c_speed_2k", "c_speed_with_ball_2k",
        "c_vertical_2k", "c_agility_2k",
    ) if c in df.columns]
    # Stats before long rating block so per-game numbers stay visible when scrolling.
    order = (
        [c for c in front if c in df.columns] + stat_cols + rating_cols
        + combine_cols
    )
    rest = [c for c in df.columns if c not in order]
    out = df[order + rest].copy()
    if "espn_rank" in out.columns:
        out = out.sort_values("espn_rank", na_position="last")
    return out


# ---------------------------------------------------------------------------
def load_audit_df(
    limit: int = 2000,
    *,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Audit log as a DataFrame (newest first)."""
    conn, own = _maybe_conn(conn)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            conn, params=[int(limit)])
    finally:
        if own:
            conn.close()
    return df


def load_formulas_df(
    *,
    conn: sqlite3.Connection | None = None,
) -> pd.DataFrame:
    """Latest formula version per attribute."""
    conn, own = _maybe_conn(conn)
    try:
        df = pd.read_sql_query(
            """
            SELECT attribute, MAX(version) AS version, r2, mae, n_samples,
                   edited_at, edited_by, notes, yaml_blob
            FROM formulas GROUP BY attribute
            ORDER BY attribute
            """,
            conn,
        )
    finally:
        if own:
            conn.close()
    return df
