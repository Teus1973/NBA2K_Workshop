"""Normalize scraped stat rows before SQLite upsert (shared across scrapers)."""

from __future__ import annotations

import re
from typing import Any, Mapping, MutableMapping


def _offensive_rebound_share(pos: str | None) -> float:
    """Rough NCAA positional offensive-rebound share of total rebounds (TRB)."""
    if not pos:
        return 0.22
    tok = re.split(r"[/,\s|]+", pos.strip().upper())[0].strip()
    if tok.startswith("PG"):
        return 0.10
    if tok.startswith("SG"):
        return 0.13
    if tok.startswith("SF"):
        return 0.18
    if tok.startswith("PF"):
        return 0.28
    if tok.startswith("C"):
        return 0.32
    return 0.22


def fill_rebound_splits(
    stats: MutableMapping[str, Any],
    pos: str | None = None,
) -> None:
    """Ensure ``oreb`` / ``dreb`` exist when ``reb`` (TRB per game) is known.

    Many sources publish total rebounds without an offensive/defensive split.
    Formulas such as ``offensive_rebound_2k`` / ``defensive_rebound_2k`` need
    both; we derive missing pieces by subtraction or a coarse positional split.
    Mutates ``stats`` in place.
    """
    reb = stats.get("reb")
    if reb is None:
        return
    try:
        reb_f = float(reb)
    except (TypeError, ValueError):
        return
    if reb_f <= 0:
        return

    def _to_f(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    o = _to_f(stats.get("oreb"))
    d = _to_f(stats.get("dreb"))
    if o is not None and d is not None:
        return
    if o is not None and d is None:
        stats["dreb"] = round(max(0.0, reb_f - o), 4)
        return
    if d is not None and o is None:
        stats["oreb"] = round(max(0.0, reb_f - d), 4)
        return

    share = _offensive_rebound_share(pos)
    stats["oreb"] = round(reb_f * share, 4)
    stats["dreb"] = round(reb_f * (1.0 - share), 4)


def apply_stat_normalizers(
    stats: MutableMapping[str, Any],
    *,
    pos: str | None = None,
) -> None:
    """Entry point for pre-upsert cleanup."""
    fill_rebound_splits(stats, pos)


def count_missing_stat_fields(
    stats: Mapping[str, Any],
    columns: tuple[str, ...],
) -> int:
    """Count ``STAT_COLUMNS`` keys that are missing or ``None``."""
    n = 0
    for c in columns:
        if stats.get(c) is None:
            n += 1
    return n


def merge_missing_stat_fields(
    base: MutableMapping[str, Any],
    overlay: Mapping[str, Any],
    columns: tuple[str, ...],
) -> bool:
    """Fill ``None`` slots in ``base`` from ``overlay``. Returns whether any field changed."""
    changed = False
    for c in columns:
        if base.get(c) is None and overlay.get(c) is not None:
            base[c] = overlay[c]
            changed = True
    return changed


def stats_need_supplemental_fill(stats: Mapping[str, Any], columns: tuple[str, ...]) -> bool:
    """Whether we should try a second source (e.g. ESPN) to patch holes."""
    if not stats.get("gp"):
        return False
    if stats.get("pts") is None or stats.get("min") is None:
        return True
    if stats.get("fgm") is None or stats.get("fga") is None:
        return True
    # Sparse SR/mobile layouts often drop whole blocks of columns.
    return count_missing_stat_fields(stats, columns) >= 6
