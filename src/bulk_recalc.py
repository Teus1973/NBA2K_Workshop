"""
Bulk prospect rating recompute: single DB session + optional progress callback.

Used from Settings, Formulas, and CLI so Streamlit can show a progress bar
instead of a long silent hang.

Writes all rating rows in **one SQLite transaction** (``BEGIN IMMEDIATE`` …
``COMMIT``) against :func:`db.connect`'s autocommit connection so Windows
fsync storm is avoided.

Schema **v4** ``prospect_ratings_computed`` column order is driven by
:data:`config.RATING_ATTRIBUTES` (37 ints) then ``potential`` (TEXT), matching
workbook export names for ``intangibles_2k`` / ``durability_2k`` (e.g. Excel
headers *Integnagbles* / *Durablity*) via the same snake_case DB fields.

The optional ``progress_cb`` is **throttled** (~20 calls per run) so Streamlit is
not flooded with ``st.progress`` updates (which can freeze the UI).
"""

from __future__ import annotations

from collections.abc import Callable

import sqlite3

from . import audit, config, db
from .exporters import data_loader
from .formulas import apply as fapply, registry as _reg
from .logger import get_logger

log = get_logger("bulk_recalc")

ProgressCallback = Callable[[int, int, str], None]


def _progress_indices(total: int) -> set[int]:
    """~20 indices in [1, total] for throttling UI progress (first, last, spread)."""
    if total <= 0:
        return set()
    if total == 1:
        return {1}
    out = {1, total}
    step = max(1, (total + 19) // 20)  # ceil(total / 20)
    for j in range(step, total, step):
        out.add(j)
    return out


def recompute_prospect_ratings(
    *,
    progress_cb: ProgressCallback | None = None,
    audit_note: str = "settings",
    conn: sqlite3.Connection | None = None,
) -> int:
    """Recompute and persist ``prospect_ratings_computed`` for all prospects.

    Uses one connection to load the merged DataFrame and write rows, sets a
    SQLite ``busy_timeout`` to reduce indefinite waits on lock contention, and
    invokes ``progress_cb(i, total, slug)`` a limited number of times (first,
    last, and ~20 steps) when provided — not after every row.
    """
    reg = _reg.load_registry()
    own = False
    if conn is None:
        conn = db.connect()
        own = True
    n = 0
    wingspan_combine_hits = 0
    try:
        if own:
            conn.execute("PRAGMA busy_timeout=120000")
        pros = data_loader.load_prospects_df(conn=conn, exclude_current_nba=True)
        if pros.empty:
            return 0
        if "combine_wingspan_in" in pros.columns:
            wingspan_combine_hits = int(pros["combine_wingspan_in"].notna().sum())

        total = len(pros)
        report_at = _progress_indices(total) if progress_cb else set()

        conn.execute("BEGIN IMMEDIATE")
        try:
            for i, (_, row) in enumerate(pros.iterrows(), start=1):
                slug = str(row.get("slug") or "")
                if progress_cb and i in report_at:
                    try:
                        progress_cb(i, total, slug)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("progress_cb: %s", exc)
                ratings, _ = fapply.apply_formulas(
                    row.to_dict(),
                    config.get_rating_engine(),
                    registry=reg,
                )
                # v4: slug + RATING_ATTRIBUTES (37) + potential + meta
                cols = ["slug"] + list(config.RATING_ATTRIBUTES) + [
                    "potential", "formula_version", "manual_override_json",
                ]
                pot = ratings.get("potential")
                if pot is not None:
                    pot = int(round(float(pot)))
                values = [slug] + [ratings.get(a) for a in config.RATING_ATTRIBUTES] + [
                    pot, 1, None,
                ]
                placeholders = ", ".join(["?"] * len(cols))
                sql = (
                    f"INSERT INTO prospect_ratings_computed ({', '.join(cols)}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT(slug) DO UPDATE SET "
                    + ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
                )
                conn.execute(sql, values)
                n += 1
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        if own:
            conn.close()
    audit.log_event(
        action="rating_recalc",
        entity_type="prospect",
        note=(
            f"{audit_note}: {n} prospects; "
            f"combine_wingspan_in_override_hits={wingspan_combine_hits}"
        ),
    )
    return n
