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


# ---------------------------------------------------------------------------
def render_main_rating_engine() -> None:
    """Top-of-app control: Excel vs YAML rating engine (see :func:`config.get_rating_engine`)."""
    from .. import config as _cfg

    labels: dict[str, str] = {
        "calibrated": "Calibrated — YAML / linear (trained on NBA reference)",
        "excel_2026_class": "Excel 2026 class — workbook “2026 class” sheet logic",
    }
    if "workshop_rating_engine" not in st.session_state:
        st.session_state["workshop_rating_engine"] = _cfg.get_rating_engine()
    c1, c2 = st.columns([5, 2])
    with c1:
        st.radio(
            "Prospect **rating engine** (stats → 2K attributes, then overall from YAML). "
            "This is not the Scouting text tab; it only changes numeric formulas.",
            options=list(labels.keys()),
            format_func=lambda k: labels[k],
            key="workshop_rating_engine",
            horizontal=True,
            help=(
                "Calibrated: feature-vector formulas in data/formulas. "
                "Excel 2026: same structure as the spreadsheet’s 2026 class tab "
                f"(user settings: {_cfg.USER_WORKSHOP_SETTINGS})."
            ),
        )
    with c2:
        p = _cfg.USER_WORKSHOP_SETTINGS
        st.caption(f"Preference file: `{p.name}` in your app data folder.")
    current = _cfg.get_rating_engine()
    choice = st.session_state.get("workshop_rating_engine", current)
    if choice != current:
        _cfg.set_rating_engine(str(choice))
        bust_cache()
