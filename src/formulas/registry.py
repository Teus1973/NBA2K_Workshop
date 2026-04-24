"""
Load YAML formulas from ``data/formulas/*.yaml`` into a registry that can
evaluate them against a feature dict.

The YAML shape is documented in PLAN.md section 4.3::

    attribute: strength_2k
    version: 1
    type: linear_regression
    features:
      - {name: weight_lbs, coef: 0.42}
      - {name: height_in,  coef: 0.18}
    intercept: 12.0
    clamp: [25, 99]
    notes: "Fit on N current NBA players. R^2 = ... MAE = ..."

Supported ``type`` values:

- ``linear_regression`` -- evaluated as ``intercept + sum(coef * features[name])``.
- ``piecewise`` -- used by ``height_delta.yaml``; a dict of ``{pos_bucket: {mean, median, n}}``.
- ``position_weighted_sum`` -- used by ``overall_2k.yaml``; per-position weight
  vector over every other attribute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .. import config
from ..logger import get_logger

log = get_logger("formulas.registry")


# ---------------------------------------------------------------------------
@dataclass
class Formula:
    attribute: str
    version: int
    type: str
    raw: dict[str, Any]

    @property
    def clamp(self) -> tuple[int, int]:
        clip = self.raw.get("clamp") or [25, 99]
        return int(clip[0]), int(clip[1])

    @property
    def r2(self) -> float:
        return float(self.raw.get("r2") or 0.0)

    @property
    def mae(self) -> float:
        return float(self.raw.get("mae") or 0.0)

    @property
    def n_samples(self) -> int:
        return int(self.raw.get("n_samples") or 0)

    @property
    def notes(self) -> str:
        return str(self.raw.get("notes") or "")

    # ------------------------------------------------------------------
    def evaluate(self, features: Mapping[str, float]) -> float | None:
        """Evaluate the formula against a feature dict.

        Missing features are treated as 0.0 (i.e. they contribute nothing),
        but we log the first few so the caller can see what's happening.

        Returns an *unclamped* float or None if the formula type is one that
        only returns data (e.g. piecewise tables).
        """
        if self.type == "linear_regression":
            return self._eval_linear(features)
        if self.type == "position_weighted_sum":
            return self._eval_position_weighted(features)
        if self.type == "piecewise":
            return None
        log.warning("unknown formula type %r for %s", self.type, self.attribute)
        return None

    def _eval_linear(self, features: Mapping[str, float]) -> float:
        total = float(self.raw.get("intercept", 0.0))
        missing: list[str] = []
        for feat in self.raw.get("features", []):
            name = feat["name"]
            coef = float(feat["coef"])
            if name not in features or features[name] is None:
                missing.append(name)
                continue
            try:
                total += coef * float(features[name])
            except (TypeError, ValueError):
                missing.append(name)
        if missing:
            log.debug("%s: %d missing features %r", self.attribute, len(missing), missing[:4])
        return total

    def _eval_position_weighted(self, features: Mapping[str, float]) -> float:
        """Position-aware weighted sum for ``overall_2k``."""
        bucket = str(features.get("pos_bucket") or "SF").upper()
        per_pos = self.raw.get("per_position") or {}
        model = per_pos.get(bucket)
        if model is None:
            # Fallback: try average of available positions.
            weights_sum: dict[str, float] = {}
            intercepts: list[float] = []
            for _p, m in per_pos.items():
                intercepts.append(float(m.get("intercept", 0.0)))
                for w in m.get("weights", []):
                    weights_sum[w["name"]] = weights_sum.get(w["name"], 0.0) + float(w["coef"])
            if not weights_sum:
                return 0.0
            n = max(len(per_pos), 1)
            total = sum(intercepts) / n
            for name, coef in weights_sum.items():
                val = features.get(name)
                if val is None:
                    continue
                total += (coef / n) * float(val)
            return total

        total = float(model.get("intercept", 0.0))
        for w in model.get("weights", []):
            val = features.get(w["name"])
            if val is None:
                continue
            try:
                total += float(w["coef"]) * float(val)
            except (TypeError, ValueError):
                continue
        return total


# ---------------------------------------------------------------------------
@dataclass
class FormulaRegistry:
    formulas: dict[str, Formula] = field(default_factory=dict)

    def __contains__(self, attribute: str) -> bool:
        return attribute in self.formulas

    def __getitem__(self, attribute: str) -> Formula:
        return self.formulas[attribute]

    def get(self, attribute: str) -> Formula | None:
        return self.formulas.get(attribute)

    def attributes(self) -> Iterable[str]:
        return self.formulas.keys()

    # ------------------------------------------------------------------
    def evaluate(
        self,
        attribute: str,
        features: Mapping[str, float],
        *,
        clamp: bool = True,
        as_int: bool = True,
    ) -> float | int | None:
        """Evaluate and optionally clamp / integer-round."""
        formula = self.formulas.get(attribute)
        if formula is None:
            return None
        val = formula.evaluate(features)
        if val is None:
            return None
        if clamp:
            lo, hi = formula.clamp
            val = max(lo, min(hi, val))
        if as_int:
            return int(round(val))
        return float(val)

    def height_delta(self, pos_bucket: str) -> float:
        """Return the (listed - wo_shoes) add for a position bucket.

        Falls back to the YAML's ``default_delta`` (or 1.0 inches) when the
        requested position isn't in the piecewise table.
        """
        f = self.formulas.get("height_delta")
        if f is None:
            return 1.0
        raw = f.raw
        deltas = raw.get("deltas") or {}
        bucket_entry = deltas.get(str(pos_bucket).upper())
        if bucket_entry and "mean" in bucket_entry:
            return float(bucket_entry["mean"])
        return float(raw.get("default_delta", 1.0))


# ---------------------------------------------------------------------------
def load_registry(formulas_dir: Path | str | None = None) -> FormulaRegistry:
    """Load every ``*.yaml`` under ``data/formulas/`` into a registry."""
    formulas_dir = Path(formulas_dir) if formulas_dir else config.FORMULAS_DIR
    reg = FormulaRegistry()
    if not formulas_dir.exists():
        log.warning("formulas dir %s does not exist", formulas_dir)
        return reg
    for fp in sorted(formulas_dir.glob("*.yaml")):
        try:
            with fp.open("r", encoding="utf-8") as f:
                blob = yaml.safe_load(f) or {}
        except Exception as exc:  # noqa: BLE001
            log.error("failed to load %s: %s", fp.name, exc)
            continue
        attr = blob.get("attribute") or fp.stem
        reg.formulas[attr] = Formula(
            attribute=attr,
            version=int(blob.get("version", 1)),
            type=str(blob.get("type", "linear_regression")),
            raw=blob,
        )
    log.info("loaded %d formulas from %s", len(reg.formulas), formulas_dir)
    return reg
