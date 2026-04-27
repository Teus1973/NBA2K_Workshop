"""
NBA2K26 Workshop — append-only audit log helpers.

Every scraper, formula recalc, player add/remove, and user override writes a
row to ``audit_log``. The Logs tab reads from this table newest-first.

Usage:
    from src import audit
    audit.log_event(
        action="rating_recalc",
        entity_type="prospect",
        entity_slug="aj-dybantsa",
        field="three_point_shot_2k",
        before=72,
        after=74,
        note="formula v2 (post-combine)",
    )
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from . import db as _db
from .logger import get_logger

log = get_logger("audit")


ALLOWED_ACTIONS: frozenset[str] = frozenset({
    "stat_refresh",
    "rating_recalc",
    "player_added",
    "player_removed",
    "formula_edit",
    "formula_refit",
    "override_set",
    "override_cleared",
    "scrape_2kratings",
    "bulk_scrape_2kratings",
    "scrape_nba_stats",
    "scrape_nba_combine",
    "scrape_espn_bigboard",
    "scrape_cbb",
    "dob_enrich_cbb",
    "scrape_international",
    "scrape_scouting",
    "csv_upload",
    "export_excel",
    "export_gsheets",
    "note",
})


def _val_to_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, default=str, ensure_ascii=False)
    return str(v)


def log_event(
    *,
    action: str,
    actor: str = "system",
    entity_type: str | None = None,
    entity_slug: str | None = None,
    field: str | None = None,
    before: Any = None,
    after: Any = None,
    note: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Insert a row into ``audit_log`` and return its id.

    If ``conn`` is ``None`` a short-lived connection is opened. Pass an
    existing connection when batching to avoid open/close churn.
    """
    if action not in ALLOWED_ACTIONS:
        log.warning("audit: unknown action %r (still logging)", action)
    own_conn = conn is None
    if own_conn:
        conn = _db.connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO audit_log
                (actor, action, entity_type, entity_slug, field,
                 before_value, after_value, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                actor,
                action,
                entity_type,
                entity_slug,
                field,
                _val_to_text(before),
                _val_to_text(after),
                note,
            ),
        )
        return int(cur.lastrowid or 0)
    finally:
        if own_conn:
            conn.close()  # type: ignore[union-attr]


def log_batch(events: Iterable[dict[str, Any]], actor: str = "system") -> int:
    """Bulk insert. Each dict uses the same keys as :func:`log_event`.

    Returns the number of rows inserted.
    """
    rows = list(events)
    if not rows:
        return 0
    conn = _db.connect()
    try:
        conn.executemany(
            """
            INSERT INTO audit_log
                (actor, action, entity_type, entity_slug, field,
                 before_value, after_value, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e.get("actor", actor),
                    e["action"],
                    e.get("entity_type"),
                    e.get("entity_slug"),
                    e.get("field"),
                    _val_to_text(e.get("before")),
                    _val_to_text(e.get("after")),
                    e.get("note"),
                )
                for e in rows
            ],
        )
        return len(rows)
    finally:
        conn.close()


def recent(limit: int = 500, *, action: str | None = None,
           slug: str | None = None) -> list[sqlite3.Row]:
    """Return most-recent rows (newest first), optionally filtered."""
    conn = _db.connect()
    try:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list[Any] = []
        if action:
            sql += " AND action = ?"
            params.append(action)
        if slug:
            sql += " AND entity_slug = ?"
            params.append(slug)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        cur = conn.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def clear(confirm: bool = False) -> int:
    """Nuke the audit log. ``confirm=True`` required. Returns rows deleted."""
    if not confirm:
        raise ValueError("audit.clear requires confirm=True")
    conn = _db.connect()
    try:
        cur = conn.execute("DELETE FROM audit_log")
        return cur.rowcount or 0
    finally:
        conn.close()
