"""Unit tests for display formatting helpers."""

from __future__ import annotations

from src.formatting import height_in_to_ft_str, normalize_full_name


def test_normalize_full_name():
    assert normalize_full_name("  Stephen  Curry ") == "stephen curry"


def test_height_in_to_ft_str():
    assert height_in_to_ft_str(None) is None
    assert height_in_to_ft_str(78) == "6'6\""
    assert height_in_to_ft_str(78.7) == "6'7\""
