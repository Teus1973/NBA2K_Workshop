"""Known-input known-output tests for the formula registry + apply."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src import config
from src.formulas import apply as fapply
from src.formulas import registry as _registry


@pytest.fixture(autouse=True)
def _force_calibrated_rating_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests use a tmp YAML registry; :func:`config.get_rating_engine` must
    not follow the user’s UI choice (e.g. Excel 2026) from on-disk settings."""
    monkeypatch.setattr(config, "get_rating_engine", lambda: "calibrated")


def _write_yaml(tmp_path: Path, name: str, blob: dict) -> None:
    (tmp_path / f"{name}.yaml").write_text(
        yaml.safe_dump(blob, sort_keys=False), encoding="utf-8")


def test_linear_regression_known_input(tmp_path):
    _write_yaml(tmp_path, "strength_2k", {
        "attribute": "strength_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [
            {"name": "weight_lbs", "coef": 0.25},
            {"name": "height_in", "coef": 0.10},
        ],
        "intercept": 10.0,
        "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    # 200 * 0.25 + 78 * 0.10 + 10 = 50 + 7.8 + 10 = 67.8 -> 68
    out = reg.evaluate("strength_2k", {"weight_lbs": 200.0, "height_in": 78.0})
    assert out == 68


def test_clamp_applies(tmp_path):
    _write_yaml(tmp_path, "strength_2k", {
        "attribute": "strength_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [{"name": "weight_lbs", "coef": 10.0}],
        "intercept": 0.0,
        "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    # 10 * 500 = 5000 -> clamped to 99
    assert reg.evaluate("strength_2k", {"weight_lbs": 500.0}) == 99
    # 10 * 1 = 10 -> clamped to 25
    assert reg.evaluate("strength_2k", {"weight_lbs": 1.0}) == 25


def test_height_delta_piecewise(tmp_path):
    _write_yaml(tmp_path, "height_delta", {
        "attribute": "height_delta",
        "version": 1,
        "type": "piecewise",
        "deltas": {
            "PG": {"mean": 1.25, "median": 1.0, "n": 40},
            "C": {"mean": 0.5, "median": 0.5, "n": 25},
        },
        "default_delta": 1.0,
    })
    reg = _registry.load_registry(tmp_path)
    assert reg.height_delta("PG") == 1.25
    assert reg.height_delta("C") == 0.5
    assert reg.height_delta("SF") == 1.0  # default


def test_combine_override_wins_over_formula(tmp_path):
    _write_yaml(tmp_path, "speed_2k", {
        "attribute": "speed_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [{"name": "weight_lbs", "coef": 0.0}],
        "intercept": 50.0,
        "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    prospect = {
        "pos": "PG",
        "league": config.LEAGUE_NCAA,
        "c_speed_2k": 85,
    }
    ratings, prov = fapply.apply_to_prospect(prospect, reg)
    assert ratings["speed_2k"] == 85
    assert prov.to_dict()["speed_2k"] == "combine"


def test_league_3pt_penalty_applied(tmp_path):
    _write_yaml(tmp_path, "three_point_shot_2k", {
        "attribute": "three_point_shot_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [{"name": "fg3_pct", "coef": 0.0}],
        "intercept": 80.0,
        "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    # NCAA gets -2.5 penalty -> 80 - 2.5 = 77.5 -> 78
    ratings, prov = fapply.apply_to_prospect(
        {"pos": "SG", "league": config.LEAGUE_NCAA, "fg3_pct": 0.4}, reg)
    assert ratings["three_point_shot_2k"] == 78
    assert "league" in prov.to_dict()["three_point_shot_2k"]


def test_manual_override_trumps_combine_and_formula(tmp_path):
    _write_yaml(tmp_path, "speed_2k", {
        "attribute": "speed_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [], "intercept": 60.0, "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    prospect = {"pos": "PG", "league": config.LEAGUE_NCAA, "c_speed_2k": 85}
    ratings, prov = fapply.apply_to_prospect(
        prospect, reg, manual_overrides={"speed_2k": 99})
    assert ratings["speed_2k"] == 99
    assert prov.to_dict()["speed_2k"] == "manual"


def test_height_reconciliation_from_wo_shoes(tmp_path):
    _write_yaml(tmp_path, "height_delta", {
        "attribute": "height_delta",
        "version": 1,
        "type": "piecewise",
        "deltas": {
            "PG": {"mean": 1.0, "median": 1.0, "n": 30},
        },
        "default_delta": 1.0,
    })
    _write_yaml(tmp_path, "strength_2k", {
        "attribute": "strength_2k",
        "version": 1,
        "type": "linear_regression",
        "features": [{"name": "height_in", "coef": 1.0}],
        "intercept": 0.0,
        "clamp": [25, 99],
    })
    reg = _registry.load_registry(tmp_path)
    # No height_in but 72in wo-shoes + PG delta(1.0) -> 73
    prospect = {"pos": "PG", "league": config.LEAGUE_NCAA,
                "height_wo_shoes_in": 72.0}
    ratings, _prov = fapply.apply_to_prospect(prospect, reg)
    assert ratings["strength_2k"] == 73
