"""
Formulas tab: YAML editor per attribute, live recalc button, fit metrics
display, version history with rollback.
"""

from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st
import yaml

from .. import audit, bulk_recalc, config, db
from ..exporters import data_loader
from ..formulas import registry as _registry
from . import common


def _latest_yaml(attribute: str) -> tuple[int, str] | None:
    """Return (version, yaml_text) for the newest row of ``attribute``."""
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT version, yaml_blob FROM formulas "
            "WHERE attribute=? ORDER BY version DESC LIMIT 1",
            (attribute,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return int(row["version"]), str(row["yaml_blob"])


def _save_yaml(attribute: str, yaml_text: str, note: str = "") -> int:
    """Parse + persist a new version, return the new version number."""
    data = yaml.safe_load(yaml_text) or {}
    data["attribute"] = attribute  # force canonical name
    # Bump version.
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM formulas WHERE attribute=?",
            (attribute,),
        )
        v = int(cur.fetchone()["v"])
        data["version"] = v
        blob = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        conn.execute(
            """
            INSERT INTO formulas
                (attribute, version, yaml_blob, r2, mae, n_samples, edited_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attribute, v, blob,
                float(data.get("r2") or 0.0),
                float(data.get("mae") or 0.0),
                int(data.get("n_samples") or 0),
                "user",
                note or "manual edit",
            ),
        )
    finally:
        conn.close()

    (config.FORMULAS_DIR / f"{attribute}.yaml").write_text(
        blob, encoding="utf-8")
    audit.log_event(
        action="formula_edit",
        entity_type="formula",
        entity_slug=attribute,
        field="yaml_blob",
        note=note or f"edited -> v{v}",
    )
    return v


def render() -> None:
    st.header("Formulas")
    eng = config.get_rating_engine()
    st.caption(
        f"Active **rating engine**: `{eng}` (change at the top of the app). "
        "Every 2K attribute has a YAML formula for the **Calibrated** engine; "
        "the **Excel 2026** engine uses these YAMLs for **overall_2k** only. "
        "Edits here affect calibrated attributes and overall weights."
    )

    df = common.formulas_df()
    if df.empty:
        st.warning(
            "No formulas yet. Run calibration first: "
            "`python -m src.calibration.fit_formulas`")
        return

    st.subheader("Fit metrics")
    st.dataframe(
        df[["attribute", "version", "r2", "mae", "n_samples", "edited_at",
            "edited_by"]],
        use_container_width=True,
    )

    # Per-attribute editor
    st.subheader("Edit formula")
    attribute = st.selectbox("Attribute", sorted(df["attribute"].tolist()))
    latest = _latest_yaml(attribute)
    if latest is None:
        st.info("No saved version yet.")
        return
    version, yaml_text = latest
    st.caption(f"Current version: v{version}")
    new_text = st.text_area("YAML", value=yaml_text, height=360,
                            key=f"fe_{attribute}")
    note = st.text_input("Edit note", value="",
                         help="Stored in audit_log.")

    col_save, col_recalc = st.columns(2)
    if col_save.button("Save new version", type="primary"):
        try:
            v = _save_yaml(attribute, new_text, note=note)
            st.success(f"Saved v{v}.")
            common.bust_cache()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Save failed: {exc}")

    if col_recalc.button("Recalculate all prospect ratings"):
        prog = st.empty()
        def _rc(i: int, total: int, slug: str) -> None:
            try:
                prog.progress(
                    i / max(total, 1),
                    text=f"Recomputing [{i}/{total}] {slug}…",
                )
            except Exception:  # noqa: BLE001
                pass
        try:
            with st.spinner("Recomputing all prospect ratings…"):
                n = bulk_recalc.recompute_prospect_ratings(
                    progress_cb=_rc, audit_note="formulas",
                )
            prog.empty()
        except Exception as exc:  # noqa: BLE001
            prog.empty()
            st.error(f"Recompute failed: {exc}")
            st.exception(exc)
        else:
            common.bust_cache()
            st.success(f"Recomputed ratings for {n} prospects.")

    st.subheader("Version history")
    conn = db.connect()
    try:
        hist = pd.read_sql_query(
            "SELECT version, r2, mae, n_samples, edited_at, edited_by, notes "
            "FROM formulas WHERE attribute=? ORDER BY version DESC",
            conn, params=[attribute])
    finally:
        conn.close()
    st.dataframe(
        data_loader.round_float_columns_for_display(hist),
        use_container_width=True,
    )

    rollback_v = st.number_input(
        "Rollback to version", min_value=1,
        max_value=int(hist["version"].max()) if not hist.empty else 1,
        value=int(hist["version"].max()) if not hist.empty else 1, step=1)
    if st.button("Rollback"):
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT yaml_blob FROM formulas WHERE attribute=? AND version=?",
                (attribute, int(rollback_v)),
            ).fetchone()
        finally:
            conn.close()
        if row:
            _save_yaml(attribute, str(row["yaml_blob"]),
                       note=f"rollback to v{rollback_v}")
            st.success(f"Rolled {attribute} back to v{rollback_v}.")
            common.bust_cache()
        else:
            st.error("Version not found.")
