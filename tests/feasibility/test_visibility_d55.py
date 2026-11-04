"""D55 height-screen regressions, with real frozen attribution geometry.

Only the boundary-height unit case changes synthetic stack bases. The seeded
soundness test invokes the public visibility part at actual numerical loading
conditions, without replacing checker geometry or equilibrium.
"""
from __future__ import annotations

from collections import defaultdict
import gzip
from pathlib import Path
import random

import numpy as np
import pytest

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker.full import departure_condition
from stowage.checker.visibility import _all_cargo_geometry, _loading_condition, check_visibility
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance, MovePlan

ROOT = Path(__file__).resolve().parents[2]
INPUTS = Path(__file__).with_name("data") / "d55"
CASES = [
    ("small-100100015-2a1913beea", "QZXU0000170", "0020190"),
    ("small-100100036-14a29a0f3c", "QZXU0000009", "0020390"),
]


def _load(case):
    instance = Instance.model_validate_json(gzip.decompress((INPUTS / (case + "-instance.json.gz")).read_bytes()))
    plan = MovePlan.model_validate_json(gzip.decompress((INPUTS / (case + "-input-plan.json.gz")).read_bytes()))
    return instance, departure_condition(instance, plan)


def _last_placement(case, box):
    instance, ship = _load(case)
    data = instance.model_dump()
    data["load_list"] = [box]
    data["onboard"]["placements"] = [p.model_dump() for p in ship.placements if p.container_id != box]
    aboard = {p["container_id"] for p in data["onboard"]["placements"]}
    for stack in data["yard"]["stacks"]:
        stack["boxes"] = [identifier for identifier in stack["boxes"] if identifier not in aboard]
    data["yard"]["stacks"] = [stack for stack in data["yard"]["stacks"] if stack["boxes"]]
    return type(instance).model_validate(data)


def _engine(instance):
    return make_feasibility_engine(instance, get_action_space("boxslot"), config=FeasibilityConfig(
        extra={"completion_screens": 0, "global_screen": 0}))


@pytest.mark.parametrize("case,box,slot", CASES)
def test_d55_attributed_bay002_tier90_placements_rejected(case, box, slot):
    """Both placements passed every pre-D55 local engine check (reproduced)."""
    engine = _engine(_last_placement(case, box))
    assert not engine.is_legal(Place(box, slot))


def test_d55_placement_just_under_cap_is_allowed():
    case, box, slot = CASES[0]
    instance = _last_placement(case, box)
    engine = _engine(instance)
    T = engine.T
    si = T.slot_index[slot]
    js = T.slot_stacks[si]
    js = js[js >= 0]
    cap = min(T.extra["stack_visibility_top"][js])
    assert np.isfinite(cap)
    # SYNTHETIC boundary fixture: move this paired support base vertically,
    # keeping real ship/eye/hull and box dimensions. This isolates the exact
    # cap comparison without changing the regulatory sight-plane geometry.
    roof = max(T.stack_base_z[js] + engine._cur.sh[js]) + T.height[T.box_index[box]]
    shift = roof - (cap - 1e-5)
    data = instance.model_dump()
    affected = {T.stack_ids[int(j)] for j in js}
    for stack in data["vessel"]["stacks"]:
        if stack["id"] in affected:
            stack["base_z_m"] -= shift
    for cell in data["vessel"]["cells"]:
        if cell["stack_id"] in affected:
            cell["vcg_base_m"] -= shift
    engine = _engine(type(instance).model_validate(data))
    assert engine.is_legal(Place(box, slot))
    assert Place(box, slot) in engine.legal_actions()


def test_d55_seeded_caps_never_cut_actual_public_visibility_acceptance(record_property):
    """Compare cap with public exact visibility on supported real loading states.

    This is a seeded regression sweep, not a proof over the continuous domain.
    Each included state satisfies the independently declared D40 physical limits;
    no solution or attitude is substituted into the checker.
    """
    instance, full = _load("small-100100051-63b32eb7c3")
    engine = _engine(instance)
    T, hull = engine.T, instance.vessel.hull
    caps = T.extra["stack_visibility_top"]
    columns = defaultdict(list)
    for placement in sorted(full.placements, key=lambda p: p.slot[-2:]):
        columns[placement.slot[:-2]].append(placement)
    rng = random.Random(550044)
    accepted, compared = 0, 0
    for draw in range(12):
        # Whole supported prefixes of each physical long-box column. The final
        # draw is the archived visibility-passing complete real stow.
        placements = [p for column in columns.values()
                      for p in column[:len(column) if draw == 11 else rng.randint(0, len(column))]]
        ship = full.model_copy(deep=True, update={"placements": placements})
        condition = _loading_condition(instance, ship)
        trim = condition.t_aft_m-condition.t_fwd_m
        if not (hull.min_displacement_kg <= condition.mass_kg <= hull.max_displacement_kg
                and hull.min_trim_m <= trim <= hull.max_trim_m
                and abs(condition.heel_rad) <= hull.max_list_rad
                and condition.t_fwd_m >= hull.min_draught_fwd_m
                and max(condition.t_aft_m, condition.t_fwd_m) <= hull.max_draught_m):
            continue
        report = check_visibility(instance, ship, call_index=instance.call_index)
        assert report.ran, report.error
        if any(v.severity == "hard" for v in report.violations):
            continue
        accepted += 1
        for record in _all_cargo_geometry(instance, ship):
            if record.background or int(record.placement.slot[-2:]) < 80:
                continue
            for cell in record.physical:
                assert record.top <= caps[T.stack_index[cell.stack_id]] + 1e-8, (
                    draw, record.placement.slot, record.top, caps[T.stack_index[cell.stack_id]])
                compared += 1
    record_property("seed", 550044)
    record_property("draws", 12)
    record_property("visibility_accepted_admissible_states", accepted)
    record_property("compared_planned_support_roofs", compared)
    assert accepted >= 4, (accepted, compared)
    assert compared >= 20, (accepted, compared)
