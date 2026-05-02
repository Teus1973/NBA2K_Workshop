"""
2kratings.com scraper.

Each player has a slug URL: ``https://www.2kratings.com/{slug}``.
The page exposes:

- Overall 2K rating
- Height / weight / wingspan in the About block
- Six attribute categories each containing the individual attribute values
  we care about: Outside Scoring, Athleticism, Inside Scoring, Playmaking,
  Defense, Rebounding
- "Total Attributes" + Potential letter

We parse these into the canonical attribute dict whose keys match
``config.RATING_ATTRIBUTES``. The scraper is polite (config-driven rps) and
caches raw HTML under ``data/cache/2kratings/<slug>.html`` so tests and
calibration can be run offline.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from bs4 import BeautifulSoup

from .. import audit, config
from ..logger import get_logger
from . import _http

log = get_logger("scrapers.twokratings")

BASE = "https://www.2kratings.com"


# ---------------------------------------------------------------------------
# Attribute label → canonical key
#
# 2kratings page labels (left) are mapped to our canonical snake_case keys
# (right). Any label not in this map is dropped with a debug log line.
# ---------------------------------------------------------------------------
LABEL_TO_ATTR: dict[str, str] = {
    # Outside Scoring
    "Close Shot": "close_shot_2k",
    "Mid-Range Shot": "mid_range_shot_2k",
    "Three-Point Shot": "three_point_shot_2k",
    "Free Throw": "free_throws_2k",
    "Shot IQ": "shot_iq_2k",
    "Offensive Consistency": "offensive_consistency_2k",
    # Athleticism
    "Speed": "speed_2k",
    "Agility": "agility_2k",
    "Strength": "strength_2k",
    "Vertical": "vertical_2k",
    "Stamina": "stamina_2k",
    "Hustle": "hustle_2k",
    # Inside Scoring
    "Layup": "driving_layup_2k",          # 2K in-game: "Driving Layup"
    "Standing Dunk": "standing_dunk_2k",
    "Driving Dunk": "driving_dunk_2k",
    "Post Control": "post_control_2k",
    "Draw Foul": "draw_foul_2k",
    "Hands": "hands_2k",
    # Playmaking
    "Pass Accuracy": "pass_accuracy_2k",
    "Ball Handle": "ball_handle_2k",
    "Speed with Ball": "speed_with_ball_2k",
    "Pass IQ": "pass_iq_2k",
    "Pass Vision": "pass_vision_2k",
    # Defense
    "Interior Defense": "interior_defense_2k",
    "Perimeter Defense": "perimeter_defense_2k",
    "Steal": "steal_2k",
    "Block": "block_2k",
    "Help Defense IQ": "help_defense_iq_2k",
    "Pass Perception": "pass_perception_2k",
    "Defensive Consistency": "defensive_consistency_2k",
    # Rebounding
    "Offensive Rebound": "offensive_rebound_2k",
    "Defensive Rebound": "defensive_rebound_2k",
    "Post Hook": "post_hook_2k",
    "Post Fade": "post_fade_2k",
    "Intangibles": "intangibles_2k",
    "Overall Durability": "durability_2k",
}

# Labels scraped but not mapped (e.g. section headers); kept for future extension.
EXTRA_LABELS: frozenset[str] = frozenset()


@dataclass
class TwoKRatingsPlayer:
    """Normalized 2kratings scrape result for a single player."""
    slug: str
    full_name: str | None = None
    team: str | None = None
    pos: str | None = None
    overall_2k: int | None = None
    height_in: float | None = None
    weight_lbs: float | None = None
    wingspan_in: float | None = None
    total_attributes: int | None = None
    potential: str | None = None
    attributes: dict[str, int] = field(default_factory=dict)
    extras: dict[str, int] = field(default_factory=dict)
    source_url: str | None = None

    def as_row(self) -> dict[str, Any]:
        """Flatten into a dict suitable for upsert into ``nba_ratings_2k26``."""
        out: dict[str, Any] = {
            "slug": self.slug,
            "source_url": self.source_url,
            "total_attributes": self.total_attributes,
            "potential": self.potential,
        }
        for key in config.RATING_ATTRIBUTES:
            out[key] = self.attributes.get(key)
        if "overall_2k" in config.RATING_ATTRIBUTES and self.overall_2k is not None:
            out["overall_2k"] = self.overall_2k
        return out


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
_HEIGHT_RE = re.compile(
    r"(?P<ft>\d+)\s*feet(?:\s*(?P<inch>\d+)\s*inches)?\s*\(",
    re.IGNORECASE,
)
_HEIGHT_WS_RE = re.compile(r"(\d+)'(\d+)\"")
_WEIGHT_RE = re.compile(r"weighs?\s+(\d+)\s*(?:pounds|lbs)", re.IGNORECASE)
_WEIGHT_SIMPLE_RE = re.compile(r"(\d+)\s*lbs", re.IGNORECASE)
_WINGSPAN_RE = re.compile(
    r"wingspan of\s+(\d+)\s*feet(?:\s*(\d+)\s*inches)?",
    re.IGNORECASE,
)
_WINGSPAN_SIMPLE_RE = re.compile(r"wingspan[^0-9]*(\d+)'(\d+)\"", re.IGNORECASE)

_INT_PREFIX = re.compile(r"^\s*(\d{1,3})\s+(.+?)\s*$")
_OVERALL_RE = re.compile(r"(\d{2,3})\s+(?:Overall|Total)\s+Attributes", re.IGNORECASE)
_RATING_IS_RE = re.compile(
    r"NBA\s*2K2?6\s*Rating\s*is\s*(\d{2,3})",
    re.IGNORECASE,
)
_POTENTIAL_RE = re.compile(r"Potential\s*[:\-]?\s*([A-DF][+-]?)", re.IGNORECASE)


def _text_of(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _parse_height_in(text: str) -> float | None:
    m = _HEIGHT_RE.search(text)
    if m:
        ft = int(m.group("ft"))
        inch = int(m.group("inch") or 0)
        return float(ft * 12 + inch)
    m2 = _HEIGHT_WS_RE.search(text)
    if m2:
        return float(int(m2.group(1)) * 12 + int(m2.group(2)))
    return None


def _parse_weight_lbs(text: str) -> float | None:
    m = _WEIGHT_RE.search(text)
    if m:
        return float(m.group(1))
    m2 = _WEIGHT_SIMPLE_RE.search(text)
    if m2:
        return float(m2.group(1))
    return None


def _parse_wingspan_in(text: str) -> float | None:
    m = _WINGSPAN_RE.search(text)
    if m:
        ft = int(m.group(1))
        inch = int(m.group(2) or 0)
        return float(ft * 12 + inch)
    m2 = _WINGSPAN_SIMPLE_RE.search(text)
    if m2:
        return float(int(m2.group(1)) * 12 + int(m2.group(2)))
    return None


def _extract_label_from_row(row) -> str | None:
    """Given the enclosing row node of an ``attribute-box`` span, return the
    attribute label, or ``None`` if the row has no recognisable label.

    The 2kratings markup for a single attribute row looks like::

        <div class="d-flex justify-content-between align-items-center">
          <span> Three-Point Shot <span id="three-PointShot" role="tooltip">
            <i data-feather="help-circle" ...></i>
          </span> </span>
          <span class="mb-1">
            <span class="text-success exponent">+1</span>
            <span data-order="76.00" class="attribute-box medium">76</span>
          </span>
        </div>

    The label span is the first ``<span>`` child of the row. Its first
    direct-text child is the attribute name (tooltip text that follows the
    nested <span id=...> is discarded).
    """
    # The row has at most two direct <span> children: label span, value span.
    direct_spans = [c for c in row.find_all("span", recursive=False)]
    if not direct_spans:
        return None
    label_span = direct_spans[0]
    # The first non-empty direct text node of the label span is the label.
    for child in label_span.children:
        if isinstance(child, str):
            s = child.strip()
            if s:
                return s
    # Fall back to the full span text (will include tooltip id text but
    # typically harmless; we'll match against LABEL_TO_ATTR as prefix).
    t = label_span.get_text(" ", strip=True)
    return t or None


def parse_html(html: str, slug: str) -> TwoKRatingsPlayer:
    """Parse a 2kratings HTML payload into a TwoKRatingsPlayer.

    Pure function -- no I/O, easy to unit test against fixture HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    out = TwoKRatingsPlayer(slug=slug, source_url=f"{BASE}/{slug}")

    # Overall rating -- "<NAME>'s NBA 2K26 Rating is 95".
    m_overall = _RATING_IS_RE.search(page_text)
    if m_overall:
        out.overall_2k = int(m_overall.group(1))
    # Fallback: the big "attribute-box-player diamond/gold/silver" span that
    # wraps the OVERALL badge near the top of the page.
    if out.overall_2k is None:
        big = soup.find("span", class_=re.compile(r"attribute-box-player"))
        if big:
            try:
                out.overall_2k = int(big.get_text(strip=True))
            except ValueError:
                pass

    # Height / weight / wingspan from the About block (free-text paragraph).
    about_text = page_text
    for h2 in soup.find_all(["h2", "h3", "h4", "h5"]):
        if h2.get_text(strip=True).lower().startswith("about "):
            nxt = h2.find_next(["p", "div"])
            if nxt:
                about_text = nxt.get_text(" ", strip=True)
                break
    out.height_in = _parse_height_in(about_text)
    out.weight_lbs = _parse_weight_lbs(about_text)
    out.wingspan_in = _parse_wingspan_in(about_text)

    # Potential
    m_pot = _POTENTIAL_RE.search(page_text)
    if m_pot:
        out.potential = m_pot.group(1).upper()

    # Attributes. Every individual attribute row renders a
    # <span class="attribute-box ..."> with the integer value. We walk back up
    # to the enclosing row and pull the label from the row's first span.
    for box in soup.find_all("span", class_=re.compile(r"\battribute-box\b")):
        # Skip the big "attribute-box-player" variant used for the OVERALL
        # badge: its class contains 'attribute-box-player'.
        classes = " ".join(box.get("class") or [])
        if "attribute-box-player" in classes:
            continue
        value_text = box.get_text(strip=True)
        try:
            val = int(value_text)
        except ValueError:
            continue
        if not (1 <= val <= 99):
            continue
        # Walk up to the enclosing row (either <div class="d-flex..."> or <li>).
        row = box.find_parent(
            lambda tag: tag.name in ("div", "li")
            and (
                "d-flex" in (tag.get("class") or [])
                or tag.name == "li"
            )
        )
        if row is None:
            continue
        label = _extract_label_from_row(row)
        if not label:
            continue
        # Trim any trailing whitespace and punctuation from label.
        label = label.strip().rstrip(":")
        key = LABEL_TO_ATTR.get(label)
        if key:
            out.attributes.setdefault(key, val)
        elif label in EXTRA_LABELS:
            out.extras.setdefault(label, val)
        # else: unrecognised label (e.g. section-only badges).

    # Intangibles: rendered as a card-header title + badge (not d-flex / li).
    if "intangibles_2k" not in out.attributes:
        tip = soup.find(id="intangibles")
        header = None
        if tip is not None:
            p = tip.find_parent("div")
            while p is not None:
                cls = p.get("class") or []
                if "card-header" in cls:
                    header = p
                    break
                p = p.find_parent("div")
        if header is not None:
            for box in header.find_all("span", class_=re.compile(r"\battribute-box\b")):
                classes = " ".join(box.get("class") or [])
                if "attribute-box-player" in classes:
                    continue
                try:
                    val = int(box.get_text(strip=True))
                except ValueError:
                    continue
                if 1 <= val <= 99:
                    out.attributes.setdefault("intangibles_2k", val)
                    break

    # Total Attributes -- the <span class="attribute-box total_attributes">.
    ta_span = soup.find("span", class_=re.compile(r"\btotal_attributes\b"))
    if ta_span is not None:
        try:
            out.total_attributes = int(
                ta_span.get_text(strip=True).replace(",", "")
            )
        except ValueError:
            pass

    return out


# ---------------------------------------------------------------------------
# Public scrape entrypoint
# ---------------------------------------------------------------------------
def scrape_player(slug: str, *, force_refresh: bool = False,
                  ttl: int | None = None, log_audit: bool = True,
                  ) -> TwoKRatingsPlayer:
    """Fetch ``https://www.2kratings.com/{slug}`` and parse it.

    ``slug`` is e.g. ``"aj-dybantsa"``, ``"demar-derozan"``. The hyphen
    convention is: lowercase, non-alnum → ``-``, collapse consecutive ``-``.
    """
    url = f"{BASE}/{slug}"
    html, from_cache = _http.fetch_text(
        url,
        scope_dir=config.CACHE_2KRATINGS,
        cache_key=slug,
        suffix=".html",
        ttl=ttl,
        force_refresh=force_refresh,
    )
    player = parse_html(html, slug)
    if log_audit:
        audit.log_event(
            action="scrape_2kratings",
            entity_type="nba_player",
            entity_slug=slug,
            note=f"from_cache={from_cache}; overall={player.overall_2k}; "
                 f"n_attrs={len(player.attributes)}",
        )
    return player


def slugify_name(name: str) -> str:
    """Best-effort slug generator matching 2kratings conventions.

    Rules:
    - NFKD-normalize so ``Dončić`` -> ``Doncic`` (accents must not become
      stray hyphens like ``don-i`` under ASCII-only stripping)
    - lowercase
    - spaces and punctuation -> hyphens
    - collapse multiple hyphens
    - trim leading/trailing hyphens

    Note that 2kratings has some non-standard slugs (e.g. all-time versions
    get ``-all-time-golden-state-warriors`` suffixes). Use :func:`scrape_player`
    with an explicit slug for those cases.
    """
    s = unicodedata.normalize("NFKD", name.strip())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


# ---------------------------------------------------------------------------
# Bulk ingest helpers
# ---------------------------------------------------------------------------
def _rating_upsert_sql() -> str:
    """Build the UPSERT statement for nba_ratings_2k26.

    Lazily introspects the live table so we stay in sync if columns change.
    """
    from .. import db as _db
    conn = _db.connect()
    try:
        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info(nba_ratings_2k26)")]
    finally:
        conn.close()
    cols = [c for c in cols if c != "scraped_at"]
    placeholders = ", ".join(["?"] * len(cols))
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "player_id")
    return (
        f"INSERT INTO nba_ratings_2k26 ({', '.join(cols)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(player_id) DO UPDATE SET {updates}",
        cols,
    )


def upsert_rating(conn, player_id: int, player: TwoKRatingsPlayer) -> None:
    """Insert/update a single ``nba_ratings_2k26`` row from a scrape result."""
    sql, cols = _rating_upsert_sql()
    row = player.as_row()
    row["player_id"] = player_id
    values = [row.get(c) for c in cols]
    conn.execute(sql, values)


def bulk_scrape_and_upsert(
    *,
    limit: int | None = None,
    force_refresh: bool = False,
    only_missing: bool = False,
    progress_cb=None,
) -> dict[str, int]:
    """Iterate every row in ``nba_players`` and populate ``nba_ratings_2k26``.

    - Uses the existing polite HTTP client (rate-limited + cached).
    - Skips players with no slug.
    - If ``only_missing`` is True, only players without a ``nba_ratings_2k26``
      row are scraped (after e.g. fixing Unicode slugs).
    - On parse failure for a given player we log the error and continue.
    - ``progress_cb(i, total, slug, status)`` is invoked after each player
      so the caller (CLI or Streamlit) can render progress.

    Returns a dict with ``ok``, ``failed``, ``skipped`` counts.
    """
    from .. import db as _db

    conn = _db.connect()
    try:
        if only_missing:
            rows = conn.execute(
                "SELECT p.player_id AS player_id, p.slug AS slug, "
                "p.full_name AS full_name FROM nba_players p "
                "LEFT JOIN nba_ratings_2k26 r ON p.player_id = r.player_id "
                "WHERE r.player_id IS NULL "
                "AND p.slug IS NOT NULL AND p.slug != '' "
                "ORDER BY p.full_name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT player_id, slug, full_name FROM nba_players "
                "WHERE slug IS NOT NULL AND slug != '' ORDER BY full_name"
            ).fetchall()
    finally:
        conn.close()

    if limit is not None:
        rows = rows[:limit]

    total = len(rows)
    ok = failed = skipped = 0
    conn = _db.connect()
    try:
        for i, row in enumerate(rows, start=1):
            pid = row["player_id"]
            slug = row["slug"]
            try:
                player = scrape_player(
                    slug, force_refresh=force_refresh, log_audit=False)
                if not player.attributes:
                    skipped += 1
                    status = "no-attrs"
                else:
                    upsert_rating(conn, pid, player)
                    conn.commit()
                    ok += 1
                    status = f"ok ({len(player.attributes)} attrs)"
            except Exception as exc:  # noqa: BLE001
                failed += 1
                status = f"fail: {exc}"
                log.warning("2kratings scrape failed for %s: %s", slug, exc)
            if progress_cb is not None:
                try:
                    progress_cb(i, total, slug, status)
                except Exception:  # noqa: BLE001
                    pass
    finally:
        conn.close()

    audit.log_event(
        action="bulk_scrape_2kratings",
        entity_type="nba_player",
        note=f"only_missing={only_missing} total={total} ok={ok} "
             f"failed={failed} skipped={skipped}",
    )
    return {"total": total, "ok": ok, "failed": failed, "skipped": skipped}
