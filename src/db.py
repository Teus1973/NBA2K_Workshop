"""
NBA2K26 Workshop — SQLite schema + connection helpers.

Creates (idempotently) every table listed in Documents/PLAN.md section 2.1.
All other modules go through :func:`connect` to obtain a ``sqlite3.Connection``
with row-factory set to ``sqlite3.Row`` and foreign keys enabled.

Schema versioning: a tiny ``schema_meta`` table holds the current migration
version so we can evolve columns without losing local DBs.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config
from .logger import get_logger

log = get_logger("db")

SCHEMA_VERSION = 4

# Integer rating columns added after v3 (workbook-aligned schema).
_RATING_COLUMNS_V4: tuple[str, ...] = (
    "post_hook_2k",
    "post_fade_2k",
    "intangibles_2k",
    "durability_2k",
)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open (and initialize, if needed) the workshop SQLite DB."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    _ensure_schema(conn)
    return conn


@contextmanager
def cursor(db_path: Path | str | None = None) -> Iterator[sqlite3.Cursor]:
    """Context manager yielding a cursor and closing the connection on exit."""
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        yield cur
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def _rating_columns_sql() -> str:
    """Return ``"overall_2k" INTEGER, ...`` for :data:`config.RATING_ATTRIBUTES`."""
    return ",\n  ".join(
        f'"{attr}" INTEGER' for attr in config.RATING_ATTRIBUTES
    )


def _stat_columns_sql() -> str:
    """Return stat columns (REAL). All nullable."""
    # gp is int, rest are REAL to keep percentages + per-game totals
    cols = []
    for stat in config.STAT_COLUMNS:
        sql_type = "INTEGER" if stat == "gp" else "REAL"
        cols.append(f'"{stat}" {sql_type}')
    return ",\n  ".join(cols)


_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,

    # -------------------------------------------------------------------
    # NBA reference tables (ground truth for calibration)
    # -------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS nba_players (
        player_id     INTEGER PRIMARY KEY,
        slug          TEXT UNIQUE,
        first_name    TEXT,
        last_name     TEXT,
        full_name     TEXT,
        team          TEXT,
        pos           TEXT,
        height_in     REAL,
        weight_lbs    REAL,
        wingspan_in   REAL,
        age           REAL,
        birthdate     TEXT,
        updated_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_nba_players_slug ON nba_players(slug);
    """,

    f"""
    CREATE TABLE IF NOT EXISTS nba_ratings_2k26 (
        player_id     INTEGER PRIMARY KEY,
        slug          TEXT,
        {_rating_columns_sql()},
        total_attributes INTEGER,
        potential     TEXT,
        source_url    TEXT,
        scraped_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY(player_id) REFERENCES nba_players(player_id) ON DELETE CASCADE
    );
    """,

    f"""
    CREATE TABLE IF NOT EXISTS nba_stats_season (
        player_id     INTEGER,
        season        TEXT,
        season_type   TEXT DEFAULT 'Regular',
        {_stat_columns_sql()},
        source        TEXT,
        updated_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (player_id, season, season_type),
        FOREIGN KEY(player_id) REFERENCES nba_players(player_id) ON DELETE CASCADE
    );
    """,

    # -------------------------------------------------------------------
    # Combine tables (both NBA players and prospects use these)
    # -------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS combine_measurements (
        subject_key         TEXT,   -- nba_players.slug OR prospects.slug
        year                INTEGER,
        height_wo_shoes_in  REAL,
        height_w_shoes_in   REAL,
        wingspan_in         REAL,
        weight_lbs          REAL,
        std_reach_in        REAL,
        body_fat_pct        REAL,
        hand_length_in      REAL,
        hand_width_in       REAL,
        updated_at          TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (subject_key, year)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS combine_drills (
        subject_key                TEXT,
        year                       INTEGER,
        lane_agility_sec           REAL,
        shuttle_sec                REAL,
        three_quarter_sprint_sec   REAL,
        standing_vert_in           REAL,
        max_vert_in                REAL,
        bench_reps                 INTEGER,
        c_speed_2k                 INTEGER,
        c_speed_with_ball_2k       INTEGER,
        c_vertical_2k              INTEGER,
        c_agility_2k               INTEGER,
        updated_at                 TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (subject_key, year)
    );
    """,

    # -------------------------------------------------------------------
    # Prospects
    # -------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS prospects (
        slug            TEXT PRIMARY KEY,
        first_name      TEXT,
        last_name       TEXT,
        full_name       TEXT,
        pos             TEXT,
        school_or_team  TEXT,
        league          TEXT,           -- ncaa / euroleague / nbl / nznbl / hs / gleague
        age             REAL,
        height_in       REAL,           -- listed (pre-combine); overridden by combine post May 10
        weight_lbs      REAL,
        wingspan_in     REAL,
        espn_rank       INTEGER,
        other_rank      INTEGER,
        status          TEXT DEFAULT 'active',  -- active / withdrew / undecided / drafted
        added_by        TEXT DEFAULT 'system',  -- system / user
        notes           TEXT,
        updated_at      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    );
    """,

    f"""
    CREATE TABLE IF NOT EXISTS prospect_stats (
        slug         TEXT,
        season       TEXT,
        league       TEXT,
        {_stat_columns_sql()},
        source       TEXT,
        updated_at   TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        PRIMARY KEY (slug, season, league)
    );
    """,

    f"""
    CREATE TABLE IF NOT EXISTS prospect_ratings_computed (
        slug              TEXT PRIMARY KEY,
        {_rating_columns_sql()},
        potential         TEXT,
        formula_version   INTEGER,
        manual_override_json TEXT,       -- JSON object of user overrides per attribute
        computed_at       TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        FOREIGN KEY(slug) REFERENCES prospects(slug) ON DELETE CASCADE
    );
    """,

    # -------------------------------------------------------------------
    # Audit log (drives the Logs tab)
    # -------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        ts             TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        actor          TEXT NOT NULL,        -- system / user
        action         TEXT NOT NULL,        -- stat_refresh / rating_recalc / ...
        entity_type    TEXT,                 -- prospect / nba_player / formula
        entity_slug    TEXT,
        field          TEXT,
        before_value   TEXT,
        after_value    TEXT,
        note           TEXT
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_audit_log_ts   ON audit_log(ts DESC);
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_audit_log_slug ON audit_log(entity_slug);
    """,

    # -------------------------------------------------------------------
    # Formulas (YAML blobs mirrored from disk + versioned)
    # -------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS formulas (
        attribute    TEXT,
        version      INTEGER,
        yaml_blob    TEXT NOT NULL,
        r2           REAL,
        mae          REAL,
        n_samples    INTEGER,
        edited_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        edited_by    TEXT DEFAULT 'system',
        notes        TEXT,
        PRIMARY KEY (attribute, version)
    );
    """,
)


def _add_columns_if_missing(
    cur: sqlite3.Cursor, table: str, definitions: list[tuple[str, str]],
) -> None:
    cur.execute(f"PRAGMA table_info({table})")
    have = {row[1] for row in cur.fetchall()}
    for col, sql_type in definitions:
        if col not in have:
            cur.execute(f'ALTER TABLE {table} ADD COLUMN "{col}" {sql_type}')


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add columns on existing DBs and align ``schema_meta.version``."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(prospects)")
    cols = {row[1] for row in cur.fetchall()}
    if "date_of_birth" not in cols:
        cur.execute("ALTER TABLE prospects ADD COLUMN date_of_birth TEXT")
    if "scouting_ai_summary" not in cols:
        cur.execute(
            "ALTER TABLE prospects ADD COLUMN scouting_ai_summary TEXT")
    if "scouting_physical_text" not in cols:
        cur.execute(
            "ALTER TABLE prospects ADD COLUMN scouting_physical_text TEXT")
    if "scouting_physical_json" not in cols:
        cur.execute(
            "ALTER TABLE prospects ADD COLUMN scouting_physical_json TEXT")
    _add_columns_if_missing(cur, "prospects", [("column1", "TEXT")])

    new_ints = [(c, "INTEGER") for c in _RATING_COLUMNS_V4]
    _add_columns_if_missing(cur, "nba_ratings_2k26", new_ints)
    _add_columns_if_missing(cur, "prospect_ratings_computed", new_ints)
    _add_columns_if_missing(cur, "prospect_ratings_computed", [
        ("potential", "TEXT"),
    ])

    row = cur.execute(
        "SELECT value FROM schema_meta WHERE key='version'",
    ).fetchone()
    v = int(row[0]) if row and str(row[0]).isdigit() else 1
    if v < SCHEMA_VERSION:
        cur.execute(
            "UPDATE schema_meta SET value=? WHERE key='version'",
            (str(SCHEMA_VERSION),),
        )


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create any missing tables and bump schema_meta if empty."""
    cur = conn.cursor()
    for stmt in _SCHEMA_STATEMENTS:
        cur.execute(stmt)
    cur.execute(
        "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('version', ?)",
        (str(SCHEMA_VERSION),),
    )
    _migrate_schema(conn)
    conn.commit()


# ---------------------------------------------------------------------------
# Small convenience helpers
# ---------------------------------------------------------------------------
def table_count(conn: sqlite3.Connection, table: str) -> int:
    cur = conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
    row = cur.fetchone()
    return int(row["n"]) if row else 0


def has_any_data(conn: sqlite3.Connection) -> bool:
    """True if any ingestion has happened yet (used to gate first-run UI)."""
    for t in ("nba_ratings_2k26", "prospects"):
        if table_count(conn, t) > 0:
            return True
    return False
