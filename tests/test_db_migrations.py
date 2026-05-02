"""Verify DB schema is created idempotently and contains all expected tables."""

from __future__ import annotations

from src import config, db


def test_schema_creates_all_tables(temp_db):
    expected = {
        "schema_meta", "nba_players", "nba_ratings_2k26", "nba_stats_season",
        "combine_measurements", "combine_drills",
        "prospects", "prospect_stats", "prospect_ratings_computed",
        "audit_log", "formulas",
    }
    cur = temp_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")
    names = {row[0] for row in cur.fetchall()}
    missing = expected - names
    assert not missing, f"missing tables: {missing}"


def test_rating_columns_present(temp_db):
    cur = temp_db.execute("PRAGMA table_info(nba_ratings_2k26)")
    cols = {row[1] for row in cur.fetchall()}
    for attr in config.RATING_ATTRIBUTES:
        assert attr in cols, f"missing rating column {attr}"


def test_stat_columns_present(temp_db):
    cur = temp_db.execute("PRAGMA table_info(nba_stats_season)")
    cols = {row[1] for row in cur.fetchall()}
    for stat in config.STAT_COLUMNS:
        assert stat in cols, f"missing stat column {stat}"


def test_connect_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    c1 = db.connect()
    c2 = db.connect()
    try:
        assert db.table_count(c1, "audit_log") == 0
        assert db.table_count(c2, "nba_players") == 0
    finally:
        c1.close()
        c2.close()


def test_prospects_date_of_birth_migrated(temp_db):
    cur = temp_db.execute("PRAGMA table_info(prospects)")
    cols = {row[1] for row in cur.fetchall()}
    assert "date_of_birth" in cols


def test_prospects_scouting_columns_migrated(temp_db):
    cur = temp_db.execute("PRAGMA table_info(prospects)")
    cols = {row[1] for row in cur.fetchall()}
    assert "scouting_ai_summary" in cols
    assert "scouting_physical_text" in cols
    assert "scouting_physical_json" in cols


def test_prospects_column1_migrated(temp_db):
    cur = temp_db.execute("PRAGMA table_info(prospects)")
    cols = {row[1] for row in cur.fetchall()}
    assert "column1" in cols


def test_prospect_ratings_potential_and_workbook_attrs(temp_db):
    cur = temp_db.execute("PRAGMA table_info(prospect_ratings_computed)")
    cols = {row[1] for row in cur.fetchall()}
    assert "potential" in cols
    for attr in (
        "post_hook_2k",
        "post_fade_2k",
        "intangibles_2k",
        "durability_2k",
    ):
        assert attr in cols, f"missing {attr}"


def test_audit_log_insert(temp_db):
    from src import audit
    rid = audit.log_event(
        action="note",
        entity_type="test",
        note="hi",
        conn=temp_db,
    )
    assert rid > 0
    row = temp_db.execute(
        "SELECT action, entity_type, note FROM audit_log WHERE id=?",
        (rid,),
    ).fetchone()
    assert row["action"] == "note"
    assert row["entity_type"] == "test"
    assert row["note"] == "hi"
