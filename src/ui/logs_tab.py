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
        "Every scrape, formula refit, rating recalc, override, and export "
        "writes a row here."
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
        limit = st.number_input("Limit", min_value=50, max_value=10_000,
                                value=1000, step=50)

    df = common.audit_df(int(limit))
    if df.empty:
        st.info("Audit log is empty.")
        return
    if action:
        df = df[df["action"] == action]
    if slug:
        df = df[df["entity_slug"].fillna("").str.contains(slug, case=False)]

    st.dataframe(df, use_container_width=True, height=600)

    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button("Download CSV", buf.getvalue(),
                       file_name="audit_log.csv", mime="text/csv")

    with st.expander("Danger zone"):
        if st.button("Clear audit log", type="secondary"):
            n = audit.clear(confirm=True)
            st.success(f"Cleared {n} rows.")
            common.bust_cache()
