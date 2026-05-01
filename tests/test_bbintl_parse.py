"""Basketball-Reference international totals → per-game parsing."""

from __future__ import annotations

from src.scrapers.international import parse_bbintl_totals_per_game


MIN_HTML = """
<table class="stats_table" id="player-stats-totals-league-">
<thead><tr><th data-stat="season">Season</th></tr></thead>
<tbody>
<tr>
  <th scope="row" data-stat="season">2024-25</th>
  <td data-stat="g">25</td>
  <td data-stat="mp">572</td>
  <td data-stat="fg">79</td>
  <td data-stat="fga">173</td>
  <td data-stat="fg_pct">.457</td>
  <td data-stat="fg3">20</td>
  <td data-stat="fg3a">65</td>
  <td data-stat="fg3_pct">.308</td>
  <td data-stat="ft">63</td>
  <td data-stat="fta">85</td>
  <td data-stat="ft_pct">.741</td>
  <td data-stat="orb">35</td>
  <td data-stat="drb">83</td>
  <td data-stat="trb">118</td>
  <td data-stat="ast">30</td>
  <td data-stat="stl">16</td>
  <td data-stat="blk">23</td>
  <td data-stat="tov">34</td>
  <td data-stat="pf">68</td>
  <td data-stat="pts">241</td>
  <td data-stat="league">NBL</td>
</tr>
<tr>
  <th scope="row" data-stat="season">2025-26</th>
  <td data-stat="g">31</td>
  <td data-stat="mp">799</td>
  <td data-stat="fg">137</td>
  <td data-stat="fga">273</td>
  <td data-stat="fg_pct">.502</td>
  <td data-stat="fg3">30</td>
  <td data-stat="fg3a">92</td>
  <td data-stat="fg3_pct">.326</td>
  <td data-stat="ft">66</td>
  <td data-stat="fta">92</td>
  <td data-stat="ft_pct">.717</td>
  <td data-stat="orb">57</td>
  <td data-stat="drb">133</td>
  <td data-stat="trb">190</td>
  <td data-stat="ast">62</td>
  <td data-stat="stl">37</td>
  <td data-stat="blk">31</td>
  <td data-stat="tov">48</td>
  <td data-stat="pf">92</td>
  <td data-stat="pts">370</td>
  <td data-stat="league">NBL</td>
</tr>
</tbody>
</table>
"""


def test_parse_bbintl_prefers_matching_season() -> None:
    row = parse_bbintl_totals_per_game(MIN_HTML, season_display="2025-26")
    assert row.get("gp") == 31
    assert row.get("season") == "2025-26"
    assert abs((row.get("pts") or 0) - 11.9355) < 0.02
    assert row.get("_stats_source") == "basketball-reference-intl"


def test_parse_bbintl_latest_when_no_hint_match() -> None:
    row = parse_bbintl_totals_per_game(MIN_HTML, season_display="2099-00")
    assert row.get("season") == "2025-26"
    assert row.get("gp") == 31
