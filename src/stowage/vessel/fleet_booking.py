"""D48 expected booking CG on the actual decision geometry.

APPROXIMATION: continuous equivalent standard-height ISO20 masses anchor a
heavy-low booking reference. They are neither the actual mixed-length booking
stow nor onboard cargo, fixed mass, or move-objective inputs. The complete
checker must evaluate every real plan using its actual placement moments.
"""
from __future__ import annotations

from itertools import groupby
from math import fsum, isclose, isfinite
from numbers import Real

from stowage.conventions import CONTAINER_HEIGHT_STD_M
from stowage.model import Vessel

METHOD = "bounded_c3_heavy_low_iso20_v1"
APPROXIMATION = (
    "APPROXIMATION: D48 continuous equivalent standard-height ISO20 booking reference; "
    "not an actual mixed-length cargo stow, additional fixed mass, or move-objective input. "
    "Equivalent masses can be below equipment tare; actual plans require every full checker rule."
)


def booking_reference(vessel: Vessel, reference_placements: list[tuple[str, float]], *,
                      expected_load_mass_kg: float, reference_seed: int) -> dict:
    """Return a deterministic capacity-bounded booking reference and its audit.

    A legal C3 profile scaled down retains its exact relative masses and CG.
    Otherwise share mass across equal-height cells before moving upward, respecting
    each column's prefix, nonincreasing tier masses, height and 20-foot rating.
    The 30.480 t hold / 24.999 t deck cell caps are the existing synthetic C3
    envelope, not claims about a universal container certification rating.
    Decision hold and deck columns remain available even on a shared cover.
    """
    if (isinstance(expected_load_mass_kg, bool) or not isinstance(expected_load_mass_kg, Real)
            or not isfinite(expected_load_mass_kg) or expected_load_mass_kg < 0):
        raise ValueError("booking mass must be finite and nonnegative")
    if type(reference_seed) is not int:
        raise ValueError("booking reference seed must be an integer")
    mass = float(expected_load_mass_kg)
    cells = {cell.id: cell for cell in vessel.cells}
    for cell in cells.values():
        if not all(isfinite(value) for value in (cell.lcg_m, cell.tcg_m, cell.vcg_base_m)):
            raise ValueError("booking reference requires finite decision geometry")
    columns = []
    eligible = {}
    for stack in sorted(vessel.stacks, key=lambda item: item.id):
        rating = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
        if not all(isfinite(value) for value in (rating, stack.base_z_m, stack.max_height_m)) or rating < 0:
            raise ValueError("booking reference requires finite nonnegative stack geometry and rating")
        cap = 24999. if stack.on_deck else 30480.
        column = []
        for index, cid in enumerate(stack.cell_ids):
            if cid not in cells:
                raise ValueError(f"booking reference decision support cell unavailable: {cid}")
            cell = cells[cid]
            floor = stack.base_z_m+index*CONTAINER_HEIGHT_STD_M
            if (not cell.accepts_20 or
                    (index+1)*CONTAINER_HEIGHT_STD_M > stack.max_height_m+1e-9 or
                    not isclose(cell.vcg_base_m, floor, rel_tol=0., abs_tol=1e-6)):
                # Later tiers cannot skip an unusable or unsupported lower cell.
                break
            column.append(cid)
        capacity = min(float(rating), len(column)*cap)
        if capacity > 0:
            columns.append((stack.id, column, cap, capacity))
            eligible.update({cid: (stack.id, index, cap) for index, cid in enumerate(column)})
    capacity = fsum(column[3] for column in columns)
    if mass > capacity:
        raise ValueError(f"booking mass exceeds physical decision ISO20 capacity: {mass} > {capacity} kg")

    reference = {}
    seen = set()
    for cid, value in reference_placements:
        if (isinstance(value, bool) or not isinstance(value, Real) or
                not isfinite(value) or value < 0):
            raise ValueError("booking reference cell mass must be finite and nonnegative")
        if cid in seen:
            raise ValueError(f"duplicate booking reference cell: {cid}")
        seen.add(cid)
        if cid in cells and value > 0:
            reference[cid] = float(value)
    try:
        reference_mass = fsum(reference.values())
    except OverflowError as exc:
        raise ValueError("booking reference mass sum must be finite") from exc
    if not isfinite(reference_mass):
        raise ValueError("booking reference mass sum must be finite")
    normalization = mass/reference_mass if reference_mass > 0 else None

    def bounded(values):
        if any(cid not in eligible or not isfinite(value) or value <= 0 for cid, value in values.items()):
            return False
        for _, column, cap, limit in columns:
            weights = [values.get(cid, 0.) for cid in column]
            occupied = [cid for cid in column if cid in values]
            if occupied != column[:len(occupied)]:
                return False
            if (fsum(weights) > limit+1e-6 or any(weight > cap+1e-6 for weight in weights)
                    or any(upper > lower+1e-6 for lower, upper in zip(weights, weights[1:]))):
                return False
        return True

    if mass == 0:
        values, mode = {}, "zero_mass"
    elif reference_mass >= mass:
        values = {cid: value*normalization for cid, value in reference.items()}
        mode = "scaled_C3" if bounded(values) else "bounded_heavy_low"
    else:
        values, mode = {}, "bounded_heavy_low"
    if mode == "bounded_heavy_low":
        values = {}
        used = {sid: 0. for sid, _, _, _ in columns}
        remaining = mass
        entries = sorted((cells[cid].vcg_base_m, cid, sid, index, cap, limit, column)
                         for sid, column, cap, limit in columns for index, cid in enumerate(column))
        for _, floor_entries in groupby(entries, key=lambda entry: entry[0]):
            group = list(floor_entries)
            capacities = [max(0., min(cap, limit-used[sid],
                            values.get(column[index-1], 0.) if index else cap))
                          for _, cid, sid, index, cap, limit, column in group]
            amounts = [0.]*len(group)
            left = min(remaining, fsum(capacities))
            active = [i for i, cap in enumerate(capacities) if cap > 0]
            # Capped water filling gives tied columns equal mass until a real
            # support/rating limit binds. Cell-ID order cannot concentrate the
            # entire booking in one end of an otherwise equal-height region.
            while active and left > 0:
                share = left/len(active)
                saturated = [i for i in active if capacities[i] <= share]
                if not saturated:
                    for i in active:
                        amounts[i] = share
                    # Retain subnormal and floating residual mass without
                    # exceeding any of the independently calculated bounds.
                    residual = left-fsum(amounts[i] for i in active)
                    for i in active:
                        adjustment = min(capacities[i]-amounts[i], residual)
                        amounts[i] += adjustment
                        residual -= adjustment
                        if residual == 0:
                            break
                    break
                for i in saturated:
                    amounts[i] = capacities[i]
                left = max(0., left-fsum(capacities[i] for i in saturated))
                active = [i for i in active if i not in saturated]
            for entry, amount in zip(group, amounts):
                _, cid, sid, *_ = entry
                if amount > 0:
                    values[cid] = amount
                    used[sid] += amount
            remaining = max(0., mass-fsum(values.values()))
            if remaining == 0:
                break
        if remaining > 1e-6 or not bounded(values):
            raise ValueError("booking mass cannot fit the supported decision ISO20 capacity")

    if not isclose(fsum(values.values()), mass, rel_tol=1e-12, abs_tol=0.):
        raise ValueError("booking reference cell masses do not reconcile with booking mass")
    rows = [dict(cell_id=cid, stack_id=cells[cid].stack_id, mass_kg=value,
                 cg_m=[cells[cid].lcg_m, cells[cid].tcg_m,
                       cells[cid].vcg_base_m+CONTAINER_HEIGHT_STD_M/2])
            for cid, value in sorted(values.items())]
    if mass:
        cg = [fsum((row["mass_kg"]/mass)*row["cg_m"][axis] for row in rows) for axis in range(3)]
        zero_policy = None
    elif eligible:
        cell = min((cells[cid] for cid in eligible), key=lambda item: (item.vcg_base_m, item.id))
        cg = [cell.lcg_m, cell.tcg_m, cell.vcg_base_m+CONTAINER_HEIGHT_STD_M/2]
        zero_policy = "lowest_supported_iso20_cell"
    else:
        cg, zero_policy = [0., 0., 0.], "origin_no_supported_iso20_cells"
    if not all(isfinite(value) for value in cg):
        raise ValueError("booking reference requires finite normalized CG geometry")
    return dict(method=METHOD, allocation_policy_version=2, allocation_mode=mode, cg_m=cg, mass_kg=mass,
                cells=rows, normalization=normalization, capacity_kg=capacity,
                reference_mass_kg=reference_mass, reference_seed=reference_seed,
                standard_height_m=CONTAINER_HEIGHT_STD_M, length_ft=20,
                zero_mass_cg_policy=zero_policy, actual_booking_stow=False,
                contributes_fixed_mass=False, approximation=APPROXIMATION)
