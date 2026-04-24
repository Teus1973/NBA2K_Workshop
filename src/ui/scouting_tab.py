"""
Scouting reports tab: short summaries from ESPN big-board cache (+ optional web).
"""

from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

from .. import db
from ..scrapers import scouting
from . import common


def clear_scouting_cache() -> None:
    _scouting_table.clear()


def _short_summary(blurbs: list[str]) -> str | None:
    if not blurbs:
        return None
    text = max(blurbs, key=len)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        return text[:317].rstrip() + "..."
    return text


@st.cache_data(ttl=120, show_spinner="Loading scouting blurbs…")
def _scouting_table(*, use_web: bool, exclude_current_nba: bool) -> pd.DataFrame:
    from ..exporters import data_loader as _dl

    conn = db.connect()
    try:
        nba_slugs, nba_names = (set(), set())
        if exclude_current_nba:
            nba_slugs, nba_names = _dl.nba_roster_match_sets(conn)
        rows = conn.execute(
            """
            SELECT slug, full_name, first_name, last_name, espn_rank,
                   school_or_team
            FROM prospects
            ORDER BY (espn_rank IS NULL), espn_rank, full_name
            """
        ).fetchall()
        if exclude_current_nba and (nba_slugs or nba_names):
            rows = [
                r for r in rows
                if not _dl.is_prospect_on_nba_roster(
                    r["slug"], r["full_name"], nba_slugs, nba_names)]
    finally:
        conn.close()

    out: list[dict[str, object]] = []
    for r in rows:
        slug = str(r["slug"])
        full = (r["full_name"] or "").strip()
        blurbs: list[str] = []
        espn_b = scouting.extract_espn_blurb(slug)
        if espn_b:
            blurbs.append(espn_b)
        if use_web:
            blurbs.extend(scouting.fetch_ddg_blurbs(
                f"{full} NBA draft scouting report"))
        summary = _short_summary(blurbs)
        if espn_b and use_web and len(blurbs) > 1:
            src = "ESPN + web"
        elif espn_b:
            src = "ESPN cache"
        elif use_web and blurbs:
            src = "Web"
        else:
            src = "—"
        out.append({
            "espn_rank": r["espn_rank"],
            "last_name": r["last_name"] or "",
            "first_name": r["first_name"] or "",
            "school_or_team": r["school_or_team"] or "",
            "summary": summary or "",
            "source": src,
        })
    return pd.DataFrame(out)


def render() -> None:
    st.header("Scouting reports")
    st.caption(
        "Summaries are built from scouting copy in the cached ESPN big-board "
        "HTML (same source as prospect ingestion). Enable web snippets for "
        "extra blurbs when ``duckduckgo-search`` is installed."
    )

    use_web = st.checkbox(
        "Include optional web snippets (DuckDuckGo)",
        value=False,
        help="Slower; requires the duckduckgo-search package.",
    )
    show_nba_overlap = st.checkbox(
        "Include prospects who match a current NBA roster player",
        value=False,
        key="scouting_include_nba_overlap",
    )

    df = _scouting_table(
        use_web=use_web,
        exclude_current_nba=not show_nba_overlap,
    )
    if df.empty:
        st.warning("No prospects in the database.")
        return

    with_summary = (df["summary"] != "").sum()
    c1, c2 = st.columns(2)
    c1.metric("Prospects", len(df))
    c2.metric("With summary", int(with_summary))

    search = st.text_input(
        "Search by name / school", "", key="scouting_search_filter")
    view = df.copy()
    if search:
        s = search.strip()
        mask = (
            view["last_name"].str.contains(s, case=False, na=False)
            | view["first_name"].str.contains(s, case=False, na=False)
            | view["school_or_team"].fillna("").str.contains(
                s, case=False, na=False)
        )
        view = view[mask]

    st.caption(f"Showing {len(view)} / {len(df)} rows")

    name_cfg = common.pinned_name_column_config()
    cfg = {
        **name_cfg,
        "espn_rank": st.column_config.NumberColumn("Rank", format="%d"),
        "school_or_team": st.column_config.TextColumn("School / team"),
        "summary": st.column_config.TextColumn("Summary", width="large"),
        "source": st.column_config.TextColumn("Sources"),
    }
    st.dataframe(
        view,
        column_config=cfg,
        use_container_width=True,
        height=640,
        hide_index=True,
    )

    buf = io.StringIO()
    dl = view.rename(columns={
        "last_name": "Last Name",
        "first_name": "First Name",
        "school_or_team": "School / team",
        "espn_rank": "ESPN rank",
    })
    dl.to_csv(buf, index=False)
    st.download_button(
        "Download CSV",
        buf.getvalue(),
        file_name="scouting_summaries.csv",
        mime="text/csv",
    )
