"""Tests for ``stat_normalize`` rebound helpers."""

from __future__ import annotations

from src.config import STAT_COLUMNS
from src.scrapers.stat_normalize import (
    fill_rebound_splits,
    merge_missing_stat_fields,
    stats_need_supplemental_fill,
)


def test_fill_rebound_splits_from_total_only_pf() -> None:
    row = {"reb": 10.0}
    fill_rebound_splits(row, pos="PF")
    assert row["oreb"] == round(10.0 * 0.28, 4)
    assert row["dreb"] == round(10.0 * (1 - 0.28), 4)


def test_fill_rebound_splits_subtraction_when_one_side_known() -> None:
    row = {"reb": 8.0, "oreb": 2.5}
    fill_rebound_splits(row, pos="C")
    assert row["dreb"] == round(5.5, 4)
    assert row["oreb"] == 2.5


def test_fill_rebound_splits_noop_when_complete() -> None:
    row = {"reb": 9.0, "oreb": 3.0, "dreb": 6.0}
    fill_rebound_splits(row, pos="SF")
    assert row["oreb"] == 3.0
    assert row["dreb"] == 6.0


def test_stats_need_supplemental_fill_sparse_row() -> None:
    sparse = {"gp": 30, "pts": 12.0, "min": None}
    assert stats_need_supplemental_fill(sparse, STAT_COLUMNS)


def test_stats_need_supplemental_fill_complete_row() -> None:
    row = {c: 1.0 for c in STAT_COLUMNS}
    row["gp"] = 30
    assert not stats_need_supplemental_fill(row, STAT_COLUMNS)


def test_merge_missing_stat_fields_fills_holes() -> None:
    base: dict = {"gp": 30, "pts": 10.0, "min": None, "fgm": None}
    overlay = {"gp": 28, "min": 32.0, "fgm": 5.0}
    assert merge_missing_stat_fields(base, overlay, STAT_COLUMNS)
    assert base["min"] == 32.0
    assert base["fgm"] == 5.0
    assert base["pts"] == 10.0
    assert base["gp"] == 30
