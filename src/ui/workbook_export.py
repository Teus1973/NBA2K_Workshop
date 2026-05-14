"""
Multi-sheet workbook export (Excel / Google Sheets) for Streamlit.

Caches ``.xlsx`` bytes per ``slot`` (e.g. ``settings`` vs ``prospects``) so downloads
persist across reruns and widget keys stay unique between tabs.
"""

from __future__ import annotations

from typing import Mapping

import pandas as pd
import streamlit as st

from .. import config
from ..exporters import excel_writer, gsheets_writer


def _bytes_key(slot: str) -> str:
    return f"nba2k_wb_xlsx_bytes_{slot}"


def _name_key(slot: str) -> str:
    return f"nba2k_wb_xlsx_name_{slot}"


def clear_cached_workbook_export() -> None:
    """Clear every cached workbook blob (called after DB mutations)."""
    for k in list(st.session_state.keys()):
        if k.startswith("nba2k_wb_xlsx_bytes") or k.startswith("nba2k_wb_xlsx_name"):
            st.session_state.pop(k, None)


def render_workbook_export_section(
    *,
    provenance_by_slug: Mapping[str, Mapping[str, str]] | None = None,
    heading: str = "Excel / Google Sheets (full workbook)",
    use_expander: bool = False,
    expander_label: str | None = None,
    slot: str = "settings",
) -> None:
    """Show Excel build + persistent download and Google Sheets button.

    ``slot`` keeps Streamlit widget keys and cached bytes isolated per tab.
    """

    def body() -> None:
        bk = _bytes_key(slot)
        nk = _name_key(slot)
        kp = slot.replace(" ", "_")

        st.caption(
            "**CSV** exports are plain text — no frozen panes or hidden columns. "
            "**Build Excel workbook** writes Reference + Prospects + … ; **Prospects** "
            "includes **Height (ft)** immediately after Height (in), the full "
            "**per‑game stats** band, then 2K ratings (freeze row 1 / cols A–B)."
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button(
                "Build Excel workbook",
                type="primary",
                key=f"{kp}_nba2k_build_xlsx",
            ):
                out = (
                    config.EXPORTS_DIR
                    / f"nba2k26_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M%S}.xlsx"
                )
                try:
                    excel_writer.export_to_excel(
                        out,
                        provenance_by_slug=provenance_by_slug,
                    )
                    st.session_state[bk] = out.read_bytes()
                    st.session_state[nk] = out.name
                    st.success(f"Ready: **{out.name}** — use Download below.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Excel export failed: {exc}")
        with b2:
            if st.button(
                "Export to Google Sheets",
                key=f"{kp}_nba2k_gsheets_btn",
            ):
                try:
                    url = gsheets_writer.export_to_gsheets()
                    st.success("Spreadsheet created.")
                    st.markdown(f"[Open in Google Sheets]({url})")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Google Sheets export failed: {exc}")

        blob = st.session_state.get(bk)
        fname = st.session_state.get(nk) or "nba2k26_workshop.xlsx"
        if blob:
            st.download_button(
                label="Download .xlsx",
                data=blob,
                file_name=str(fname),
                mime=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                key=f"{kp}_nba2k_dl_xlsx",
            )
            if st.button(
                "Clear cached workbook",
                key=f"{kp}_nba2k_clear_xlsx",
            ):
                st.session_state.pop(bk, None)
                st.session_state.pop(nk, None)
                st.rerun()

    if use_expander:
        with st.expander(expander_label or heading, expanded=False):
            body()
    else:
        st.subheader(heading)
        body()
