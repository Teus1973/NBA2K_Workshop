"""Tests for Remote Play controller bridging (no vgamepad import required)."""

from __future__ import annotations

from src import config
from src.automation.controller_mapping import (
    INDEX_TO_NAV_MAP,
    _effective_rating,
    _potential_sheet_value,
    push_prospect_row_to_controller,
)


class _FakeGamepad:
    def __init__(self) -> None:
        self.left_calls: list[tuple[float, float]] = []
        self.updates = 0

    def left_joystick_float(self, x_value: float, y_value: float) -> None:
        self.left_calls.append((float(x_value), float(y_value)))

    def update(self) -> None:
        self.updates += 1


def test_index_to_nav_map_covers_87_columns() -> None:
    assert len(INDEX_TO_NAV_MAP) == len(config.PROSPECTS_TABLE_COLUMNS)
    assert set(INDEX_TO_NAV_MAP.keys()) == set(range(87))


def test_anchor_literals_match_schema_columns() -> None:
    assert INDEX_TO_NAV_MAP[34] == "Overall"
    assert INDEX_TO_NAV_MAP[35] == "Driving Layup"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("post_hook_2k")] == "Post Hook"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("post_fade_2k")] == "Post Fade"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("intangibles_2k")] == "Integnagbles"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("durability_2k")] == "Durablity"


def test_effective_rating_applies_plus_one_and_clamps() -> None:
    assert _effective_rating(24) == 25
    assert _effective_rating(50) == 51
    assert _effective_rating(98) == 99
    assert _effective_rating(99) == 99


def test_potential_meta_skips_plus_one_bump() -> None:
    assert _potential_sheet_value(77) == 77


def test_skips_bio_stats_without_edit_player_mode() -> None:
    gp = _FakeGamepad()
    row = [None] * 87
    row[34] = 79  # overall → effective 80
    push_prospect_row_to_controller(row, gamepad=gp)
    # rating deflect + rating reset + finally neutralize_virtual_stick
    assert len(gp.left_calls) == 3
    assert gp.left_calls[-1] == (0.0, 0.0)


def test_edit_player_mode_processes_early_columns_when_ratings_present() -> None:
    gp = _FakeGamepad()
    row = [None] * 87
    row[5] = 50  # pos column — not a rating; should not emit stick when never rating idx
    push_prospect_row_to_controller(row, edit_player_mode=True, gamepad=gp)
    assert gp.left_calls == [(0.0, 0.0)]  # finally neutral only


def test_potential_index_87_emits_input() -> None:
    gp = _FakeGamepad()
    row = [None] * 88
    row[87] = 82
    push_prospect_row_to_controller(row, gamepad=gp)
    assert gp.left_calls
