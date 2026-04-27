"""Merge-on-save: empty table fields must not NULL out existing DB columns."""

from __future__ import annotations

import sqlite3

from src.scouting_persist import (
    cache_entry_has_real_scouting,
    merge_scouting_for_save,
    persist_merged_scouting_for_slugs,
)


def test_merge_uses_db_when_on_screen_field_blank() -> None:
    s, p, j = merge_scouting_for_save(
        "ESPN line",
        "",
        "",
        "old summary",
        "old physical",
        '{"strength_01":0.5}',
    )
    assert s == "ESPN line"
    assert p == "old physical"
    assert j == '{"strength_01":0.5}'


def test_merge_uses_on_screen_when_present() -> None:
    s, p, j = merge_scouting_for_save(
        "new sum",
        "new phys",
        '{"a":1}',
        "old s",
        "old p",
        "old j",
    )
    assert s == "new sum"
    assert p == "new phys"
    assert j == '{"a":1}'


def test_merge_empty_ex_all_new() -> None:
    s, p, j = merge_scouting_for_save("only S", "", "", "", "", "")
    assert s == "only S"
    assert p is None
    assert j is None


def test_cache_entry_has_real_scouting() -> None:
    assert not cache_entry_has_real_scouting(None)
    assert not cache_entry_has_real_scouting({})
    assert not cache_entry_has_real_scouting(
        {"summary": "", "synthesis_failed": True},
    )
    assert cache_entry_has_real_scouting(
        {"summary": "x", "physical_traits": "", "scouting_json": ""},
    )


def test_persist_merged_roundtrip() -> None:
    """In-memory DB with one prospect row: merge + update."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE prospects (
            slug TEXT PRIMARY KEY,
            scouting_ai_summary TEXT,
            scouting_physical_text TEXT,
            scouting_physical_json TEXT,
            updated_at TEXT
        )
        """,
    )
    conn.execute(
        "INSERT INTO prospects (slug) VALUES ('a-b')",
    )
    n = persist_merged_scouting_for_slugs(
        conn,
        [("a-b", "sum", "phys", '{"strength_01":0.6}')],
    )
    assert n == 1
    row = conn.execute(
        "SELECT * FROM prospects WHERE slug='a-b'",
    ).fetchone()
    assert row["scouting_ai_summary"] == "sum"
    assert "strength" in (row["scouting_physical_json"] or "")
    conn.close()
