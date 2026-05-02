"""Prospects table column layout matches ``prospects 2026 (1).xlsx`` (87 columns)."""

from __future__ import annotations

from pathlib import Path

from src import config


def test_prospects_table_is_87_columns() -> None:
    assert len(config.PROSPECTS_TABLE_COLUMNS) == 87


def test_overall_is_column_35_and_driving_layup_36() -> None:
    """1-based Excel columns 35–36 == indices 34–35."""
    assert config.PROSPECTS_TABLE_COLUMNS.index("overall_2k") == 34
    assert config.PROSPECTS_TABLE_COLUMNS.index("driving_layup_2k") == 35


def test_bio_is_14_then_stats() -> None:
    head = config.PROSPECTS_TABLE_COLUMNS[:14]
    assert head == (
        "espn_rank",
        "slug",
        "last_name",
        "first_name",
        "pos",
        "school_or_team",
        "league",
        "age",
        "date_of_birth",
        "height_in",
        "height_ft",
        "weight_lbs",
        "wingspan_in",
        "status",
    )
    assert config.PROSPECTS_TABLE_COLUMNS[14:34] == tuple(config.STAT_COLUMNS)


def test_post_hook_fade_positions() -> None:
    t = config.PROSPECTS_TABLE_COLUMNS
    assert t.index("post_hook_2k") + 1 == 43
    assert t.index("post_fade_2k") + 1 == 44


def test_intangibles_shot_hustle_durability_positions() -> None:
    t = config.PROSPECTS_TABLE_COLUMNS
    assert t.index("intangibles_2k") + 1 == 68
    assert t.index("shot_iq_2k") + 1 == 69
    assert t.index("hustle_2k") + 1 == 70
    assert t.index("durability_2k") + 1 == 71


def test_meta_full_name_through_column1() -> None:
    tail = config.PROSPECTS_TABLE_COLUMNS[71:87]
    assert tail == (
        "full_name",
        "other_rank",
        "added_by",
        "notes",
        "updated_at",
        "scouting_ai_summary",
        "scouting_physical_text",
        "scouting_physical_json",
        "season",
        "league_stats",
        "source",
        "updated_at_stats",
        "formula_version",
        "manual_override_json",
        "computed_at",
        "column1",
    )


def test_rating_tuple_matches_workbook_block() -> None:
    """Indices 34–70 (0-based) of PROSPECTS_TABLE_COLUMNS == RATING_ATTRIBUTES."""
    slab = config.PROSPECTS_TABLE_COLUMNS[34:71]
    assert slab == tuple(config.RATING_ATTRIBUTES)


def test_optional_workbook_row1_has_87_columns() -> None:
    import pytest

    candidates = [
        Path(__file__).resolve().parents[1] / "data" / "prospects 2026 (1).xlsx",
        Path(r"c:\Users\ofirs\OneDrive\NBA2K25\prospects 2026 (1).xlsx"),
    ]
    p = next((c for c in candidates if c.is_file()), None)
    if p is None:
        pytest.skip("No prospects workbook on disk for header check")
    import openpyxl

    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
    try:
        name = "prospects" if "prospects" in wb.sheetnames else wb.sheetnames[0]
        ws = wb[name]
        row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        wb.close()
    headers = [c for c in row if c is not None and str(c).strip() != ""]
    assert len(headers) == 87, f"{name!r} row1: expected 87 headers, got {len(headers)}"
