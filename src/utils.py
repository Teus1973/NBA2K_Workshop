"""
Project-wide singletons — combine ↔ prospect overrides, ingest hints.

Append ``KNOWN_NBA_IDS`` when the NBA Combine feed returns ``PLAYER_NAME``
strings that diverge too far from workshop ``slug`` / ``full_name``, so ingest
still keys ``nba:{player_id}`` rows into ``prospect:{slug}`` mirrors.
"""

from __future__ import annotations

from typing import Final

KNOWN_NBA_IDS: Final[dict[str, int]] = {
    "aj-dybantsa": 1643407,
    "brayden-burries": 1643415,
    "nate-ament": 1643417,
}
