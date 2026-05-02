"""
Apply the loaded formula registry to a single prospect row.

Takes a prospect dict (from ``prospects`` joined with ``prospect_stats`` +
optional ``combine_measurements`` / ``combine_drills``) and returns a dict of
``{attribute: rating_int}`` plus a ``provenance`` dict describing where each
number came from (scraped / combine-override / formula / manual-override).

Key responsibilities:

- Expand raw stat columns into the same per-36 / usage / position features
  the calibration corpus used (must stay in sync with
  :mod:`src.calibration.build_corpus`).
- Apply the combine override path: if ``c_speed_2k`` / ``c_agility_2k`` /
  ``c_vertical_2k`` are present on the prospect, they win over the regressed
  ``speed_2k`` / ``agility_2k`` / ``vertical_2k`` formulas.
- Apply the league-3pt-line penalty to ``three_point_shot_2k`` for non-NBA
  leagues (NCAA / FIBA-line / HS).
- Apply the height-delta reconciliation: if ``height_wo_shoes_in`` is present
  (post-combine) but ``height_in`` isn't, derive ``height_in`` via the
  piecewise ``height_delta`` formula.
- Respect manual overrides: ``manual_override_json`` (parsed to dict) takes
  precedence over both computed and combine-override values.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from .. import config
from ..logger import get_logger
from .registry import FormulaRegistry

log = get_logger("formulas.apply")

# Workbook-derived attributes (see :func:`excel_2026_class.calculate_excel_2026_ratings`).
_WORKBOOK_DERIVED_RATING_KEYS: frozenset[str] = frozenset({
    "post_hook_2k",
    "post_fade_2k",
    "intangibles_2k",
    "durability_2k",
})

_WORKBOOK_DERIVED_ALL_KEYS: frozenset[str] = (
    _WORKBOOK_DERIVED_RATING_KEYS | frozenset({"potential"})
)


def _normalize_engine(engine: str) -> str:
    """Map UI / alias strings to :data:`config.RATING_ENGINES` slugs."""
    raw = (engine or "").strip().lower().replace(" ", "_").replace("-", "_")
    if raw in config.RATING_ENGINES:
        return raw
    if "excel" in raw and "2026" in raw:
        return "excel_2026_class"
    if raw in ("yaml", "linear", "regression", "calibrated"):
        return "calibrated"
    return "calibrated"


def _ensure_output_keys(computed: dict[str, Any]) -> None:
    """Guarantee all :data:`config.RATING_ATTRIBUTES` keys plus ``potential`` exist."""
    for a in config.RATING_ATTRIBUTES:
        computed.setdefault(a, None)
    computed.setdefault("potential", None)


def _merge_workbook_derived(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
    *,
    which: frozenset[str],
) -> None:
    """Apply :func:`excel_2026_class.calculate_excel_2026_ratings`; tag ``excel_2026+derived``."""
    from .excel_2026_class import calculate_excel_2026_ratings

    merged: dict[str, Any] = dict(prospect)
    merged.update(computed)
    derived = calculate_excel_2026_ratings(merged)
    for k in which:
        if k not in derived:
            continue
        computed[k] = derived[k]
        provenance.mark(k, "excel_2026+derived")


def _apply_calibrated_derived_fallbacks(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
    registry: FormulaRegistry,
    feats: dict[str, float],
) -> None:
    """YAML when present; else workbook formulas for five derived fields + ``potential``."""
    from .excel_2026_class import calculate_excel_2026_ratings

    merged: dict[str, Any] = dict(prospect)
    merged.update(computed)
    derived = calculate_excel_2026_ratings(merged)
    feat_all = dict(feats)
    feat_all.update({
        k: float(v) for k, v in computed.items()
        if v is not None and k in config.RATING_ATTRIBUTES
    })

    for k in _WORKBOOK_DERIVED_RATING_KEYS:
        if registry.get(k) is not None:
            val = registry.evaluate(k, feat_all)
            if val is not None:
                computed[k] = int(val)
                provenance.mark(k, "formula")
                continue
        computed[k] = derived[k]
        provenance.mark(k, "excel_2026+derived")

    if registry.get("potential") is not None:
        pot = registry.evaluate(
            "potential",
            feat_all,
        )
        if pot is not None:
            computed["potential"] = int(pot)
            provenance.mark("potential", "formula")
        else:
            computed["potential"] = derived["potential"]
            provenance.mark("potential", "excel_2026+derived")
    else:
        computed["potential"] = derived["potential"]
        provenance.mark("potential", "excel_2026+derived")


# ---------------------------------------------------------------------------
# Per-league 3-point penalties (inches of line difference, roughly).
# Rough v1 magnitudes; the YAML for three_point_shot_2k can re-fit these.
# NBA line 22'1.75" corners / 23'9" wings. NCAA/Euroleague/FIBA all 22'1.75".
# High-school varies; G-League is NBA.
# ---------------------------------------------------------------------------
LEAGUE_3PT_PENALTY: dict[str, float] = {
    config.LEAGUE_NCAA: -2.5,
    config.LEAGUE_EUROLEAGUE: -2.0,
    config.LEAGUE_NBL: -1.0,
    config.LEAGUE_NZNBL: -1.5,
    config.LEAGUE_GLEAGUE: 0.0,
    config.LEAGUE_HS: -4.0,
    config.LEAGUE_OTHER: -3.0,
    config.LEAGUE_NBA: 0.0,
}


# ---------------------------------------------------------------------------
@dataclass
class Provenance:
    """Where each attribute's value came from. Used for cell-color coding."""

    by_attribute: dict[str, str] = field(default_factory=dict)

    def mark(self, attribute: str, source: str) -> None:
        self.by_attribute[attribute] = source

    def to_dict(self) -> dict[str, str]:
        return dict(self.by_attribute)


# ---------------------------------------------------------------------------
def _pos_bucket(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        return "SF"
    s = raw.upper()
    if "C" in s and "SF" not in s and "PF" not in s:
        return "C"
    if "PF" in s or "PF/C" in s:
        return "PF"
    if "PG" in s:
        return "PG"
    if "SG" in s:
        return "SG"
    return "SF"


def _safe_float(val: Any) -> float | None:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if np.isnan(f) or np.isinf(f):
        return None
    return f


# ---------------------------------------------------------------------------
def build_feature_vector(prospect: Mapping[str, Any],
                         registry: FormulaRegistry) -> dict[str, float]:
    """Expand a raw prospect record into the feature dict expected by
    :mod:`src.calibration.build_corpus`."""
    f: dict[str, float] = {}

    # -- physicals ------------------------------------------------------
    height_in = _safe_float(prospect.get("height_in"))
    height_wo = _safe_float(prospect.get("height_wo_shoes_in"))
    if height_in is None and height_wo is not None:
        bucket = _pos_bucket(prospect.get("pos"))
        height_in = height_wo + registry.height_delta(bucket)

    weight_lbs = _safe_float(prospect.get("weight_lbs"))
    wingspan_in = _safe_float(prospect.get("wingspan_in")) or \
                   _safe_float(prospect.get("combine_wingspan_in"))

    if height_in is not None:
        f["height_in"] = height_in
    if weight_lbs is not None:
        f["weight_lbs"] = weight_lbs
    if wingspan_in is not None:
        f["wingspan_in"] = wingspan_in
    if height_in and weight_lbs:
        f["bmi"] = weight_lbs / (height_in ** 2) * 703
    if wingspan_in and height_in:
        f["wingspan_minus_height"] = wingspan_in - height_in

    for k, v in _prospect_scouting_01(prospect).items():
        f[k] = v

    for name in ("std_reach_in", "max_vert_in", "standing_vert_in",
                 "lane_agility_sec", "shuttle_sec", "three_quarter_sprint_sec",
                 "body_fat_pct"):
        v = _safe_float(prospect.get(name))
        if v is not None:
            f[name] = v

    # -- raw stats ------------------------------------------------------
    for col in config.STAT_COLUMNS:
        v = _safe_float(prospect.get(col))
        if v is not None:
            f[col] = v

    mp_per_game = f.get("min")
    if mp_per_game and mp_per_game > 0:
        factor = 36.0 / mp_per_game
        for col in ("fg3m", "fg3a", "fta", "ast", "tov", "stl", "blk",
                    "oreb", "dreb", "reb"):
            if col in f:
                f[f"{col}_per36"] = f[col] * factor
        # Usage proxy
        fga = f.get("fga", 0.0)
        fta = f.get("fta", 0.0)
        tov = f.get("tov", 0.0)
        f["usg_proxy"] = (fga + 0.44 * fta + tov) * factor

    # -- position one-hots + bucket ------------------------------------
    bucket = _pos_bucket(prospect.get("pos"))
    f["pos_bucket"] = bucket  # type: ignore[assignment]
    for p in ("PG", "SG", "SF", "PF", "C"):
        f[f"is_{p.lower()}"] = 1.0 if bucket == p else 0.0

    return f


# ---------------------------------------------------------------------------
def _prospect_scouting_01(
    prospect: Mapping[str, Any],
) -> dict[str, float]:
    """0–1 hints from :attr:`prospects.scouting_physical_json` (AI or hand-edited)."""
    raw = prospect.get("scouting_physical_json")
    if not raw:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except (AttributeError, TypeError):
            return {}
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            blob = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    elif isinstance(raw, dict):
        blob = raw
    else:
        return {}
    if not isinstance(blob, dict):
        return {}
    out: dict[str, float] = {}
    for key in (
        "strength_01", "leaping_01", "athleticism_01", "stamina_01",
    ):
        if key not in blob:
            continue
        try:
            v = float(blob[key])
        except (TypeError, ValueError):
            continue
        out[f"scouting_{key}"] = max(0.0, min(1.0, v))
    return out


def _rtg_from_01(x01: float) -> int:
    return int(max(25, min(99, round(25.0 + 74.0 * x01))))


def _nudge_to_scouting_01(
    current: int,
    x01: float | None,
    *,
    weight: float = 0.52,
) -> int:
    if x01 is None:
        return current
    target = _rtg_from_01(x01)
    return int(round((1.0 - weight) * current + weight * target))


def scouting_proxy_source_tags(
    prospect: Mapping[str, Any],
) -> dict[str, str]:
    """Map rating attributes to ``scouting_proxy`` for UI if :func:`_apply_scouting_physical_proxy` would
    nudge them (no combine / bench where applicable)."""
    s01 = _prospect_scouting_01(prospect)
    if not s01:
        return {}
    st = s01.get("scouting_strength_01")
    leap = s01.get("scouting_leaping_01")
    ath = s01.get("scouting_athleticism_01")
    stm = s01.get("scouting_stamina_01")

    def has_time2(path: str) -> bool:
        v = _safe_float(prospect.get(path))
        return v is not None

    def c_set2(col: str) -> bool:
        v = prospect.get(col)
        if v is None or v is False:
            return False
        try:
            ival = int(round(float(v)))
        except (TypeError, ValueError):
            return False
        return 25 <= ival <= 99

    tags: dict[str, str] = {}
    if st is not None and prospect.get("bench_reps") is None:
        tags["strength_2k"] = "scouting_proxy"
    if (leap is not None
            and not c_set2("c_vertical_2k")
            and _safe_float(prospect.get("max_vert_in")) is None):
        tags["vertical_2k"] = "scouting_proxy"
    if ath is not None:
        if not c_set2("c_speed_2k") and not has_time2("three_quarter_sprint_sec"):
            tags["speed_2k"] = "scouting_proxy"
        if (not c_set2("c_agility_2k")
                and not has_time2("lane_agility_sec")
                and not has_time2("shuttle_sec")):
            tags["agility_2k"] = "scouting_proxy"
        if (not c_set2("c_speed_with_ball_2k")
                and not has_time2("three_quarter_sprint_sec")):
            tags["speed_with_ball_2k"] = "scouting_proxy"
    if stm is not None and not any(
        has_time2(x) for x in (
            "max_vert_in", "lane_agility_sec", "three_quarter_sprint_sec",
        )
    ):
        tags["stamina_2k"] = "scouting_proxy"
    return tags


def _apply_scouting_physical_proxy(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
) -> None:
    """Blend key physical 2K ratings toward AI 0–1 hints when real combine
    data is not available (no drill numbers / no ``c_*`` overrides for that
    area). See :class:`src.scrapers.scouting.ScoutingSynthesis` features.
    """
    s01 = _prospect_scouting_01(prospect)
    if not s01:
        return

    st = s01.get("scouting_strength_01")
    leap = s01.get("scouting_leaping_01")
    ath = s01.get("scouting_athleticism_01")
    stm = s01.get("scouting_stamina_01")

    def has_time(path: str) -> bool:
        v = _safe_float(prospect.get(path))
        return v is not None

    def c_set(col: str) -> bool:
        v = prospect.get(col)
        if v is None or v is False:
            return False
        try:
            ival = int(round(float(v)))
        except (TypeError, ValueError):
            return False
        return 25 <= ival <= 99

    if (st is not None and "strength_2k" in computed
            and prospect.get("bench_reps") is None):
        before = computed["strength_2k"]
        after = _nudge_to_scouting_01(before, st)
        if after != before:
            computed["strength_2k"] = after
            provenance.mark("strength_2k", "scouting_proxy")

    if (leap is not None and "vertical_2k" in computed
            and not c_set("c_vertical_2k")
            and _safe_float(prospect.get("max_vert_in")) is None):
        b = computed["vertical_2k"]
        a = _nudge_to_scouting_01(b, leap)
        if a != b:
            computed["vertical_2k"] = a
            provenance.mark("vertical_2k", "scouting_proxy")

    if ath is not None:
        if ("speed_2k" in computed and not c_set("c_speed_2k")
                and not has_time("three_quarter_sprint_sec")):
            b, a = computed["speed_2k"], _nudge_to_scouting_01(
                computed["speed_2k"], ath,
            )
            if a != b:
                computed["speed_2k"] = a
                provenance.mark("speed_2k", "scouting_proxy")
        if ("agility_2k" in computed and not c_set("c_agility_2k")
                and not has_time("lane_agility_sec")
                and not has_time("shuttle_sec")):
            b, a = computed["agility_2k"], _nudge_to_scouting_01(
                computed["agility_2k"], ath,
            )
            if a != b:
                computed["agility_2k"] = a
                provenance.mark("agility_2k", "scouting_proxy")
        if ("speed_with_ball_2k" in computed and not c_set(
                "c_speed_with_ball_2k")
                and not has_time("three_quarter_sprint_sec")):
            b, a = computed["speed_with_ball_2k"], _nudge_to_scouting_01(
                computed["speed_with_ball_2k"], ath,
            )
            if a != b:
                computed["speed_with_ball_2k"] = a
                provenance.mark("speed_with_ball_2k", "scouting_proxy")

    has_drill = any(
        has_time(x) for x in (
            "max_vert_in", "lane_agility_sec", "three_quarter_sprint_sec",
        )
    )
    if stm is not None and "stamina_2k" in computed and not has_drill:
        b = computed["stamina_2k"]
        a = _nudge_to_scouting_01(b, stm, weight=0.45)
        if a != b:
            computed["stamina_2k"] = a
            provenance.mark("stamina_2k", "scouting_proxy")


def _apply_combine_override(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
) -> None:
    """If the prospect has ``c_speed_2k`` / ``c_agility_2k`` / ``c_vertical_2k``
    populated, override the regression output."""
    mapping = {
        "c_speed_2k": "speed_2k",
        "c_agility_2k": "agility_2k",
        "c_vertical_2k": "vertical_2k",
        "c_speed_with_ball_2k": "speed_with_ball_2k",
    }
    for src_col, target in mapping.items():
        val = prospect.get(src_col)
        if val is None:
            continue
        try:
            ival = int(round(float(val)))
        except (TypeError, ValueError):
            continue
        computed[target] = max(25, min(99, ival))
        provenance.mark(target, "combine")


def _apply_league_3pt_penalty(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
) -> None:
    if "three_point_shot_2k" not in computed:
        return
    league = (prospect.get("league") or config.LEAGUE_OTHER).lower()
    penalty = LEAGUE_3PT_PENALTY.get(league, 0.0)
    if not penalty:
        return
    before = computed["three_point_shot_2k"]
    after = max(25, min(99, int(round(before + penalty))))
    computed["three_point_shot_2k"] = after
    provenance.mark("three_point_shot_2k", f"formula+league({league}:{penalty:+.1f})")


def _nudge_overall_for_missing_stats(
    computed: dict[str, Any],
    provenance: Provenance,
    prospect: Mapping[str, Any],
    feats: dict[str, float],
) -> None:
    """Blend ``overall_2k`` with draft position when per-game stats are absent.

    Trained linear formulas assume NBA-level ``gp``/``min``/shooting; without
    them, all features that need per36 stats are ~0, so the model collapses
    near each attribute intercept. A soft rank-based target keeps the overall
    in a more scout-plausible range until CBB stats are ingested.
    """
    if "overall_2k" not in computed or computed["overall_2k"] is None:
        return
    gp = _safe_float(prospect.get("gp"))
    if gp is not None and gp > 0 and feats.get("min", 0) > 0:
        return
    raw = prospect.get("espn_rank")
    if raw is None:
        return
    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
        return
    try:
        rank = int(raw)
    except (TypeError, ValueError):
        return
    if not 1 <= rank <= 200:
        return
    # Target OVR: top of board ~ high 80s, mid-first ~ 80, late first ~ 75, undrafted tail ~ 68
    target = 89.0 - 0.22 * float(rank)
    target = max(64.0, min(90.0, target))
    old = float(computed["overall_2k"])
    blended = 0.42 * old + 0.58 * target
    out = int(round(max(25.0, min(99.0, blended))))
    if out == computed["overall_2k"]:
        return
    computed["overall_2k"] = out
    provenance.mark("overall_2k", f"formula+rank_nudge(r{rank})")


def _parse_override_json(raw: Any) -> dict[str, int]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        blob = raw
    else:
        try:
            blob = json.loads(raw)
        except (TypeError, ValueError):
            log.warning("manual_override_json is not valid JSON: %r", raw)
            return {}
    out: dict[str, int] = {}
    for k, v in blob.items():
        try:
            out[str(k)] = int(round(float(v)))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
def apply_formulas(
    player_data: Mapping[str, Any],
    engine: str,
    *,
    registry: FormulaRegistry,
    manual_overrides: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], Provenance]:
    """Unified rating orchestrator for both engines.

    Returns a dict with every :data:`config.RATING_ATTRIBUTES` key and
    ``potential`` (values may be ``None`` before manual fill). Workbook-derived
    fields use :func:`excel_2026_class.calculate_excel_2026_ratings` when the
    Excel engine runs, or as a YAML fallback for Calibrated when no formula
    exists.
    """
    eng = _normalize_engine(str(engine))
    feats = build_feature_vector(player_data, registry)
    computed: dict[str, Any] = {}
    provenance = Provenance()

    if eng == "excel_2026_class":
        from .excel_2026_class import apply_excel_2026_to_prospect

        computed, _prov = apply_excel_2026_to_prospect(player_data, registry)
        for k, v in _prov.items():
            provenance.mark(k, v)
        _apply_combine_override(computed, provenance, player_data)
        _apply_scouting_physical_proxy(computed, provenance, player_data)
        overall_features: dict[str, float] = dict(feats)
        overall_features.update({
            k: float(v) for k, v in computed.items()
            if k != "overall_2k" and v is not None
        })
        overall = registry.evaluate("overall_2k", overall_features)
        if overall is not None:
            computed["overall_2k"] = int(overall)
            provenance.mark("overall_2k", "excel_2026+overall_yaml")
        # Refresh workbook derivations after combine / scouting / overall YAML.
        _merge_workbook_derived(
            computed, provenance, player_data,
            which=_WORKBOOK_DERIVED_ALL_KEYS,
        )
    else:
        for attr in config.RATING_ATTRIBUTES:
            if attr == "overall_2k":
                continue
            if attr in _WORKBOOK_DERIVED_RATING_KEYS:
                continue
            val = registry.evaluate(attr, feats)
            if val is None:
                continue
            computed[attr] = int(val)
            provenance.mark(attr, "formula")

        _apply_combine_override(computed, provenance, player_data)
        _apply_scouting_physical_proxy(computed, provenance, player_data)
        _apply_league_3pt_penalty(computed, provenance, player_data)

        overall_features = dict(feats)
        overall_features.update({
            k: float(v) for k, v in computed.items() if v is not None
        })
        overall = registry.evaluate("overall_2k", overall_features)
        if overall is not None:
            computed["overall_2k"] = int(overall)
            provenance.mark("overall_2k", "formula")

        _nudge_overall_for_missing_stats(
            computed, provenance, player_data, feats)

        _apply_calibrated_derived_fallbacks(
            computed, provenance, player_data, registry, feats)

    overrides = dict(manual_overrides or {})
    overrides.update(_parse_override_json(player_data.get("manual_override_json")))
    for k, v in overrides.items():
        if k not in config.RATING_ATTRIBUTES and k != "potential":
            continue
        try:
            if k == "potential":
                computed[k] = int(round(float(v)))
            else:
                computed[k] = max(25, min(99, int(v)))
            provenance.mark(k, "manual")
        except (TypeError, ValueError):
            continue

    _ensure_output_keys(computed)
    return computed, provenance


def apply_to_prospect(
    prospect: Mapping[str, Any],
    registry: FormulaRegistry,
    *,
    manual_overrides: Mapping[str, int] | None = None,
) -> tuple[dict[str, Any], Provenance]:
    """Compute every 2K attribute for a single prospect record.

    ``prospect`` must be a dict-like with a ``league`` key and whatever stats
    / physical columns are available. Missing features are treated as 0.

    Returns ``(ratings, provenance)`` where ratings includes every
    :data:`config.RATING_ATTRIBUTES` key plus ``potential`` (values may be
    ``None``). Provenance tags each attribute with its source.
    """
    return apply_formulas(
        prospect,
        config.get_rating_engine(),
        registry=registry,
        manual_overrides=manual_overrides,
    )
