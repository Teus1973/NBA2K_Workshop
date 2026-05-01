"""
Settings tab: run ingestion pipelines, upload CSV/XLSX of additional
prospects or overrides, kick off formula recalibration, export to
Excel / Google Sheets.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import audit, bulk_recalc, config, db
from ..exporters import data_loader, excel_writer, gsheets_writer
from . import common


REQUIRED_UPLOAD_COLS = {"full_name"}
OPTIONAL_UPLOAD_COLS = {
    "rank", "pos", "school_or_team", "league", "age", "date_of_birth",
    "height_in", "weight_lbs", "wingspan_in", "notes",
}


def _parse_upload(file) -> pd.DataFrame:
    name = getattr(file, "name", "upload").lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        return pd.read_excel(file)
    return pd.read_csv(file)


def _scrape_sports_reference_cbb(
    progress_placeholder,
    *,
    force_refresh: bool = False,
) -> dict[str, int | str]:
    from ..scrapers import sports_reference_cbb as cbb

    def _cb(i: int, total: int, slug: str, status: str) -> None:
        if progress_placeholder is not None and total:
            try:
                progress_placeholder.progress(
                    i / max(total, 1),
                    text=f"[{i}/{total}] {slug}: {status}",
                )
            except Exception:  # noqa: BLE001
                pass

    return cbb.bulk_scrape_ncaa_prospects(
        progress_cb=_cb, force_refresh=force_refresh)


def _scrape_international_stats(
    progress_placeholder,
    *,
    force_refresh: bool = False,
) -> dict[str, int]:
    from ..scrapers import international as intl

    def _cb(i: int, total: int, slug: str, status: str) -> None:
        if progress_placeholder is not None and total:
            try:
                progress_placeholder.progress(
                    i / max(total, 1),
                    text=f"[intl {i}/{total}] {slug}: {status}",
                )
            except Exception:  # noqa: BLE001
                pass

    return intl.bulk_scrape_international_prospects(
        progress_cb=_cb, force_refresh=force_refresh)


def _db_stats() -> dict[str, int]:
    conn = db.connect()
    try:
        out = {}
        for t in ("nba_players", "nba_stats_season", "nba_ratings_2k26",
                  "prospects", "prospect_stats", "prospect_ratings_computed",
                  "formulas", "audit_log"):
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    finally:
        conn.close()
    return out


def _run_bootstrap(progress_placeholder) -> None:
    """Run the full first-time pipeline with live progress updates."""
    from ..calibration import fit_formulas
    from ..scrapers import espn_bigboard, nba_combine, nba_stats, twokratings

    log = st.session_state.setdefault("_bootstrap_log", [])

    def tick(msg: str) -> None:
        log.append(msg)
        progress_placeholder.markdown("```\n" + "\n".join(log[-20:]) + "\n```")

    tick("1/6  Fetching NBA season totals + bios...")
    totals = nba_stats.fetch_season_totals(
        season_type="Regular Season", force_refresh=True)
    bio = nba_stats.fetch_bio_stats(force_refresh=True)
    stat_rows = nba_stats.season_stats_rows(
        totals, season=config.CURRENT_SEASON, season_type="Regular")
    bio_rows_list = nba_stats.bio_rows(bio)
    conn = db.connect()
    try:
        nba_stats.upsert_players(conn, bio_rows_list)
        nba_stats.upsert_stats(conn, stat_rows)
    finally:
        conn.close()
    tick(f"     ok: {len(bio_rows_list)} players, {len(stat_rows)} stat rows")

    tick("2/6  Fetching NBA combine history (draft years 2000–2026)...")
    conn = db.connect()
    try:
        n_comb = nba_combine.refresh_all_years(conn)
    finally:
        conn.close()
    tick(f"     ok: {n_comb} combine rows")

    tick("3/6  Loading ESPN big board + seed prospects...")
    prospects = espn_bigboard.load_prospects(force_refresh=True)
    conn = db.connect()
    try:
        espn_bigboard.upsert_prospects(conn, prospects)
    finally:
        conn.close()
    tick(f"     ok: {len(prospects)} prospects")

    tick("4/7  Bulk-scraping 2kratings.com (this takes ~10 min)...")
    def _cb(i, total, slug, status):
        if i == 1 or i % 20 == 0 or i == total:
            tick(f"     [{i}/{total}] {slug}: {status}")
    res = twokratings.bulk_scrape_and_upsert(progress_cb=_cb)
    tick(f"     done: {res}")

    tick("5/7  Scraping sports-reference CBB for prospects...")
    try:
        from ..scrapers import sports_reference_cbb as _cbb
        res_cbb = _cbb.bulk_scrape_ncaa_prospects(progress_cb=_cb)
        tick(f"     done: {res_cbb}")
    except Exception as exc:  # noqa: BLE001
        tick(f"     skipped (non-fatal): {exc}")

    tick("5b/7  Scraping proballers.com for non-NCAA prospects...")
    try:
        from ..scrapers import international as _intl
        res_intl = _intl.bulk_scrape_international_prospects(progress_cb=_cb)
        tick(f"     done: {res_intl}")
    except Exception as exc:  # noqa: BLE001
        tick(f"     skipped (non-fatal): {exc}")

    tick("6/7  Refitting 36 formulas against new corpus...")
    fit_results = fit_formulas.fit_all()
    fitted = sum(1 for v in fit_results.values() if v.get("n_samples", 0) > 0)
    tick(f"     ok: {fitted}/{len(fit_results)} formulas trained")

    tick("7/7  Recomputing prospect ratings...")
    n_recalc = bulk_recalc.recompute_prospect_ratings(audit_note="bootstrap")
    tick(f"     ok: {n_recalc} prospects")
    tick("")
    tick("Bootstrap complete. Re-open the Reference / Prospects tabs.")


def render() -> None:
    st.header("Settings & pipelines")

    # -------------------------------------------------------------------
    # Bootstrap: the first-run one-click orchestrator
    # -------------------------------------------------------------------
    stats = _db_stats()
    has_ratings = stats["nba_ratings_2k26"] > 0
    has_trained = False
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT MAX(n_samples) FROM formulas"
        ).fetchone()
        has_trained = bool(row and row[0] and row[0] > 0)
    finally:
        conn.close()

    st.subheader("First-time bootstrap")
    if not has_ratings or not has_trained:
        st.warning(
            "**Your database has no 2K26 reference ratings yet** "
            f"(nba_ratings_2k26 = {stats['nba_ratings_2k26']} rows, formulas "
            f"trained = {'yes' if has_trained else 'no'}). Click below to run "
            "the full first-time ingestion (~10-15 min at 1 req/s). It's safe "
            "to close and re-open -- every scraped page is cached on disk."
        )
    else:
        st.success(
            f"Bootstrap complete ({stats['nba_ratings_2k26']} 2K26 ratings, "
            f"formulas trained). Re-run only if you want to refresh everything."
        )

    if st.button("Run bootstrap now",
                 type="primary", disabled=False,
                 help="Fetches NBA stats + combine + ESPN prospects, then "
                      "bulk-scrapes 2kratings.com for every current NBA "
                      "player, refits all 36 formulas, and recomputes prospect "
                      "ratings."):
        placeholder = st.empty()
        try:
            _run_bootstrap(placeholder)
            common.bust_cache()
            st.success("Bootstrap finished. Switch to the Reference tab to verify.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Bootstrap failed: {exc}")
            st.exception(exc)

    st.divider()

    st.subheader("Individual pipelines")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Scrape NBA stats (current season)"):
            try:
                from ..scrapers import nba_stats
                totals = nba_stats.fetch_season_totals(
                    season_type="Regular Season", force_refresh=True)
                bio = nba_stats.fetch_bio_stats(force_refresh=True)
                stat_rows = nba_stats.season_stats_rows(
                    totals, season=config.CURRENT_SEASON, season_type="Regular")
                bio_rows_list = nba_stats.bio_rows(bio)
                conn = db.connect()
                try:
                    nba_stats.upsert_players(conn, bio_rows_list)
                    nba_stats.upsert_stats(conn, stat_rows)
                finally:
                    conn.close()
                common.bust_cache()
                st.success(
                    f"NBA stats refreshed: {len(stat_rows)} season rows, "
                    f"{len(bio_rows_list)} players.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Scrape failed: {exc}")

    with col2:
        if st.button("Scrape NBA combine (2000-2026)"):
            try:
                from ..scrapers import nba_combine
                conn = db.connect()
                try:
                    n = nba_combine.refresh_all_years(conn)
                finally:
                    conn.close()
                common.bust_cache()
                st.success(f"Combine refresh wrote {n} rows.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Combine scrape failed: {exc}")

    with col3:
        if st.button("Refit formulas"):
            try:
                from ..calibration import fit_formulas
                res = fit_formulas.fit_all()
                common.bust_cache()
                fitted = sum(1 for v in res.values() if v.get("n_samples", 0) > 0)
                st.success(f"Refit {fitted}/{len(res)} formulas with samples.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Refit failed: {exc}")

    col_2k, _sp1, _sp2 = st.columns(3)
    with col_2k:
        if st.button("Bulk-scrape 2kratings.com"):
            try:
                from ..scrapers import twokratings
                prog = st.empty()
                def _cb(i, total, slug, status):
                    if i == 1 or i % 10 == 0 or i == total:
                        prog.progress(
                            i / max(total, 1),
                            text=f"[{i}/{total}] {slug}: {status}")
                res = twokratings.bulk_scrape_and_upsert(progress_cb=_cb)
                common.bust_cache()
                st.success(f"2kratings scrape: {res}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Bulk scrape failed: {exc}")

    st.divider()

    st.subheader("Prospects")
    st.caption(
        "Ratings use trained formulas on **NBA** data; they get realistic when "
        "formulas are refit after 2K scrapes and **sports-reference CBB** fills "
        "``prospect_stats``. Without CBB lines, a **rank-aware overall nudge** is "
        "applied; with stats, the full regression drives attributes. "
        "**Recompute** runs in one SQLite batch (fast on disk); a spinner and "
        "occasional progress updates show while it works. If the database is open "
        "in another app, close it so SQLite is not locked."
    )
    col4, col5 = st.columns(2)
    with col4:
        if st.button("Load ESPN bigboard + seed list"):
            from ..scrapers import espn_bigboard
            prospects = espn_bigboard.load_prospects(force_refresh=True)
            conn = db.connect()
            try:
                espn_bigboard.upsert_prospects(conn, prospects)
            finally:
                conn.close()
            common.bust_cache()
            st.success(f"Loaded {len(prospects)} prospects.")
    with col5:
        p_recalc = st.empty()
        if st.button("Recompute prospect ratings"):
            def _rec_cb(i: int, total: int, slug: str) -> None:
                try:
                    p_recalc.progress(
                        i / max(total, 1),
                        text=f"Recomputing [{i}/{total}] {slug}…",
                    )
                except Exception:  # noqa: BLE001
                    pass
            try:
                with st.spinner("Recomputing prospect ratings (batch write)…"):
                    n = bulk_recalc.recompute_prospect_ratings(
                        progress_cb=_rec_cb,
                        audit_note="settings",
                    )
                p_recalc.empty()
                common.bust_cache()
                st.success(f"Recomputed ratings for {n} prospects.")
            except Exception as exc:  # noqa: BLE001
                p_recalc.empty()
                st.error(f"Recompute failed: {exc}")
                st.exception(exc)

    st.divider()
    prospect_stats_force_refresh = st.checkbox(
        "Bypass HTML cache when scraping prospect stats (slower; use if oreb/dreb "
        "stay empty or numbers look stale)",
        value=False,
        key="nba2k_prospect_stats_force_refresh",
    )
    cbb_col, pipe_col = st.columns(2)
    with cbb_col:
        cbb_ph = st.empty()
        if st.button(
            "Scrape sports-reference CBB (NCAA per-game stats)",
            help="NCAA **only** (cbb/players/…). Non-NCAA leagues never appear there — "
                 "use **international scrape** below or **NCAA + international**.",
        ):
            try:
                res = _scrape_sports_reference_cbb(
                    cbb_ph, force_refresh=prospect_stats_force_refresh)
                common.bust_cache()
                st.success(
                    f"CBB done: {res.get('ok', 0)}/{res.get('total', 0)} ok, "
                    f"skipped: {res.get('skipped', 0)}. "
                    "Run **Refit formulas** then **Recompute** (or the pipeline).")
            except Exception as exc:  # noqa: BLE001
                st.error(f"CBB scrape failed: {exc}")
                st.exception(exc)
        st.caption(
            "**Dates of birth:** Sports-Reference CBB pages usually **omit birthdates "
            "now**. Use **Fill missing dates…** — it tries SR where relevant, then "
            "**Wikidata** (Wikipedia)."
        )
        if st.button(
            "Fill missing dates of birth (Wikipedia / Wikidata + SR fallback)",
            help="Sets ``date_of_birth`` from Wikidata when the English Wikipedia "
                 "article maps to an entity with birth date (P569). NCAA prospects "
                 "also try cached SR pages first if ``Born:`` exists.",
        ):
            from ..scrapers import sports_reference_cbb as cbb2

            pb = st.empty()

            def _dob_cb(i: int, total: int, slug: str, status: str) -> None:
                if total:
                    try:
                        pb.progress(
                            i / max(total, 1), text=f"[{i}/{total}] {slug} — {status}"
                        )
                    except Exception:  # noqa: BLE001
                        pass

            try:
                res = cbb2.enrich_missing_dates_of_birth(
                    only_missing=True, progress_cb=_dob_cb,
                )
                common.bust_cache()
                st.success(
                    f"DOB: filled {res.get('filled', 0)}/"
                    f"{res.get('total', 0)}; not found: {res.get('not_found', 0)} "
                    f"(SR: {res.get('from_sr', 0)}, Wikidata: {res.get('from_wikidata', 0)}).",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"DOB enrich failed: {exc}")
                st.exception(exc)

        intl_ph = st.empty()
        if st.button(
            "Scrape non-NCAA stats (Proballers + Basketball-Reference intl.)",
            help="Prospects whose ``league`` is not ``ncaa``. Tries proballers.com "
                 "slugs first, then Basketball-Reference international player pages.",
        ):
            from ..scrapers import international as intl_sc

            def _intl_cb(i: int, total: int, slug: str, status: str) -> None:
                if total:
                    try:
                        intl_ph.progress(
                            i / max(total, 1),
                            text=f"[{i}/{total}] {slug}: {status}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

            try:
                res_intl = intl_sc.bulk_scrape_international_prospects(
                    progress_cb=_intl_cb,
                    force_refresh=prospect_stats_force_refresh,
                )
                common.bust_cache()
                st.success(
                    f"International done: {res_intl.get('ok', 0)}/"
                    f"{res_intl.get('total', 0)} ok, skipped: "
                    f"{res_intl.get('skipped', 0)}.",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"International scrape failed: {exc}")
                st.exception(exc)

        combo_ph = st.empty()
        if st.button(
            "Scrape NCAA + non-NCAA stats (both pipelines)",
            help="Runs sports-reference CBB for NCAA prospects, then international "
                 "(Proballers + Basketball-Reference) for everyone else.",
        ):
            try:
                res_cb = _scrape_sports_reference_cbb(
                    combo_ph, force_refresh=prospect_stats_force_refresh)
                combo_ph.empty()
                res_intl = _scrape_international_stats(
                    combo_ph, force_refresh=prospect_stats_force_refresh)
                common.bust_cache()
                combo_ph.empty()
                st.success(
                    f"NCAA CBB: {res_cb.get('ok', 0)}/{res_cb.get('total', 0)} ok; "
                    f"international: {res_intl.get('ok', 0)}/"
                    f"{res_intl.get('total', 0)} ok. "
                    "Then **Refit formulas** + **Recompute** if needed.",
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Combined scrape failed: {exc}")
                st.exception(exc)

    with pipe_col:
        if st.button(
            "Full prospect pipeline: CBB + refit formulas + recompute",
            type="primary",
            help="Runs college stats, retrains all formulas on the NBA+2K corpus, "
                 "then recomputes every prospect card.",
        ):
            pbar = st.empty()
            try:
                res = _scrape_sports_reference_cbb(
                    pbar, force_refresh=prospect_stats_force_refresh)
                st.caption(f"Step 1 — CBB: {res}")
                from ..scrapers import international as intl_mod

                def _intl_pipe_cb(i: int, total: int, slug: str, status: str) -> None:
                    try:
                        pbar.progress(
                            i / max(total, 1),
                            text=f"Intl [{i}/{total}] {slug}: {status}",
                        )
                    except Exception:  # noqa: BLE001
                        pass

                res_intl = intl_mod.bulk_scrape_international_prospects(
                    progress_cb=_intl_pipe_cb,
                    force_refresh=prospect_stats_force_refresh,
                )
                st.caption(f"Step 1b — Proballers (non-NCAA): {res_intl}")
                from ..calibration import fit_formulas
                fit = fit_formulas.fit_all()
                fitted = sum(1 for v in fit.values() if v.get("n_samples", 0) > 0)

                def _recpipe(i: int, total: int, slug: str) -> None:
                    try:
                        pbar.progress(
                            i / max(total, 1),
                            text=f"Recomputing [{i}/{total}] {slug}…",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                with st.spinner("Recomputing prospect ratings (batch write)…"):
                    n = bulk_recalc.recompute_prospect_ratings(
                        progress_cb=_recpipe, audit_note="pipeline",
                    )
                pbar.empty()
                common.bust_cache()
                st.success(
                    f"Pipeline complete. Formulas with samples: {fitted}/"
                    f"{len(fit)}. Recomputed: {n} prospects."
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Pipeline failed: {exc}")
                st.exception(exc)

    st.divider()

    # ---------------- CSV/XLSX upload ---------------------------------
    st.subheader("Upload prospects / overrides")
    st.caption(
        f"Columns: required {sorted(REQUIRED_UPLOAD_COLS)}, "
        f"optional {sorted(OPTIONAL_UPLOAD_COLS)}."
    )
    up = st.file_uploader("CSV or XLSX", type=["csv", "xlsx", "xls"])
    if up is not None:
        try:
            df = _parse_upload(up)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Parse failed: {exc}")
            return
        st.write("Preview:")
        st.dataframe(
            data_loader.round_float_columns_for_display(df),
            use_container_width=True,
            height=240,
        )
        missing = REQUIRED_UPLOAD_COLS - set(df.columns)
        if missing:
            st.error(f"Missing required columns: {sorted(missing)}")
        elif st.button("Import"):
            from ..scrapers import espn_bigboard

            save_path = config.USER_UPLOADS_DIR / up.name
            save_path.write_bytes(up.getvalue() if hasattr(up, "getvalue")
                                  else up.read())
            prospects = []
            for _, row in df.iterrows():
                dob_raw = row.get("date_of_birth")
                dob: str | None = None
                if dob_raw is not None and not (
                    isinstance(dob_raw, float) and pd.isna(dob_raw)
                ):
                    s = str(dob_raw).strip()[: 10]  # YYYY-MM-DD or Excel date
                    if len(s) == 10 and s[4] == "-":
                        dob = s
                prospects.append(espn_bigboard.Prospect(
                    rank=int(row["rank"]) if pd.notna(row.get("rank")) else None,
                    full_name=str(row["full_name"]),
                    pos=str(row.get("pos") or "") or None,
                    school_or_team=str(row.get("school_or_team") or "") or None,
                    league=str(row.get("league") or config.LEAGUE_NCAA),
                    age=float(row["age"]) if pd.notna(row.get("age")) else None,
                    date_of_birth=dob,
                    height_in=float(row["height_in"]) if pd.notna(row.get("height_in")) else None,
                    weight_lbs=float(row["weight_lbs"]) if pd.notna(row.get("weight_lbs")) else None,
                    source="csv_upload",
                    notes=str(row.get("notes") or "") or None,
                ))
            conn = db.connect()
            try:
                espn_bigboard.upsert_prospects(conn, prospects)
            finally:
                conn.close()
            audit.log_event(
                action="csv_upload",
                entity_type="prospects",
                note=f"{up.name}: {len(prospects)} rows",
            )
            common.bust_cache()
            st.success(f"Imported {len(prospects)} prospects.")

    st.divider()

    # ---------------- Export ------------------------------------------
    st.subheader("Export")
    col_e1, col_e2 = st.columns(2)
    with col_e1:
        if st.button("Export to Excel", type="primary"):
            out = config.EXPORTS_DIR / f"nba2k26_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M%S}.xlsx"
            try:
                excel_writer.export_to_excel(out)
                with open(out, "rb") as fh:
                    st.download_button(
                        "Download Excel", fh.read(),
                        file_name=out.name,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                st.success(f"Wrote {out}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Excel export failed: {exc}")

    with col_e2:
        if st.button("Export to Google Sheets"):
            try:
                url = gsheets_writer.export_to_gsheets()
                st.success(f"Created: {url}")
                st.markdown(f"[Open sheet]({url})")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Google Sheets export failed: {exc}")

    st.divider()
    st.subheader("Environment")
    st.code(
        f"Python: {config.PROJECT_ROOT}\n"
        f"DB: {config.DB_PATH}\n"
        f"Season: {config.CURRENT_SEASON}\n"
        f"Draft year: {config.DRAFT_YEAR}\n"
        f"Prospect target: {config.PROSPECT_TARGET}\n"
        f"Rate limit (rps): {config.SCRAPE_RPS}\n"
        f"Google creds path: {config.GOOGLE_CREDENTIALS_PATH or '(unset)'}\n"
    )
