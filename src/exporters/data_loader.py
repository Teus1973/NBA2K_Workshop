"""
Shared data loaders for exporters + UI tabs.

Each function returns a ``pandas.DataFrame`` ready for display / export.
Keeps the SQL in one place so the Streamlit tabs, Excel writer, and
Google-Sheets writer all show identical data.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Collection
from typing import Any

import pandas as pd

from .. import config, db
from ..formatting import height_in_to_ft_str, normalize_full_name
from ..logger import get_logger
from ..scrapers import twokratings as _twok

log = get_logger("exporters.data_loader")


def _normalize_slug_filter(slugs: Collection[str] | None) -> tuple[str, ...] | None:
    if not slugs:
        return None
    cleaned = tuple(
        dict.fromkeys(s.strip() for s in slugs if s is not None and str(s).strip()))
    return cleaned or None


def _sanitize_row_for_rating_input(row: pd.Series) -> dict[str, Any]:
    """Flatten a merged prospect row into primitives suitable for SQLite + formulas."""
    d: dict[str, Any] = {}
    for k, v in row.items():
        if v is pd.NA:
            d[k] = None
            continue
        try:
            if pd.isna(v):
                d[k] = None
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            d[k] = None
            continue
        d[k] = v
    return d


def load_single_prospect_row_dict_for_rating(
    slug: str,
    *,
    conn: sqlite3.Connection | None = None,
    latest_season_only: bool = True,
    exclude_current_nba: bool = False,
) -> dict[str, Any] | None:
    """Assemble stats + combine + coalesced physicals identical to :func:`load_prospects_df`.

    Prospect-tab single recompute historically merged raw SQLite rows without
    ``combine_*`` renames → formulas saw missing combine inputs; always use this
    or :func:`load_prospects_df` with ``slugs=`` for parity with bulk compute.
    """
    slug_clean = slug.strip()
    if not slug_clean:
        return None
    df = load_prospects_df(
        conn=conn,
        latest_season_only=latest_season_only,
        exclude_current_nba=exclude_current_nba,
        slugs=[slug_clean],
    )
    if df.empty:
        return None
    return _sanitize_row_for_rating_input(df.iloc[0])


def _series_height_ft_from_display_inches(series: pd.Series) -> pd.Series:
    """``height_ft`` helper: fractional inches match combine-style tapes when helpful."""

    def cell(h: object) -> str | None:
        if h is pd.NA:
            return None
        try:
            if pd.isna(h):
                return None
        except (TypeError, ValueError):
            pass
        try:
            v = float(h)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        frac_tail = abs(v - round(v))
        return height_in_to_ft_str(
            v,
            fractional_inches=(frac_tail >= 0.04),
        )

    return series.map(cell)


def _to_merge_int64(series: pd.Series | None) -> pd.Series | None:
    """Nullable pandas Int64 merge key (NBA combine / roster ids align with ints)."""
    if series is None:
        return None
    return pd.to_numeric(series, errors="coerce").astype("Int64")


# Official anthro → formula merge: height **without shoes** aligns with 2K regression baseline
# (avoids ~+1–1.5\" inflation vs height w/shoes for strength / vertical models).
_COMBINE_MEASUREMENT_PHYSICAL_RENAME = {
    "wingspan_in": "combine_wingspan_in",
    "height_wo_shoes_in": "combine_height_in",
    "weight_lbs": "combine_weight_lbs",
}


def _prep_combine_measurements_nba(comb_m: pd.DataFrame) -> pd.DataFrame:
    cm = comb_m[comb_m["subject_key"].str.startswith("nba:", na=False)].copy()
    if cm.empty:
        return pd.DataFrame()
    cm["_nba_pid"] = cm["subject_key"].str[4:]
    cm = cm[cm["_nba_pid"].str.match(r"^\d+$", na=False)]
    if cm.empty:
        return pd.DataFrame()
    cm["_nba_pid"] = _to_merge_int64(cm["_nba_pid"])
    cm = cm.sort_values("year").drop_duplicates("_nba_pid", keep="last")
    drop_meta = {"subject_key", "updated_at"}
    cols = [c for c in cm.columns if c not in drop_meta]
    cm = cm[cols].rename(columns=_COMBINE_MEASUREMENT_PHYSICAL_RENAME)
    if "combine_height_in" not in cm.columns:
        cm["combine_height_in"] = pd.NA
    cm = cm.rename(columns={"_nba_pid": "nba_id"})
    return cm


def _prep_combine_measurements_prospect_slug(comb_m: pd.DataFrame) -> pd.DataFrame:
    cm = comb_m[comb_m["subject_key"].str.startswith(
        "prospect:", na=False)].copy()
    if cm.empty:
        return pd.DataFrame()
    cm["slug"] = cm["subject_key"].str[len("prospect:"):]
    cm = cm.sort_values("year").drop_duplicates("slug", keep="last")
    drop_meta = {"subject_key", "updated_at"}
    keep = ["slug"] + [
        c for c in cm.columns
        if c not in drop_meta and c != "slug"
    ]
    out = cm[keep].rename(columns=_COMBINE_MEASUREMENT_PHYSICAL_RENAME)
    if "combine_height_in" not in out.columns:
        out["combine_height_in"] = pd.NA
    return out


def _prep_combine_drills_nba(comb_d: pd.DataFrame) -> pd.DataFrame:
    cd = comb_d[comb_d["subject_key"].str.startswith("nba:", na=False)].copy()
    if cd.empty:
        return pd.DataFrame()
    cd["_nba_pid"] = cd["subject_key"].str[4:]
    cd = cd[cd["_nba_pid"].str.match(r"^\d+$", na=False)]
    if cd.empty:
        return pd.DataFrame()
    cd["_nba_pid"] = _to_merge_int64(cd["_nba_pid"])
    cd = cd.sort_values("year").drop_duplicates("_nba_pid", keep="last")
    drop_meta = {"subject_key", "updated_at"}
    cols = [c for c in cd.columns if c not in drop_meta]
    cd = cd[cols].rename(columns={"_nba_pid": "nba_id"})
    return cd


def _prep_combine_drills_prospect_slug(comb_d: pd.DataFrame) -> pd.DataFrame:
    cd = comb_d[comb_d["subject_key"].str.startswith(
        "prospect:", na=False)].copy()
    if cd.empty:
        return pd.DataFrame()
    cd["slug"] = cd["subject_key"].str[len("prospect:"):]
    cd = cd.sort_values("year").drop_duplicates("slug", keep="last")
    drop_meta = {"subject_key", "updated_at"}
    extra = [c for c in cd.columns if c not in drop_meta and c != "slug"]
    return cd[["slug"] + extra]


def _coalesce_merge_suffix(
    df: pd.DataFrame,
    base_cols: list[str],
    suffix: str,
) -> pd.DataFrame:
    for c in base_cols:
        r = f"{c}{suffix}"
        if r not in df.columns:
            continue
        if c not in df.columns:
            df[c] = df[r]
        else:
            df[c] = df[c].where(df[c].notna(), df[r])
        df.drop(columns=[r], inplace=True, errors="ignore")
    return df


def _merge_prospect_combine_measurements(
    df: pd.DataFrame,
    comb_m: pd.DataFrame,
) -> pd.DataFrame:
    """Left-join NBA combine anthro rows by nullable ``nba_id``, then ``slug``.

    Rows keyed ``prospect:{slug}`` fill gaps when roster id linking failed.
    """
    if df.empty or comb_m.empty:
        return df
    df = df.copy()
    if "nba_id" in df.columns:
        df["nba_id"] = _to_merge_int64(df["nba_id"])
    nba = _prep_combine_measurements_nba(comb_m)
    if not nba.empty and "nba_id" in df.columns:
        df = df.merge(nba, on="nba_id", how="left")
    slug_part = _prep_combine_measurements_prospect_slug(comb_m)
    if not slug_part.empty:
        phys = [c for c in slug_part.columns if c != "slug"]
        df = df.merge(slug_part, on="slug", how="left", suffixes=("", "_pcomb"))
        df = _coalesce_merge_suffix(df, phys, "_pcomb")
    return df


def _merge_prospect_combine_drills(
    df: pd.DataFrame,
    comb_d: pd.DataFrame,
) -> pd.DataFrame:
    if df.empty or comb_d.empty:
        return df
    df = df.copy()
    if "nba_id" in df.columns:
        df["nba_id"] = _to_merge_int64(df["nba_id"])
    nba = _prep_combine_drills_nba(comb_d)
    if not nba.empty and "nba_id" in df.columns:
        df = df.merge(nba, on="nba_id", how="left")
    slug_part = _prep_combine_drills_prospect_slug(comb_d)
    if not slug_part.empty:
        dcols = [c for c in slug_part.columns if c != "slug"]
        df = df.merge(
            slug_part, on="slug", how="left", suffixes=("", "_pdrl"),
        )
        df = _coalesce_merge_suffix(df, dcols, "_pdrl")
    return df


def round_float_columns_for_display(
    df: pd.DataFrame, *, ndigits: int = 2
) -> pd.DataFrame:
    """Round float columns for Streamlit tables and CSV export consistency."""
    if df.empty:
        return df
    out = df.copy()
    for c in out.columns:
        if pd.api.types.is_float_dtype(out[c]):
            out[c] = out[c].round(ndigits)
    return out


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
    """Apply official combine anthro first, then listing / bio values."""
    if df.empty:
        return df
    out = df.copy()
    idx = out.index
    if "combine_wingspan_in" in out.columns:
        base = (
            out["wingspan_in"]
            if "wingspan_in" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["wingspan_in"] = out["combine_wingspan_in"].where(
            out["combine_wingspan_in"].notna(), base)
    if "combine_height_in" in out.columns:
        base = (
            out["height_in"]
            if "height_in" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["height_in"] = out["combine_height_in"].where(
            out["combine_height_in"].notna(), base)
    if "combine_weight_lbs" in out.columns:
        base = (
            out["weight_lbs"]
            if "weight_lbs" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["weight_lbs"] = out["combine_weight_lbs"].where(
            out["combine_weight_lbs"].notna(), base)
    return out


def _coalesce_prospect_physicals(df: pd.DataFrame) -> pd.DataFrame:
    """Combine measurements override listing/school physicals when present."""
    if df.empty:
        return df
    if not any(
        c in df.columns
        for c in ("combine_wingspan_in", "combine_height_in", "combine_weight_lbs")
    ):
        return df
    out = df.copy()
    idx = out.index
    if "combine_wingspan_in" in out.columns:
        base = (
            out["wingspan_in"]
            if "wingspan_in" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["wingspan_in"] = out["combine_wingspan_in"].where(
            out["combine_wingspan_in"].notna(), base)
    if "combine_height_in" in out.columns:
        base = (
            out["height_in"]
            if "height_in" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["height_in"] = out["combine_height_in"].where(
            out["combine_height_in"].notna(), base)
    if "combine_weight_lbs" in out.columns:
        base = (
            out["weight_lbs"]
            if "weight_lbs" in out.columns
            else pd.Series(float("nan"), index=idx, dtype="float64")
        )
        out["weight_lbs"] = out["combine_weight_lbs"].where(
            out["combine_weight_lbs"].notna(), base)
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

    # Keep most-recent combine row per player (official anthro → combine_* names).
    if not comb_m.empty:
        nba_m = _prep_combine_measurements_nba(comb_m)
        if not nba_m.empty:
            nba_m = nba_m.rename(columns={"nba_id": "player_id"})
            df["player_id"] = _to_merge_int64(df["player_id"])
            nba_m["player_id"] = _to_merge_int64(nba_m["player_id"])
            df = df.merge(nba_m, on="player_id", how="left", suffixes=("", "_cm"))
    if not comb_d.empty:
        nba_d = _prep_combine_drills_nba(comb_d)
        if not nba_d.empty:
            nba_d = nba_d.rename(columns={"nba_id": "player_id"})
            df["player_id"] = _to_merge_int64(df["player_id"])
            nba_d["player_id"] = _to_merge_int64(nba_d["player_id"])
            df = df.merge(nba_d, on="player_id", how="left", suffixes=("", "_cd"))

    df = _coalesce_nba_physicals(df)

    df = df.copy()
    disp = df["height_in"].copy()
    if "height_wo_shoes_in" in df.columns:
        disp = disp.fillna(df["height_wo_shoes_in"])
    df = df.assign(height_ft=_series_height_ft_from_display_inches(disp))

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
    return round_float_columns_for_display(df[order + rest])


# ---------------------------------------------------------------------------
def load_prospects_df(
    *,
    conn: sqlite3.Connection | None = None,
    latest_season_only: bool = True,
    exclude_current_nba: bool = True,
    slugs: Collection[str] | None = None,
) -> pd.DataFrame:
    """Prospects sheet: one row per prospect with stats + combine + computed ratings.

    When ``exclude_current_nba`` is True, rows matching a current ``nba_players``
    slug or full name are dropped (ESPN boards sometimes list NBA veterans).

    ``slugs`` optionally restricts rows (and trims ``prospect_stats`` /
    ``prospect_ratings_computed``) for fast single-row merges identical to bulk.
    """
    slugs_f = _normalize_slug_filter(slugs)

    conn, own = _maybe_conn(conn)
    try:
        nba_slugs: set[str] = set()
        nba_names: set[str] = set()
        if exclude_current_nba:
            nba_slugs, nba_names = nba_roster_match_sets(conn)

        pq = "SELECT * FROM prospects"
        p_params: list[str] | None = None
        if slugs_f:
            placeholders = ",".join("?" * len(slugs_f))
            pq += f" WHERE slug IN ({placeholders})"
            p_params = list(slugs_f)

        prospects = (
            pd.read_sql_query(pq, conn, params=p_params)
            if p_params is not None
            else pd.read_sql_query(pq, conn))

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
        if slugs_f and not stats.empty:
            stats = stats[stats["slug"].isin(slugs_f)].copy()
        comb_m = pd.read_sql_query(
            "SELECT * FROM combine_measurements", conn)
        comb_d = pd.read_sql_query(
            "SELECT * FROM combine_drills", conn)
        computed = pd.read_sql_query(
            "SELECT * FROM prospect_ratings_computed", conn)
        if slugs_f and not computed.empty:
            computed = computed[computed["slug"].isin(slugs_f)].copy()
    finally:
        if own:
            conn.close()

    df = prospects.copy()
    if not stats.empty:
        if latest_season_only:
            yr_extract = pd.to_numeric(
                stats["season"].astype(str).str.extract(
                    r"(\d{4})", expand=False),
                errors="coerce").fillna(-1)
            stats = (
                stats.assign(_season_yr=yr_extract)
                .sort_values(
                    by=["slug", "_season_yr", "season"], kind="mergesort")
                .drop_duplicates("slug", keep="last")
                .drop(columns=["_season_yr"]))
        df = df.merge(stats, on="slug", how="left", suffixes=("", "_stats"))
    df = _merge_prospect_combine_measurements(df, comb_m)
    df = _merge_prospect_combine_drills(df, comb_d)
    if not computed.empty:
        df = df.merge(computed, on="slug", how="left",
                      suffixes=("", "_rating"))

    df = _coalesce_prospect_physicals(df)
    disp = df["height_in"].copy()
    if "height_wo_shoes_in" in df.columns:
        disp = disp.fillna(df["height_wo_shoes_in"])
    df = df.assign(height_ft=_series_height_ft_from_display_inches(disp))

    # Fixed 87-column workbook order + any extra DB columns after (automation
    # still uses PROSPECTS_TABLE_COLUMNS order; we only rearrange display/export).
    preferred = [c for c in config.PROSPECTS_TABLE_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in preferred]
    if "height_ft" in rest:
        rest.remove("height_ft")
        try:
            hi_ix = preferred.index("height_in")
            preferred.insert(hi_ix + 1, "height_ft")
        except ValueError:
            preferred.insert(0, "height_ft")
    out = df[preferred + rest].copy()
    if "espn_rank" in out.columns:
        out = out.sort_values("espn_rank", na_position="last")
    return round_float_columns_for_display(out)


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
    return round_float_columns_for_display(df)


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
    return round_float_columns_for_display(df)
