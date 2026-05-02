# Automation logic synced with schema v4 and vgamepad Xbox 360 bridge

from __future__ import annotations

import time
from collections.abc import Callable

from src import config

OnProgress = Callable[[float, str], None]


def _default_nav_label(column_key: str) -> str:
    base = column_key[:-3] if column_key.endswith("_2k") else column_key
    return base.replace("_", " ").title()


def _build_index_to_nav_map() -> dict[int, str]:
    cols = config.PROSPECTS_TABLE_COLUMNS
    nav = {i: _default_nav_label(cols[i]) for i in range(len(cols))}
    anchors = {
        cols.index("overall_2k"): "Overall",
        cols.index("driving_layup_2k"): "Driving Layup",
        cols.index("post_hook_2k"): "Post Hook",
        cols.index("post_fade_2k"): "Post Fade",
        cols.index("intangibles_2k"): "Integnagbles",
        cols.index("durability_2k"): "Durablity",
    }
    nav.update(anchors)
    return nav


INDEX_TO_NAV_MAP: dict[int, str] = _build_index_to_nav_map()

_RATING_INDICES: frozenset[int] = frozenset(
    config.PROSPECTS_TABLE_COLUMNS.index(attr) for attr in config.RATING_ATTRIBUTES
)


def _effective_rating(raw: object) -> int | None:
    """Return controller rating in [25, 99] from sheet cell (schema +1 step)."""
    if raw is None:
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    val += 1
    return max(25, min(99, val))


def _rating_to_left_stick_x(rating: int) -> float:
    lo, hi = 25, 99
    return (rating - lo) / (hi - lo) * 2.0 - 1.0


def _apply_rating_input(gamepad: object, rating: int) -> None:
    """Drive Xbox 360 left analog X from normalized rating (Remote Play bridge)."""
    x = float(_rating_to_left_stick_x(rating))
    gamepad.left_joystick_float(x_value=x, y_value=0.0)
    gamepad.update()
    gamepad.left_joystick_float(x_value=0.0, y_value=0.0)
    gamepad.update()


def _navigate_to_field(gamepad: object, nav_command: str) -> None:
    """Placeholder hook for future menu traversal (INDEX_TO_NAV_MAP labels)."""
    _ = (gamepad, nav_command)


def _potential_sheet_value(raw: object) -> int | None:
    """Potential meta-column value mapped to [25, 99] without the rating +1 bump."""
    if raw is None:
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return max(25, min(99, val))


def _apply_potential_meta(gamepad: object, raw: object) -> None:
    """Meta-column 88 (index 87): numeric potential uses same stick bridge."""
    rating = _potential_sheet_value(raw)
    if rating is None:
        return
    _navigate_to_field(gamepad, "Potential")
    _apply_rating_input(gamepad, rating)


def neutralize_virtual_stick(gamepad: object) -> None:
    """Return the virtual left stick to neutral (0, 0); safe after errors or interrupts."""
    try:
        gamepad.left_joystick_float(x_value=0.0, y_value=0.0)
        gamepad.update()
    except Exception:
        pass


def push_prospect_row_to_controller(
    row_data: list,
    *,
    edit_player_mode: bool = False,
    gamepad: object | None = None,
    on_progress: OnProgress | None = None,
) -> None:
    """Push one prospect row (87 core columns + optional potential at index 87) via vgamepad."""
    if gamepad is None:
        import vgamepad as vg

        gp = vg.VX360Gamepad()
    else:
        gp = gamepad

    try:
        for idx in range(87):
            label = INDEX_TO_NAV_MAP[idx]
            raw = row_data[idx] if idx < len(row_data) else None
            frac = (idx + 1) / 87.0

            if idx < 34 and not edit_player_mode:
                if on_progress:
                    on_progress(frac, f"Skipping {label}…")
                continue

            if idx in _RATING_INDICES:
                rating = _effective_rating(raw)
                if rating is None:
                    if on_progress:
                        on_progress(frac, f"Skipping {label} (no value)…")
                    continue
                if on_progress:
                    on_progress(frac, f"Pushing {label}…")
                _navigate_to_field(gp, label)
                _apply_rating_input(gp, rating)
                time.sleep(0.05)
            else:
                if on_progress:
                    on_progress(frac, f"Skipping {label}…")

        if len(row_data) > 87:
            pot_raw = row_data[87]
            if _potential_sheet_value(pot_raw) is not None:
                if on_progress:
                    on_progress(1.0, "Pushing Potential…")
                _apply_potential_meta(gp, pot_raw)
                time.sleep(0.05)
            elif on_progress:
                on_progress(1.0, "Skipping Potential (no numeric value)…")
        elif on_progress:
            on_progress(1.0, "Done.")
    finally:
        neutralize_virtual_stick(gp)
