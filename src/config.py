"""
NBA2K26 Workshop — configuration constants, paths, and env loading.

Mirrors the pattern in SubtitleForge/src/config.py: a PROJECT_ROOT + .env load
at import time, per-user %APPDATA% data dir helper, and a bundle of typed
constants that everything else imports from here rather than from os.environ.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)

CACHE_2KRATINGS = CACHE_DIR / "2kratings"
CACHE_NBA = CACHE_DIR / "nba"
CACHE_ESPN = CACHE_DIR / "espn"
CACHE_CBB = CACHE_DIR / "cbb"
CACHE_INTL = CACHE_DIR / "international"
for _d in (CACHE_2KRATINGS, CACHE_NBA, CACHE_ESPN, CACHE_CBB, CACHE_INTL):
    _d.mkdir(parents=True, exist_ok=True)

FORMULAS_DIR = DATA_DIR / "formulas"
FORMULAS_DIR.mkdir(exist_ok=True)

USER_UPLOADS_DIR = DATA_DIR / "user_uploads"
USER_UPLOADS_DIR.mkdir(exist_ok=True)

EXPORTS_DIR = DATA_DIR / "exports"
EXPORTS_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "workshop.db"


def get_user_data_dir() -> Path:
    """Per-user app data dir (persistent settings, OAuth tokens, etc.)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA", str(Path.home()))
        d = Path(base) / "NBA2KWorkshop"
    else:
        d = Path.home() / ".config" / "NBA2KWorkshop"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Scraping knobs
# ---------------------------------------------------------------------------
def _as_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _as_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


SCRAPE_RPS = _as_float("NBA2K_WORKSHOP_SCRAPE_RPS", 1.0)
"""Requests per second ceiling for polite scraping (per-host)."""

# Calendar years sent to ``DraftCombinePlayerAnthro`` / ``DraftCombineDrillResults``
# as ``SeasonYear`` (the draft class year). A wide span lets veteran NBA players
# match their historical combine row via ``nba:{player_id}``.
NBA_COMBINE_SEASON_YEARS: tuple[int, ...] = tuple(range(2000, 2027))

USER_AGENT = (
    os.environ.get("NBA2K_WORKSHOP_USER_AGENT", "").strip()
    or "NBA2K-Workshop/0.1 (+personal-use)"
)

CACHE_TTL_SECONDS = _as_int("NBA2K_WORKSHOP_CACHE_TTL", 86400)
"""After this age, cached HTML/JSON is refetched on next scrape."""

# ---------------------------------------------------------------------------
# Streamlit + HTTP
# ---------------------------------------------------------------------------
STREAMLIT_PORT = _as_int("NBA2K_WORKSHOP_PORT", 8506)

HTTP_TIMEOUT = _as_float("NBA2K_WORKSHOP_HTTP_TIMEOUT", 20.0)

# ---------------------------------------------------------------------------
# Google Sheets export (optional)
# ---------------------------------------------------------------------------
GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", "").strip() or None

# ---------------------------------------------------------------------------
# Ollama (local AI for scouting summaries)
# ---------------------------------------------------------------------------
# Default: try the local Ollama server. Set NBA2K_WORKSHOP_USE_OLLAMA=0 to never
# call it (e.g. offline-only or automated tests without a server).
USE_OLLAMA = os.environ.get("NBA2K_WORKSHOP_USE_OLLAMA", "1").strip() != "0"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e4b").strip()

# Scouting tab: only the first N rows (by ESPN rank) run Wikipedia, web search,
# and Ollama per page load. The rest use ESPN text / DB cache only, so the UI
# does not run 100+ sequential LLM calls. Override with
# ``NBA2K_WORKSHOP_SCOUTING_ENRICH_CAP``.
SCOUTING_ENRICH_ROW_CAP = _as_int("NBA2K_WORKSHOP_SCOUTING_ENRICH_CAP", 35)

# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------
CURRENT_SEASON = os.environ.get("NBA2K_WORKSHOP_SEASON", "2025-26")
DRAFT_YEAR = _as_int("NBA2K_WORKSHOP_DRAFT_YEAR", 2026)
PROSPECT_TARGET = _as_int("NBA2K_WORKSHOP_PROSPECT_TARGET", 120)

# "calibrated" = YAML/linear :mod:`src.formulas` registry. "excel_2026_class" =
# sheet-faithful engine from the 2026 class template.
# Env default; the UI and :func:`get_rating_engine` also read
# :data:`USER_WORKSHOP_SETTINGS` for a persistent choice.
RATING_ENGINE = os.environ.get("NBA2K_RATING_ENGINE", "calibrated").strip().lower()

RATING_ENGINES: tuple[str, ...] = ("calibrated", "excel_2026_class")
USER_WORKSHOP_SETTINGS: Path = get_user_data_dir() / "workshop_settings.json"
_rating_engine_mtimes: float = 0.0
_rating_engine_cache: str | None = None


def get_rating_engine() -> str:
    """Active rating engine: user settings file (if present) else :data:`RATING_ENGINE`."""
    global _rating_engine_mtimes, _rating_engine_cache
    p = USER_WORKSHOP_SETTINGS
    try:
        m = p.stat().st_mtime
    except OSError:
        m = 0.0
    if m == _rating_engine_mtimes and _rating_engine_cache is not None and m > 0:
        return _rating_engine_cache
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            e = str(data.get("rating_engine", "")).strip().lower()
            if e in RATING_ENGINES:
                _rating_engine_mtimes = m
                _rating_engine_cache = e
                return e
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            pass
    dflt = RATING_ENGINE if RATING_ENGINE in RATING_ENGINES else "calibrated"
    _rating_engine_mtimes = m
    _rating_engine_cache = dflt
    return dflt


def set_rating_engine(name: str) -> None:
    """Persist engine choice to disk; next :func:`get_rating_engine` reflects it."""
    global _rating_engine_mtimes, _rating_engine_cache
    n = name.strip().lower() if isinstance(name, str) else "calibrated"
    if n not in RATING_ENGINES:
        n = "calibrated"
    p = USER_WORKSHOP_SETTINGS
    p.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    data["rating_engine"] = n
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _rating_engine_cache = n
    try:
        _rating_engine_mtimes = p.stat().st_mtime
    except OSError:
        _rating_engine_mtimes = 0.0

# The user's requested attribute list (section 2.2 of PLAN.md).
# Ordering here is the canonical column order for every exporter and UI table.
RATING_ATTRIBUTES: tuple[str, ...] = (
    "overall_2k",
    "driving_layup_2k",
    "post_control_2k",
    "draw_foul_2k",
    "close_shot_2k",
    "mid_range_shot_2k",
    "three_point_shot_2k",
    "free_throws_2k",
    "ball_handle_2k",
    "pass_iq_2k",
    "pass_accuracy_2k",
    "offensive_rebound_2k",
    "standing_dunk_2k",
    "driving_dunk_2k",
    "shot_iq_2k",
    "pass_vision_2k",
    "hands_2k",
    "defensive_rebound_2k",
    "interior_defense_2k",
    "perimeter_defense_2k",
    "block_2k",
    "steal_2k",
    "speed_2k",
    "speed_with_ball_2k",
    "vertical_2k",
    "strength_2k",
    "stamina_2k",
    "hustle_2k",
    "agility_2k",
    "pass_perception_2k",
    "defensive_consistency_2k",
    "help_defense_iq_2k",
    "offensive_consistency_2k",
)

# Human-readable attribute display names (for UI + export headers).
RATING_DISPLAY_NAMES: dict[str, str] = {
    "overall_2k": "Overall Rating 2K",
    "driving_layup_2k": "Driving Layup 2K",
    "post_control_2k": "Post Control 2K",
    "draw_foul_2k": "Draw Foul 2K",
    "close_shot_2k": "Close Shot 2K",
    "mid_range_shot_2k": "Mid-Range Shot 2K",
    "three_point_shot_2k": "3 Point Shot 2K",
    "free_throws_2k": "Free Throws 2K",
    "ball_handle_2k": "Ball Handle 2K",
    "pass_iq_2k": "Pass IQ 2K",
    "pass_accuracy_2k": "Pass Accuracy 2K",
    "offensive_rebound_2k": "Offensive Rebound 2K",
    "standing_dunk_2k": "Standing Dunk 2K",
    "driving_dunk_2k": "Driving Dunk 2K",
    "shot_iq_2k": "Shot IQ 2K",
    "pass_vision_2k": "Pass Vision 2K",
    "hands_2k": "Hands 2K",
    "defensive_rebound_2k": "Defensive Rebound 2K",
    "interior_defense_2k": "Interior Defense 2K",
    "perimeter_defense_2k": "Perimeter Defense 2K",
    "block_2k": "Block 2K",
    "steal_2k": "Steal 2K",
    "speed_2k": "Speed 2K",
    "speed_with_ball_2k": "Speed With Ball 2K",
    "vertical_2k": "Vertical 2K",
    "strength_2k": "Strength 2K",
    "stamina_2k": "Stamina 2K",
    "hustle_2k": "Hustle 2K",
    "agility_2k": "Agility 2K",
    "pass_perception_2k": "Pass Perception 2K",
    "defensive_consistency_2k": "Defensive Consistency 2K",
    "help_defense_iq_2k": "Help Defense IQ 2K",
    "offensive_consistency_2k": "Offensive Consistency 2K",
}

# Combine-derived "C *" columns (user spec 2.2.iii).
COMBINE_ATTRIBUTES: tuple[str, ...] = (
    "c_speed_2k",
    "c_speed_with_ball_2k",
    "c_vertical_2k",
    "c_agility_2k",
    "c_wingspan_in",
)

# Stats columns the user listed (section 2.2.ii).
STAT_COLUMNS: tuple[str, ...] = (
    "gp", "min", "pts",
    "fgm", "fga", "fg_pct",
    "fg3m", "fg3a", "fg3_pct",
    "ftm", "fta", "ft_pct",
    "oreb", "dreb", "reb",
    "ast", "tov", "stl", "blk", "pf",
)

LEAGUE_NCAA = "ncaa"
LEAGUE_NBA = "nba"
LEAGUE_EUROLEAGUE = "euroleague"
LEAGUE_NBL = "nbl"
LEAGUE_NZNBL = "nznbl"
LEAGUE_GLEAGUE = "gleague"
LEAGUE_HS = "hs"
LEAGUE_OTHER = "other"

VALID_LEAGUES = {
    LEAGUE_NCAA, LEAGUE_NBA, LEAGUE_EUROLEAGUE, LEAGUE_NBL,
    LEAGUE_NZNBL, LEAGUE_GLEAGUE, LEAGUE_HS, LEAGUE_OTHER,
}
