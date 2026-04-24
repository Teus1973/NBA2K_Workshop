"""One-shot backfill: derive slug from full_name for existing nba_players rows."""
from __future__ import annotations

from src import db
from src.scrapers import twokratings

conn = db.connect()
try:
    rows = conn.execute(
        "SELECT player_id, full_name FROM nba_players "
        "WHERE slug IS NULL OR slug = ''"
    ).fetchall()
    print(f"Rows to backfill: {len(rows)}")
    cur = conn.cursor()
    n = 0
    for r in rows:
        slug = twokratings.slugify_name(r["full_name"] or "")
        if slug:
            cur.execute(
                "UPDATE nba_players SET slug=? WHERE player_id=?",
                (slug, r["player_id"]))
            n += 1
    conn.commit()
    print(f"Updated {n} rows.")
    sample = conn.execute(
        "SELECT player_id, full_name, slug FROM nba_players LIMIT 5"
    ).fetchall()
    for s in sample:
        print(dict(s))
finally:
    conn.close()
