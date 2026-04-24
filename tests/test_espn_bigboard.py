"""Parser + seed CSV tests for the ESPN big-board scraper."""

from __future__ import annotations

from src.scrapers import espn_bigboard


SAMPLE_HTML = """
<html><body>
  <div id="article-body">
    <p>1. Cooper Flagg, F, Duke</p>
    <p>2. Dylan Harper, G, Rutgers</p>
    <p>3. Ace Bailey, F, Rutgers</p>
    <p>not-a-rank-line</p>
    <p>4. VJ Edgecombe, G, Baylor</p>
  </div>
</body></html>
"""


def test_parse_bigboard_finds_ranked_players():
    out = espn_bigboard.parse_bigboard_html(SAMPLE_HTML)
    names = [p.full_name for p in out]
    assert "Cooper Flagg" in names
    assert "Dylan Harper" in names
    assert "Ace Bailey" in names
    assert "VJ Edgecombe" in names
    # Ranks preserved.
    by_rank = {p.rank: p.full_name for p in out}
    assert by_rank[1] == "Cooper Flagg"
    assert by_rank[2] == "Dylan Harper"


def test_seed_csv_loads_at_least_100():
    seed = espn_bigboard.load_seed_prospects()
    # Per plan we ship 120; but 100+ is acceptable.
    assert len(seed) >= 100, f"only {len(seed)} seed prospects"
    for p in seed[:5]:
        assert p.full_name
        assert p.league


def test_load_prospects_merges_and_truncates():
    # With no live scrape available, load_prospects should still return >=100.
    out = espn_bigboard.load_prospects(target=120)
    assert len(out) <= 120
    assert len(out) >= 100
    slugs = [p.slug for p in out]
    assert len(slugs) == len(set(slugs)), "duplicate slugs"


def test_upsert_prospects_writes_rows(temp_db):
    prospects = [
        espn_bigboard.Prospect(rank=1, full_name="Test Prospect",
                               pos="PG", school_or_team="Nowhere",
                               league="ncaa"),
    ]
    n = espn_bigboard.upsert_prospects(temp_db, prospects)
    assert n == 1
    row = temp_db.execute(
        "SELECT * FROM prospects WHERE slug='test-prospect'").fetchone()
    assert row["full_name"] == "Test Prospect"
    assert row["pos"] == "PG"
