"""Print row counts for every workshop table (dev smoke)."""
from __future__ import annotations

from src import db

TABLES = (
    "nba_players",
    "nba_stats_season",
    "nba_ratings_2k26",
    "combine_measurements",
    "combine_drills",
    "prospects",
    "prospect_stats",
    "prospect_ratings_computed",
    "audit_log",
    "formulas",
)


def main() -> None:
    conn = db.connect()
    try:
        for t in TABLES:
            n = conn.execute(f"select count(*) from {t}").fetchone()[0]
            print(f"{t:32s} {n:>6}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
