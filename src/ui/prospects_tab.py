"""
Prospects tab: all user-specified columns, per-cell color coding by
provenance (scraped / combine / formula / manual-override), add-player
dialog, remove-player button.

UI-to-Console Bridge: Streamlit trigger for Xbox 360 virtual input.
"""

from __future__ import annotations

import io
import json
import re
from datetime import date

import pandas as pd
import streamlit as st

from .. import audit, config, db
from ..automation.controller_mapping import (
    neutralize_virtual_stick,
    push_prospect_row_to_controller,
    send_capture_handshake,
)
from ..formulas import apply as fapply, registry as _registry
from ..scrapers import espn_bigboard
from . import common, workbook_export
from .vision_lab_tab import automation_ocr_roi_tuple

PROVENANCE_COLORS = {
    "scraped": "#C6EFCE",
    "combine": "#BDD7EE",
    "formula": "#FFEB9C",
    "manual":  "#D9D9D9",
    "scouting_proxy": "#E1D5F1",
}
PROVENANCE_TEXT = "#1f1f1f"
_CELL_STYLE = "background-color: {bg}; color: " + PROVENANCE_TEXT + ";"


def _render_automation_sidebar() -> None:
    """Sidebar: virtual Xbox 360 controller setup for PS5 Remote Play."""
    st.session_state.pop("automation_roi_live_preview", None)
    st.sidebar.caption(
        "Chiaki OCR ROI calibration lives on the **Vision Lab** tab. Controller bridge below."
    )

    with st.sidebar.expander("Automation Settings", expanded=False):
        st.caption(
            "Requires ViGEmBus. Initializes **one** ``vgamepad.VX360Gamepad`` "
            "stored in session state."
        )
        st.toggle(
            "Edit player mode (push bio/stats indices 0–33)",
            value=False,
            key="automation_edit_player_mode",
        )
        st.caption(
            "Enable **OCR rating feedback** on **Vision Lab** so **Push to PS5** can use "
            "Tesseract delta-nudging with your ROI."
        )
        if st.button(
            "Initialize Virtual Controller",
            key="automation_init_vgamepad",
        ):
            if st.session_state.get("automation_gamepad") is not None:
                st.warning(
                    "A virtual controller is already active in this session. "
                    "Restart the Streamlit app if you need a fresh device."
                )
            else:
                try:
                    import vgamepad as vg
                except ImportError:
                    st.session_state["automation_vgamepad_missing"] = True
                    st.error("The **vgamepad** package is not installed.")
                else:
                    st.session_state.pop("automation_vgamepad_missing", None)
                    st.session_state.pop("automation_show_vgamepad_pip", None)
                    try:
                        st.session_state["automation_gamepad"] = vg.VX360Gamepad()
                        st.success("Virtual controller ready.")
                    except OSError as e:
                        st.error(f"ViGEmBus / driver error: {e}")
                    except Exception as e:
                        st.error(f"Could not initialize virtual controller: {e}")

        if st.session_state.get("automation_vgamepad_missing"):
            if st.button(
                "Install Dependency",
                key="automation_install_vgamepad_help",
                help="Show pip install line for vgamepad",
            ):
                st.session_state["automation_show_vgamepad_pip"] = True
            if st.session_state.get("automation_show_vgamepad_pip"):
                st.code("pip install vgamepad", language="bash")
                st.caption(
                    "You still need **ViGEmBus** (virtual Xbox driver) for the device."
                )

        _gp_ready = st.session_state.get("automation_gamepad") is not None
        if st.button(
            "Send Test Input (Capture Handshake)",
            key="automation_capture_handshake",
            disabled=not _gp_ready,
            help="D-pad down pulse for Chiaki-ng controller capture (no prospect row / anchors).",
        ):
            try:
                send_capture_handshake(st.session_state["automation_gamepad"])
                st.success("Handshake sent (D-pad down pulse).")
            except ImportError as e:
                st.error(f"vgamepad not available: {e}")
            except Exception as e:
                st.error(f"Handshake failed: {e}")


def _prospect_series_to_controller_row(series: pd.Series) -> list:
    """Build row list in ``PROSPECTS_TABLE_COLUMNS`` order; append potential at index 87."""
    row_data: list = []
    for col in config.PROSPECTS_TABLE_COLUMNS:
        val = series.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            row_data.append(None)
        else:
            row_data.append(val)
    pot = series.get("potential")
    if pot is not None and not (isinstance(pot, float) and pd.isna(pot)):
        row_data.append(pot)
    return row_data


def _provenance_for(slug: str, conn) -> dict[str, str]:
    row = conn.execute(
        "SELECT manual_override_json FROM prospect_ratings_computed WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        return {}
    try:
        blob = json.loads(row["manual_override_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return {k: "manual" for k in blob.keys()}


def _style_ratings(df: pd.DataFrame,
                   provenance_by_slug: dict[str, dict[str, str]]):
    """Return a Styler that color-codes every rating cell by source."""
    if df.empty or "slug" not in df.columns:
        return df.style
    color_df = pd.DataFrame("", index=df.index, columns=df.columns)
    for i, slug in enumerate(df["slug"].tolist()):
        prov = provenance_by_slug.get(slug) or {}
        for col in df.columns:
            if col in config.RATING_ATTRIBUTES:
                src = prov.get(col, "formula")
                color = PROVENANCE_COLORS.get(src.split("+")[0])
                if color:
                    color_df.iat[i, df.columns.get_loc(col)] = (
                        _CELL_STYLE.format(bg=color))
    return df.style.apply(lambda _: color_df, axis=None)


def _recompute_one(slug: str, manual_overrides: dict[str, int] | None = None) -> dict[str, int]:
    from ..exporters import data_loader

    reg = _registry.load_registry()
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM prospects WHERE slug=?", (slug,)
        ).fetchone()
        if not row:
            return {}
        prospect = dict(row)
        stats = conn.execute(
            "SELECT * FROM prospect_stats WHERE slug=? ORDER BY season DESC LIMIT 1",
            (slug,),
        ).fetchone()
        if stats:
            for k in stats.keys():
                if k != "slug":
                    prospect[k] = stats[k]
        comb_m = conn.execute(
            "SELECT * FROM combine_measurements WHERE subject_key=? "
            "ORDER BY year DESC LIMIT 1", (f"prospect:{slug}",),
        ).fetchone()
        if comb_m:
            for k in comb_m.keys():
                prospect.setdefault(k, comb_m[k])
        comb_d = conn.execute(
            "SELECT * FROM combine_drills WHERE subject_key=? "
            "ORDER BY year DESC LIMIT 1", (f"prospect:{slug}",),
        ).fetchone()
        if comb_d:
            for k in comb_d.keys():
                prospect.setdefault(k, comb_d[k])

        ratings, _prov = fapply.apply_to_prospect(
            prospect, reg, manual_overrides=manual_overrides)

        cols = ["slug"] + list(config.RATING_ATTRIBUTES) + [
            "potential", "formula_version", "manual_override_json"]
        values = [slug] + [ratings.get(a) for a in config.RATING_ATTRIBUTES] + [
            ratings.get("potential"), 1, json.dumps(manual_overrides) if manual_overrides else None]
        placeholders = ", ".join(["?"] * len(cols))
        sql = (
            f"INSERT INTO prospect_ratings_computed ({', '.join(cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT(slug) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
        )
        conn.execute(sql, values)
    finally:
        conn.close()
    return ratings


# ---------------------------------------------------------------------------
def render() -> None:
    st.header("Prospects -- 2026 NBA draft")
    st.caption(
        "One row per prospect. Ratings cells are color-coded: "
        "yellow = formula-derived, blue = combine override, "
        "light purple = scouting proxy (AI physical hints with no combine), "
        "grey = manual override, green = scraped."
    )

    show_nba_overlap = st.checkbox(
        "Include prospects who match a current NBA roster player (off by default)",
        value=False,
        key="prospects_include_nba_overlap",
        help="ESPN big boards can list NBA veterans; hide them unless you need them.",
    )
    df = common.prospects_df(
        exclude_current_nba=not show_nba_overlap)
    if df.empty:
        st.warning(
            "No prospects to display. Use **Settings** to load the board, or "
            "enable **Include prospects who match a current NBA roster player** "
            "if rows were filtered out.")
        return

    _render_automation_sidebar()

    # Formula-trained sanity check: if no formula has samples, warn loudly.
    conn = db.connect()
    try:
        row = conn.execute("SELECT MAX(n_samples) FROM formulas").fetchone()
        has_trained = bool(row and row[0] and row[0] > 0)
        n_ratings = conn.execute(
            "SELECT COUNT(*) FROM nba_ratings_2k26").fetchone()[0]
    finally:
        conn.close()
    if not has_trained:
        st.warning(
            f"Formulas are **not trained yet** (NBA 2K26 ratings loaded: "
            f"`{n_ratings}`). Every rating below is just the intercept clamp. "
            f"Go to **Settings -> Run bootstrap now** to scrape reference "
            f"ratings and refit the formulas."
        )

    # Provenance: by default every rating is 'formula'; combine cells win if
    # combine columns are populated; manual cells win if manual_override_json
    # names them.
    provenance_by_slug: dict[str, dict[str, str]] = {}
    conn = db.connect()
    try:
        for _, row in df.iterrows():
            slug = row["slug"]
            prov: dict[str, str] = {a: "formula" for a in config.RATING_ATTRIBUTES if a in df.columns}
            # Combine overrides
            if row.get("c_speed_2k"):
                prov["speed_2k"] = "combine"
            if row.get("c_agility_2k"):
                prov["agility_2k"] = "combine"
            if row.get("c_vertical_2k"):
                prov["vertical_2k"] = "combine"
            if row.get("c_speed_with_ball_2k"):
                prov["speed_with_ball_2k"] = "combine"
            # Manual overrides
            prov.update(_provenance_for(slug, conn))
            # AI scouting physical nudge (when no combine) — for cell coloring
            for a, src in fapply.scouting_proxy_source_tags(
                row.to_dict(),
            ).items():
                if prov.get(a) == "formula":
                    prov[a] = src
            provenance_by_slug[slug] = prov
    finally:
        conn.close()

    n_with_stats = (
        int(df["gp"].notna().sum()) if "gp" in df.columns else 0)

    c1, c2, c3 = st.columns(3)
    c1.metric("Prospects", len(df))
    c2.metric("With stats (this table)", n_with_stats)
    c3.metric("Formulas trained?", "yes" if has_trained else "no")
    st.caption(
        "**Stats** (GP, PTS, …) come from **Settings → Scrape sports-reference CBB** "
        "or **Full prospect pipeline** (bootstrap also runs CBB in step 5). "
        "**Ratings** need **2K + refit** data; use the full pipeline for "
        "CBB + refit + recompute, or only **Overall** is **rank-nudged** when "
        "stats are missing. **Height (ft)** is from height in inches."
    )

    search = st.text_input(
        "Search by name / school", "", key="prospects_search_filter")
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
        if "school_or_team" in view.columns:
            mask = mask | view["school_or_team"].fillna("").str.contains(
                search, case=False, na=False)
        view = view[mask]

    st.caption(f"Showing {len(view)} / {len(df)} prospects")
    _col_cfg = common.pinned_name_column_config()
    try:
        st.dataframe(
            _style_ratings(view, provenance_by_slug),
            column_config=_col_cfg,
            use_container_width=True,
            height=640,
            hide_index=True,
        )
    except Exception:
        # Fallback if styling fails for any reason
        st.dataframe(
            view,
            column_config=_col_cfg,
            use_container_width=True,
            height=640,
            hide_index=True,
        )

    buf = io.StringIO()
    view.rename(columns={
        "last_name": "Last Name",
        "first_name": "First Name",
    }).to_csv(buf, index=False)
    st.download_button(
        "Download CSV (quick)",
        buf.getvalue(),
        file_name="prospects.csv",
        mime="text/csv",
        help="Spreadsheet-ready plain CSV — no freezing/hiding (use workbook export below).",
    )

    workbook_export.render_workbook_export_section(
        provenance_by_slug=provenance_by_slug,
        use_expander=True,
        expander_label="Excel / Google Sheets — full workshop workbook",
        slot="prospects",
    )

    # ---------------- add/remove/override -----------------------------
    st.divider()
    left, right = st.columns(2)

    with left:
        st.subheader("Add player")
        with st.form("add_player"):
            full_name = st.text_input("Full name")
            pos = st.selectbox("Position", ["PG", "SG", "SF", "PF", "C"])
            school = st.text_input("School or team")
            league = st.selectbox(
                "League",
                sorted(config.VALID_LEAGUES),
                index=sorted(config.VALID_LEAGUES).index(config.LEAGUE_NCAA),
            )
            age = st.number_input("Age", min_value=16.0, max_value=30.0,
                                  value=19.0, step=0.1)
            dob_s = st.text_input(
                "Date of birth (optional, YYYY-MM-DD)", "",
                help="Example: 2006-03-15",
            )
            height_in = st.number_input("Height (in)", min_value=60.0,
                                        max_value=96.0, value=78.0, step=0.5)
            weight_lbs = st.number_input("Weight (lbs)", min_value=120.0,
                                         max_value=360.0, value=200.0, step=1.0)
            wingspan_in = st.number_input("Wingspan (in)", min_value=60.0,
                                          max_value=100.0, value=80.0, step=0.5)
            rank = st.number_input("ESPN rank", min_value=0, max_value=200,
                                   value=0, step=1)
            submit = st.form_submit_button("Add", type="primary")
        if submit and full_name.strip():
            dob_val: str | None = None
            dob_error: str | None = None
            if dob_s.strip():
                s = dob_s.strip()
                if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
                    try:
                        date.fromisoformat(s)
                        dob_val = s
                    except ValueError:
                        dob_error = "Date of birth must be a valid YYYY-MM-DD."
                else:
                    dob_error = "Date of birth: use YYYY-MM-DD or leave blank."
            if dob_error:
                st.error(dob_error)
            else:
                prospect = espn_bigboard.Prospect(
                    rank=int(rank) or None,
                    full_name=full_name.strip(),
                    pos=pos,
                    school_or_team=school.strip() or None,
                    league=league,
                    age=float(age),
                    height_in=float(height_in),
                    weight_lbs=float(weight_lbs),
                    date_of_birth=dob_val,
                    source="manual",
                )
                conn = db.connect()
                try:
                    espn_bigboard.upsert_prospects(conn, [prospect])
                    conn.execute(
                        "UPDATE prospects SET wingspan_in=?, added_by='user' "
                        "WHERE slug=?",
                        (float(wingspan_in), prospect.slug),
                    )
                finally:
                    conn.close()
                audit.log_event(
                    action="player_added",
                    entity_type="prospect",
                    entity_slug=prospect.slug,
                    after={"rank": rank, "pos": pos, "school": school,
                           "league": league},
                    note="manual add",
                )
                _recompute_one(prospect.slug)
                common.bust_cache()
                st.success(f"Added {full_name}.")

    with right:
        st.subheader("Remove player / override")
        slug_to_edit = st.selectbox(
            "Select prospect", sorted(df["slug"].tolist()),
            key="prospect_management_slug",
        )

        if st.button(
            "Push to PS5",
            type="primary",
            key="automation_push_ps5",
            help="Send the selected prospect row to the virtual Xbox 360 controller.",
        ):
            # Session-backed controls: read at click so the run matches the current UI.
            gp_for_push = st.session_state.get("automation_gamepad")
            include_bio = bool(
                st.session_state.get("automation_edit_player_mode", False),
            )
            use_ocr = bool(
                st.session_state.get("automation_use_ocr_feedback", False),
            )
            roi_bbox = automation_ocr_roi_tuple()

            if gp_for_push is None:
                st.warning(
                    "Initialize **Virtual Controller** (Sidebar → **Automation Settings**). "
                    "Calibrate OCR on **Vision Lab**."
                )
            else:
                row_series = df.loc[df["slug"] == slug_to_edit].iloc[0]
                row_list = _prospect_series_to_controller_row(row_series)
                prog = st.progress(0)
                status = st.empty()

                def _on_progress(frac: float, msg: str) -> None:
                    prog.progress(min(1.0, max(0.0, frac)))
                    status.caption(msg)

                try:
                    if gp_for_push is None:
                        raise RuntimeError(
                            "automation_gamepad missing from session state "
                            "(singleton check failed)."
                        )
                    push_prospect_row_to_controller(
                        row_list,
                        gamepad=gp_for_push,
                        edit_player_mode=include_bio,
                        use_ocr_feedback=use_ocr,
                        ocr_roi_relative_xywh=roi_bbox if use_ocr else None,
                        on_progress=_on_progress,
                    )
                    prog.progress(1.0)
                    status.caption("Complete.")
                    audit.log_event(
                        action="automation_push",
                        entity_type="prospect",
                        entity_slug=slug_to_edit,
                        note=f"completed ocr_feedback={use_ocr} bio={include_bio}",
                    )
                    st.success("Push to PS5 finished.")
                except OSError as e:
                    prog.progress(1.0)
                    status.caption("Stopped (driver error).")
                    audit.log_event(
                        action="automation_push",
                        entity_type="prospect",
                        entity_slug=slug_to_edit,
                        note=f"driver_error: {e}"[:1800],
                    )
                    st.error(f"ViGEmBus / driver error: {e}")
                except Exception as e:
                    prog.progress(1.0)
                    status.caption("Stopped (error).")
                    audit.log_event(
                        action="automation_push",
                        entity_type="prospect",
                        entity_slug=slug_to_edit,
                        note=f"error: {e}"[:1800],
                    )
                    st.error(f"Push to PS5 failed: {e}")
                finally:
                    neutralize_virtual_stick(gp_for_push)

        if st.button("Remove", type="secondary"):
            conn = db.connect()
            try:
                conn.execute("DELETE FROM prospects WHERE slug=?", (slug_to_edit,))
            finally:
                conn.close()
            audit.log_event(
                action="player_removed",
                entity_type="prospect",
                entity_slug=slug_to_edit,
            )
            common.bust_cache()
            st.success(f"Removed {slug_to_edit}.")

        st.markdown("**Manual overrides** (leave blank to skip)")
        with st.form("override_form"):
            picked = st.multiselect(
                "Attributes to override",
                list(config.RATING_ATTRIBUTES), max_selections=6,
            )
            new_values: dict[str, int] = {}
            for attr in picked:
                new_values[attr] = int(st.slider(
                    config.RATING_DISPLAY_NAMES.get(attr, attr),
                    25, 99, 75))
            note = st.text_input("Reason", "")
            submit2 = st.form_submit_button("Apply override")
        if submit2 and new_values:
            ratings = _recompute_one(slug_to_edit, manual_overrides=new_values)
            events = []
            for attr, after in new_values.items():
                events.append({
                    "action": "override_set",
                    "entity_type": "prospect",
                    "entity_slug": slug_to_edit,
                    "field": attr,
                    "after": after,
                    "note": note or "manual override",
                })
            audit.log_batch(events)
            common.bust_cache()
            st.success(f"Applied {len(new_values)} override(s).")
