"""Unit tests for ESPN men's college basketball stat normalization helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.scrapers import espn_mens_cbb as em


def test_espn_core_season_year() -> None:
    assert em.espn_core_season_year("2025-26") == 2026


def test_school_matches() -> None:
    assert em.school_matches("Alabama", "Alabama Crimson Tide")
    assert em.school_matches("North Carolina", "North Carolina Tar Heels")
    assert em.school_matches("BYU", "Brigham Young Cougars")
    assert em.school_matches("UConn", "Connecticut Huskies")
    assert not em.school_matches("Duke", "North Carolina Tar Heels")


def test_search_name_variants_strips_suffix() -> None:
    assert em._search_name_variants("Labaron Philon Jr.") == [
        "Labaron Philon Jr.",
        "Labaron Philon",
    ]


def test_flatten_statistics_minimal() -> None:
    payload = {
        "splits": {
            "categories": [
                {
                    "stats": [
                        {"name": "gamesPlayed", "value": 10},
                        {"name": "avgPoints", "value": 20.5},
                    ]
                }
            ]
        }
    }
    flat = em.flatten_statistics(payload)
    assert flat["gamesPlayed"] == 10.0
    assert flat["avgPoints"] == 20.5


def test_statistics_to_prospect_stats_requires_gp() -> None:
    assert em.statistics_to_prospect_stats({}, season_display="2025-26") == {}
    row = em.statistics_to_prospect_stats(
        {"gamesPlayed": 5, "avgMinutes": 28.0},
        season_display="2025-26",
    )
    assert row["gp"] == 5
    assert row["min"] == 28.0
    assert row["_stats_source"] == "espn-mcb"


def test_resolve_single_ncaa_hit_when_team_ref_missing() -> None:
    ncaa_hit = [
        {"id": "4873090", "league": em.LEAGUE_SLUG, "displayName": "Labaron Philon Jr."},
    ]
    athlete_doc = {"team": {}}

    with patch.object(em, "search_players", return_value=ncaa_hit):
        with patch.object(em, "fetch_json_ref", return_value=athlete_doc):
            aid = em.resolve_mens_cbb_athlete_id(
                "Labaron Philon Jr.",
                "Alabama",
                2026,
                force_refresh=True,
            )
    assert aid == "4873090"
