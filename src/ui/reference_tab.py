"""
Reference tab: NBA players joined across ratings + stats + combine.
Read-only, sortable, CSV export.
"""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from .. import config
from . import common


def render() -> None:
    st.header("Reference -- current NBA players")
    st.caption(
        "Ground truth for calibration: scraped **2K26** attributes, season stats, "
        "and **NBA draft combine** anthro + drills. Combine rows are the official "
        "reference for those measurements and feed the **combine-derived 2K fields** "
        "(e.g. `c_speed_2k`, `c_agility_2k`, `c_vertical_2k`, `c_speed_with_ball_2k`) "
        "used in this project’s formulas—the same class of inputs 2K ties to real "
        "combine testing."
    )

    col_a, col_b = st.columns([2, 1])
    with col_a:
        season = st.text_input(
            "Season", value=config.CURRENT_SEASON,
            help="Season of stats to join (e.g. 2025-26)",
        )
    with col_b:
        season_type = st.selectbox(
            "Season type", ["Regular", "Playoffs", "Pre Season"], index=0,
        )

    df = common.reference_df(season, season_type)
    if df.empty:
        from .. import db
        conn = db.connect()
        try:
            have_seasons = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT season FROM nba_stats_season ORDER BY season")
            ]
            n_ratings = conn.execute(
                "SELECT COUNT(*) FROM nba_ratings_2k26").fetchone()[0]
        finally:
            conn.close()
        st.warning(
            f"No reference data for **{season} / {season_type}**. "
            f"Seasons loaded: `{have_seasons or 'none'}`. "
            f"2K26 ratings loaded: `{n_ratings}`."
        )
        st.info(
            "Go to the **Settings** tab and click **Run bootstrap now** to "
            "populate the DB from scratch (~10-15 min). Or if only the season "
            "filter is stale, just change the *Season* box above to one of the "
            "loaded values."
        )
        return

    cnt_rating = df.get("overall_2k", pd.Series(dtype=float)).notna().sum() \
        if "overall_2k" in df.columns else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Players", f"{len(df):,}")
    c2.metric("With 2K26 rating", f"{cnt_rating:,}")
    c3.metric("Season / type", f"{season} / {season_type}")
    st.caption(
        "**Roster listing:** **Height (ft)**, `height_in`, and weight from the NBA "
        "bio API (current listing). **Combine block:** official combine anthro + "
        "drills (and calibrated **`c_*`** columns)—keep this populated via "
        "**Settings → Scrape NBA combine** (draft years 2000–2026) for reference "
        "and for attributes that mirror in-game combine-driven ratings."
    )

    search = st.text_input("Search by name / team", "")
    view = df.copy()
    if search:
        mask = pd.Series(False, index=view.index)
        if "full_name" in view.columns:
            mask = mask | view["full_name"].str.contains(
                search, case=False, na=False)
        for col in ("last_name", "first_name"):
            if col in view.columns:
                mask = mask | view[col].fillna("").str.contains(
                    search, case=False, na=False)
        if "team" in view.columns:
            mask = mask | view["team"].fillna("").str.contains(
                search, case=False, na=False)
        view = view[mask]

    col_cfg = common.pinned_name_column_config()
    st.dataframe(
        view,
        column_config=col_cfg,
        use_container_width=True,
        height=600,
        hide_index=True,
    )

    buf = io.StringIO()
    dl = view.rename(columns={
        "last_name": "Last Name",
        "first_name": "First Name",
    })
    dl.to_csv(buf, index=False)
    st.download_button(
        "Download CSV",
        buf.getvalue(),
        file_name=f"reference_{season}_{season_type.lower()}.csv",
        mime="text/csv",
    )
