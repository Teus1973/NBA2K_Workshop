"""One-off utility: fetch three live 2kratings pages and save as fixtures.

Run from the project root:

    python tests\_build_twokratings_fixtures.py

Captures raw HTML for:
  - guard:  stephen-curry
  - wing:   lebron-james
  - big:    nikola-jokic

These slugs are stable across seasons. We re-use the cached copy on subsequent
runs (respects ``config.CACHE_TTL_SECONDS``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src import config
from src.scrapers import twokratings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "twokratings"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

SLUGS = ["stephen-curry", "lebron-james", "nikola-jokic"]


def main() -> None:
    for slug in SLUGS:
        player = twokratings.scrape_player(slug, log_audit=False)
        cache_html = config.CACHE_2KRATINGS / f"{slug}.html"
        target = FIXTURE_DIR / f"{slug}.html"
        if cache_html.is_file():
            shutil.copy2(cache_html, target)
        print(f"{slug:20s} overall={player.overall_2k} "
              f"n_attrs={len(player.attributes)} "
              f"height_in={player.height_in} weight={player.weight_lbs} "
              f"wingspan_in={player.wingspan_in}")


if __name__ == "__main__":
    main()
