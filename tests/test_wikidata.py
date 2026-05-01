"""Wikidata birth-date helpers."""

from __future__ import annotations

from src.scrapers.wikidata import _wikidata_time_to_iso, birth_date_iso_from_enwiki_title


def test_wikidata_time_to_iso_day_precision() -> None:
    assert (
        _wikidata_time_to_iso(
            {"time": "+2004-09-04T00:00:00Z", "precision": 11},
        )
        == "2004-09-04"
    )


def test_wikidata_time_to_iso_year_precision() -> None:
    assert (
        _wikidata_time_to_iso({"time": "+2004-00-00T00:00:00Z", "precision": 9})
        == "2004-07-01"
    )


def test_wikidata_time_to_iso_month_precision() -> None:
    assert (
        _wikidata_time_to_iso({"time": "+2004-07-01T00:00:00Z", "precision": 10})
        == "2004-07-01"
    )


def test_birth_date_iso_from_enwiki_title_elliot(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs: object) -> object:
        params = kwargs.get("params") or {}
        if "wbgetentities" in url or params.get("action") == "wbgetentities":
            calls.append(("wd", dict(params)))
            class Resp:
                status_code = 200

                def json(self) -> dict:
                    return {
                        "entities": {
                            "Q1": {
                                "claims": {
                                    "P569": [
                                        {
                                            "mainsnak": {
                                                "snaktype": "value",
                                                "datavalue": {
                                                    "type": "time",
                                                    "value": {
                                                        "time": "+2004-09-04T00:00:00Z",
                                                        "precision": 11,
                                                    },
                                                },
                                            },
                                        },
                                    ],
                                },
                            },
                        },
                    }

            return Resp()
        raise AssertionError("unexpected URL")

    monkeypatch.setattr("src.scrapers.wikidata.requests.get", fake_get)
    assert birth_date_iso_from_enwiki_title("Elliot Cadeau") == "2004-09-04"
    assert calls and calls[0][0] == "wd"
