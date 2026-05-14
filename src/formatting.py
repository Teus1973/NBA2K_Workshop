"""Display helpers shared by loaders and UI."""

from __future__ import annotations

import math
import re


_TOKEN_DOT_INITIALS_RE = re.compile(r"^(?:[a-z]\.)+[a-z]?$")


def _squash_dot_initials_token(tok: str) -> str:
    """``a.j.k.`` → ``ajk`` while leaving tokens like ``jr.`` untouched."""
    t = tok.strip().lower()
    if "." in t and _TOKEN_DOT_INITIALS_RE.fullmatch(t):
        return "".join(part for part in t.split(".") if part)
    return t


def normalize_full_name(name: str | None) -> str:
    """Lowercase single-spaced name for roster matching.

    Strips stray punctuation typical of initials (``A.J.`` → ``aj`` before the
    last name) while keeping hyphenated names and apostrophes.
    """
    s = re.sub(r"\s+", " ", (name or "").strip().lower())
    if not s:
        return ""
    parts = [_squash_dot_initials_token(t) for t in s.split()]
    glue = " ".join(parts)
    glue = re.sub(r"[^\w\s'\-]", " ", glue)
    return re.sub(r"\s+", " ", glue.strip())


def height_in_to_ft_str(
    height_in: float | None,
    *,
    fractional_inches: bool = False,
) -> str | None:
    """Convert total inches to a feet-inches label.

    Defaults to **whole-inch** rounding (classic 2K-style ``6'6\"``). With
    ``fractional_inches=True``, keeps sub-inch remainder (NBA combine-style
    ``6' 3.75\"``) without drifting the Automation template column indices.
    """
    if height_in is None:
        return None
    try:
        total = float(height_in)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(total):
        return None

    if not fractional_inches:
        inches_whole = int(round(total))
        ft, inch = divmod(inches_whole, 12)
        return f"{ft}'{inch}\""
    ft_i = math.floor(total / 12.0)
    inch_frac = total - (12.0 * ft_i)
    if inch_frac <= 1e-6:
        inch_s = "0"
    else:
        inch_s = f"{inch_frac:.3f}".rstrip("0").rstrip(".")
    return f"{ft_i}' {inch_s}\""
