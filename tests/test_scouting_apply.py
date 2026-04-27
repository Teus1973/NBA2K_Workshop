"""Scouting 0–1 features blended into physical ratings when combine is missing."""

from __future__ import annotations

import json

import pytest

from src.formulas import apply as fapply


def test_scouting_proxy_bumps_strength_without_bench() -> None:
    p = {
        "bench_reps": None,
        "scouting_physical_json": json.dumps({
            "strength_01": 1.0,
            "leaping_01": 0.5,
            "athleticism_01": 0.5,
            "stamina_01": 0.5,
        }),
    }
    c = {"strength_2k": 50}
    prov = fapply.Provenance()
    fapply._apply_scouting_physical_proxy(c, prov, p)
    assert c["strength_2k"] > 50
    assert prov.by_attribute.get("strength_2k") == "scouting_proxy"


def test_scouting_proxy_skips_strength_when_bench_exists() -> None:
    p = {
        "bench_reps": 12,
        "scouting_physical_json": json.dumps({"strength_01": 1.0}),
    }
    c = {"strength_2k": 50}
    prov = fapply.Provenance()
    fapply._apply_scouting_physical_proxy(c, prov, p)
    assert c["strength_2k"] == 50


def test_prospect_scouting_01_parses_json_string() -> None:
    p = {
        "scouting_physical_json": (
            '{"strength_01":0.2,"leaping_01":0.8,'
            '"athleticism_01":0.3,"stamina_01":0.9}'
        ),
    }
    m = fapply._prospect_scouting_01(p)
    assert m["scouting_leaping_01"] == pytest.approx(0.8)


def test_scouting_proxy_source_tags_expected_keys() -> None:
    p = {
        "bench_reps": None,
        "c_vertical_2k": None,
        "max_vert_in": None,
        "c_speed_2k": None,
        "three_quarter_sprint_sec": None,
        "c_agility_2k": None,
        "lane_agility_sec": None,
        "shuttle_sec": None,
        "c_speed_with_ball_2k": None,
        "scouting_physical_json": json.dumps({
            "strength_01": 0.5,
            "leaping_01": 0.5,
            "athleticism_01": 0.5,
            "stamina_01": 0.5,
        }),
    }
    t = fapply.scouting_proxy_source_tags(p)
    assert "strength_2k" in t
    assert "vertical_2k" in t
    assert "stamina_2k" in t
