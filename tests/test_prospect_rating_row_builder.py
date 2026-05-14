"""Parity tests: single-row rating input must mirror bulk-loaded prospect merges."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.db import connect
from src.exporters import data_loader


def test_slug_filtered_row_has_combine_height_mappings(tmp_path: Path) -> None:
    dbp = tmp_path / "row_builder.sqlite"
    conn = connect(dbp)
    try:
        conn.execute(
            "INSERT INTO prospects (slug, full_name, pos, league, height_in) "
            "VALUES (?,?,?,?,?)",
            ("fixture-combo", "Fixture Combo", "SF", "ncaa", 78.5),
        )
        conn.execute(
            "INSERT INTO prospect_stats (slug, season, league, gp, min, pts) "
            "VALUES (?,?,?,?,?,?)",
            ("fixture-combo", "2025-26", "ncaa", 31.0, 28.5, 12.5),
        )
        conn.execute(
            """INSERT INTO combine_measurements
            (subject_key, year, height_wo_shoes_in, height_w_shoes_in,
             wingspan_in, weight_lbs, std_reach_in, body_fat_pct,
             hand_length_in, hand_width_in)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("prospect:fixture-combo", 2026, 81.0, None, 85.5, None,
             None, None, None, None),
        )

        bulk = data_loader.load_prospects_df(
            conn=conn, slugs=["fixture-combo"], exclude_current_nba=False)
        solo = data_loader.load_single_prospect_row_dict_for_rating(
            "fixture-combo", conn=conn, exclude_current_nba=False)

        assert len(bulk) == 1
        assert bulk.iloc[0]["combine_height_in"] == pytest.approx(81.0)
        assert bulk.iloc[0]["height_in"] == pytest.approx(81.0)
        assert bulk.iloc[0]["combine_wingspan_in"] == pytest.approx(85.5)
        assert solo is not None
        assert solo.get("combine_height_in") == pytest.approx(81.0)
        assert solo.get("gp") == pytest.approx(31.0)
    finally:
        conn.close()
