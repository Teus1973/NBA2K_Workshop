"""
Logs tab: filterable view of ``audit_log`` (newest first), CSV export, clear.
"""

from __future__ import annotations

import io

import streamlit as st

from .. import audit
from . import common


def render() -> None:
    st.header("Audit log")
    st.caption(
        "Every scrape, formula refit, rating recalc, override, export, and "
        "**Push to PS5** run writes a row here."
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        action = st.selectbox(
            "Action filter",
            [""] + sorted(audit.ALLOWED_ACTIONS),
            index=0,
        )
    with col_b:
        slug = st.text_input("Entity slug filter", "").strip()
    with col_c:
        limit = st.number_input(
            "Limit",
            min_value=50,
            max_value=10_000,
            value=1000,
            step=50,
        )

    df = common.audit_df(int(limit))
    view_df = df
    if action:
        view_df = view_df[view_df["action"] == action]
    if slug:
        view_df = view_df[
            view_df["entity_slug"].fillna("").str.contains(slug, case=False)
        ]

    if df.empty:
        st.info("Audit log is empty.")
    elif view_df.empty:
        st.info("No rows match the current filters.")
    else:
        st.dataframe(view_df, use_container_width=True, height=600)

    st.divider()
    st.markdown("**Export & clear**")
    st.caption(
        "Download respects the filters above. **Clear** removes **all** rows in "
        "the audit log (SQLite `audit_log`), not just the filtered view."
    )
    exp_col, clear_col = st.columns(2)
    with exp_col:
        buf = io.StringIO()
        view_df.to_csv(buf, index=False)
        st.download_button(
            "Download CSV (filtered view)",
            buf.getvalue(),
            file_name="audit_log.csv",
            mime="text/csv",
        )
    with clear_col:
        purge_ok = st.checkbox(
            "I understand this permanently deletes every audit row",
            key="audit_log_purge_confirm",
        )
        if st.button(
            "Clear entire audit log",
            type="secondary",
            disabled=not purge_ok,
            help="Requires confirmation checkbox. Reloads this page after delete.",
        ):
            n = audit.clear(confirm=True)
            st.success(f"Cleared {n} row(s).")
            common.bust_cache()
            st.rerun()
