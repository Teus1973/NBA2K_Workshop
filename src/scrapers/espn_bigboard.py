"""
ESPN 2026 NBA Draft big-board scraper + seed-list loader.

ESPN publishes a "Best Available" / big board article that gets updated
every few weeks. The clean machine-readable version lives at
``espn.com/espn/print?id=<ARTICLE_ID>``. The article id changes each draft
cycle; for 2026 we set a sensible default that the user can override via
``BIG_BOARD_URL`` in ``.env``.

Because ESPN's article markup can change without notice, this module also
supports a **seed CSV fallback**: ``data/seed_prospects_2026.csv`` ships with
the top 120 prospects for the 2026 class (rank, name, school, position).
``load_prospects(year=2026)`` merges scraped rows on top of the seed list,
which guarantees we always have at least 120 prospects even when the live
page fails to parse.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.espn_bigboard")

DEFAULT_BIG_BOARD_URL = os.environ.get(
    "BIG_BOARD_URL",
    "https://www.espn.com/espn/print?id=46886245",
)

SEED_CSV_PATH = config.DATA_DIR / "seed_prospects_2026.csv"


@dataclass
class Prospect:
    rank: int | None
    full_name: str
    pos: str | None = None
    school_or_team: str | None = None
    height_in: float | None = None
    weight_lbs: float | None = None
    age: float | None = None
    league: str = config.LEAGUE_NCAA
    source: str = "espn_big_board"
    notes: str | None = None

    @property
    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.full_name.lower()).strip("-")
        return re.sub(r"-{2,}", "-", s)

    @property
    def first_name(self) -> str:
        return self.full_name.split(" ", 1)[0]

    @property
    def last_name(self) -> str:
        parts = self.full_name.split(" ", 1)
        return parts[1] if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------
_RANK_RE = re.compile(r"^\s*(\d{1,3})[.)]\s*(.*)$")
_NAME_SCHOOL_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z'\-\.]+(?:\s+[A-Z][A-Za-z'\-\.]+){1,3})"
    r"(?:\s*[,|]\s*(?P<pos>[A-Z/]{1,4}))?"
    r"(?:\s*[,|]\s*(?P<school>.+?))?\s*$"
)


def parse_bigboard_html(html: str) -> list[Prospect]:
    """Best-effort parser for ESPN's bigboard HTML.

    Extracts ranked prospect lines like::

        1. Cooper Flagg, F, Duke
        2. Dylan Harper, G, Rutgers

    Returns an empty list if nothing matches — callers should fall back to
    the seed CSV.
    """
    soup = BeautifulSoup(html, "lxml")
    # ESPN's big-board article body is typically in <div id="article-body">
    # or nested inside .article-body.
    body = (
        soup.find("div", id="article-body")
        or soup.find("div", class_="article-body")
        or soup.body
        or soup
    )
    prospects: list[Prospect] = []
    seen_ranks: set[int] = set()
    for tag in body.find_all(["p", "li", "h2", "h3", "h4"]):
        text = tag.get_text(" ", strip=True)
        m = _RANK_RE.match(text)
        if not m:
            continue
        rank = int(m.group(1))
        if not 1 <= rank <= 200:
            continue
        if rank in seen_ranks:
            continue
        rest = m.group(2).strip()
        nm = _NAME_SCHOOL_RE.match(rest)
        if nm:
            name = nm.group("name").strip()
            pos = (nm.group("pos") or "").strip() or None
            school = (nm.group("school") or "").strip() or None
        else:
            # Fall back: first 2-3 words are the name.
            parts = rest.split(",", 2)
            name = parts[0].strip()
            pos = parts[1].strip() if len(parts) > 1 else None
            school = parts[2].strip() if len(parts) > 2 else None
        if not name or len(name) < 3:
            continue
        prospects.append(Prospect(
            rank=rank, full_name=name, pos=pos, school_or_team=school,
            source="espn_big_board",
        ))
        seen_ranks.add(rank)
    return prospects


def scrape_bigboard(url: str = DEFAULT_BIG_BOARD_URL, *,
                    force_refresh: bool = False) -> list[Prospect]:
    """Fetch and parse the ESPN big board HTML."""
    cache_key = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:80]
    try:
        html, from_cache = _http.fetch_text(
            url,
            scope_dir=config.CACHE_ESPN,
            cache_key=cache_key,
            suffix=".html",
            force_refresh=force_refresh,
        )
    except Exception as exc:
        log.warning("ESPN big board fetch failed: %s", exc)
        return []
    prospects = parse_bigboard_html(html)
    audit.log_event(
        action="scrape_espn_bigboard",
        entity_type="prospects",
        note=f"url={url}; cache={from_cache}; n={len(prospects)}",
    )
    return prospects


# ---------------------------------------------------------------------------
# Seed CSV (fallback + canonical list of 120)
# ---------------------------------------------------------------------------
def load_seed_prospects(path: Path | None = None) -> list[Prospect]:
    """Load ``data/seed_prospects_2026.csv`` (or ``path``) into a list.

    Columns (header row required):
        rank,full_name,pos,school_or_team,league,height_in,weight_lbs,age,notes
    """
    p = path or SEED_CSV_PATH
    if not p.is_file():
        log.warning("Seed CSV missing: %s", p)
        return []
    prospects: list[Prospect] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rank = int(row["rank"]) if row.get("rank") else None
            except ValueError:
                rank = None
            prospects.append(Prospect(
                rank=rank,
                full_name=row["full_name"].strip(),
                pos=(row.get("pos") or "").strip() or None,
                school_or_team=(row.get("school_or_team") or "").strip() or None,
                height_in=_to_float(row.get("height_in")),
                weight_lbs=_to_float(row.get("weight_lbs")),
                age=_to_float(row.get("age")),
                league=(row.get("league") or config.LEAGUE_NCAA).strip(),
                source="seed_csv",
                notes=(row.get("notes") or "").strip() or None,
            ))
    return prospects


def _to_float(val: object) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_prospects(year: int = config.DRAFT_YEAR, *,
                   force_refresh: bool = False,
                   target: int = config.PROSPECT_TARGET,
                   ) -> list[Prospect]:
    """Return merged prospect list (scrape ∪ seed), de-duplicated on slug,
    truncated to ``target``.

    Scraped rows win on rank/position/school if present; otherwise seed values
    fill in. When the scrape returns an empty list (e.g. ESPN structure
    changed), the seed list is returned unmodified.
    """
    seed = load_seed_prospects()
    scraped = scrape_bigboard(force_refresh=force_refresh)
    by_slug: dict[str, Prospect] = {p.slug: p for p in seed}
    for s in scraped:
        slug = s.slug
        if slug in by_slug:
            base = by_slug[slug]
            base.rank = s.rank or base.rank
            base.pos = s.pos or base.pos
            base.school_or_team = s.school_or_team or base.school_or_team
            base.source = "espn_big_board"
        else:
            by_slug[slug] = s
    merged = sorted(
        by_slug.values(),
        key=lambda p: (p.rank if p.rank is not None else 999),
    )
    return merged[:target]


# ---------------------------------------------------------------------------
# DB writer
# ---------------------------------------------------------------------------
def upsert_prospects(conn, prospects: Iterable[Prospect]) -> int:
    """Write a prospect list to the ``prospects`` table."""
    cur = conn.cursor()
    n = 0
    for p in prospects:
        cur.execute(
            """
            INSERT INTO prospects
                (slug, first_name, last_name, full_name, pos, school_or_team,
                 league, age, height_in, weight_lbs, espn_rank, status,
                 added_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 'system', ?)
            ON CONFLICT(slug) DO UPDATE SET
                first_name    =excluded.first_name,
                last_name     =excluded.last_name,
                full_name     =excluded.full_name,
                pos           =COALESCE(excluded.pos, prospects.pos),
                school_or_team=COALESCE(excluded.school_or_team,
                                        prospects.school_or_team),
                league        =COALESCE(excluded.league, prospects.league),
                age           =COALESCE(excluded.age, prospects.age),
                height_in     =COALESCE(excluded.height_in, prospects.height_in),
                weight_lbs    =COALESCE(excluded.weight_lbs, prospects.weight_lbs),
                espn_rank     =COALESCE(excluded.espn_rank, prospects.espn_rank),
                updated_at    =strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (p.slug, p.first_name, p.last_name, p.full_name, p.pos,
             p.school_or_team, p.league, p.age, p.height_in, p.weight_lbs,
             p.rank, p.notes),
        )
        n += 1
    return n
