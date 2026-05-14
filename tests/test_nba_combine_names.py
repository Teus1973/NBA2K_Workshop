"""Combine ↔ prospect name linkage (generation suffix stripping)."""

from __future__ import annotations

from collections import defaultdict

from src.scrapers.nba_combine import (
    _combine_name_resolve_slug,
    _prospect_name_bucket_keys,
    _strip_generation_suffix,
)


def test_strip_generation_suffix() -> None:
    assert _strip_generation_suffix("darius acuff jr.") == "darius acuff"
    assert _strip_generation_suffix("john smith iii") == "john smith"
    assert _strip_generation_suffix("marcus smith, jr") == "marcus smith"


def test_prospect_bucket_keys_duplicate_safe() -> None:
    xs = _prospect_name_bucket_keys("only name")
    assert xs == ["only name"]
    jr = _prospect_name_bucket_keys("x y jr.")
    assert "x y jr." in jr and "x y" in jr


def test_prospect_bucket_keys_include_first_name_aliases() -> None:
    xs = _prospect_name_bucket_keys("nate ament")
    assert "nate ament" in xs
    assert "nathaniel ament" in xs


def test_resolve_combine_name_junior_vs_board() -> None:
    slug_by_key: defaultdict[str, list[str]] = defaultdict(list)
    slug_by_key["darius acuff"].append("darius-acuff")
    assert (
        _combine_name_resolve_slug(slug_by_key, "darius acuff jr.") == "darius-acuff"
    )


def test_resolve_combine_name_first_name_alias() -> None:
    slug_by_key: defaultdict[str, list[str]] = defaultdict(list)
    for key in _prospect_name_bucket_keys("nate ament"):
        slug_by_key[key].append("nate-ament")
    assert (
        _combine_name_resolve_slug(slug_by_key, "nathaniel ament") == "nate-ament"
    )


def test_resolve_ambiguous_base_returns_none() -> None:
    slug_by_key: defaultdict[str, list[str]] = defaultdict(list)
    slug_by_key["john smith"].extend(["john-smith-a", "john-smith-b"])
    assert _combine_name_resolve_slug(slug_by_key, "john smith") is None
