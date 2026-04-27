"""
Scouting reports tab: short summaries from ESPN big-board cache (+ optional web).
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable

import pandas as pd
import streamlit as st

from .. import config, db
from ..scouting_persist import (
    cache_entry_has_real_scouting,
    merge_scouting_for_save,
    persist_merged_scouting_for_slugs,
)
from ..scrapers import scouting
from . import common


ProgressFn = Callable[[int, int, str], None]

# Session cache for Ollama output (same browser session) — avoids repeat calls
# and supports incremental (one row per refresh) with Stop.
SCOUT_OLLAMA_CACHE_KEY = "scout_ollama_cache_v1"
SCOUT_HALT_KEY = "scout_research_halt_v1"


def clear_scouting_cache() -> None:
    """Legacy no-op (table is no longer ``st.cache_data``-backed)."""
    try:
        st.cache_data.clear()
    except Exception:  # noqa: BLE001
        pass


def _short_summary(blurbs: list[str]) -> str | None:
    """Legacy: longest blurb (used when AI falls back after a failed synthesis)."""
    if not blurbs:
        return None
    text = max(blurbs, key=len)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        return text[:317].rstrip() + "..."
    return text


def _espn_line_summary(blurbs: list[str]) -> str | None:
    """First blurb only (``collect_prospect_blurbs`` order: ESPN, then Wikipedia, …)."""
    if not blurbs:
        return None
    text = re.sub(r"<[^>]+>", " ", blurbs[0])
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 320:
        return text[:317].rstrip() + "..."
    return text


def _row_dict(r: object) -> dict[str, object]:
    """sqlite3 Row -> plain dict for :func:`scouting.format_listing_for_scouting`."""
    d: dict[str, object] = {}
    try:
        keys = r.keys()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return d
    for k in keys:
        d[k] = r[k]  # type: ignore[index]
    return d


def _ollama_cache_get(
    ollama_cache: dict[str, dict[str, object]] | None, slug: str
) -> dict[str, object] | None:
    if not ollama_cache:
        return None
    ent = ollama_cache.get(slug)
    return ent if isinstance(ent, dict) else None


def _ollama_cache_has_stored_output(c: dict[str, object] | None) -> bool:
    """True if the session entry already has any saved AI text/JSON to display."""
    if not c or not isinstance(c, dict):
        return False
    if (str(c.get("summary") or "")).strip():
        return True
    if (str(c.get("physical_traits") or "")).strip():
        return True
    if (str(c.get("scouting_json") or "")).strip():
        return True
    return False


def _next_ollama_slug(
    rows: list, cap: int, ollama_ok: bool, use_oll: bool, ollama_cache: dict[str, dict]
) -> str | None:
    if not ollama_ok or not use_oll:
        return None
    for idx, r in enumerate(rows):
        if idx >= cap:
            return None
        s = str(r["slug"])
        c = ollama_cache.get(s)
        if c and c.get("synthesis_failed"):
            continue
        if _ollama_cache_has_stored_output(c if isinstance(c, dict) else None):
            continue
        return s
    return None


def _build_scouting_dataframe(
    *,
    use_web: bool,
    use_wikipedia: bool,
    use_ollama: bool,
    exclude_current_nba: bool,
    enrich_cap: int,
    on_progress: ProgressFn | None = None,
    ollama_cache: dict[str, dict[str, object]] | None = None,
    incremental_ollama: bool = False,
    halt_ollama: bool = False,
    regenerate_ai: bool = False,
    auto_persist_to_db: bool = False,
) -> tuple[pd.DataFrame, bool, int]:
    """Build the scouting table (not Streamlit-cached: shows live progress).

    Returns ``(df, should_rerun, n_autosaved)`` where *n_autosaved* is how many
    rows were written to SQLite by **auto-persist** (if enabled).

    When ``incremental_ollama`` is True, only the next uncached row inside the
    cap runs the LLM; ``should_rerun`` tells the UI to :func:`st.rerun` to
    continue. ``halt_ollama`` skips all LLM work for that run.

    When ``regenerate_ai`` is False, DB scouting is copied into the session cache
    for any slug that does **not** already have a non-empty cache entry (empty
    failed-run placeholders are overwritten from the DB on reload so saved work
    reappears). Check **Regenerate AI** in the UI to force a fresh model pass.
    """
    from ..exporters import data_loader as _dl

    cache = ollama_cache if ollama_cache is not None else {}
    conn = db.connect()
    try:
        nba_slugs, nba_names = (set(), set())
        if exclude_current_nba:
            nba_slugs, nba_names = _dl.nba_roster_match_sets(conn)
        rows = conn.execute(
            """
            SELECT slug, full_name, first_name, last_name, espn_rank,
                   school_or_team, league, pos, height_in, weight_lbs, wingspan_in,
                   scouting_ai_summary, scouting_physical_text,
                   scouting_physical_json
            FROM prospects
            ORDER BY (espn_rank IS NULL), espn_rank, full_name
            """
        ).fetchall()
        if exclude_current_nba and (nba_slugs or nba_names):
            rows = [
                r
                for r in rows
                if not _dl.is_prospect_on_nba_roster(
                    r["slug"], r["full_name"], nba_slugs, nba_names
                )
            ]
    finally:
        conn.close()

    if not regenerate_ai and cache is not None:
        for r in rows:
            slug = str(r["slug"])
            ex = cache.get(slug) if isinstance(cache, dict) else None
            if ex is not None and cache_entry_has_real_scouting(ex):
                continue
            sm = (str(r["scouting_ai_summary"] or "")).strip()
            ph = (str(r["scouting_physical_text"] or "")).strip()
            jt = (str(r["scouting_physical_json"] or "")).strip()
            if not sm and not ph and not jt:
                continue
            cache[slug] = {
                "summary": sm,
                "physical_traits": ph,
                "scouting_json": jt,
                "source": "Database",
            }

    ollama_ok = use_ollama and config.USE_OLLAMA and scouting.ollama_server_reachable()
    cap = max(0, int(enrich_cap))
    if regenerate_ai and cache is not None:
        n_top = min(cap, len(rows))
        for r in rows[:n_top]:
            cache.pop(str(r["slug"]), None)
    any_expensive = use_web or use_wikipedia or use_ollama
    n_rows = len(rows)
    if halt_ollama:
        only_slug: str | None = None
    else:
        only_slug = (
            _next_ollama_slug(rows, cap, ollama_ok, use_ollama, cache)
            if incremental_ollama
            else None
        )
    out: list[dict[str, object]] = []
    ran_ollama_this_run = False
    pending_autosave: list[tuple[str, str, str, str]] = []
    for idx, r in enumerate(rows):
        slug = str(r["slug"])
        full = (r["full_name"] or "").strip()
        if on_progress and (
            idx == 0
            or (idx + 1) % max(1, n_rows // 20) == 0
            or idx + 1 == n_rows
        ):
            on_progress(idx + 1, n_rows, slug)

        do_full = any_expensive and idx < cap
        oll = bool(ollama_ok and do_full) and not halt_ollama
        listing = scouting.format_listing_for_scouting(_row_dict(r))
        c_ent = _ollama_cache_get(cache, slug) if cache is not None else None
        def _p(key: str) -> str | None:
            v = r[key]  # type: ignore[index]
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        c_hit = c_ent and (
            c_ent.get("synthesis_failed")
            or _ollama_cache_has_stored_output(c_ent)
        )
        if c_hit:
            # Wikipedia / DDG are **inputs to Ollama** only. Fetching them without AI
            # produced long bios in the summary column and looked like "no report."
            blurbs, src = scouting.collect_prospect_blurbs(
                slug, full, use_wikipedia=False, use_duckduckgo=False
            )
            jtxt = str(c_ent.get("scouting_json") or "")
            summary = str(c_ent.get("summary") or "")
            physical = str(c_ent.get("physical_traits") or "")
            if c_ent.get("source"):
                src = str(c_ent.get("source") or src)
        else:
            if oll and incremental_ollama and only_slug and slug != only_slug:
                blurbs, src = scouting.collect_prospect_blurbs(
                    slug, full, use_wikipedia=False, use_duckduckgo=False
                )
                jtxt, summary, physical = "", _espn_line_summary(blurbs) or "", ""
            elif oll and not incremental_ollama:
                use_w = use_wikipedia
                use_d = use_web
                blurbs, src = scouting.collect_prospect_blurbs(
                    slug,
                    full,
                    use_wikipedia=use_w,
                    use_duckduckgo=use_d,
                    pos=_p("pos"),
                    school=_p("school_or_team"),
                    league=_p("league"),
                )
            elif oll and incremental_ollama and only_slug == slug:
                use_w = use_wikipedia
                use_d = use_web
                blurbs, src = scouting.collect_prospect_blurbs(
                    slug,
                    full,
                    use_wikipedia=use_w,
                    use_duckduckgo=use_d,
                    pos=_p("pos"),
                    school=_p("school_or_team"),
                    league=_p("league"),
                )
            else:
                blurbs, src = scouting.collect_prospect_blurbs(
                    slug, full, use_wikipedia=False, use_duckduckgo=False
                )
                jtxt, summary, physical = "", _espn_line_summary(blurbs) or "", ""

            joined = "\n\n".join(blurbs) if blurbs else ""
            want_synth = oll and joined and (not incremental_ollama or only_slug == slug)
            if not c_ent and want_synth:
                list_for_llm = f"## Workshop listing\n{listing}" if listing else None
                synth = scouting.synthesize_scouting_with_ollama(
                    full, joined, listing=list_for_llm
                )
                if synth:
                    summary = (synth.scouting_text or "").strip()
                    physical = (synth.physical_text or "").strip()
                    jtxt = json.dumps(synth.features) if synth.features else ""
                    if cache is not None:
                        cache[slug] = {
                            "summary": summary,
                            "physical_traits": physical,
                            "scouting_json": jtxt,
                            "source": src,
                        }
                    if auto_persist_to_db and (summary or physical or jtxt):
                        pending_autosave.append(
                            (slug, summary, physical, jtxt),
                        )
                    ran_ollama_this_run = True
                else:
                    summary = (_short_summary(blurbs) or _espn_line_summary(blurbs) or "")
                    physical, jtxt = "", ""
                    if cache is not None and incremental_ollama:
                        cache[slug] = {
                            "summary": summary,
                            "physical_traits": "",
                            "scouting_json": "",
                            "source": src,
                            "synthesis_failed": True,
                        }
                    ran_ollama_this_run = bool(incremental_ollama)
            elif not c_ent and oll and not want_synth:
                summary, physical, jtxt = (
                    _espn_line_summary(blurbs) or "",
                    "",
                    "",
                )
            elif not c_ent and not oll:
                summary, physical, jtxt = (
                    _espn_line_summary(blurbs) or "",
                    "",
                    "",
                )

        ph_db = (
            (r["scouting_physical_text"] or "").strip()
            if r["scouting_physical_text"]
            else ""
        )
        js_db = (
            (r["scouting_physical_json"] or "").strip()
            if r["scouting_physical_json"]
            else ""
        )
        if not physical and ph_db:
            physical = ph_db
        if not jtxt and js_db:
            jtxt = js_db
        if not summary and (r["scouting_ai_summary"] or ""):
            summary = (r["scouting_ai_summary"] or "").strip()
        out.append(
            {
                "slug": slug,
                "espn_rank": r["espn_rank"],
                "last_name": r["last_name"] or "",
                "first_name": r["first_name"] or "",
                "school_or_team": r["school_or_team"] or "",
                "summary": summary,
                "physical_traits": physical,
                "scouting_json": jtxt,
                "source": src,
            }
        )
    more_pending = (
        incremental_ollama
        and ollama_ok
        and use_ollama
        and not halt_ollama
        and _next_ollama_slug(rows, cap, ollama_ok, use_ollama, cache) is not None
    )
    should_rerun = bool(
        ran_ollama_this_run
        and incremental_ollama
        and not halt_ollama
        and more_pending
    )
    n_autosaved = 0
    if auto_persist_to_db and pending_autosave:
        c = db.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            n_autosaved = persist_merged_scouting_for_slugs(
                c, pending_autosave,
            )
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            raise
        finally:
            c.close()

    return pd.DataFrame(out), should_rerun, n_autosaved


def render() -> None:
    st.header("Scouting reports")
    st.caption(
        "A real **scouting report** (play style, pros, cons, **physical traits**) is "
        "created only when **AI (Ollama)** is on and running. Wikipedia and web search are "
        "**extra context for that AI**—they are not shown as the main write-up by themselves. "
        "With AI off, **Scouting summary** is the short **ESPN rank line** from your cache. "
        "**Save to database** writes AI text and physical JSON for ratings. Only the first *N* "
        "rows run AI/web each load—raise *N* in steps if needed."
    )
    st.info(
        "**No narrative report without AI:** turn on **Write play style, pros, and cons "
        "(AI on this computer)** and keep the **max rows** at a number you can wait for "
        "(each player can take ~20–90s). **Physical traits** are filled by the same AI step."
    )

    c_cap, c_ai = st.columns(2)
    with c_cap:
        enrich_cap = st.number_input(
            "Max rows for AI + Wikipedia + web (per page load, by rank)",
            min_value=1,
            max_value=200,
            value=int(config.SCOUTING_ENRICH_ROW_CAP),
            help="The rest of the list still shows ESPN (and any text already saved in the "
            "database). Prevents 100+ slow network/LLM calls in one go.",
        )
    with c_ai:
        use_ai = st.checkbox(
            "Write play style, pros, and cons (AI on this computer)",
            value=True,
            help="Required for real scouting text and physical traits. First N rows only. "
            "Typical 20–90s per player depending on model and GPU.",
        )
    regenerate_ai = st.checkbox(
        "Regenerate AI for first N rows (ignore saved database reports)",
        value=False,
        help="**Off (default):** use **Save to database** text when present so opening this tab "
        "does not re-run Ollama. **On:** always call the model for empty or filled rows in the top "
        "*N* (still respects session cache until you clear it).",
    )
    auto_save_scouting = st.checkbox(
        "Auto-save new AI scouting to the database",
        value=True,
        key="scouting_autosave_db",
        help="When Ollama writes a new report, save it to SQLite immediately so a browser "
        "refresh keeps your work (same merge rules as **Save to database**).",
    )
    if use_ai and config.USE_OLLAMA and not scouting.ollama_server_reachable():
        st.info(
            "The workshop could not reach the Ollama app on this computer. If you want "
            "AI-written scouting notes, install **Ollama** from [ollama.com](https://ollama.com) "
            "and open it; it can download a language model the first time you use it, then come "
            "back here. Or uncheck the option above to show only the rank lines from ESPN."
        )

    use_wiki = st.checkbox(
        "Add Wikipedia (as extra input for AI only — not shown as the main summary)",
        value=True,
        help="Fetched only when **AI** is on, for the first N rows. Feeds the model; "
        "it is not pasted into the table as the scouting write-up.",
    )
    use_web = st.checkbox(
        "Add DuckDuckGo snippets (extra input for AI only)",
        value=True,
        help="Only when **AI** is on, first N rows. Requires ``duckduckgo-search``; recommended "
        "for athleticism/scouting context beyond ESPN and Wikipedia.",
    )
    show_nba_overlap = st.checkbox(
        "Include prospects who match a current NBA roster player",
        value=False,
        key="scouting_include_nba_overlap",
    )
    c_inc, c_stop, c_clr = st.columns(3)
    with c_inc:
        incremental_oll = st.checkbox(
            "One AI player per page refresh (stops between players; use with Stop / Resume)",
            value=False,
            help="After each Ollama call, the app refreshes so the **Stop research** button "
            "can take effect before the next player. The full board still loads in the table; "
            "only the next uncached top-N prospect runs AI. Uses session memory until you clear it.",
        )
    with c_stop:
        if st.button("Stop research", type="secondary", help="Finishes the current work unit, then skips further AI in **incremental** mode until you refresh options."):
            st.session_state[SCOUT_HALT_KEY] = True
    with c_clr:
        if st.button("Clear AI session cache", help="Forgets in-memory Ollama results for this session so the next run can call the model again."):
            st.session_state.pop(SCOUT_OLLAMA_CACHE_KEY, None)
    if SCOUT_OLLAMA_CACHE_KEY not in st.session_state:
        st.session_state[SCOUT_OLLAMA_CACHE_KEY] = {}
    ollama_cache: dict[str, dict[str, object]] = st.session_state[SCOUT_OLLAMA_CACHE_KEY]  # type: ignore[assignment]
    halt_oll = bool(st.session_state.get(SCOUT_HALT_KEY, False))
    with st.expander("What the speed limit means", expanded=False):
        st.markdown(
            """
The app sorts prospects by **ESPN rank** and only runs **Ollama, Wikipedia, and web search** for the **first *N* rows** each time this page runs (`Max rows` above). **Everyone** still gets the short **ESPN line** from your cached big board. Players **below *N** show that ESPN line (and any text you already **saved to the database** from an earlier run).

- **With summary: 60** means 60 non-empty “Scouting summary” cells in the table (often: those rows got an AI or ESPN line, depending on *N* and filters).
- To cover a **long board** without one giant wait: use **One AI player per page refresh**, raise `Max rows`, and click **Save to database in batches** as you go—or increase `Max rows` and run again (saved DB text fills in lower rows even when *N* is small).

**Why:** each AI pass can take tens of seconds; processing 100+ players in one go would look frozen and is hard to cancel in a browser.
            """
        )

    n_in_db = 0
    try:
        conn = db.connect()
        n_in_db = int(
            conn.execute("SELECT COUNT(*) AS n FROM prospects").fetchone()["n"]
        )
    finally:
        conn.close()

    prog = st.empty()
    df = pd.DataFrame()
    n_autosaved = 0
    should_rerun = False
    halt_before = bool(st.session_state.get(SCOUT_HALT_KEY, False))
    try:
        with st.spinner("Building scouting table (progress below)…"):

            def _on_prog(i: int, total: int, slug: str) -> None:
                try:
                    prog.progress(
                        i / max(total, 1),
                        text=f"[{i}/{total}] {slug}",
                    )
                except Exception:  # noqa: BLE001
                    pass

            df, should_rerun, n_autosaved = _build_scouting_dataframe(
                use_web=use_web,
                use_wikipedia=use_wiki,
                use_ollama=use_ai,
                exclude_current_nba=not show_nba_overlap,
                enrich_cap=int(enrich_cap),
                on_progress=_on_prog,
                ollama_cache=ollama_cache,
                incremental_ollama=incremental_oll and use_ai,
                halt_ollama=halt_oll,
                regenerate_ai=regenerate_ai and use_ai,
                auto_persist_to_db=bool(auto_save_scouting and use_ai),
            )
    finally:
        try:
            prog.empty()
        except Exception:  # noqa: BLE001
            pass
    if halt_oll:
        st.session_state[SCOUT_HALT_KEY] = False
    if n_autosaved > 0:
        st.success(
            f"Auto-saved **{n_autosaved}** scouting row(s) to the database (reload will keep them).",
        )
    if should_rerun:
        st.caption("Continuing AI research in the next refresh…")
        st.rerun()
    if halt_before and use_ai:
        st.info(
            "**Stop** took effect: no new Ollama runs on that pass for uncached players. "
            "For a true pause between each player, turn on **One AI player per page refresh**. "
            "**Clear AI session cache** forces the model to run again for those rows."
        )

    if (
        not df.empty
        and len(df) > int(enrich_cap)
        and (use_ai or use_wiki or use_web)
    ):
        st.warning(
            f"**Row cap:** only the first **{enrich_cap}** prospects (by **ESPN rank**) ran "
            f"**Ollama, Wikipedia, and web** on this run. The other **{len(df) - int(enrich_cap)}** "
            "rows show the **ESPN** big-board line and any **AI text already saved in the database**. "
            "Increase **Max rows**, use **One AI player per page refresh** + **Save to database** in batches, or run the tab again for the next block."
        )

    if df.empty:
        st.warning(
            "No prospects to show. Load a big board under **Settings → Load ESPN "
            "bigboard + seed list**, or enable **Include prospects who match…** "
            "if every row was filtered as an NBA roster match.",
        )
        st.caption("Load the big board under **Settings**, then return here.")
        return

    with_summary = (df["summary"] != "").sum()
    c1, c2 = st.columns(2)
    c1.metric("Prospects", len(df))
    c2.metric("With summary", int(with_summary))
    if with_summary == 0 and len(df) > 0:
        st.info(
            "No ESPN text in the cache yet. Use **Settings → Load ESPN big board**. "
            "For full **scouting reports**, also turn on **AI** and run Ollama."
        )

    if st.button("Save current scouting to database (for 2K ratings)", type="primary"):
        n_ok = 0
        c = db.connect()
        try:
            slugs: list[str] = []
            for _, row in df.iterrows():
                s = str(row.get("slug") or "").strip()
                if s:
                    slugs.append(s)
            existing: dict[str, tuple[str, str, str]] = {}
            if slugs:
                qs = ",".join("?" * len(slugs))
                cur = c.execute(
                    f"""
                    SELECT slug, scouting_ai_summary, scouting_physical_text,
                           scouting_physical_json
                    FROM prospects
                    WHERE slug IN ({qs})
                    """,
                    slugs,
                )
                for r0 in cur.fetchall():
                    existing[str(r0[0])] = (
                        str(r0[1] or ""),
                        str(r0[2] or ""),
                        str(r0[3] or ""),
                    )
            for _, row in df.iterrows():
                slug = str(row.get("slug") or "")
                if not slug:
                    continue
                sm = (row.get("summary") or "").strip()
                ph = (row.get("physical_traits") or "").strip()
                jt = (row.get("scouting_json") or "").strip()
                if not sm and not jt and not ph:
                    continue
                ex = existing.get(slug, ("", "", ""))
                out_s, out_p, out_j = merge_scouting_for_save(
                    sm, ph, jt, ex[0], ex[1], ex[2]
                )
                c.execute(
                    """
                    UPDATE prospects
                    SET scouting_ai_summary=?, scouting_physical_text=?,
                        scouting_physical_json=?,
                        updated_at=strftime('%%Y-%%m-%%dT%%H:%%M:%%fZ', 'now')
                    WHERE slug=?
                    """,
                    (out_s, out_p, out_j, slug),
                )
                n_ok += 1
        finally:
            c.close()
        st.success(
            f"Updated scouting fields for {n_ok} prospects. Use **Settings → Recompute** "
            "or **Formulas** to refresh ratings."
        )
        clear_scouting_cache()

    search = st.text_input(
        "Search by name / school", "", key="scouting_search_filter"
    )
    view = df.copy()
    if search:
        s = search.strip()
        mask = (
            view["last_name"].str.contains(s, case=False, na=False)
            | view["first_name"].str.contains(s, case=False, na=False)
            | view["school_or_team"].fillna("").str.contains(
                s, case=False, na=False
            )
        )
        view = view[mask]

    st.caption(f"Showing {len(view)} / {len(df)} rows")

    name_cfg = common.pinned_name_column_config()
    show = view.drop(columns=["slug", "scouting_json"], errors="ignore")
    cfg = {
        **name_cfg,
        "espn_rank": st.column_config.NumberColumn("Rank", format="%d"),
        "school_or_team": st.column_config.TextColumn("School / team"),
        "summary": st.column_config.TextColumn("Scouting summary", width="large"),
        "physical_traits": st.column_config.TextColumn(
            "Physical traits", width="large"
        ),
        "source": st.column_config.TextColumn("Sources"),
    }
    st.dataframe(
        show,
        column_config=cfg,
        use_container_width=True,
        height=640,
        hide_index=True,
    )

    buf = io.StringIO()
    dl = view.drop(
        columns=["slug", "scouting_json"],
        errors="ignore",
    ).rename(
        columns={
            "last_name": "Last Name",
            "first_name": "First Name",
            "school_or_team": "School / team",
            "espn_rank": "ESPN rank",
            "summary": "Scouting summary",
            "physical_traits": "Physical traits",
        }
    )
    dl.to_csv(buf, index=False)
    st.download_button(
        "Download CSV",
        buf.getvalue(),
        file_name="scouting_summaries.csv",
        mime="text/csv",
    )
