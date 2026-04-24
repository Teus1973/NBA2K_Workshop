"""Show non-null sample values for key columns in each table."""
from __future__ import annotations

from src import db

conn = db.connect()
try:
    print("=== nba_stats_season (sample 3) ===")
    for r in conn.execute(
        "SELECT player_id, season, season_type, gp, pts, ast, reb, fg3_pct "
        "FROM nba_stats_season LIMIT 3"
    ):
        print(dict(r))

    print("\n=== nba_stats_season non-null counts for key fields ===")
    fields = ("gp", "pts", "ast", "reb", "fg3_pct", "fg_pct", "min")
    for f in fields:
        n = conn.execute(
            f"SELECT COUNT(*) FROM nba_stats_season WHERE {f} IS NOT NULL"
        ).fetchone()[0]
        print(f"  {f:10s}: {n}")

    print("\n=== nba_players sample ===")
    for r in conn.execute(
        "SELECT player_id, full_name, team, pos, height_in, weight_lbs "
        "FROM nba_players LIMIT 3"
    ):
        print(dict(r))

    print("\n=== season distribution ===")
    for r in conn.execute(
        "SELECT season, season_type, COUNT(*) FROM nba_stats_season "
        "GROUP BY season, season_type"
    ):
        print(tuple(r))

    print("\n=== prospects sample (first 3) ===")
    for r in conn.execute(
        "SELECT slug, full_name, pos, school_or_team, league, height_in, weight_lbs "
        "FROM prospects LIMIT 3"
    ):
        print(dict(r))

    print("\n=== prospect_ratings_computed sample (first 3) ===")
    for r in conn.execute(
        "SELECT slug, overall_2k, three_point_shot_2k, strength_2k, speed_2k, vertical_2k "
        "FROM prospect_ratings_computed LIMIT 3"
    ):
        print(dict(r))

    print("\n=== formulas sample (first 5) ===")
    for r in conn.execute(
        "SELECT attribute, version, r2, mae, n_samples FROM formulas LIMIT 5"
    ):
        print(dict(r))
finally:
    conn.close()
