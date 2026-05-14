"""Combine max-vertical → 2K scaling (elite draft-class curves)."""

from __future__ import annotations

from src.formulas.excel_2026_class import vertical_2k_from_max_vert_inches


def test_vertical_floor_40_5_inches() -> None:
    assert vertical_2k_from_max_vert_inches(40.5) >= 92


def test_vertical_about_40_inches_in_90_band() -> None:
    assert 90 <= vertical_2k_from_max_vert_inches(40.0) <= 99


def test_vertical_caps_and_monotone_sample() -> None:
    low = vertical_2k_from_max_vert_inches(26.0)
    high = vertical_2k_from_max_vert_inches(46.0)
    assert 25 <= low <= 99
    assert high == 99
    assert vertical_2k_from_max_vert_inches(39.0) < vertical_2k_from_max_vert_inches(
        41.0
    )

