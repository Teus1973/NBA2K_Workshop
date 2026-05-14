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
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nba_api.stats.endpoints import (
    draftcombinedrillresults,
    draftcombineplayeranthro,
    draftcombinestats,
)
from nba_api.stats.library.parameters import LeagueID

from .. import audit, config
from ..formatting import normalize_full_name
from ..logger import get_logger
from ..utils import KNOWN_NBA_IDS

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


def _cache_json_is_empty(cache_path: Path | str) -> bool:
    """True if cached JSON is ``[]`` / ``{}`` so we refetch (avoids sticky empty 2026)."""
    try:
        raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, json.JSONDecodeError):
        return False
    return raw == [] or raw == {}


def _dedupe_nba_g_league_frames(*dfs: pd.DataFrame) -> pd.DataFrame:
    """Prefer NBA (first frame) over G-League when the same player appears twice."""
    parts = []
    for i, df in enumerate(dfs):
        if df is None or df.empty:
            continue
        parts.append(df.assign(_src_rank=i))
    if not parts:
        return pd.DataFrame()
    all_df = pd.concat(parts, ignore_index=True)
    pids = pd.to_numeric(all_df.get("PLAYER_ID"), errors="coerce")
    tmp = all_df.get("TEMP_PLAYER_ID")
    if tmp is None:
        tmp_s = pd.Series([""] * len(all_df))
    else:
        tmp_s = tmp.astype(str).replace({"nan": "", "None": ""})
    pids = pids.fillna(0).astype(int)
    keys = np.where(pids > 0, "id:" + pids.astype(str), "tmp:" + tmp_s)
    all_df = all_df.assign(_dedupe_k=keys)
    all_df = all_df.sort_values("_src_rank").drop_duplicates(
        "_dedupe_k", keep="first")
    return all_df.drop(columns=["_src_rank", "_dedupe_k"], errors="ignore")


def _fetch_anthro_live_year(year: int) -> pd.DataFrame:
    """Call DraftCombinePlayerAnthro for NBA + G-League; merge player rows."""

    def one_league(league_id: str, label: str) -> pd.DataFrame:
        def call():
            ep = draftcombineplayeranthro.DraftCombinePlayerAnthro(
                league_id=league_id,
                season_year=_season_year_str(year),
            )
            return ep.get_data_frames()[0]

        try:
            return _retry(call, name=f"DraftCombinePlayerAnthro({label})")
        except Exception as exc:  # noqa: BLE001
            log.warning("DraftCombinePlayerAnthro %s %s failed: %s",
                        year, label, exc)
            return pd.DataFrame()

    nba_df = one_league(LeagueID.nba, "nba")
    gleague_df = one_league(LeagueID.g_league, "gleague")
    return _dedupe_nba_g_league_frames(nba_df, gleague_df)


def _fetch_drills_live_year(year: int) -> pd.DataFrame:
    def one_league(league_id: str, label: str) -> pd.DataFrame:
        def call():
            ep = draftcombinedrillresults.DraftCombineDrillResults(
                league_id=league_id,
                season_year=_season_year_str(year),
            )
            return ep.get_data_frames()[0]

        try:
            return _retry(call, name=f"DraftCombineDrillResults({label})")
        except Exception as exc:  # noqa: BLE001
            log.warning("DraftCombineDrillResults %s %s failed: %s",
                        year, label, exc)
            return pd.DataFrame()

    nba_df = one_league(LeagueID.nba, "nba")
    gleague_df = one_league(LeagueID.g_league, "gleague")
    return _dedupe_nba_g_league_frames(nba_df, gleague_df)


# ---------------------------------------------------------------------------
# Anthro / drills fetchers
# ---------------------------------------------------------------------------
def fetch_anthro(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """DraftCombinePlayerAnthro for NBA + G-League for the given draft year."""
    cache_path = config.CACHE_NBA / f"combine_anthro_{year}.json"
    use_cache = (
        cache_path.is_file()
        and not force_refresh
        and not _cache_json_is_empty(cache_path)
    )
    if use_cache:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    df = _fetch_anthro_live_year(year)
    cache_path.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_combine", entity_type="combine",
                    note=f"anthro {year}: {len(df)} rows (nba+gleague)")
    return df


def fetch_drills(year: int, *, force_refresh: bool = False) -> pd.DataFrame:
    """DraftCombineDrillResults for NBA + G-League for the given draft year."""
    cache_path = config.CACHE_NBA / f"combine_drills_{year}.json"
    use_cache = (
        cache_path.is_file()
        and not force_refresh
        and not _cache_json_is_empty(cache_path)
    )
    if use_cache:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return pd.DataFrame(data)

    df = _fetch_drills_live_year(year)
    cache_path.write_text(df.to_json(orient="records"), encoding="utf-8")
    audit.log_event(action="scrape_nba_combine", entity_type="combine",
                    note=f"drills {year}: {len(df)} rows (nba+gleague)")
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
    use_cache = (
        cache_path.is_file()
        and not force_refresh
        and not _cache_json_is_empty(cache_path)
    )
    if use_cache:
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


# Jr / Sr / Roman numerals (combine vs big-board punctuation often mismatches).
_NAME_GEN_SUFFIX_RE = re.compile(
    r",?\s*\b(?:jr\.?|sr\.?|ii|iii|iv|v)\s*$",
    re.IGNORECASE,
)

_FIRST_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "nate": ("nathaniel",),
    "nathaniel": ("nate",),
}


def _strip_generation_suffix(norm_name: str) -> str:
    """Drop trailing generational markers from normalize_full_name output."""
    s = norm_name.strip()
    if not s:
        return s
    prev = None
    while prev != s:
        prev = s
        s = _NAME_GEN_SUFFIX_RE.sub("", s).strip()
        s = re.sub(r"\s+", " ", s)
    return s


def _name_alias_keys(norm_name: str) -> list[str]:
    """Alternate first-name spellings seen between boards and NBA combine feeds."""
    n = norm_name.strip()
    if not n:
        return []
    parts = n.split()
    if len(parts) < 2:
        return []
    aliases = _FIRST_NAME_ALIASES.get(parts[0], ())
    return [" ".join((alias, *parts[1:])) for alias in aliases]


def _prospect_name_bucket_keys(norm_prospect: str) -> list[str]:
    """Indexing keys used to match Draft Combine ``PLAYER_NAME`` strings."""
    n = norm_prospect.strip()
    if not n:
        return []
    base = _strip_generation_suffix(n)
    keys: list[str] = []
    for candidate in (n, base):
        if not candidate:
            continue
        keys.append(candidate)
        keys.extend(_name_alias_keys(candidate))
    return list(dict.fromkeys(keys))


def _combine_name_resolve_slug(
    slug_by_key: defaultdict[str, list[str]], normalized_combine_name: str,
) -> str | None:
    """Resolve a Combine display name → unique prospect slug using exact + suffix-stripped lookups."""
    c = normalized_combine_name.strip()
    if not c:
        return None
    keys: list[str] = []
    for candidate in (c, _strip_generation_suffix(c)):
        if not candidate:
            continue
        keys.append(candidate)
        keys.extend(_name_alias_keys(candidate))
    for key in dict.fromkeys(keys):
        if not key:
            continue
        cands = slug_by_key[key]
        if len(cands) == 1:
            return cands[0]
    return None


def _nba_player_id_from_row(row: dict[str, Any]) -> int | None:
    raw = row.get("PLAYER_ID")
    try:
        if raw is None or raw == "":
            return None
        v = int(float(raw))
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _pid_from_nba_subject_key(subject_key: str) -> int | None:
    if not subject_key.startswith("nba:"):
        return None
    rest = subject_key[4:].strip()
    try:
        v = int(float(rest))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _prospect_slug_by_nba_id(
    conn, anthro_df: pd.DataFrame,
) -> dict[int, str]:
    """Map NBA ``PLAYER_ID`` → prospect slug.

    ``KNOWN_NBA_IDS`` (slug → id) overrides weak text matches first. Rows then use
    ``prospects.nba_id``. When that is missing but ``PLAYER_NAME`` aligns
    (including Jr./III vs bare last name), we persist ``PLAYER_ID`` on the slug.
    """
    cur = conn.cursor()
    # Hard-pin workshop slugs ↔ NBA rookie/combine PLAYER_ID regardless of fuzzy name drift.
    for slug_key, nid in KNOWN_NBA_IDS.items():
        cur.execute(
            "UPDATE prospects SET nba_id=? WHERE slug=?",
            (int(nid), str(slug_key)),
        )

    cur.execute("SELECT slug, full_name, nba_id FROM prospects")
    rows = cur.fetchall()
    by_id: dict[int, str] = {}
    slug_by_key: defaultdict[str, list[str]] = defaultdict(list)
    for r in rows:
        slug = str(r["slug"])
        fn = (r["full_name"] or "").strip()
        if fn:
            n = normalize_full_name(fn)
            for k in _prospect_name_bucket_keys(n):
                slug_by_key[k].append(slug)
        if r["nba_id"] is not None:
            try:
                by_id[int(r["nba_id"])] = slug
            except (TypeError, ValueError):
                pass
    for _, row in anthro_df.iterrows():
        rd = row.to_dict()
        pid = _nba_player_id_from_row(rd)
        if pid is not None and pid in by_id:
            continue

        pname = rd.get("PLAYER_NAME")
        if not pname:
            fn, ln = rd.get("FIRST_NAME"), rd.get("LAST_NAME")
            if fn or ln:
                pname = f"{fn or ''} {ln or ''}".strip()
        if not pname:
            continue

        slug = _combine_name_resolve_slug(
            slug_by_key,
            normalize_full_name(str(pname).strip()),
        )
        if slug is None:
            continue
        # Mirror + ``UPDATE prospects.nba_id`` require a Combine ``PLAYER_ID``.
        if pid is None:
            continue

        by_id.setdefault(pid, slug)
        cur.execute(
            "UPDATE prospects SET nba_id=? WHERE slug=? AND nba_id IS NULL",
            (pid, slug),
        )
    return by_id


def _mirror_combine_for_prospects(
    measurements: list[CombineMeasurement],
    drills: list[CombineDrill],
    slug_by_pid: dict[int, str],
) -> tuple[list[CombineMeasurement], list[CombineDrill]]:
    extra_m: list[CombineMeasurement] = []
    for m in measurements:
        pid = _pid_from_nba_subject_key(m.subject_key)
        if pid is None:
            continue
        slug = slug_by_pid.get(pid)
        if not slug:
            continue
        extra_m.append(CombineMeasurement(
            subject_key=f"prospect:{slug}",
            year=m.year,
            height_wo_shoes_in=m.height_wo_shoes_in,
            height_w_shoes_in=m.height_w_shoes_in,
            wingspan_in=m.wingspan_in,
            weight_lbs=m.weight_lbs,
            std_reach_in=m.std_reach_in,
            body_fat_pct=m.body_fat_pct,
            hand_length_in=m.hand_length_in,
            hand_width_in=m.hand_width_in,
        ))
    extra_d: list[CombineDrill] = []
    for d in drills:
        pid = _pid_from_nba_subject_key(d.subject_key)
        if pid is None:
            continue
        slug = slug_by_pid.get(pid)
        if not slug:
            continue
        extra_d.append(CombineDrill(
            subject_key=f"prospect:{slug}",
            year=d.year,
            lane_agility_sec=d.lane_agility_sec,
            shuttle_sec=d.shuttle_sec,
            three_quarter_sprint_sec=d.three_quarter_sprint_sec,
            standing_vert_in=d.standing_vert_in,
            max_vert_in=d.max_vert_in,
            bench_reps=d.bench_reps,
            c_speed_2k=d.c_speed_2k,
            c_speed_with_ball_2k=d.c_speed_with_ball_2k,
            c_vertical_2k=d.c_vertical_2k,
            c_agility_2k=d.c_agility_2k,
        ))
    return measurements + extra_m, drills + extra_d


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

    Rows are also written as ``prospect:{slug}`` when ``PLAYER_ID`` matches
    ``prospects.nba_id`` or a unique ``full_name`` match on the big board.

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

    slug_by_pid = _prospect_slug_by_nba_id(conn, anthro_df)
    m_rows, d_rows = _mirror_combine_for_prospects(
        m_rows, d_rows, slug_by_pid)

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
    audit.log_event(
        action="scrape_nba_combine",
        entity_type="combine",
        note=f"refresh_all_years rows={total} n_years={len(years)} "
             f"force_refresh={force_refresh}",
    )
    return total
