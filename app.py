"""
NBA2K26 Workshop -- Streamlit entry point.

Run: ``streamlit run app.py`` (or ``python launcher.py`` / the .bat).
"""

from __future__ import annotations

import streamlit as st

from src import config
from src.logger import configure_session_logging, get_logger
from src.ui import (
    common,
    europe_tab,
    formulas_tab,
    logs_tab,
    prospects_tab,
    reference_tab,
    scouting_tab,
    settings_tab,
)

log = get_logger("app")


def main() -> None:
    st.set_page_config(
        page_title="NBA2K26 Workshop",
        page_icon="🏀",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    configure_session_logging()

    st.sidebar.title("NBA2K26 Workshop")
    st.sidebar.caption(f"Draft year: **{config.DRAFT_YEAR}**")
    st.sidebar.caption(f"Season: **{config.CURRENT_SEASON}**")
    st.sidebar.caption(f"Prospect target: **{config.PROSPECT_TARGET}**")
    common.render_db_stats()

    tabs = st.tabs([
        "Reference", "Prospects", "Scouting",
        "Europeans", "Formulas", "Logs", "Settings",
    ])
    with tabs[0]:
        reference_tab.render()
    with tabs[1]:
        prospects_tab.render()
    with tabs[2]:
        scouting_tab.render()
    with tabs[3]:
        europe_tab.render()
    with tabs[4]:
        formulas_tab.render()
    with tabs[5]:
        logs_tab.render()
    with tabs[6]:
        settings_tab.render()


if __name__ == "__main__":
    main()
