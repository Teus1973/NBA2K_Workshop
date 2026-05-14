"""
Build the calibration corpus: one row per NBA player with
``stats + physicals + (optional) combine + 2K26 rating``.

Usage:
    from src.calibration import build_corpus
    df = build_corpus.build()  # pandas.DataFrame indexed by player_id

Requires ``nba_players``, ``nba_stats_season``, ``nba_ratings_2k26``, and
ideally ``combine_measurements`` / ``combine_drills`` to be populated. Returns
a DataFrame with *every* feature that :mod:`src.calibration.fit_formulas`
needs to fit the per-attribute linear regressions defined in
``data/formulas/*.yaml``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config, db
from ..logger import get_logger

log = get_logger("calibration.build_corpus")


@dataclass
class CorpusOptions:
    season: str = config.CURRENT_SEASON
    season_type: str = "Regular"
    min_gp: int = 20          # filter out low-sample seasons
    min_min_per_game: float = 12.0


FEATURES_PHYSICAL = [
    "height_in", "weight_lbs", "wingspan_in", "bmi",
    "wingspan_minus_height", "std_reach_in",
]
FEATURES_COMBINE_DRILL = [
    "max_vert_in", "standing_vert_in",
    "lane_agility_sec", "shuttle_sec", "three_quarter_sprint_sec",
]
FEATURES_PER_GAME = list(config.STAT_COLUMNS)
FEATURES_DERIVED = [
    "fg3m_per36", "fg3a_per36", "fta_per36",
    "ast_per36", "tov_per36", "stl_per36",
    "blk_per36", "oreb_per36", "dreb_per36", "reb_per36",
    "usg_proxy",
]


# ---------------------------------------------------------------------------
def _load_sql(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn)


def build(opts: CorpusOptions | None = None,
          conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Return the joined calibration DataFrame."""
    opts = opts or CorpusOptions()
    own = conn is None
    if own:
        conn = db.connect()
    try:
        players = _load_sql(conn, "SELECT * FROM nba_players")
        stats = _load_sql(
            conn,
            f"SELECT * FROM nba_stats_season "
            f"WHERE season='{opts.season}' AND season_type='{opts.season_type}'",
        )
        ratings = _load_sql(conn, "SELECT * FROM nba_ratings_2k26")
        comb_m = _load_sql(conn, "SELECT * FROM combine_measurements")
        comb_d = _load_sql(conn, "SELECT * FROM combine_drills")
    finally:
        if own:
            conn.close()

    # Join stats + players (stats keyed by player_id). Players with no stats
    # this season are dropped from the corpus.
    df = stats.merge(players, on="player_id", how="inner", suffixes=("", "_p"))
    df = df[(df["gp"] >= opts.min_gp) & (df["min"] >= opts.min_min_per_game)]

    # Left-join 2K ratings on player_id.
    df = df.merge(
        ratings,
        on="player_id",
        how="left",
        suffixes=("", "_r"),
    )

    # Left-join combine data using the "nba:<player_id>" subject_key.
    comb_m = comb_m.copy()
    comb_m["player_id"] = comb_m["subject_key"].str.replace(
        "nba:", "", regex=False
    ).apply(lambda s: int(s) if s.isdigit() else None)
    comb_m = comb_m.dropna(subset=["player_id"]).astype({"player_id": int})
    # Keep most recent year per player.
    comb_m = comb_m.sort_values("year").drop_duplicates(
        "player_id", keep="last")

    comb_d = comb_d.copy()
    comb_d["player_id"] = comb_d["subject_key"].str.replace(
        "nba:", "", regex=False
    ).apply(lambda s: int(s) if s.isdigit() else None)
    comb_d = comb_d.dropna(subset=["player_id"]).astype({"player_id": int})
    comb_d = comb_d.sort_values("year").drop_duplicates(
        "player_id", keep="last")

    df = df.merge(
        comb_m[["player_id", "height_wo_shoes_in", "height_w_shoes_in",
                "wingspan_in", "std_reach_in", "body_fat_pct"]].rename(
            columns={"wingspan_in": "combine_wingspan_in"}
        ),
        on="player_id",
        how="left",
    )
    df = df.merge(
        comb_d[["player_id", "lane_agility_sec", "shuttle_sec",
                "three_quarter_sprint_sec", "standing_vert_in",
                "max_vert_in", "bench_reps"]],
        on="player_id",
        how="left",
    )

    # Combine-first wingspan (aligned with :mod:`src.exporters.data_loader`).
    if "wingspan_in" in df.columns and "combine_wingspan_in" in df.columns:
        df["wingspan_in"] = df["combine_wingspan_in"].where(
            df["combine_wingspan_in"].notna(), df["wingspan_in"])
    elif "combine_wingspan_in" in df.columns:
        df["wingspan_in"] = df["combine_wingspan_in"]

    # Derived physical features
    # BMI (in lbs/in^2 scaled). Standard formula uses metric; we keep imperial.
    df["bmi"] = df["weight_lbs"] / (df["height_in"].replace(0, np.nan) ** 2) * 703
    df["wingspan_minus_height"] = df["wingspan_in"] - df["height_in"]

    # Per-36 stat features
    mp_per_game = df["min"].replace(0, np.nan)
    factor = 36.0 / mp_per_game
    for col in ("fg3m", "fg3a", "fta", "ast", "tov", "stl", "blk",
                "oreb", "dreb", "reb"):
        df[f"{col}_per36"] = df[col] * factor

    # Very rough usage proxy: (FGA + 0.44*FTA + TOV) scaled to 36 min
    df["usg_proxy"] = (df["fga"] + 0.44 * df["fta"] + df["tov"]) * factor

    # Position one-hots (PG/SG/SF/PF/C inferred from primary pos string).
    def _pos_bucket(raw: object) -> str:
        if not isinstance(raw, str) or not raw:
            return "SF"
        s = raw.upper()
        if "C" in s and "SF" not in s and "PF" not in s:
            return "C"
        if "PF" in s or "PF/C" in s:
            return "PF"
        if "PG" in s:
            return "PG"
        if "SG" in s:
            return "SG"
        return "SF"

    df["pos_bucket"] = df["pos"].apply(_pos_bucket)
    for p in ("PG", "SG", "SF", "PF", "C"):
        df[f"is_{p.lower()}"] = (df["pos_bucket"] == p).astype(float)

    return df
