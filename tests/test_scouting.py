"""Scouting blurb extraction (ESPN cache + helpers)."""

from __future__ import annotations

import pytest

from src.scrapers import scouting


SAMPLE_BOARD = """
<html><body><div id="article-body">
<p>1. Cooper Flagg, F, Duke</p>
<p>2. Jane Smith, G, UConn</p>
</div></body></html>
"""

LEDE_AND_LINES = """
<html><body><div id="article-body">
<p>For the first time this cycle, there's a change at No. 1: AJ Dybantsa takes the mantle
as the best available prospect. We're taking stock of the 2026 NBA draft big board in
this weekly update with our full best available list across every position this week.</p>
<p>1. AJ Dybantsa, F, BYU</p>
<p>2. Cooper Flagg, F, Duke</p>
</div></body></html>
"""


def test_extract_espn_prefers_list_line_not_article_lede(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "espn"
    cache.mkdir()
    (cache / "board.html").write_text(LEDE_AND_LINES, encoding="utf-8")
    monkeypatch.setattr(scouting.config, "CACHE_ESPN", cache)
    a = scouting.extract_espn_blurb("aj-dybantsa", full_name="AJ Dybantsa")
    assert a
    assert "1." in a
    assert "Dybantsa" in a
    assert "BYU" in a
    assert "For the first time" not in a


def test_extract_espn_finds_list_line_in_cache(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "espn"
    cache.mkdir()
    (cache / "board.html").write_text(SAMPLE_BOARD, encoding="utf-8")
    monkeypatch.setattr(scouting.config, "CACHE_ESPN", cache)
    a = scouting.extract_espn_blurb("jane-smith", full_name="Jane Smith")
    assert a
    assert "Jane Smith" in a
    assert "UConn" in a or "2." in a


def test_names_for_blurb() -> None:
    assert "Cooper Flagg" in scouting._names_for_blurb(
        "cooper-flagg", "Cooper Flagg",
    )


def test_synthesize_ollama_returns_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(scouting.config, "USE_OLLAMA", False)
    assert scouting.synthesize_scouting_with_ollama("X", "some context here") is None


def test_split_scouting_synthesis_text() -> None:
    raw = """
Play style: Slashing wing.
Pros: Finishing, length.
Cons: Jumper.
Physical traits:
Strong frame for age; plus leaping.
SCOUTING_PHYSICAL_JSON
{"strength_01":0.6,"leaping_01":0.75,"athleticism_01":0.55,"stamina_01":0.5}
"""
    head, phys, feat = scouting.split_scouting_synthesis_text(raw)
    assert "Slashing" in head
    assert "leaping" in phys.lower() or "Strong" in phys
    assert feat.get("strength_01") == pytest.approx(0.6)
    assert feat.get("leaping_01") == pytest.approx(0.75)


def test_ollama_server_reachable_false_when_globally_disabled(monkeypatch) -> None:
    monkeypatch.setattr(scouting.config, "USE_OLLAMA", False)
    assert scouting.ollama_server_reachable() is False


def test_format_listing_for_scouting_non_empty_with_rank_and_size() -> None:
    block = scouting.format_listing_for_scouting({
        "full_name": "Test Player",
        "espn_rank": 5,
        "pos": "G",
        "school_or_team": "Duke",
        "height_in": 78.0,
        "weight_lbs": 190.0,
    })
    assert "Duke" in block
    assert "5" in block
    assert "6'" in block or "78" in block


def test_wikipedia_intro_returns_none_when_api_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(scouting, "_wikipedia_api_get", lambda *a, **k: None)
    assert scouting.wikipedia_intro("Meleek Thomas") is None


def test_collect_prospect_blurbs_skips_wikipedia_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        scouting,
        "extract_espn_blurb",
        lambda *a, **k: "ESPN only",
    )
    monkeypatch.setattr(scouting, "wikipedia_intro", lambda _: "WIKI")
    blurbs, label = scouting.collect_prospect_blurbs(
        "x", "Y",
        use_wikipedia=False,
        use_duckduckgo=False,
    )
    assert blurbs == ["ESPN only"]
    assert "Wikipedia" not in label
