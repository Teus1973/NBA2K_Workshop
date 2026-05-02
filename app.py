"""
NBA2K Workshop -- Streamlit entry point.

Run: ``streamlit run app.py`` (or ``python launcher.py`` / the .bat).
"""

from __future__ import annotations

import base64
from pathlib import Path

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

_APP_DIR = Path(__file__).resolve().parent
APP_LOGO = (_APP_DIR / "assets" / "app_logo.png").resolve()


def _reinforce_favicon_png(path: Path) -> None:
    """``page_icon`` path can be ignored depending on cwd; duplicate favicon via markup."""
    try:
        b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return
    st.markdown(
        f'<link rel="icon" type="image/png" href="data:image/png;base64,{b64}" />',
        unsafe_allow_html=True,
    )


def _sidebar_brand_png(path: Path) -> None:
    """Sidebar logo + title: HTML avoids ``st.image`` opaque backing; CSS bleeds past block padding."""
    try:
        b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return
    st.sidebar.markdown(
        "<style>"
        "[data-testid='stSidebar'] .sidebar-brand-wrap { overflow: visible; "
        "box-sizing: border-box; width: calc(100% + 2.75rem); "
        "max-width: none; margin: -0.35rem -1.375rem 0.25rem; "
        "padding: 0.15rem 0.65rem 0.55rem; text-align: center; line-height: 1.05; "
        "}"
        "[data-testid='stSidebar'] .sidebar-brand-wrap img.sidebar-brand-logo {"
        "background: transparent !important; "
        "width: 140% !important; max-width: none !important; "
        "height: auto; display: block; margin-left: -20%; "
        "}"
        "[data-testid='stSidebar'] .sidebar-brand-title { margin: 0.45rem 0 0; "
        "font-weight: 700; font-size: 1.7rem; line-height: 1.12;"
        "}"
        "</style>"
        '<div class="sidebar-brand-wrap">'
        f'<img class="sidebar-brand-logo" alt="NBA2K Workshop" src="data:image/png;base64,{b64}" />'
        '<p class="sidebar-brand-title">NBA2K Workshop</p>'
        "</div>",
        unsafe_allow_html=True,
    )


def main() -> None:
    _page_icon: str = str(APP_LOGO) if APP_LOGO.is_file() else "🏀"
    st.set_page_config(
        page_title="NBA2K Workshop",
        page_icon=_page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if APP_LOGO.is_file():
        _reinforce_favicon_png(APP_LOGO)
    configure_session_logging()

    if APP_LOGO.is_file():
        _sidebar_brand_png(APP_LOGO)
    else:
        st.sidebar.title("NBA2K Workshop")
    st.sidebar.caption(f"Draft year: **{config.DRAFT_YEAR}**")
    st.sidebar.caption(f"Season: **{config.CURRENT_SEASON}**")
    st.sidebar.caption(f"Prospect target: **{config.PROSPECT_TARGET}**")
    common.render_db_stats()

    main_top = st.container()
    with main_top:
        common.render_main_rating_engine()

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
