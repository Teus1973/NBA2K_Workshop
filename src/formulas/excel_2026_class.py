"""
2026 class rating engine ported from
``nba_class_25_template_with_updated_formulas_Updated V2.xlsx`` sheet
``2026 class`` (prospect / college stat columns + 2K outputs).

**Interior and perimeter defense** do not use the old Excel cells for those
attributes: they use the same **linear-regression** feature sets as
``data/formulas/interior_defense_2k.yaml`` and ``perimeter_defense_2k.yaml``
(fitted on NBA reference players), with wingspan from the prospect (or
``height + 3.5"``) and optional ``lane_agility_sec``; if lane is missing, a
neutral combine-time default is imputed so ratings stay well-behaved for college
prospects.

A few workbook columns are **retuned in code** (steal, shot IQ, projected
speed / agility / speed-with-ball, offensive and defensive consistency) to sit
closer to NBA 2K26 reference numbers for draft-class prospects; combine/raw
``c_*`` columns and ``raw_speed_2k`` / ``raw_agility_2k`` overrides still take priority.

The spreadsheet’s remaining columns still evaluate in a cross-referenced loop;
we iterate a stable sequence so forward references (e.g. Driving Layup uses
Speed / Vert from the same row) settle like Excel.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

from .. import config
from .registry import FormulaRegistry

# Penalty cells BN1=4, BN2=3 on the template.
PENALTY1 = 4.0
PENALTY2 = 3.0

# Small nudges vs the stock workbook: guards/wings get extra projected speed and
# agility (college steal rates under-represent quickness); steals / consistency
# lifted; shot_iq base scaled down so outputs sit closer to NBA 2K26 references.
def _bump_speed_agility(n: _X) -> tuple[float, float]:
    """(speed_pts, agility_pts) when we infer from size + stats, not raw combine."""
    pu = _pos_upper(n.c)
    sp, ag = 0.0, 0.0
    if re.search(r"\b(PG|SG)\b", pu) or pu.strip() == "G" or re.search(
        r"(^|[-/ ])(F[-/]G|G[-/]F)($|[-/ ])", pu
    ):
        sp, ag = 2.0, 1.5
    elif "SF" in pu and "PF" not in pu and "C" not in pu:
        sp, ag = 1.25, 1.0
    return sp, ag

# ---------------------------------------------------------------------------
# Calibrated linear defense (``data/formulas/*_defense_2k.yaml``) — same
# feature space as the NBA reference corpus, not the legacy Excel heuristics.
# ---------------------------------------------------------------------------
_INTERIOR = {
    "intercept": -129.043191,
    "blk_per36": 2.643707,
    "dreb_per36": 0.880364,
    "height_in": 0.095267,
    "wingspan_in": 1.677534,
    "weight_lbs": 0.170887,
}
_PERIMETER = {
    "intercept": 179.939767,
    "stl_per36": 10.477481,
    "wingspan_minus_height": 0.561085,
    "lane_agility_sec": -11.460386,
}
# When a prospect has no combine lane time, impute a neutral NBA-ish value so
# the perimeter line does not collapse to 25/99 from the misfit intercept.
_DEFAULT_LANE_SEC = 11.35
_DEFAULT_WINGSPAN_OFFSET_IN = 3.5

# Internal keys match Excel 2K column block (row 1 headers).
_INT_KEYS: tuple[str, ...] = (
    "driving_layup_2k", "post_control_2k", "draw_foul_2k", "close_shot_2k",
    "mid_range_shot_2k", "three_point_shot_2k", "free_throws_2k",
    "ball_handle_2k", "pass_iq_2k", "pass_accuracy_2k", "offensive_rebound_2k",
    "standing_dunk_2k", "driving_dunk_2k", "shot_iq_2k", "pass_vision_2k",
    "hands_2k", "defensive_rebound_2k", "interior_defense_2k",
    "perimeter_defense_2k", "block_2k", "steal_2k", "speed_2k",
    "speed_with_ball_2k", "vertical_2k", "strength_2k", "stamina_2k",
    "hustle_2k", "agility_2k", "pass_perception_2k", "defensive_consistency_2k",
    "help_defense_iq_2k", "offensive_consistency_2k",
)

_ATTR_ORDER: list[str] = [a for a in config.RATING_ATTRIBUTES if a != "overall_2k"]


# ---------------------------------------------------------------------------
@dataclass
class _X:
    """Per-sheet columns I–AB + raw combine AC–AF (mapped from prospect)."""
    g: float  # height inches
    h: float  # weight lbs
    i: float  # gp
    j: float  # min per game
    k: float  # pts
    l: float  # fgm
    m: float  # fga
    n: float  # fg% on 0-100 scale (40.7 means 40.7%)
    o: float  # 3pm (column O)
    p: float  # 3pa (column P)
    q: float  # fg3% 0-100
    s_f: float  # fta
    r: float  # ftm
    t: float  # ft% 0-100
    u: float  # oreb
    v: float  # dreb
    w: float  # reb
    x: float  # ast
    y: float  # tov
    z: float  # stl
    aa: float  # blk
    ab: float  # pf
    ac: float | None  # raw speed
    ad: float | None
    ae: float | None
    af: float | None
    c: str  # position text
    ws: float  # wingspan (in), estimated if missing
    la: float | None  # lane_agility_sec (optional combine)
    mv: float | None  # max vertical leap inches (combine drills)


def _f(v: Any) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return 0.0
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _iferror(expr: float, default: float = 0.0) -> float:
    if math.isnan(expr) or math.isinf(expr):
        return default
    return expr


def _p36(numer: float, j: float) -> float:
    """``numer*36/j``; Excel #DIV/0! → 0. Must be used for all per-36 when ``j`` (min/g) can be 0.

    (Python evaluates ``a/b`` *before* :func:`_iferror` is called, so
    ``_iferror(n.z*36/n.j, 0)`` still raised ``ZeroDivisionError``.)"""
    if j is None or (isinstance(j, (int, float)) and (j <= 0 or math.isnan(j) or math.isinf(j))):
        return 0.0
    return float(numer) * 36.0 / float(j)


def _qdiv(numer: float, denom: float) -> float:
    """``numer / denom``; 0 if ``denom`` is 0/NaN/inf."""
    if denom is None or (isinstance(denom, (int, float)) and (denom == 0 or math.isnan(denom) or math.isinf(denom))):
        return 0.0
    return float(numer) / float(denom)


def _st_get(st: Mapping[str, int], k: str, default: float = 60.0) -> float:
    v = st.get(k)
    if v is None:
        return default
    return float(v)


def _pos_upper(c: str) -> str:
    return (c or "").upper()


def _row_from_prospect(p: Mapping[str, Any]) -> _X:
    ch = _f(p.get("combine_height_in"))
    gh = _f(p.get("height_in"))
    g = ch if ch > 0 else gh
    cw = _f(p.get("combine_weight_lbs"))
    hw = _f(p.get("weight_lbs"))
    h = cw if cw > 0 else hw
    return _X(
        g=g,
        h=h,
        i=_f(p.get("gp")),
        j=_f(p.get("min")),
        k=_f(p.get("pts")),
        l=_f(p.get("fgm")),
        m=_f(p.get("fga")),
        n=(lambda r: (r * 100.0) if 0 < r <= 1.0 else r)(_f(p.get("fg_pct"))),
        o=_f(p.get("fg3m")),
        p=_f(p.get("fg3a")),
        q=(lambda r: (r * 100.0) if 0 < r <= 1.0 else r)(_f(
            p.get("fg3_pct"))),
        r=_f(p.get("ftm")),
        s_f=_f(p.get("fta")),
        t=(lambda r: (r * 100.0) if 0 < r <= 1.0 else r)(_f(
            p.get("ft_pct"))),
        u=_f(p.get("oreb")),
        v=_f(p.get("dreb")),
        w=_f(p.get("reb")),
        x=_f(p.get("ast")),
        y=_f(p.get("tov")),
        z=_f(p.get("stl")),
        aa=_f(p.get("blk")),
        ab=_f(p.get("pf")),
        ac=(None if p.get("raw_speed_2k") is None else _f(p.get("raw_speed_2k"))),
        ad=None if p.get("raw_speed_with_ball_2k") is None else _f(
            p.get("raw_speed_with_ball_2k")),
        ae=None if p.get("raw_vertical_2k") is None else _f(
            p.get("raw_vertical_2k")),
        af=None if p.get("raw_agility_2k") is None else _f(
            p.get("raw_agility_2k")),
        c=str(p.get("pos") or ""),
        ws=_wingspan_in(p),
        la=_lane_agility(p),
        mv=_max_vert_inches(p),
    )


def _wingspan_in(p: Mapping[str, Any]) -> float:
    for k in ("combine_wingspan_in", "wingspan_in", "wingspan"):
        w = _f(p.get(k))
        if w > 0:
            return w
    g = _f(p.get("combine_height_in")) or _f(p.get("height_in"))
    if g > 0:
        return g + _DEFAULT_WINGSPAN_OFFSET_IN
    return 0.0


def _lane_agility(p: Mapping[str, Any]) -> float | None:
    for k in ("lane_agility_sec", "lane_agility"):
        v = p.get(k)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x > 0 and not (math.isnan(x) or math.isinf(x)):
            return x
    return None


def _max_vert_inches(p: Mapping[str, Any]) -> float | None:
    for k in ("max_vert_in", "MAX_VERTICAL_LEAP"):
        v = p.get(k)
        if v is None:
            continue
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        if x > 0 and not (math.isnan(x) or math.isinf(x)):
            return x
    return None


def vertical_2k_from_max_vert_inches(max_vert_in: float) -> int:
    """2K-scale vertical strictly from Combine max leap (elite ~40½\" ⇒ 92+).

    Clamp to **[25, 99]** with an explicit elite floor once **≥ 40½\"**.
    """
    mv = float(max_vert_in)
    if not math.isfinite(mv):
        return 25
    if mv <= 0:
        return 25
    linear = ((mv - 24.0) / 18.0) * 74.0 + 25.0
    out = int(linear)
    if mv >= 40.5:
        out = max(out, 92)
    elif mv >= 40.0:
        out = max(out, 90)
    return max(25, min(99, out))


def _weight_for_defense(n: _X) -> float:
    if n.h > 0:
        return n.h
    if n.g <= 0:
        return 0.0
    return 195.0 if n.g < 78.0 else 235.0


# --- attribute calculators: return 0-99 int --------------------------------
def _agility(n: _X, st: Mapping[str, int]) -> int:
    if n.af is not None and n.af > 0:
        return int(max(25, min(99, n.af)))
    if n.g <= 0:
        return 60
    v = -2.26 * n.g - 0.09 * n.h + 273.41
    _, a_b = _bump_speed_agility(n)
    v += a_b
    return int(max(25, min(99, round(v))))


def _speed(n: _X, st: Mapping[str, int]) -> int:
    acv = 0.0
    if n.ac is not None:
        acv = float(n.ac)
    if acv >= 40:
        return int(max(25, min(99, acv)))
    t = 86.0
    t -= 0.32 * max(0, n.h - 215.0)
    t -= 0.6 * max(0, n.g - 79.0)
    in_br = max(
        -7.0,
        min(
            7.0,
            1.2
            * (_p36(n.z, n.j) * min(1.0, max(0.75, n.j / 28.0)) - 1.0)
            - 0.02 * max(0, n.h - 225)
            - 0.08 * max(0, n.g - 82),
        ),
    )
    t += in_br
    s_b, _ = _bump_speed_agility(n)
    t += s_b
    return int(max(25, min(99, round(t))))


def _strength(n: _X, st: Mapping[str, int]) -> int:
    if n.h <= 0:
        return 25
    return int(max(25, min(99, round(0.45 * n.h - 0.5 * n.g + 3.0))))


def _stamina(n: _X, st: Mapping[str, int]) -> int:
    bb = _st_get(st, "speed_2k")
    bh = _st_get(st, "agility_2k")
    v = 82.0
    v += 0.25 * min(36.0, n.j)
    v += 0.1 * max(0, bb - 70.0)
    v += 0.08 * max(0, bh - 70.0)
    v -= 0.05 * max(0, n.h - 225)
    v -= PENALTY2
    return int(max(25, min(99, round(v))))


def _ball_handle(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    bh = _st_get(st, "agility_2k")
    bb = _st_get(st, "speed_2k")
    t = 62.0
    t += 0.7 * ((0.75 * bh + 0.25 * bb) - 60.0)
    t += 12.0 * min(1.0, max(0.0, (_p36(n.x, n.j) - 2.0) / 4.0)) * min(
        1.0, max(0.75, n.j / 28.0))
    t += 8.0 * min(
        1.0,
        max(0.0, ((_p36(n.x, n.j)) / max(0.5, _p36(n.y, n.j)) - 1.2) / 1.8),
    ) * min(1.0, max(0.75, n.j / 28.0))
    t -= 7.0 * max(0.0, _p36(n.y, n.j) - 3.0)
    t -= 0.55 * max(0.0, n.g - 78.0)
    t -= 0.1 * max(0.0, n.h - 215.0)
    t -= PENALTY1
    return int(max(45, min(95, round(t))))


def _speed_with_ball(n: _X, st: Mapping[str, int]) -> int:
    an = _st_get(st, "ball_handle_2k")
    bb = _st_get(st, "speed_2k")
    bh = _st_get(st, "agility_2k")
    t = 0.55 * bb + 0.25 * an + 0.2 * bh - 0.3 * max(0.0, n.g - 78.0)
    s_b, _ = _bump_speed_agility(n)
    if s_b > 0:
        t += 0.3 * s_b
    return int(max(25, min(99, round(t))))


def _vertical(n: _X, st: Mapping[str, int]) -> int:
    if n.mv is not None and n.mv > 0:
        return vertical_2k_from_max_vert_inches(n.mv)
    if n.j <= 0:
        return 0
    bb = _st_get(st, "speed_2k")
    bh = _st_get(st, "agility_2k")
    bc = _st_get(st, "speed_with_ball_2k")
    bgv = _st_get(st, "hustle_2k")
    t = 60.0
    t += 0.34 * (bb - 60.0) + 0.26 * (bh - 60.0) + 0.12 * (bc - 60.0) + 0.06 * (
        bgv - 60.0
    )
    t += 6.0 * min(1.0, max(0.0, (_p36(n.aa, n.j) - 0.8) / 1.6)) * min(
        1.0, max(0.75, n.j / 28.0))
    t += 3.0 * min(1.0, max(0.0, (_p36(n.z, n.j) - 1.0) / 1.5)) * min(
        1.0, max(0.75, n.j / 28.0))
    t -= 0.1 * max(0.0, n.h - 215.0) + 0.12 * max(0.0, n.g - 79.0)
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(25, min(99, round(t))))


def _hustle(n: _X, st: Mapping[str, int]) -> int:
    bb = _st_get(st, "speed_2k")
    bh = _st_get(st, "agility_2k")
    bf = _st_get(st, "stamina_2k")
    t = 54.0
    t += 7.0 * min(2.5, max(0.0, _p36(n.z, n.j) * min(
        1.0, max(0.75, n.j / 28.0))))
    t += 2.0 * min(2.5, max(0.0, _p36(n.u, n.j) * min(
        1.0, max(0.75, n.j / 28.0)))) * max(0.7, 1.0 - 0.015 * max(0.0, n.g - 79.0))
    t += 1.5 * min(5.0, max(0.0, _p36(n.v, n.j) * min(
        1.0, max(0.75, n.j / 28.0)))) * max(0.7, 1.0 - 0.015 * max(0.0, n.g - 79.0))
    t += 1.5 * min(1.5, max(0.0, _p36(n.aa, n.j) * min(
        1.0, max(0.75, n.j / 28.0))))
    t += 0.08 * max(0.0, bf - 82.0) + 0.06 * max(0.0, bb - 72.0) + 0.1 * max(
        0.0, bh - 72.0)
    t -= 2.0 * max(0.0, _p36(n.ab, n.j) - 4.0)
    return int(max(45, min(95, round(t))))


def _driving_layup(n: _X, st: Mapping[str, int]) -> int:
    bh = _st_get(st, "agility_2k")
    bc = _st_get(st, "speed_with_ball_2k")
    bd = _st_get(st, "vertical_2k")
    t = 47.0
    t += 0.24 * (bh - 60.0) + 0.2 * (bc - 60.0) + 0.1 * (bd - 60.0)
    t += 2.0 * min(6.0, max(0.0, _p36(n.x, n.j) - 2.0)) * min(
        1.0, max(0.75, n.j / 20.0))
    t += (
        5.2
        * min(1.0, max(0.0, (min(0.55, _qdiv(n.s_f, n.m)) - 0.1) / 0.25))
        * min(1.0, _p36(n.m, n.j) / 9.0)
        * min(1.0, max(0.75, n.j / 20.0))
        * (1.0 - 0.02 * max(0, min(10, n.g - 78)) - 0.006 * max(0, min(40, n.h - 220)))
    )
    t += 2.8 * min(8.0, max(0.0, _p36(n.s_f, n.j) * min(
        1.0, max(0.75, n.j / 28.0)))) * (
        1.0 - 0.02 * max(0, min(10, n.g - 78)) - 0.006 * max(0, min(40, n.h - 220))
    )
    t += 5.0 * min(1.0, max(0.0, (_iferror(
        (n.l - n.o) / max(1.0, n.m - n.p), 0.0) - 0.48) / 0.14)) * min(
        1.0, max(0.75, n.j / 20.0)) * min(1.0, _p36(n.m - n.p, n.j) / 10.0)
    t += 2.0 * max(0.0, min(1.0, (0.32 - _qdiv(n.p, n.m)) / 0.32)) * min(
        1.0, _p36(n.s_f, n.j) / 5.0)
    t += 4.0 * max(-0.08, min(0.08, _qdiv(n.l, n.m) - 0.46)) * min(
        1.0, _p36(n.m, n.j) / 10.0)
    t -= 0.55 * max(0.0, n.g - 80.0) + 0.1 * max(0.0, n.h - 225.0) + PENALTY1
    return int(max(40, min(99, round(t))))


def _post_control(n: _X, st: Mapping[str, int]) -> int:
    be = _st_get(st, "strength_2k")
    t = 45.0
    t += 0.55 * (be - 60.0) + 0.18 * max(0, n.g - 79.0) + 0.08 * max(0, n.h - 215.0)
    t += 10.0 * min(1.0, max(0.0, (_qdiv(n.s_f, n.m) - 0.1) / 0.25)) * min(
        1.0, max(0.75, n.j / 20.0))
    t -= 18.0 * min(1.0, max(0.0, (_qdiv(n.p, n.m) - 0.25) / 0.3))
    t -= 6.0 * min(1.0, max(0.0, (_p36(n.x, n.j) - 6.0) / 8.0))
    t -= PENALTY1
    return int(max(25, min(99, round(t))))


def _off_reb(n: _X, st: Mapping[str, int]) -> int:
    t = 34.0 + 11.0 * n.u + 8.0 * max(0, n.u - 2.0) + 10.0 * max(0, n.u - 3.0)
    t += 0.8 * max(0, n.g - 78.0) + 0.2 * max(0, n.h - 210.0) - PENALTY1
    return int(max(25, min(99, round(t))))


def _def_reb(n: _X, st: Mapping[str, int]) -> int:
    t = 32.0 + 7.0 * n.v + 4.0 * max(0, n.v - 6.0) + 4.0 * max(0, n.v - 8.0)
    t += 10.0 * max(0, n.v - 9.0) + 0.8 * max(0, n.g - 78.0) + 0.2 * max(
        0, n.h - 210.0) - PENALTY2
    return int(max(25, min(99, round(t))))


def _interior_def(n: _X, st: Mapping[str, int]) -> int:
    """Interior defense from the trained linear model (``interior_defense_2k.yaml``)."""
    blk = _p36(n.aa, n.j)
    drb = _p36(n.v, n.j)
    wgt = _weight_for_defense(n)
    wsp = n.ws if n.ws > 0 and n.g > 0 else n.g
    t = _INTERIOR["intercept"]
    t += _INTERIOR["blk_per36"] * blk
    t += _INTERIOR["dreb_per36"] * drb
    t += _INTERIOR["height_in"] * n.g
    t += _INTERIOR["wingspan_in"] * wsp
    t += _INTERIOR["weight_lbs"] * wgt
    return int(max(25, min(99, round(t))))


def _block(n: _X, st: Mapping[str, int]) -> int:
    t = 40.0
    t += 18.0 * min(2.6, _iferror(n.aa / max(8.0, n.j) * 36.0, 0.0))
    t += 8.0 * max(0.0, min(2.6, _iferror(
        n.aa / max(8.0, n.j) * 36.0, 0.0) - 1.8))
    t -= 6.0 * max(0.0, (14.0 - n.j) / 14.0)
    t -= PENALTY2
    return int(max(35, min(96, round(t))))


def _steal(n: _X, st: Mapping[str, int]) -> int:
    t = 48.0
    t += 42.0 * min(1.0, max(0.0, (_p36(n.z, n.j) - 0.55) / 2.0)) * min(
        1.0, n.j / 28.0)
    t += 4.0 * min(1.0, max(0.0, (_p36(n.z, n.j) - 2.3) / 0.7)) * min(
        1.0, n.j / 28.0) - PENALTY2
    return int(max(25, min(99, round(t))))


def _perimeter_def(n: _X, st: Mapping[str, int]) -> int:
    """Perimeter defense from the trained linear model (``perimeter_defense_2k.yaml``)."""
    stl = _p36(n.z, n.j)
    wsp = n.ws if n.ws > 0 else 0.0
    wmh = (wsp - n.g) if wsp > 0 and n.g > 0 else 0.0
    lane = n.la if n.la is not None else _DEFAULT_LANE_SEC
    t = _PERIMETER["intercept"]
    t += _PERIMETER["stl_per36"] * stl
    t += _PERIMETER["wingspan_minus_height"] * wmh
    t += _PERIMETER["lane_agility_sec"] * lane
    return int(max(25, min(99, round(t))))


def _stand_dunk(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    be = _st_get(st, "strength_2k")
    bd = _st_get(st, "vertical_2k")
    ax_ = _st_get(st, "interior_defense_2k")
    cap = 95.0
    if n.g <= 76:
        cap = 50.0
    elif n.g <= 77:
        cap = 60.0
    elif n.g <= 78:
        cap = 70.0
    elif n.g <= 79:
        cap = 80.0
    else:
        cap = 95.0
    base = 62.0
    base += 1.25 * (n.g - 78.0) + 0.45 * (n.h - 235.0) + 0.45 * (be - 60.0) + 0.35 * (
        bd - 60.0) + 0.12 * (ax_ - 60.0) + 1.0 * max(0, n.g - 82.0)
    base -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(
        max(25, min(95, round(min(cap, base)))),
    )


def _drive_dunk(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    bd = _st_get(st, "vertical_2k")
    bb = _st_get(st, "speed_2k")
    bh = _st_get(st, "agility_2k")
    bc = _st_get(st, "speed_with_ball_2k")
    be = _st_get(st, "strength_2k")
    sc = min(1.0, max(0.75, n.j / 28.0))
    base = 60.0
    base += 0.85 * (bd - 60.0) + 0.22 * (bb - 60.0) + 0.16 * (bh - 60.0) + 0.1 * (
        bc - 60.0) + 0.08 * (be - 60.0) + 0.25 * max(0, min(12, n.g - 76.0))
    base += 8.0 * min(1.0, max(0.0, (_p36(n.k, n.j) - 11.0) / 13.0)) * sc
    base -= 0.16 * max(0, n.h - 235.0) + PENALTY1 * sc
    ccap = 35.0 if n.g <= 72 else 45.0 if n.g <= 73 else 55.0 if n.g <= 74 else 95.0
    return int(max(25, min(95, round(min(ccap, base)))))


def _three(n: _X, st: Mapping[str, int]) -> int:
    t = 42.0 + 0.8 * n.q + 3.0 * math.sqrt(max(0, n.p))
    t += 1.0 * max(0, n.q - 35.0) + 1.2 * max(0, n.q - 40.0) + 1.6 * max(0, n.q - 44.0)
    t -= 10.0 * min(1.0, max(0.0, (2.0 - n.p) / 2.0))
    t -= PENALTY1
    return int(max(40, min(99, round(t))))


def _free_throws(n: _X, st: Mapping[str, int]) -> int:
    p = n.t
    if p == 0:
        p = 0.0
    if 0 < p <= 1.0:
        p = p * 100.0
    if p == 0.0 and n.s_f < 0.01:
        return 0
    fta = n.s_f
    gp = n.i
    mnp = n.j
    fta_tot = fta * gp
    min_tot = mnp * gp
    w_gp = min(1.0, gp / 15.0)
    w_fta = min(1.0, fta_tot / 8.0)
    w_min = min(1.0, min_tot / 500.0)
    w = max(w_gp, w_fta, w_min)
    floor = 65.0
    raw = p * w + floor * (1.0 - w)
    cap = 94.0 if gp < 8.0 else 99.0
    return int(max(25, min(99, round(min(cap, max(25.0, raw))))))


def _pass_accuracy(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    t = 52.0
    t += 2.8 * min(10.0, n.x * 36.0 / max(12.0, n.j)) * min(1.0, max(0.75, n.j / 28.0))
    t += 12.0 * min(1.0, max(0.0, ((n.x / max(0.5, n.y)) - 1.3) / 2.2)) * min(
        1.0, max(0.75, n.j / 28.0))
    t -= 3.2 * max(0.0, n.y * 36.0 / max(12.0, n.j) - 3.0)
    t -= 0.15 * max(0, n.g - 79.0)
    t -= PENALTY1
    return int(max(40, min(99, round(t))))


def _pass_vision(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    t = 44.0
    t += 36.0 * min(1.0, max(0.0, (_iferror(
        n.x * 36.0 / max(12.0, n.j), 0.0) - 1.0) / 7.0)) * min(1.0, max(0.75, n.j / 28.0))
    t += 12.0 * min(1.0, max(0.0, (_iferror(
        n.x / max(0.5, n.y), 0.0) - 1.3) / 2.2)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 6.0 * min(1.0, max(0.0, (_iferror(
        n.y * 36.0 / max(12.0, n.j), 0.0) - 1.8) / 2.2)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 0.15 * max(0, n.g - 79.0)
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(38, min(99, round(t))))


def _pass_iq(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    ap = _st_get(st, "pass_accuracy_2k")
    au = _st_get(st, "pass_vision_2k")
    t = 40.0 + (0.3 * au + 0.3 * ap) * min(1.0, _iferror(
        n.x * 36.0 / max(12.0, n.j), 0.0) / 7.0)
    t += 9.0 * min(1.0, max(0.0, (_iferror(
        n.x * 36.0 / max(12.0, n.j), 0.0) - 2.2) / 6.0)) * min(1.0, max(0.75, n.j / 28.0))
    t += 6.0 * min(1.0, max(0.0, (_iferror(
        n.x / max(0.5, n.y), 0.0) - 1.3) / 2.2)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 12.0 * min(1.0, max(0.0, (_iferror(
        n.y * 36.0 / max(12.0, n.j), 0.0) - 2.0) / 2.0)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 10.0 * min(1.0, max(0.0, (2.0 - _iferror(
        n.x * 36.0 / max(12.0, n.j), 0.0)) / 2.0)) * min(1.0, max(0.75, n.j / 28.0))
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(40, min(95, round(t))))


def _shot_iq(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    pu = _pos_upper(n.c)
    c_big = "C" in pu or "PF" in pu
    w = max(0.25, min(1.0, n.j / 34.0), min(1.0, n.m / 20.0))
    ts_denom = 2.0 * (n.m + 0.44 * n.s_f)
    ts_e = 0.0 if ts_denom == 0.0 else n.k / ts_denom
    n_fg = _iferror(
        (n.n / 100.0) if n.n > 1.0 else n.n,
        0.0 if n.m == 0.0 else n.l / n.m,
    )
    p3 = _iferror(
        (n.q / 100.0) if n.q > 1.0 else n.q,
        0.0 if n.p == 0.0 else n.o / n.p,
    )
    p36 = _p36(n.p, n.j)
    in_br = (
        90.0
        * (ts_e - 0.52)
        * (0.85 if c_big else 1.0)
        + 45.0
        * (n_fg - 0.45)
        * (0.8 if c_big else 1.0)
        + 35.0
        * (p3 - 0.33)
        * min(1.0, p36 / 4.0)
        * (0.95 if c_big and p36 >= 2.0 else 1.0)
        + 0.75
        * max(-6.0, min(10.0, _p36(n.m, n.j) - 12.0))
        + 0.65
        * max(-6.0, min(10.0, _p36(n.p, n.j) - 4.0))
        + 0.45
        * max(0.0, min(6.0, _p36(n.x, n.j) - 3.0))
        - 0.3 * max(0.0, _p36(n.y, n.j) - 3.0)
    )
    if re.search(r"PG|SG|G|SF", pu) and "PF" not in pu and "C" not in pu:
        in_br += 2.0
    if c_big:
        in_br -= 1.0
    t = 57.0 + 0.9 * w * in_br
    t += 11.0 * min(1.0, n.j / 34.0) * max(0.0, min(1.0, (n.k - 10.0) / 16.0))
    t -= PENALTY2
    return int(max(40, min(96, round(t))))


def _draw_foul(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    t = 34.0
    t += 28.0 * min(1.0, max(0.0, (_p36(n.s_f, n.j) - 2.0) / 6.0)) * min(1.0, max(0.75, n.j / 28.0))
    t += 18.0 * min(1.0, max(0.0, (_qdiv(n.s_f, n.m) - 0.12) / 0.28)) * min(
        1.0, max(0.75, n.j / 28.0))
    t += 8.0 * min(1.0, max(0.0, (_p36(n.k, n.j) - 10.0) / 14.0)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 14.0 * min(1.0, max(0.0, (_qdiv(n.p, n.m) - 0.45) / 0.35)) * min(1.0, max(0.75, n.j / 28.0))
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(25, min(99, round(t))))


def _close_shot(n: _X, st: Mapping[str, int]) -> int:
    t = 58.0
    t += 20.0 * min(1.0, max(0.0, (min(0.65, _qdiv(n.s_f, n.m) - 0.18) / 0.3) + 0.015 * max(0, min(12, n.g - 78)))) * min(
        1.0, _p36(n.m, n.j) / 7.0) * min(1.0, max(0.75, n.j / 20.0))
    t += 8.0 * max(0.0, min(1.0, (0.28 - _qdiv(n.p, n.m)) / 0.28)) * min(1.0, _p36(n.m, n.j) / 7.0)
    t += 95.0 * max(-0.1, min(0.1, _iferror(
        (n.l - n.o) / max(0.5, n.m - n.p), 0.0) - 0.54)) * min(1.0, _p36(n.m - n.p, n.j) / 6.0) * min(1.0, max(0.75, n.j / 20.0))
    t += 0.25 * (n.t - 70.0) + 0.3 * max(0, n.g - 79.0) + 0.05 * max(0, n.h - 215.0) + 0.2 * max(0, 79.0 - n.g)
    t -= 9.0 * max(0.0, _qdiv(n.p, n.m) - 0.38)
    t -= (
        8.0
        * max(0.0, 0.54 - _qdiv(n.l, n.m))
        * min(1.0, _p36(n.m, n.j) / 10.0)
    )
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 20.0))
    return int(max(40, min(99, round(t))))


def _mid_range(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    t = 45.0
    t += 35.0 * min(1.0, max(0.0, (_iferror(
        (n.l - n.o) / max(0.5, n.m - n.p), 0.0) - 0.44) / 0.16)) * min(
        1.0, max(0.75, n.j / 28.0)) * min(1.0, _p36(max(0.5, n.m - n.p), n.j) / 8.0)
    t += (
        18.0
        * min(1.0, max(0.0, (_iferror(n.t / 100.0, 0.0) - 0.62) / 0.23))
        * min(1.0, max(0.75, n.j / 28.0))
    )
    t -= 10.0 * min(1.0, max(0.0, (_qdiv(n.p, n.m) - 0.65) / 0.25)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 8.0 * min(1.0, max(0.0, (0.4 - _iferror(
        (n.l - n.o) / max(0.5, n.m - n.p), 0.0)) / 0.1)) * min(1.0, max(0.75, n.j / 28.0))
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(40, min(95, round(t))))


def _hands(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    an = _st_get(st, "ball_handle_2k")
    baa = _st_get(st, "steal_2k")
    azz = _st_get(st, "block_2k")
    t = 40.0 + 0.45 * an + 0.1 * baa + 0.05 * azz
    t -= 5.0 * max(0.0, _p36(n.y, n.j) - 3.0)
    t -= 0.12 * max(0.0, n.g - 79.0)
    t -= 0.04 * max(0.0, n.h - 215.0)
    t -= PENALTY2
    return int(max(40, min(99, round(t))))


def _def_cons(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    ay_ = _st_get(st, "perimeter_defense_2k")
    ax_ = _st_get(st, "interior_defense_2k")
    bg_ = _st_get(st, "hustle_2k")
    bf_ = _st_get(st, "stamina_2k")
    be_ = _st_get(st, "strength_2k")
    ba_ = _st_get(st, "steal_2k")
    az_ = _st_get(st, "block_2k")
    t = 57.0
    t += 0.26 * (ay_ - 60.0) + 0.26 * (ax_ - 60.0) + 0.22 * (bg_ - 60.0) + 0.15 * (bf_ - 60.0) + 0.12 * (be_ - 60.0) + 0.08 * (ba_ - 60.0) + 0.08 * (az_ - 60.0)
    t += 8.0 * min(1.0, max(0.0, (_p36(n.z, n.j) - 1.0) / 1.4)) * min(1.0, max(0.75, n.j / 28.0))
    t += 6.0 * min(1.0, max(0.0, (_p36(n.aa, n.j) - 0.8) / 1.1)) * min(1.0, max(0.75, n.j / 28.0))
    t -= 4.0 * max(0.0, _p36(n.ab, n.j) - 3.4) * min(1.0, max(0.75, n.j / 28.0))
    t -= 3.0 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(25, min(99, round(t))))


def _pass_perc(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    ba_ = _st_get(st, "steal_2k")
    bj_ = _st_get(st, "defensive_consistency_2k")
    av_ = _st_get(st, "hands_2k")
    bh_ = _st_get(st, "agility_2k")
    t = 55.0 + 0.38 * (ba_ - 60.0) + 0.18 * (bj_ - 60.0) + 0.22 * (av_ - 60.0) + 0.17 * (bh_ - 60.0)
    t += 8.0 * min(1.0, max(0.0, (_p36(n.z, n.j) - 1.0) / 1.5)) * min(1.0, max(0.75, n.j / 28.0)) + 0.15 * max(0, min(8, n.g - 77.0)) - PENALTY1
    return int(max(25, min(99, round(t))))


def _help_iq(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    bj_ = _st_get(st, "defensive_consistency_2k")
    ax_ = _st_get(st, "interior_defense_2k")
    ba_ = _st_get(st, "steal_2k")
    az_ = _st_get(st, "block_2k")
    pu = _pos_upper(n.c)
    t = 26.5 + 0.52 * bj_ + 0.13 * ax_ + 0.02 * ba_ + 0.01 * az_
    if re.search(r"PG|SG|G", pu):
        t += 1.5 * min(1.0, max(0.0, (_p36(n.z, n.j) - 1.2) / 1.0)) * min(1.0, max(0.75, n.j / 28.0))
    t -= PENALTY1 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(25, min(99, round(t))))


def _off_cons(n: _X, st: Mapping[str, int]) -> int:
    if n.j <= 0:
        return 0
    t = 45.0
    t += 0.65 * (_p36(n.k, n.j)) * min(1.0, max(0.75, n.j / 28.0))
    t += 18.0 * min(1.0, max(0.0, (n.n / 100.0 - 0.42) / 0.18)) * min(1.0, max(0.75, n.j / 28.0)) * min(1.0, _p36(n.m, n.j) / 12.0)
    t += 18.0 * min(1.0, max(0.0, (n.q / 100.0 - 0.3) / 0.2)) * min(1.0, max(0.75, n.j / 28.0)) * min(1.0, _p36(n.p, n.j) / 5.0) - 2.5 * max(0, _p36(n.y, n.j) - 3.0) - 2.0 * min(1.0, max(0.75, n.j / 28.0))
    return int(max(25, min(99, round(t))))


_COMPUTE_ORDER: list[tuple[str, Any]] = [
    ("agility_2k", _agility),
    ("speed_2k", _speed),
    ("strength_2k", _strength),
    ("interior_defense_2k", _interior_def),
    ("block_2k", _block),
    ("steal_2k", _steal),
    ("stamina_2k", _stamina),
    ("ball_handle_2k", _ball_handle),
    ("speed_with_ball_2k", _speed_with_ball),
    ("hustle_2k", _hustle),
    ("vertical_2k", _vertical),
    ("offensive_rebound_2k", _off_reb),
    ("defensive_rebound_2k", _def_reb),
    ("standing_dunk_2k", _stand_dunk),
    ("driving_dunk_2k", _drive_dunk),
    ("driving_layup_2k", _driving_layup),
    ("post_control_2k", _post_control),
    ("draw_foul_2k", _draw_foul),
    ("close_shot_2k", _close_shot),
    ("mid_range_shot_2k", _mid_range),
    ("three_point_shot_2k", _three),
    ("free_throws_2k", _free_throws),
    ("pass_accuracy_2k", _pass_accuracy),
    ("pass_vision_2k", _pass_vision),
    ("pass_iq_2k", _pass_iq),
    ("shot_iq_2k", _shot_iq),
    ("perimeter_defense_2k", _perimeter_def),
    ("hands_2k", _hands),
    ("defensive_consistency_2k", _def_cons),
    ("pass_perception_2k", _pass_perc),
    ("help_defense_iq_2k", _help_iq),
    ("offensive_consistency_2k", _off_cons),
]


def compute_attribute_dict(
    prospect: Mapping[str, Any],
    *,
    iterations: int = 12,
) -> dict[str, int]:
    n = _row_from_prospect(prospect)
    st: dict[str, int] = {a: 60 for a in _ATTR_ORDER}
    for _ in range(max(1, iterations)):
        for key, fn in _COMPUTE_ORDER:
            st[key] = int(max(0, min(99, fn(n, st))))
    return {k: st[k] for k in _ATTR_ORDER if k in st}


def calculate_excel_2026_ratings(player_data: dict) -> dict:
    """Derive workbook-aligned post game, intangibles, durability, and potential.

    Durability uses attendance vs ``team_total_games`` (availability penalty below
    90% ``gp`` ratio); ``Durablity`` / automation spelling stays in
    :mod:`src.automation.controller_mapping`.
    """
    # Logic aligned with prospects 2026 (1).xlsx index mapping
    def _num(key: str, default: float = 0.0) -> float:
        v = player_data.get(key)
        if v is None:
            return default
        try:
            x = float(v)
            if math.isnan(x) or math.isinf(x):
                return default
            return x
        except (TypeError, ValueError):
            return default

    c_h = _num("combine_height_in")
    height_in = c_h if c_h > 0 else _num("height_in")
    age = _num("age", 19.0)
    close_s = _num("close_shot_2k", 60.0)
    post_c = _num("post_control_2k", 60.0)
    mid_r = _num("mid_range_shot_2k", 60.0)
    shot_iq = _num("shot_iq_2k", 60.0)
    hustle = _num("hustle_2k", 60.0)
    off_c = _num("offensive_consistency_2k", 60.0)
    def_c = _num("defensive_consistency_2k", 60.0)
    overall_2k = _num("overall_2k", 60.0)

    if height_in >= 84:
        height_mod = 5.0
    elif height_in >= 82:
        height_mod = 3.0
    else:
        height_mod = 0.0

    post_hook_f = (close_s * 0.7) + (post_c * 0.3) + height_mod
    post_fade_f = (mid_r * 0.75) + (post_c * 0.25)
    avg_consistency = (off_c + def_c) / 2.0
    intangibles_f = (
        (shot_iq * 0.35) + (hustle * 0.35) + (avg_consistency * 0.30)
    )

    years_over_19 = max(0.0, age - 19.0)

    gp = max(0.0, _num("gp"))
    ttg_raw = _num("team_total_games")
    ttg = float(ttg_raw) if ttg_raw > 0 else (gp if gp > 0 else 0.0)
    if gp <= 0 or ttg <= 0:
        gp_ratio = 1.0
    else:
        gp_ratio = min(1.0, gp / ttg)
    if gp_ratio >= 0.90:
        avail_penalty = 0.0
    else:
        avail_penalty = (0.90 - gp_ratio) * 40.0

    durability_f = 85.0 - (1.5 * years_over_19) - avail_penalty

    rank = player_data.get("espn_rank")
    if rank is None:
        rank_term = 0.0
    else:
        try:
            rank_term = (100.0 - float(rank)) * 0.12
        except (TypeError, ValueError):
            rank_term = 0.0
        if math.isnan(rank_term) or math.isinf(rank_term):
            rank_term = 0.0

    age_gap = (24.0 - age) * 2.2
    potential_f = overall_2k + age_gap + rank_term

    def _clamp_attr(x: float) -> int:
        return int(max(25, min(99, round(x))))

    return {
        "post_hook_2k": _clamp_attr(post_hook_f),
        "post_fade_2k": _clamp_attr(post_fade_f),
        "intangibles_2k": _clamp_attr(intangibles_f),
        "durability_2k": _clamp_attr(durability_f),
        "potential": int(round(potential_f)),
    }


def apply_excel_2026_to_prospect(
    prospect: Mapping[str, Any],
    registry: FormulaRegistry,
) -> tuple[dict[str, int], dict[str, str]]:
    base = compute_attribute_dict(prospect)
    feats = {k: float(v) for k, v in base.items()}
    overall = registry.evaluate("overall_2k", feats)
    out = dict(base)
    if overall is not None:
        out["overall_2k"] = int(max(25, min(99, round(overall))))
    else:
        out["overall_2k"] = int(
            max(25, min(99, round(sum(base.values()) / max(1, len(base))))))
    merged: dict[str, Any] = dict(prospect)
    merged.update(out)
    derived = calculate_excel_2026_ratings(merged)
    out.update(derived)
    prov = {k: "excel_2026" for k in out}
    prov["overall_2k"] = "excel_2026+overall_yaml"
    for k in derived:
        prov[k] = "excel_2026+derived"
    return out, prov
