"""Unified :func:`apply_formulas` orchestrator (both engines)."""

from __future__ import annotations

from src import config
from src.formulas import registry as regmod
from src.formulas.apply import _normalize_engine, apply_formulas

_PROSPECT = {
    "slug": "test-player",
    "league": config.LEAGUE_NCAA,
    "espn_rank": 10,
    "pos": "G",
    "height_in": 78.0,
    "age": 19.0,
    "gp": 21,
    "min": 20.4,
    "pts": 8.1,
    "fgm": 2.8,
    "fga": 6.9,
    "fg_pct": 40.7,
    "fg3m": 1.3,
    "fg3a": 3.8,
    "fg3_pct": 34.2,
    "ftm": 1.2,
    "fta": 1.4,
    "ft_pct": 83.3,
    "oreb": 0.7,
    "dreb": 2.9,
    "reb": 3.5,
    "ast": 1.1,
    "tov": 1.0,
    "stl": 1.2,
    "blk": 0.3,
    "team_total_games": 30,
    "pf": 1.9,
}


def test_normalize_engine_aliases() -> None:
    assert _normalize_engine("Excel 2026 Class") == "excel_2026_class"
    assert _normalize_engine("excel-2026-class") == "excel_2026_class"
    assert _normalize_engine("Calibrated") == "calibrated"


def test_apply_formulas_calibrated_has_all_keys_and_derived_provenance() -> None:
    reg = regmod.load_registry()
    ratings, prov = apply_formulas(_PROSPECT, "calibrated", registry=reg)
    for a in config.RATING_ATTRIBUTES:
        assert a in ratings
    assert "potential" in ratings
    assert prov.by_attribute.get("post_hook_2k") == "excel_2026+derived"
    assert prov.by_attribute.get("potential") in ("excel_2026+derived", "formula")
