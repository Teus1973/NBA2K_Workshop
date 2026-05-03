"""Regression: ``2026 class`` template row 2 (Matt Able) vs :mod:`src.formulas.excel_2026_class`."""

from __future__ import annotations

from src.formulas.excel_2026_class import compute_attribute_dict

_ROW2 = {
    "name": "Matt Able",
    "pos": "G",
    "height_in": 78,
    "weight_lbs": 205,
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
    "pf": 1.9,
}

# Row2 snapshot: template-style stats; interior/perimeter use the same linear
# coefficients as ``data/formulas/*_defense_2k.yaml`` (NBA reference fit), not
# legacy Excel heuristics — re-snap if those YAMLs are refit.
_EXPECTED: dict[str, int] = {
    "driving_layup_2k": 62,
    "post_control_2k": 25,
    "draw_foul_2k": 35,
    "close_shot_2k": 52,
    "mid_range_shot_2k": 59,
    "three_point_shot_2k": 71,
    "free_throws_2k": 83,
    "ball_handle_2k": 74,
    "pass_iq_2k": 45,
    "pass_accuracy_2k": 52,
    "offensive_rebound_2k": 38,
    "standing_dunk_2k": 50,
    "driving_dunk_2k": 86,
    "shot_iq_2k": 56,
    "pass_vision_2k": 45,
    "hands_2k": 80,
    "defensive_rebound_2k": 49,
    "interior_defense_2k": 56,
    "perimeter_defense_2k": 74,
    "block_2k": 47,
    "steal_2k": 69,
    "speed_2k": 89,
    "speed_with_ball_2k": 84,
    "vertical_2k": 78,
    "strength_2k": 56,
    "stamina_2k": 87,
    "hustle_2k": 76,
    "agility_2k": 80,
    "pass_perception_2k": 68,
    "defensive_consistency_2k": 69,
    "help_defense_iq_2k": 70,
    "offensive_consistency_2k": 53,
}


def test_compute_row2_matt_able() -> None:
    out = compute_attribute_dict(_ROW2, iterations=12)
    for k, exp in _EXPECTED.items():
        assert out.get(k) == exp, f"{k}: expected {exp}, got {out.get(k)}"


def test_min_per_game_zero_does_not_divide_by_zero() -> None:
    """Column J (min/g) can be 0 for sparse rows; per-36 math must not raise."""
    row = dict(_ROW2)
    row["min"] = 0.0
    out = compute_attribute_dict(row, iterations=12)
    assert isinstance(out["speed_2k"], int)
    assert 0 <= out["speed_2k"] <= 99


def test_r_minus_equals_penalty_still_decrements_three_point() -> None:
    """
    The sheet subtracts $BN$1 from 3P; a bug using ``t -= a - P`` would *add* P in Python.
    For row2, result must stay 71.
    """
    from src.formulas.excel_2026_class import PENALTY1

    assert PENALTY1 == 4.0
    out = compute_attribute_dict(_ROW2)
    assert out["three_point_shot_2k"] == 71

    # Trivial bracket check: 71+8 would be 79 with the sign bug (observed in dev)
    assert out["three_point_shot_2k"] != 79


def test_defense_not_extreme_for_sparse_prospect() -> None:
    """Guards with thin stats should not get int 25 / per 99 from defense formulas."""
    row = {
        "pos": "SG",
        "height_in": 76,
        "weight_lbs": 0,
        "gp": 5,
        "min": 18.0,
        "pts": 6, "fgm": 2, "fga": 6, "fg_pct": 35,
        "fg3m": 0.5, "fg3a": 2, "fg3_pct": 30,
        "ftm": 0.5, "fta": 1, "ft_pct": 70,
        "oreb": 0.2, "dreb": 1.0, "reb": 1.2, "ast": 1, "tov": 0.8,
        "stl": 0.4, "blk": 0, "pf": 1.2,
    }
    out = compute_attribute_dict(row, iterations=8)
    assert 30 <= out["interior_defense_2k"] <= 80
    assert 35 <= out["perimeter_defense_2k"] <= 90
    assert out["perimeter_defense_2k"] - out["interior_defense_2k"] < 35


def test_calculate_excel_2026_derived_ratings() -> None:
    """Hand-checked against workbook spec formulas."""
    from src.formulas.excel_2026_class import calculate_excel_2026_ratings

    d = calculate_excel_2026_ratings({
        "height_in": 78,
        "age": 20,
        "gp": 25,
        "team_total_games": 25,
        "close_shot_2k": 70,
        "post_control_2k": 50,
        "mid_range_shot_2k": 60,
        "shot_iq_2k": 55,
        "hustle_2k": 70,
        "offensive_consistency_2k": 60,
        "defensive_consistency_2k": 50,
        "overall_2k": 75,
        "espn_rank": 10,
    })
    assert d["post_hook_2k"] == 64
    assert d["post_fade_2k"] == 58
    assert d["intangibles_2k"] == 60
    assert d["durability_2k"] == 84
    assert d["potential"] == 95

    low_avail = calculate_excel_2026_ratings({
        "height_in": 78,
        "age": 19,
        "gp": 18,
        "team_total_games": 40,
        "close_shot_2k": 50,
        "post_control_2k": 50,
        "mid_range_shot_2k": 50,
        "shot_iq_2k": 50,
        "hustle_2k": 50,
        "offensive_consistency_2k": 50,
        "defensive_consistency_2k": 50,
        "overall_2k": 70,
    })
    # ratio 0.45 → penalty (0.9 - 0.45) * 40 = 18; age 19 → 85 - 18 = 67
    assert low_avail["durability_2k"] == 67

    tall = calculate_excel_2026_ratings({
        "height_in": 84,
        "age": 19,
        "close_shot_2k": 50,
        "post_control_2k": 50,
        "mid_range_shot_2k": 50,
        "shot_iq_2k": 50,
        "hustle_2k": 50,
        "offensive_consistency_2k": 50,
        "defensive_consistency_2k": 50,
        "overall_2k": 80,
    })
    # (50*0.7 + 50*0.3) + 5 = 50 + 5 = 55
    assert tall["post_hook_2k"] == 55


def test_derived_potential_not_clamped() -> None:
    from src.formulas.excel_2026_class import calculate_excel_2026_ratings

    d = calculate_excel_2026_ratings({
        "height_in": 78,
        "age": 18,
        "close_shot_2k": 99,
        "post_control_2k": 99,
        "mid_range_shot_2k": 99,
        "shot_iq_2k": 99,
        "hustle_2k": 99,
        "offensive_consistency_2k": 99,
        "defensive_consistency_2k": 99,
        "overall_2k": 99,
        "espn_rank": 1,
    })
    assert d["potential"] > 99
