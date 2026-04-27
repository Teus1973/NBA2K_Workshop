"""
Merge rules for persisting scouting text to ``prospects`` (Scouting tab).

Kept separate from the Streamlit UI so it can be tested without ``streamlit``.
"""

from __future__ import annotations

import sqlite3


def merge_scouting_for_save(
    new_summary: str,
    new_physical: str,
    new_json: str,
    ex_summary: str | None,
    ex_physical: str | None,
    ex_json: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Merge on-screen text with what is already in the DB for this prospect.

    If the current row leaves **physical** or **JSON** blank (e.g. row-cap-only ESPN
    line) but the database has saved AI content, we keep the DB value instead of
    writing ``NULL`` over it.
    """
    s = (new_summary or "").strip()
    p = (new_physical or "").strip()
    j = (new_json or "").strip()
    e_s = (ex_summary or "").strip()
    e_p = (ex_physical or "").strip()
    e_j = (ex_json or "").strip()
    out_s: str | None = s or (e_s or None)
    out_p: str | None = p or (e_p or None)
    out_j: str | None = j or (e_j or None)
    return (out_s, out_p, out_j)


def cache_entry_has_real_scouting(cache_val: object) -> bool:
    """True if the session-cache entry has any non-empty AI/DB scouting text."""
    if not cache_val or not isinstance(cache_val, dict):
        return False
    if (str(cache_val.get("summary") or "")).strip():
        return True
    if (str(cache_val.get("physical_traits") or "")).strip():
        return True
    if (str(cache_val.get("scouting_json") or "")).strip():
        return True
    return False


def persist_merged_scouting_for_slugs(
    conn: sqlite3.Connection,
    items: list[tuple[str, str, str, str]],
) -> int:
    """``(slug, summary, physical, json)`` on-screen text merged with current DB, then ``UPDATE``."""
    n = 0
    for slug, sm, ph, jt in items:
        if not (sm or ph or jt):
            continue
        row = conn.execute(
            """
            SELECT scouting_ai_summary, scouting_physical_text, scouting_physical_json
            FROM prospects WHERE slug=?
            """,
            (slug,),
        ).fetchone()
        ex_s = ex_p = ex_j = ""
        if row:
            ex_s, ex_p, ex_j = (str(row[0] or ""), str(row[1] or ""), str(row[2] or ""))
        out_s, out_p, out_j = merge_scouting_for_save(
            sm, ph, jt, ex_s, ex_p, ex_j,
        )
        if not out_s and not out_p and not out_j:
            continue
        conn.execute(
            """
            UPDATE prospects
            SET scouting_ai_summary=?,
                scouting_physical_text=?,
                scouting_physical_json=?,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE slug=?
            """,
            (out_s, out_p, out_j, slug),
        )
        n += 1
    return n
