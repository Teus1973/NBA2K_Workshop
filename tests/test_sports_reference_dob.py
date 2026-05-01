"""Sports-reference CBB: date of birth line parsing."""

from __future__ import annotations

import pytest

from src.scrapers.sports_reference_cbb import (
    _parse_dob_from_info_text,
    pick_sr_player_stats,
    prospect_school_slug,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        (" Born: Dec 2, 2000 ", "2000-12-02"),
        ("x Born: July 1, 2001 in Somewhere", "2001-07-01"),
        ("No birth here", None),
    ],
)
def test_parse_dob_from_info_text(text: str, expected: str | None) -> None:
    assert _parse_dob_from_info_text(text) == expected


def test_prospect_school_slug_basic() -> None:
    assert prospect_school_slug("Duke") == "duke"
    assert prospect_school_slug("North Carolina") == "north-carolina"
    assert prospect_school_slug(None) is None
    assert prospect_school_slug("   ") is None


def test_pick_sr_player_stats_prefers_school() -> None:
    wrong = {"gp": 80, "sr_school_slug": "auburn", "pts": 3.0}
    right = {"gp": 38, "sr_school_slug": "duke", "pts": 22.5}
    assert pick_sr_player_stats([wrong, right], "Duke") == right


def test_pick_sr_player_stats_single_candidate() -> None:
    only = {"gp": 10, "sr_school_slug": "troy", "pts": 1.0}
    assert pick_sr_player_stats([only], "Duke") == only


def test_pick_sr_player_stats_ambiguous_no_school_falls_back_first() -> None:
    a = {"gp": 1, "sr_school_slug": "auburn"}
    b = {"gp": 2, "sr_school_slug": "duke"}
    assert pick_sr_player_stats([a, b], None) == a


def test_pick_sr_player_stats_no_match_empty() -> None:
    a = {"gp": 1, "sr_school_slug": "auburn"}
    b = {"gp": 2, "sr_school_slug": "troy"}
    assert pick_sr_player_stats([a, b], "Duke") == {}
