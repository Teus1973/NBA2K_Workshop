"""Display helpers shared by loaders and UI."""

from __future__ import annotations

import math
import re


def normalize_full_name(name: str | None) -> str:
    """Lowercase single-spaced name for roster matching."""
    return re.sub(r"\s+", " ", (name or "").lower().strip())


def height_in_to_ft_str(height_in: float | None) -> str | None:
    """Convert total inches (e.g. 78.5) to a feet-inches label (e.g. 6'6\")."""
    if height_in is None:
        return None
    try:
        total = float(height_in)
    except (TypeError, ValueError):
        return None
    if math.isnan(total):
        return None
    inches = int(round(total))
    ft, inch = divmod(inches, 12)
    return f"{ft}'{inch}\""
