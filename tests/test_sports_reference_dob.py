"""Sports-reference CBB: date of birth line parsing."""

from __future__ import annotations

import pytest

from src.scrapers.sports_reference_cbb import _parse_dob_from_info_text


@pytest.mark.parametrize(
    "text, expected",
    [
        (" Born: Dec 2, 2000 ", "2000-12-02"),
        ("x Born: July 1, 2001 in Somewhere", "2001-07-01"),
        ("No birth here", None),
    ],
)
def test_parse_dob_from_info_text(text: str, expected: str | None) -> None:
    assert _parse_dob_from_info_text(text) == expected
