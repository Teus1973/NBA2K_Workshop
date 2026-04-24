from src import db

c = db.connect()
total = c.execute("SELECT COUNT(*) FROM nba_players").fetchone()[0]
with_slug = c.execute(
    "SELECT COUNT(*) FROM nba_players WHERE slug IS NOT NULL AND slug != ''"
).fetchone()[0]
print(f"total={total} with_slug={with_slug}")
for r in c.execute("SELECT player_id, full_name, slug FROM nba_players LIMIT 5"):
    print(dict(r))
