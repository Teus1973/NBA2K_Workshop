"""Recompute slug from full_name for every nba_players row (Unicode-safe)."""
from __future__ import annotations

from src import db
from src.scrapers import twokratings

conn = db.connect()
try:
    rows = conn.execute("SELECT player_id, full_name, slug FROM nba_players").fetchall()
    cur = conn.cursor()
    changed = 0
    for r in rows:
        new_slug = twokratings.slugify_name(r["full_name"] or "")
        if new_slug != (r["slug"] or ""):
            cur.execute(
                "UPDATE nba_players SET slug=? WHERE player_id=?",
                (new_slug, r["player_id"]),
            )
            changed += 1
    conn.commit()
    print(f"Updated {changed} / {len(rows)} slugs")
    luka = conn.execute(
        "SELECT slug FROM nba_players WHERE player_id = 1629029"
    ).fetchone()
    if luka:
        print("Luka Doncic slug:", luka["slug"])
finally:
    conn.close()
