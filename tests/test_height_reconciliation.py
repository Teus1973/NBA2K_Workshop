"""Sanity tests for the height-delta piecewise lookup."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.formulas import registry as _registry


def test_unknown_position_falls_back_to_default(tmp_path: Path):
    (tmp_path / "height_delta.yaml").write_text(yaml.safe_dump({
        "attribute": "height_delta",
        "type": "piecewise",
        "version": 1,
        "deltas": {"PG": {"mean": 1.3, "median": 1.0, "n": 10}},
        "default_delta": 0.8,
    }), encoding="utf-8")
    reg = _registry.load_registry(tmp_path)
    assert reg.height_delta("PG") == 1.3
    # UNK bucket falls back to default_delta.
    assert reg.height_delta("UNK") == 0.8


def test_missing_yaml_returns_one_inch_default(tmp_path: Path):
    reg = _registry.load_registry(tmp_path)
    assert reg.height_delta("PG") == 1.0
