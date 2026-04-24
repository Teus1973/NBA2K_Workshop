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
from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from .. import config
from ..logger import get_logger
from .registry import FormulaRegistry

log = get_logger("formulas.apply")


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
def _apply_combine_override(
    computed: dict[str, int],
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
    computed: dict[str, int],
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
def apply_to_prospect(
    prospect: Mapping[str, Any],
    registry: FormulaRegistry,
    *,
    manual_overrides: Mapping[str, int] | None = None,
) -> tuple[dict[str, int], Provenance]:
    """Compute every 2K attribute for a single prospect record.

    ``prospect`` must be a dict-like with a ``league`` key and whatever stats
    / physical columns are available. Missing features are treated as 0.

    Returns ``(ratings, provenance)`` where ratings is
    ``{attribute: int in [25,99]}`` and provenance tags each attribute with
    its source (``formula`` / ``combine`` / ``manual``).
    """
    feats = build_feature_vector(prospect, registry)
    computed: dict[str, int] = {}
    provenance = Provenance()

    # 1. Regress every attribute defined in RATING_ATTRIBUTES.
    for attr in config.RATING_ATTRIBUTES:
        if attr == "overall_2k":
            continue  # computed at the very end
        val = registry.evaluate(attr, feats)
        if val is None:
            continue
        computed[attr] = int(val)
        provenance.mark(attr, "formula")

    # 2. Combine overrides win over regression output.
    _apply_combine_override(computed, provenance, prospect)

    # 3. League 3pt-line penalty.
    _apply_league_3pt_penalty(computed, provenance, prospect)

    # 4. Overall (uses the just-computed per-attribute ratings as features).
    overall_features: dict[str, float] = dict(feats)
    overall_features.update({k: float(v) for k, v in computed.items()})
    overall = registry.evaluate("overall_2k", overall_features)
    if overall is not None:
        computed["overall_2k"] = int(overall)
        provenance.mark("overall_2k", "formula")

    # 5. Manual overrides trump everything.
    overrides = dict(manual_overrides or {})
    overrides.update(_parse_override_json(prospect.get("manual_override_json")))
    for k, v in overrides.items():
        if k not in config.RATING_ATTRIBUTES:
            continue
        try:
            computed[k] = max(25, min(99, int(v)))
            provenance.mark(k, "manual")
        except (TypeError, ValueError):
            continue

    return computed, provenance
