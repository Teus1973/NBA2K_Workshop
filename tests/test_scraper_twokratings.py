"""
Offline tests for the 2kratings.com parser.

We commit three fixture HTML files (guard / wing / big) under
``tests/fixtures/twokratings/`` and assert the parser extracts a complete
attribute row set, the expected Overall rating, and physical measurements.

Fixtures were captured with ``tests/_build_twokratings_fixtures.py`` against
live 2kratings.com pages on 2026-04-24. They can be re-captured at any time
by re-running that script; the assertions in this file compare against known
published values for three stable veteran players.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config
from src.scrapers import twokratings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "twokratings"


def _load(slug: str) -> twokratings.TwoKRatingsPlayer:
    html_path = FIXTURE_DIR / f"{slug}.html"
    if not html_path.is_file():
        pytest.skip(f"Fixture {html_path} not present; run _build_twokratings_fixtures.py")
    html = html_path.read_text(encoding="utf-8", errors="replace")
    return twokratings.parse_html(html, slug)


def test_curry_guard_fixture():
    """Guard fixture: Stephen Curry (PG)."""
    p = _load("stephen-curry")
    assert p.overall_2k == 95
    # Every user-specified attribute except "overall_2k" must be present.
    for attr in config.RATING_ATTRIBUTES:
        if attr == "overall_2k":
            continue
        assert attr in p.attributes, f"missing {attr} for curry"
        val = p.attributes[attr]
        assert 25 <= val <= 99, f"bad {attr}={val} for curry"
    # Physicals
    assert p.height_in == 74.0          # 6'2"
    assert p.weight_lbs == 185.0
    assert p.wingspan_in == 76.0        # 6'4"
    assert p.total_attributes is not None
    assert 1500 <= p.total_attributes <= 3500


def test_lebron_wing_fixture():
    """Wing fixture: LeBron James (SF/PF)."""
    p = _load("lebron-james")
    assert p.overall_2k == 92
    for attr in config.RATING_ATTRIBUTES:
        if attr == "overall_2k":
            continue
        assert attr in p.attributes, f"missing {attr} for lebron"
    assert p.height_in == 81.0          # 6'9"
    assert p.weight_lbs == 250.0
    assert p.wingspan_in == 84.0        # 7'0"


def test_jokic_big_fixture():
    """Big fixture: Nikola Jokic (C)."""
    p = _load("nikola-jokic")
    assert p.overall_2k == 98
    for attr in config.RATING_ATTRIBUTES:
        if attr == "overall_2k":
            continue
        assert attr in p.attributes, f"missing {attr} for jokic"
    assert p.height_in == 83.0          # 6'11"
    assert p.weight_lbs == 284.0
    assert p.wingspan_in == 87.0        # 7'3"


def test_slugify_name():
    s = twokratings.slugify_name
    assert s("Stephen Curry") == "stephen-curry"
    assert s("Luka Doncic") == "luka-doncic"
    assert s("Luka Dončić") == "luka-doncic"
    assert s("A.J. Dybantsa") == "a-j-dybantsa"
    assert s("  ODD  Spacing   ") == "odd-spacing"


def test_as_row_has_all_canonical_keys():
    """The as_row() output must contain every canonical attribute key,
    regardless of whether the underlying 2kratings page had that attribute.
    """
    p = _load("stephen-curry")
    row = p.as_row()
    for attr in config.RATING_ATTRIBUTES:
        assert attr in row
