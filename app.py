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


def _sidebar_logo_png(path: Path) -> None:
    """PNG with alpha: HTML avoids ``st.image`` painting an opaque backing in some themes."""
    try:
        b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return
    st.sidebar.markdown(
        "<style>"
        "[data-testid='stSidebar'] .sidebar-logo-html { overflow: visible; }"
        "[data-testid='stSidebar'] .sidebar-logo-html img {"
        "background: transparent !important;"
        "width: 114%;"
        "max-width: none;"
        "height: auto;"
        "margin-left: -7%;"
        "display: inline-block;"
        "vertical-align: top;"
        "}"
        "</style>"
        '<div class="sidebar-logo-html" style="background:transparent;text-align:center;line-height:0;">'
        '<img alt="NBA2K Workshop" '
        f'src="data:image/png;base64,{b64}" '
        "/>"
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
        _sidebar_logo_png(APP_LOGO)
        st.sidebar.markdown(
            '<p style="text-align:center;margin:0.2rem 0 0.8rem;font-weight:600;font-size:1.1rem;">NBA2K Workshop</p>',
            unsafe_allow_html=True,
        )
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
