"""Debug: scrape 3 players explicitly and show what happens."""
from __future__ import annotations

import traceback

from src import db
from src.scrapers import twokratings

conn = db.connect()
try:
    rows = conn.execute(
        "SELECT player_id, slug, full_name FROM nba_players "
        "WHERE slug IS NOT NULL ORDER BY full_name LIMIT 3"
    ).fetchall()
finally:
    conn.close()

print(f"Target players: {[(r['player_id'], r['slug']) for r in rows]}")

for r in rows:
    pid = r["player_id"]
    slug = r["slug"]
    name = r["full_name"]
    print(f"\n--- {name} ({slug}) ---")
    try:
        p = twokratings.scrape_player(slug, log_audit=False)
        print(f"  overall={p.overall_2k}  #attrs={len(p.attributes)}")
        print(f"  sample attrs: {list(p.attributes.items())[:3]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        traceback.print_exc()
