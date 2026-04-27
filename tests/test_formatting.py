"""Unit tests for display formatting helpers."""

from __future__ import annotations

import pandas as pd

from src.exporters import data_loader
from src.formatting import height_in_to_ft_str, normalize_full_name


def test_normalize_full_name():
    assert normalize_full_name("  Stephen  Curry ") == "stephen curry"


def test_height_in_to_ft_str():
    assert height_in_to_ft_str(None) is None
    assert height_in_to_ft_str(78) == "6'6\""
    assert height_in_to_ft_str(78.7) == "6'7\""


def test_round_float_columns_for_display():
    df = pd.DataFrame(
        {"a": [1, 2], "b": [1.234567, 2.0], "c": ["x", "y"]}
    )
    out = data_loader.round_float_columns_for_display(df)
    assert list(out["a"]) == [1, 2]
    assert out["b"].tolist() == [1.23, 2.0]
    assert list(out["c"]) == ["x", "y"]
