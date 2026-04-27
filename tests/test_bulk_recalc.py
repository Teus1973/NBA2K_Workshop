"""Throttling helper for Streamlit progress during bulk recompute."""

from __future__ import annotations

from src.bulk_recalc import _progress_indices


def test_progress_indices_singleton() -> None:
    assert _progress_indices(0) == set()
    assert _progress_indices(1) == {1}


def test_progress_indices_caps_count() -> None:
    s = _progress_indices(500)
    assert 1 in s and 500 in s
    assert len(s) <= 25
