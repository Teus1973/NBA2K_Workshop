"""
Fit v1 linear-regression formulas for every 2K26 attribute and write them as
YAML files under ``data/formulas/``.

Each YAML file has the exact shape documented in ``PLAN.md`` section 4.3::

    attribute: strength_2k
    version: 1
    type: linear_regression
    features:
      - {name: weight_lbs, coef: 0.42}
      - {name: height_in,  coef: 0.18}
      - {name: bmi,        coef: -0.05}
    intercept: 12.0
    clamp: [25, 99]
    notes: "Fit on N current NBA players. R^2 = ...  MAE = ..."

Usage::

    python -m src.calibration.fit_formulas

This rebuilds every YAML under ``data/formulas/`` and writes a matching row
into the ``formulas`` SQLite table (version-bumped per rerun).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from sklearn.linear_model import LinearRegression

from .. import audit, config, db
from ..logger import get_logger
from . import build_corpus

log = get_logger("calibration.fit_formulas")


# ---------------------------------------------------------------------------
# Per-attribute recipes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Recipe:
    attribute: str
    features: tuple[str, ...]
    clamp: tuple[int, int] = (25, 99)
    notes: str | None = None


# Section 4.2 of PLAN.md -- linear baseline. Exotic non-linearities can be
# swapped in by editing the YAML later (formula-version bumps per rerun).
RECIPES: tuple[Recipe, ...] = (
    # Physicals
    Recipe("strength_2k", ("weight_lbs", "height_in", "bmi", "wingspan_in")),
    Recipe("vertical_2k", ("max_vert_in", "weight_lbs", "height_in"),
           notes="Combine override applies when c_vertical_2k is present."),
    Recipe("speed_2k", ("three_quarter_sprint_sec", "weight_lbs", "height_in"),
           notes="Combine override applies when c_speed_2k is present."),
    Recipe("agility_2k", ("lane_agility_sec", "shuttle_sec", "weight_lbs"),
           notes="Combine override applies when c_agility_2k is present."),
    Recipe("speed_with_ball_2k", ("three_quarter_sprint_sec", "weight_lbs",
                                  "is_pg", "is_sg")),
    Recipe("stamina_2k", ("min", "gp")),
    Recipe("hustle_2k", ("stl_per36", "oreb_per36", "blk_per36")),

    # Shooting
    Recipe("three_point_shot_2k",
           ("fg3_pct", "fg3a_per36", "ft_pct"),
           notes="League 3pt-line penalty is applied at inference time."),
    Recipe("free_throws_2k", ("ft_pct", "fta_per36")),
    Recipe("mid_range_shot_2k", ("fg_pct", "fg3_pct")),
    Recipe("close_shot_2k", ("fg_pct", "fta_per36", "pts")),
    Recipe("shot_iq_2k", ("fg_pct", "fg3_pct", "ft_pct", "usg_proxy")),
    Recipe("offensive_consistency_2k", ("fg_pct", "ft_pct", "usg_proxy", "tov_per36")),

    # Inside scoring
    Recipe("driving_layup_2k", ("fg_pct", "fta_per36", "max_vert_in",
                                "weight_lbs")),
    Recipe("driving_dunk_2k", ("max_vert_in", "weight_lbs",
                               "wingspan_minus_height")),
    Recipe("standing_dunk_2k", ("std_reach_in", "max_vert_in", "is_c")),
    Recipe("post_control_2k", ("fg_pct", "weight_lbs", "height_in", "is_c")),
    Recipe("draw_foul_2k", ("fta_per36", "pts", "fg_pct")),
    Recipe("hands_2k", ("fg_pct", "tov_per36")),

    # Playmaking
    Recipe("ball_handle_2k", ("ast_per36", "tov_per36", "is_pg", "is_sg")),
    Recipe("pass_iq_2k", ("ast_per36", "tov_per36")),
    Recipe("pass_accuracy_2k", ("ast_per36", "tov_per36")),
    Recipe("pass_vision_2k", ("ast_per36", "tov_per36", "usg_proxy")),

    # Defense
    Recipe("interior_defense_2k", ("blk_per36", "dreb_per36", "height_in",
                                   "wingspan_in", "weight_lbs")),
    Recipe("perimeter_defense_2k", ("stl_per36", "wingspan_minus_height",
                                    "lane_agility_sec")),
    Recipe("block_2k", ("blk_per36", "height_in", "wingspan_in",
                        "max_vert_in")),
    Recipe("steal_2k", ("stl_per36", "wingspan_minus_height")),
    Recipe("defensive_rebound_2k", ("dreb_per36", "height_in", "weight_lbs",
                                    "wingspan_in")),
    Recipe("offensive_rebound_2k", ("oreb_per36", "height_in", "weight_lbs")),
    Recipe("help_defense_iq_2k", ("blk_per36", "stl_per36", "dreb_per36")),
    Recipe("pass_perception_2k", ("stl_per36", "wingspan_in")),
    Recipe("defensive_consistency_2k", ("stl_per36", "blk_per36", "dreb_per36",
                                        "gp")),
)


# Separate recipe bundle for derived combine "C ... 2K" scalings
COMBINE_SCALING_RECIPES: tuple[Recipe, ...] = (
    Recipe("c_speed_2k",
           ("three_quarter_sprint_sec", "weight_lbs"),
           notes="Raw-drill-time -> 2K-scaled Speed rating; learned from "
                 "current NBA combine alumni whose 2K26 ratings we have."),
    Recipe("c_agility_2k",
           ("lane_agility_sec", "shuttle_sec", "weight_lbs")),
    Recipe("c_vertical_2k",
           ("max_vert_in", "standing_vert_in", "weight_lbs")),
)


# ---------------------------------------------------------------------------
# Fit one recipe
# ---------------------------------------------------------------------------
def _fit_one(df: pd.DataFrame, recipe: Recipe,
             target_override: str | None = None) -> dict[str, Any]:
    """Fit a single OLS regression; return YAML-ready dict + metrics."""
    target = target_override or recipe.attribute
    if target not in df.columns:
        log.warning("target %s missing from corpus -- skipping", target)
        return {}

    cols = list(recipe.features) + [target]
    sub = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    n = len(sub)
    if n < 25:
        log.warning("insufficient samples for %s (n=%d); writing stub formula",
                    recipe.attribute, n)
        return {
            "attribute": recipe.attribute,
            "version": 1,
            "type": "linear_regression",
            "features": [{"name": f, "coef": 0.0} for f in recipe.features],
            "intercept": 70.0,
            "clamp": list(recipe.clamp),
            "r2": 0.0,
            "mae": 0.0,
            "n_samples": n,
            "notes": (recipe.notes or "") + f" (stub; n={n})",
        }

    X = sub[list(recipe.features)].to_numpy()
    y = sub[target].to_numpy()
    model = LinearRegression().fit(X, y)
    y_hat = model.predict(X)
    resid = y - y_hat
    ss_res = float((resid ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
    r2 = 1.0 - ss_res / ss_tot
    mae = float(np.abs(resid).mean())

    feats = [
        {"name": f, "coef": round(float(c), 6)}
        for f, c in zip(recipe.features, model.coef_)
    ]
    return {
        "attribute": recipe.attribute,
        "version": 1,
        "type": "linear_regression",
        "features": feats,
        "intercept": round(float(model.intercept_), 6),
        "clamp": list(recipe.clamp),
        "r2": round(r2, 4),
        "mae": round(mae, 3),
        "n_samples": int(n),
        "notes": recipe.notes or "",
    }


# ---------------------------------------------------------------------------
# Overall composite (position-aware)
# ---------------------------------------------------------------------------
def _fit_overall(df: pd.DataFrame) -> dict[str, Any] | None:
    """Position-aware weighted sum. One weight vector per position bucket.

    The target is ``overall_2k``; features are *every* individually-fit
    attribute (so the composite recomposes an Overall from the per-attribute
    ratings rather than re-inventing the wheel).
    """
    target = "overall_2k"
    if target not in df.columns:
        return None
    attr_features = [r.attribute for r in RECIPES if r.attribute in df.columns]
    if not attr_features:
        return None
    cols = attr_features + ["pos_bucket", target]
    sub = df[cols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) < 30:
        return None

    per_pos_models: dict[str, dict[str, Any]] = {}
    for p in ("PG", "SG", "SF", "PF", "C"):
        pdf = sub[sub["pos_bucket"] == p]
        if len(pdf) < 15:
            continue
        X = pdf[attr_features].to_numpy()
        y = pdf[target].to_numpy()
        m = LinearRegression().fit(X, y)
        y_hat = m.predict(X)
        ss_res = float(((y - y_hat) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum()) or 1.0
        r2 = 1.0 - ss_res / ss_tot
        mae = float(np.abs(y - y_hat).mean())
        per_pos_models[p] = {
            "weights": [
                {"name": f, "coef": round(float(c), 6)}
                for f, c in zip(attr_features, m.coef_)
            ],
            "intercept": round(float(m.intercept_), 6),
            "r2": round(r2, 4),
            "mae": round(mae, 3),
            "n_samples": int(len(pdf)),
        }
    if not per_pos_models:
        return None

    # Aggregate overall metrics (unweighted average over positions).
    r2 = round(np.mean([v["r2"] for v in per_pos_models.values()]), 4)
    mae = round(np.mean([v["mae"] for v in per_pos_models.values()]), 3)

    return {
        "attribute": "overall_2k",
        "version": 1,
        "type": "position_weighted_sum",
        "per_position": per_pos_models,
        "clamp": [25, 99],
        "r2": r2,
        "mae": mae,
        "n_samples": int(len(sub)),
        "notes": "Position-aware weighted sum of the 32 per-attribute ratings.",
    }


# ---------------------------------------------------------------------------
# Height-delta reconciliation (combine vs listed)
# ---------------------------------------------------------------------------
def _fit_height_delta(df: pd.DataFrame) -> dict[str, Any]:
    """Piecewise linear ``delta = listed_height - height_wo_shoes`` by
    position and wo-shoes bucket. Writes ``height_delta.yaml``.

    Prospects post-combine get their pre-combine listed height replaced by
    ``height_wo_shoes + height_delta(pos_bucket, wo_shoes_bucket)``.
    """
    sub = df.dropna(subset=["height_wo_shoes_in", "height_in"]).copy()
    if len(sub) < 15:
        return {
            "attribute": "height_delta",
            "version": 1,
            "type": "piecewise",
            "deltas": {},
            "notes": "insufficient samples -- defaulting to +1.0in add",
            "default_delta": 1.0,
            "n_samples": int(len(sub)),
        }
    sub["delta"] = sub["height_in"] - sub["height_wo_shoes_in"]
    deltas: dict[str, dict[str, float]] = {}
    for p in ("PG", "SG", "SF", "PF", "C"):
        psub = sub[sub["pos_bucket"] == p]
        if len(psub) < 5:
            continue
        deltas[p] = {
            "mean": round(float(psub["delta"].mean()), 3),
            "median": round(float(psub["delta"].median()), 3),
            "n": int(len(psub)),
        }
    default_delta = round(float(sub["delta"].mean()), 3)
    return {
        "attribute": "height_delta",
        "version": 1,
        "type": "piecewise",
        "deltas": deltas,
        "default_delta": default_delta,
        "notes": "Average of (NBA listed height - combine wo_shoes height) "
                 "by position bucket. Applied to prospect combine heights.",
        "n_samples": int(len(sub)),
    }


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------
def _yaml_plain(obj: Any) -> Any:
    """Recursively convert numpy / pandas scalars to native Python for PyYAML."""
    if isinstance(obj, dict):
        return {k: _yaml_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_yaml_plain(v) for v in obj]
    if hasattr(obj, "item") and callable(getattr(obj, "item")):
        try:
            return _yaml_plain(obj.item())
        except (ValueError, AttributeError):
            return obj
    return obj


def write_yaml(formula: dict[str, Any]) -> Path:
    """Write a single formula dict to ``data/formulas/<attribute>.yaml``.
    Returns the path."""
    attr = formula["attribute"]
    path = config.FORMULAS_DIR / f"{attr}.yaml"
    safe = _yaml_plain(formula)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(safe, f, sort_keys=False, allow_unicode=True)
    return path


def _persist_to_db(conn, formula: dict[str, Any], source: str = "fit_formulas") -> None:
    conn.execute(
        """
        INSERT INTO formulas
            (attribute, version, yaml_blob, r2, mae, n_samples, edited_by, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(attribute, version) DO UPDATE SET
            yaml_blob=excluded.yaml_blob,
            r2=excluded.r2,
            mae=excluded.mae,
            n_samples=excluded.n_samples,
            edited_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
        """,
        (
            formula["attribute"],
            int(formula.get("version", 1)),
            yaml.safe_dump(
                _yaml_plain(formula), sort_keys=False, allow_unicode=True),
            float(formula.get("r2") or 0.0),
            float(formula.get("mae") or 0.0),
            int(formula.get("n_samples") or 0),
            source,
            formula.get("notes") or "",
        ),
    )


def fit_all(opts: build_corpus.CorpusOptions | None = None) -> dict[str, dict[str, Any]]:
    """Fit every recipe; write YAMLs + DB rows; return a dict attr -> formula.

    Does not raise if a recipe has too few samples -- writes a stub formula
    instead so inference always has something to load.
    """
    corpus = build_corpus.build(opts)
    log.info("calibration corpus: %d rows, %d cols",
             len(corpus), len(corpus.columns))

    conn = db.connect()
    try:
        results: dict[str, dict[str, Any]] = {}
        for recipe in RECIPES:
            formula = _fit_one(corpus, recipe)
            if not formula:
                continue
            write_yaml(formula)
            _persist_to_db(conn, formula)
            results[recipe.attribute] = formula
            audit.log_event(
                action="formula_refit",
                entity_type="formula",
                entity_slug=recipe.attribute,
                note=(f"R^2={formula.get('r2')} MAE={formula.get('mae')} "
                      f"n={formula.get('n_samples')}"),
            )

        # Combine scaling recipes use an alternate target name; for v1 we
        # target the matching base attribute (speed_2k, agility_2k, vertical_2k)
        # because they are the labels on 2kratings.com. The combine-override
        # path in src/formulas/apply.py will substitute the scaled value.
        for recipe in COMBINE_SCALING_RECIPES:
            target = recipe.attribute.replace("c_", "").rstrip("_2k") + "_2k"
            formula = _fit_one(corpus, recipe, target_override=target)
            if not formula:
                continue
            formula["attribute"] = recipe.attribute  # rename for storage
            write_yaml(formula)
            _persist_to_db(conn, formula)
            results[recipe.attribute] = formula

        # Overall composite (position-aware)
        overall = _fit_overall(corpus)
        if overall is not None:
            write_yaml(overall)
            _persist_to_db(conn, overall)
            results["overall_2k"] = overall

        # Height delta reconciliation
        hd = _fit_height_delta(corpus)
        write_yaml(hd)
        _persist_to_db(conn, hd)
        results["height_delta"] = hd
    finally:
        conn.close()

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit all 2K26 formulas.")
    parser.add_argument("--season", default=config.CURRENT_SEASON)
    parser.add_argument("--season-type", default="Regular")
    args = parser.parse_args()

    results = fit_all(build_corpus.CorpusOptions(
        season=args.season, season_type=args.season_type,
    ))
    print(json.dumps({
        a: {"r2": v.get("r2"), "mae": v.get("mae"), "n": v.get("n_samples")}
        for a, v in results.items()
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
