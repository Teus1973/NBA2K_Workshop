"""
Shared helpers for Streamlit tabs: cached data loaders, small widgets,
and a central place to force cache busts after a mutation.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from .. import config, db
from ..exporters import data_loader


# ---------------------------------------------------------------------------
def pinned_name_column_config() -> dict[str, object]:
    """Pin last / first name so they stay visible during horizontal scroll."""
    return {
        "last_name": st.column_config.TextColumn(
            "Last Name", pinned=True, width="medium"),
        "first_name": st.column_config.TextColumn(
            "First Name", pinned=True, width="medium"),
    }


# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def reference_df(season: str = config.CURRENT_SEASON,
                 season_type: str = "Regular") -> pd.DataFrame:
    return data_loader.load_reference_df(season, season_type)


@st.cache_data(ttl=30, show_spinner=False)
def prospects_df(*, exclude_current_nba: bool = True) -> pd.DataFrame:
    return data_loader.load_prospects_df(
        exclude_current_nba=exclude_current_nba)


@st.cache_data(ttl=15, show_spinner=False)
def audit_df(limit: int = 2000) -> pd.DataFrame:
    return data_loader.load_audit_df(limit)


@st.cache_data(ttl=30, show_spinner=False)
def formulas_df() -> pd.DataFrame:
    return data_loader.load_formulas_df()


def bust_cache() -> None:
    """Call after any mutation so the next tab render reloads fresh data."""
    reference_df.clear()
    prospects_df.clear()
    audit_df.clear()
    formulas_df.clear()
    from . import scouting_tab

    scouting_tab.clear_scouting_cache()


# ---------------------------------------------------------------------------
def _conn_stats() -> dict[str, int]:
    conn = db.connect()
    try:
        return {
            t: db.table_count(conn, t)
            for t in (
                "nba_players", "nba_ratings_2k26", "nba_stats_season",
                "combine_measurements", "combine_drills",
                "prospects", "prospect_stats", "prospect_ratings_computed",
                "audit_log", "formulas",
            )
        }
    finally:
        conn.close()


def render_db_stats() -> None:
    """Sidebar widget showing DB row counts."""
    stats = _conn_stats()
    st.sidebar.caption("Database contents")
    for k, v in stats.items():
        st.sidebar.write(f"- {k}: **{v}**")
