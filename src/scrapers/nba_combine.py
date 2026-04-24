"""
NBA Draft Combine scrape via nba_api.

Three endpoints:

- ``DraftCombinePlayerAnthro`` — height / wingspan / reach / hand size.
- ``DraftCombineDrillResults`` — standing / max vert, lane agility, 3/4 sprint,
  bench press reps.
- ``DraftCombineStats`` — a superset that also contains the anthro + drills
  plus shooting-spot results. Available for historical seasons but flaky on
  the most-recent season.

**Research note (2026-04-24):** the pre-computed "2K"-scaled columns that
older ``DraftCombineStats`` responses used to expose (``SPEED_2K``,
``AGILITY_2K``, ``VERTICAL_2K``, ``SPEED_WITH_BALL_2K``) are **not present**
in ``nba_api`` 1.11.4. We therefore derive ``c_speed_2k``, ``c_agility_2k``,
``c_vertical_2k``, ``c_speed_with_ball_2k`` ourselves in
:mod:`src.calibration.fit_formulas` from raw drill times by regression against
current NBA 2K26 ratings of combine alumni.

Usage::

    from src.scrapers import nba_combine
    anthro = nba_combine.fetch_anthro(2024)   # pandas.DataFrame
    drills = nba_combine.fetch_drills(2024)
    merged = nba_combine.fetch_combine(2024)  # union of anthro + drills
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd
from nba_api.stats.endpoints import (
    draftcombinedrillresults,
    draftcombineplayeranthro,
    draftcombinestats,
)

from .. import audit, config
from ..logger import get_logger

log = get_logger("scrapers.nba_combine")


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------
def _retry(call, *, name: str, max_attempts: int = 4, base_delay: float = 1.5):
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:  # nba_api raises json.JSONDecodeError, etc.
            last_exc = exc
            wait = base_delay * (2 ** (attempt - 1))
            log.warning("nba_api %s failed (attempt %d/%d): %s; sleep %.1fs",
                        name, attempt, max_attempts, exc, wait)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _season_year_str(year: int) -> str:
    return str(int(year))


def _season_all_time_str(year: int) -> str:
    """``2024`` -> ``"2024-25"`` (NBA season notation)."""
    return f"{int(year)}-{str(int(year) + 1)[-2:]}"


# ---------------------------------------------------------------------------
# Anthro / drills fetchers
# ---------------------------------------------------------------------------
def fetch_anthro(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """DraftCombinePlayerAnthro for the given year."""
    cache_path = config.CACHE_NBA / f"combine_anthro_{year}.json"
    if cache_path.is_file() and not force_refresh:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    def call():
        ep = draftcombineplayeranthro.DraftCombinePlayerAnthro(
            season_year=_season_year_str(year)
        )
        return ep.get_data_frames()[0]

    df = _retry(call, name="DraftCombinePlayerAnthro")
    cache_path.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_combine", entity_type="combine",
                    note=f"anthro {year}: {len(df)} rows")
    return df


def fetch_drills(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """DraftCombineDrillResults for the given year."""
    cache_path = config.CACHE_NBA / f"combine_drills_{year}.json"
    if cache_path.is_file() and not force_refresh:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    def call():
        ep = draftcombinedrillresults.DraftCombineDrillResults(
            season_year=_season_year_str(year)
        )
        return ep.get_data_frames()[0]

    df = _retry(call, name="DraftCombineDrillResults")
    cache_path.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_combine", entity_type="combine",
                    note=f"drills {year}: {len(df)} rows")
    return df


def fetch_combine_stats(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """DraftCombineStats (the big flat endpoint: anthro + drills + shooting).

    Notes
    -----
    - Flaky for the most recent season; always has data for past seasons.
    - The historical ``SPEED_2K`` / ``AGILITY_2K`` / ``VERTICAL_2K`` columns
      are no longer present (as of nba_api 1.11.x). We derive them from raw
      drill times ourselves (see ``src.calibration.fit_formulas``).
    """
    cache_path = config.CACHE_NBA / f"combine_stats_{year}.json"
    if cache_path.is_file() and not force_refresh:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    def call():
        ep = draftcombinestats.DraftCombineStats(
            season_all_time=_season_all_time_str(year)
        )
        return ep.get_data_frames()[0]

    df = _retry(call, name="DraftCombineStats")
    cache_path.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_combine", entity_type="combine",
                    note=f"stats {year}: {len(df)} rows")
    return df


# ---------------------------------------------------------------------------
# Normalization to our DB schema
# ---------------------------------------------------------------------------
@dataclass
class CombineMeasurement:
    """One row of ``combine_measurements``."""
    subject_key: str
    year: int
    height_wo_shoes_in: float | None = None
    height_w_shoes_in: float | None = None
    wingspan_in: float | None = None
    weight_lbs: float | None = None
    std_reach_in: float | None = None
    body_fat_pct: float | None = None
    hand_length_in: float | None = None
    hand_width_in: float | None = None


@dataclass
class CombineDrill:
    """One row of ``combine_drills``. 2K-scaled columns are always ``None``
    here — they are filled in by the calibration pipeline."""
    subject_key: str
    year: int
    lane_agility_sec: float | None = None
    shuttle_sec: float | None = None
    three_quarter_sprint_sec: float | None = None
    standing_vert_in: float | None = None
    max_vert_in: float | None = None
    bench_reps: int | None = None
    c_speed_2k: int | None = None
    c_speed_with_ball_2k: int | None = None
    c_vertical_2k: int | None = None
    c_agility_2k: int | None = None


def _f(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _i(val: Any) -> int | None:
    f = _f(val)
    return int(f) if f is not None else None


def anthro_row_to_measurement(row: dict[str, Any], year: int,
                              subject_key: str) -> CombineMeasurement:
    return CombineMeasurement(
        subject_key=subject_key,
        year=year,
        height_wo_shoes_in=_f(row.get("HEIGHT_WO_SHOES")),
        height_w_shoes_in=_f(row.get("HEIGHT_W_SHOES")),
        wingspan_in=_f(row.get("WINGSPAN")),
        weight_lbs=_f(row.get("WEIGHT")),
        std_reach_in=_f(row.get("STANDING_REACH")),
        body_fat_pct=_f(row.get("BODY_FAT_PCT")),
        hand_length_in=_f(row.get("HAND_LENGTH")),
        hand_width_in=_f(row.get("HAND_WIDTH")),
    )


def drill_row_to_measurement(row: dict[str, Any], year: int,
                             subject_key: str) -> CombineDrill:
    return CombineDrill(
        subject_key=subject_key,
        year=year,
        lane_agility_sec=_f(row.get("LANE_AGILITY_TIME")),
        shuttle_sec=_f(row.get("MODIFIED_LANE_AGILITY_TIME")),
        three_quarter_sprint_sec=_f(row.get("THREE_QUARTER_SPRINT")),
        standing_vert_in=_f(row.get("STANDING_VERTICAL_LEAP")),
        max_vert_in=_f(row.get("MAX_VERTICAL_LEAP")),
        bench_reps=_i(row.get("BENCH_PRESS")),
    )


# ---------------------------------------------------------------------------
# DB writer
# ---------------------------------------------------------------------------
def upsert_combine(conn, year: int, anthro_df: pd.DataFrame,
                   drills_df: pd.DataFrame,
                   subject_key_fn=None) -> tuple[int, int]:
    """Write a year's combine to the workshop DB.

    ``subject_key_fn(row)`` maps a raw nba_api row to our ``subject_key``
    (nba_players.slug for current NBA players, prospect slug otherwise).
    Default keys on ``PLAYER_ID`` prefixed with ``"nba:"``.

    Returns ``(n_measurements, n_drills)``.
    """
    if subject_key_fn is None:
        def subject_key_fn(r):  # noqa: E306
            pid = r.get("PLAYER_ID") or r.get("TEMP_PLAYER_ID")
            return f"nba:{pid}" if pid else None

    m_rows: list[CombineMeasurement] = []
    for _, row in anthro_df.iterrows():
        key = subject_key_fn(row)
        if not key:
            continue
        m_rows.append(anthro_row_to_measurement(row.to_dict(), year, key))

    d_rows: list[CombineDrill] = []
    for _, row in drills_df.iterrows():
        key = subject_key_fn(row)
        if not key:
            continue
        d_rows.append(drill_row_to_measurement(row.to_dict(), year, key))

    cur = conn.cursor()
    for m in m_rows:
        cur.execute(
            """
            INSERT INTO combine_measurements
                (subject_key, year, height_wo_shoes_in, height_w_shoes_in,
                 wingspan_in, weight_lbs, std_reach_in, body_fat_pct,
                 hand_length_in, hand_width_in)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_key, year) DO UPDATE SET
                height_wo_shoes_in=excluded.height_wo_shoes_in,
                height_w_shoes_in =excluded.height_w_shoes_in,
                wingspan_in       =excluded.wingspan_in,
                weight_lbs        =excluded.weight_lbs,
                std_reach_in      =excluded.std_reach_in,
                body_fat_pct      =excluded.body_fat_pct,
                hand_length_in    =excluded.hand_length_in,
                hand_width_in     =excluded.hand_width_in,
                updated_at        =strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (m.subject_key, m.year, m.height_wo_shoes_in, m.height_w_shoes_in,
             m.wingspan_in, m.weight_lbs, m.std_reach_in, m.body_fat_pct,
             m.hand_length_in, m.hand_width_in),
        )
    for d in d_rows:
        cur.execute(
            """
            INSERT INTO combine_drills
                (subject_key, year, lane_agility_sec, shuttle_sec,
                 three_quarter_sprint_sec, standing_vert_in, max_vert_in,
                 bench_reps)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject_key, year) DO UPDATE SET
                lane_agility_sec        =excluded.lane_agility_sec,
                shuttle_sec             =excluded.shuttle_sec,
                three_quarter_sprint_sec=excluded.three_quarter_sprint_sec,
                standing_vert_in        =excluded.standing_vert_in,
                max_vert_in             =excluded.max_vert_in,
                bench_reps              =excluded.bench_reps,
                updated_at              =strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (d.subject_key, d.year, d.lane_agility_sec, d.shuttle_sec,
             d.three_quarter_sprint_sec, d.standing_vert_in, d.max_vert_in,
             d.bench_reps),
        )
    return len(m_rows), len(d_rows)


# ---------------------------------------------------------------------------
# Convenience: refresh every year of combine data we care about.
# ---------------------------------------------------------------------------
def refresh_all_years(
    conn,
    years: tuple[int, ...] | None = None,
    *,
    force_refresh: bool = False,
) -> int:
    """Pull every listed year of combine data into SQLite.

    Returns the total number of (measurement + drill) rows written.
    Years whose endpoint 404s are skipped silently.
    """
    if years is None:
        years = config.NBA_COMBINE_SEASON_YEARS
    total = 0
    for yr in years:
        try:
            anthro = fetch_anthro(yr, force_refresh=force_refresh)
            drills = fetch_drills(yr, force_refresh=force_refresh)
        except Exception as exc:  # noqa: BLE001
            log.warning("combine %d failed: %s", yr, exc)
            continue
        n_m, n_d = upsert_combine(conn, yr, anthro, drills)
        log.info("combine %d: %d anthro + %d drill rows", yr, n_m, n_d)
        total += n_m + n_d
        time.sleep(0.25)
    return total
