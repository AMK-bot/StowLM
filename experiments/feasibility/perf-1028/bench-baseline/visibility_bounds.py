"""D55 necessary visibility bounds, independent of any linear qualification.

This uses the exact waterplane and roof sight-plane geometry of
``checker.visibility`` / ``HullGeometry._axes``. The D40 manual envelope is an
outer set of every qualified slice envelope: its supremum is therefore safe
also while a linear model is unavailable or undergoing requalification.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import math
from types import MappingProxyType
from typing import Mapping

from stowage.conventions import parse_slot


@dataclass(frozen=True)
class VisibilityCaps:
    """Absolute roof elevations; infinity means this screen proves nothing."""

    top_by_stack: Mapping[str, float]
    diagnostics: tuple[str, ...]
    cache_key: str


def compute_visibility_caps(instance) -> VisibilityCaps:
    """Return a content-cached, necessary-sound cap for each decision stack.

    Content includes all mutable vessel geometry and loading-manual declarations,
    hence slices with the same vessel ID cannot reuse another slice's caps.
    Missing evidence disables this *additional* screen; the full checker and C2
    continue to enforce their missing-data contracts.
    """
    content = instance.vessel.model_dump_json()
    # Canonicalization makes equivalent model serializations share their entry.
    canonical = json.dumps(json.loads(content), sort_keys=True, separators=(",", ":"))
    return _cached_caps(canonical)


@lru_cache(maxsize=64)
def _cached_caps(content: str) -> VisibilityCaps:
    vessel = json.loads(content)
    key = hashlib.sha256(("d55-waterplane-envelope-v1\0" + content).encode()).hexdigest()
    stacks = vessel.get("stacks", [])
    caps = {stack["id"]: math.inf for stack in stacks}
    notes = []

    def result():
        return VisibilityCaps(MappingProxyType(caps), tuple(notes), key)

    hull, bridge = vessel.get("hull"), vessel.get("bridge")
    if not hull or not bridge or not hull.get("hull_offsets_ref"):
        notes.append("visibility cap unavailable: bridge/hull geometry is missing")
        return result()
    required = ("lpp_m", "loa_m", "max_draught_m", "min_draught_fwd_m",
                "min_trim_m", "max_trim_m", "max_list_rad",
                "min_displacement_kg", "max_displacement_kg")
    try:
        values = [float(hull[field]) for field in required]
        ex, ez, bx = (float(bridge[field]) for field in ("x_m", "eye_z_m", "bow_x_m"))
        if not all(math.isfinite(v) for v in (*values, ex, ez, bx)):
            raise ValueError("nonfinite envelope")
        length, loa, ta_max, tf_min, trim_min, trim_max, heel, dmin, dmax = values
        if (length <= 0 or loa <= 0 or bx <= ex or trim_min > trim_max or
                not 0 <= heel < math.pi/2-1e-5 or dmin <= 0 or dmax < dmin):
            raise ValueError("invalid envelope")
        umin, umax = trim_min/length, trim_max/length
        uma = max(abs(umin), abs(umax))
        vmax = math.tan(heel)
        hmin = ez + min(umin*ex, umax*ex) - ta_max
        # SOLAS V/22.1.1, reproduced in data/raw/irs/main_rules_july_2026.txt,
        # Pt.5 Ch.31 §3.1.4.3, printed p.6 / PDF p.2439: the sea-surface view
        # extends "two ship lengths, or 500 m, whichever is less" before the bow.
        limit = min(2*loa, 500.)
        fmax = bx-ex+limit
        if not all(math.isfinite(v) for v in (umin, umax, vmax, hmin, fmax)) or hmin <= 0 or fmax <= 0:
            raise ValueError("eye is not above water throughout the envelope")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        notes.append(f"visibility cap unavailable: {exc}")
        return result()

    cells = {cell["id"]: cell for cell in vessel.get("cells", [])}
    # Geometry identical to checker.visibility: n=(u,-v,1)/sqrt(1+u²+v²),
    # along=((1+v²),u*v,-u)/(sqrt(1+u²+v²)*sqrt(1+v²)),
    # across=(0,1,v)/sqrt(1+v²), u=trim/Lpp, v=tan(heel).
    denmax = math.sqrt(1+uma*uma+vmax*vmax)*math.sqrt(1+vmax*vmax)
    wedge = math.tan(math.radians(10))
    # Bound the range boundary from below to ensure a tested roof point lies
    # before it; otherwise a nonpositive roof height could escape the checker.
    ta_min = tf_min+trim_min
    bow_z_abs_max = max(abs(ta-u*bx-ez) for ta in (ta_min,ta_max)
                       for u in (umin,umax))
    bow_num_min = bx-ex - uma*bow_z_abs_max
    if bow_num_min <= 0:
        notes.append("visibility cap unavailable: forward bow projection is not certified positive")
        return result()
    boundary_min = bow_num_min/denmax + limit
    for stack in stacks:
        sid = stack["id"]
        # Planned hold cargo is deliberately omitted by the visibility checker.
        if not stack.get("on_deck") or not stack.get("cell_ids"):
            continue
        physical = [cells.get(cid) for cid in stack["cell_ids"]]
        if any(cell is None for cell in physical):
            notes.append(f"{sid}: visibility cap unavailable: missing cell geometry")
            continue
        # Every support centre must belong to the actual box roof. On a long
        # slot the engine uses both support caps; each point is inside its roof.
        # Varying coordinates within a stack require the weakest cap to cover
        # every candidate tier with this single per-stack value.
        per_cell = []
        for cell in physical:
            x, y = float(cell["lcg_m"]), float(cell["tcg_m"])
            zlo = float(stack["base_z_m"])
            zhi = zlo + float(stack["max_height_m"])
            X = x-ex
            # The checker centres long boxes at the mean of their two support
            # coordinates. A malformed/novel pair may put a support centre
            # outside that box; it then cannot be used as a roof witness.
            address = parse_slot(cell["id"])
            witness_inside = True
            for neighbour in (address.bay-2, address.bay+2):
                other = cells.get(f"{neighbour:03d}{address.row:02d}{address.tier:02d}")
                if other is None:
                    continue
                for length_ft, length_m in ((40,12.192),(45,13.716)):
                    if cell.get(f"accepts_{length_ft}") and other.get(f"accepts_{length_ft}"):
                        witness_inside &= (abs(x-float(other["lcg_m"])) <= length_m and
                                           abs(y-float(other["tcg_m"])) <= 2.438)
            zabs = max(abs(zlo-ez), abs(zhi-ez))
            amin = (X-uma*vmax*abs(y)-uma*zabs)/denmax
            amax = (1+vmax*vmax)*X+uma*vmax*abs(y)+uma*zabs
            across_abs_max = abs(y)+vmax*zabs
            if (not witness_inside or not all(math.isfinite(v) for v in (x,y,zlo,zhi)) or X <= 0 or
                    amin <= 1e-6 or amax >= boundary_min-1e-6 or
                    across_abs_max > wedge*amin-1e-6):
                per_cell.append(math.inf)
                continue
            # Eliminating checker.visibility's waterplane projections gives the
            # exact roof sight plane, with H=u*eye_x+eye_z-ta and
            # F=bow_x-eye_x+limit/sqrt(1+u²/(1+v²)):
            #   z <= eye_z-u*X+v*y-H*X/F.
            # Independent interval bounds H>=hmin>0, F<=fmax and X>0
            # RELAXATION: these bounds give the following outer upper bound,
            # without assuming the
            # physical equilibrium or a non-monotone extremum is at a corner.
            # A larger aft draught raises this absolute sight-plane height.
            upper = ez + max(-umin*X,-umax*X)+vmax*abs(y)-hmin*X/fmax
            per_cell.append(math.nextafter(upper+1e-6, math.inf))
        caps[sid] = max(per_cell)
        if not math.isfinite(caps[sid]):
            notes.append(f"{sid}: no point certified inside sight wedge over the full envelope")
    notes.append("D55 uses an analytic outer bound over the full D40 trim/list/draught envelope; qualified bands and instance limits can only tighten it")
    return result()
