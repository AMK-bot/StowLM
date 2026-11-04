"""Flip-gate fleet agreement for the feasibility API consumed by the mask.

These native-geometry fixtures deliberately have no hull: they establish move
and static-rule agreement, not a full physics-checker qualification. Each trace
completes its seeded manifest; no short prefix is counted as a completion.
"""
from __future__ import annotations

from collections import Counter
import random
from time import perf_counter

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker.rules import check_static_rules, placement_violations
from stowage.conventions import make_container_id
from stowage.model.fixtures import build_instance
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel


def _case(vessel_name, rung, h, seed):
    rng = random.Random(seed)
    vessel = load_vessel(vessel_name, with_hull=False)
    cells = {c.id: c for c in vessel.cells}
    deck = [s for s in vessel.stacks if s.on_deck and s.bay == 1]
    deck.sort(key=lambda s: cells[s.cell_ids[0]].tcg_m)  # far to near, port berth
    chosen = [deck[0], deck[-1]] if rung == "tiny" else [deck[0], deck[len(deck)//2], deck[-1]]
    stacks = chosen
    selected = {c for s in stacks for c in s.cell_ids[:h]}
    vessel = vessel.model_copy(update={
        "cells": [c for c in vessel.cells if c.id in selected],
        "stacks": [s.model_copy(update={"cell_ids": [c for c in s.cell_ids if c in selected]}) for s in stacks],
        "hatch_covers": [cover.model_copy(update={"hold_stack_ids": [],
            "deck_stack_ids": [s.id for s in stacks if s.id in cover.deck_stack_ids]})
            for cover in vessel.hatch_covers if any(s.id in cover.deck_stack_ids for s in stacks)],
        "fixed_loads": [], "bridge": None})
    inst = build_instance()
    inst.id = f"feasibility-agreement-{vessel_name}-{rung}-H{h}-{seed}"
    inst.size_class, inst.seed, inst.vessel = rung, seed, vessel
    inst.port = load_port("small_feeder" if h == 4 else "laem_chabang_like")
    inst.port.max_crane_height_tiers = h
    inst.port.quay_crane.max_on_deck_tiers = h
    inst.rotation.calls = inst.rotation.calls[:2]
    destination = inst.rotation.calls[1]
    destination.locode, destination.archetype = inst.port.locode, inst.port.archetype
    destination.max_on_deck_tiers = h
    inst.onboard = inst.onboard.model_copy(update={"vessel_id": vessel.id, "placements": [], "tank_masses_kg": {}})
    inst.yard = inst.yard.model_copy(update={"yard_id": inst.port.yard.id, "stacks": [], "quay_buffer": []})
    template = inst.containers[2]
    boxes, trace, yard = [], [], []
    from stowage.model.yard import YardStack
    for stack in vessel.stacks:
        targets = stack.cell_ids[:1] if rung == "tiny" else stack.cell_ids
        for slot in targets:
            cid = make_container_id("QZWU", seed*1000+len(boxes)+1)
            boxes.append(template.model_copy(update={"id": cid, "dg": None, "oog": None,
                "special_stowage": [], "reefer": False, "reefer_setpoint_c": None,
                "height_m": 2.591, "gross_mass_kg": 9000., "pol": inst.port.locode, "pod": destination.locode}))
            trace.append(Place(cid, slot))
            yard.append(YardStack(key=f"{inst.port.yard.blocks[0].id}-{len(boxes):03d}-01",
                                  length_ft=20, boxes=[cid]))
    # Seed changes box identities and independent yard order, without changing
    # the far-to-near completed witness required when a stack reaches H.
    rng.shuffle(yard)
    inst.containers, inst.load_list, inst.discharge_list = boxes, [b.id for b in boxes], []
    inst.yard.stacks = yard
    inst.family = {"vessel": vessel_name, "port": inst.port.archetype}
    return inst, trace


@pytest.mark.parametrize("enabled", [False, True], ids=["legacy", "activated"])
@pytest.mark.parametrize("vessel", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("rung", ["tiny", "small"])
@pytest.mark.parametrize("crane_h", [4, 8])
def test_seeded_fleet_move_and_completed_static_agreement(h, crane_h, vessel, rung, enabled, record_property):
    with rule_freeze_v1(enabled=enabled):
        inst, trace = _case(vessel, rung, crane_h, 23)
        eng = h.engine(inst, {"global_screen": 0})
        ex, world, counts = Expander(inst), initial_state(inst), Counter()
        for expected in trace:
            t0 = perf_counter()
            offered = eng.legal_actions()
            counts["prefix_us"] += (perf_counter()-t0)*1e6
            counts["prefixes"] += 1
            assert tuple(eng.decode_index(int(flat)) for flat in eng.legal_index()) == offered
            assert all(eng.is_legal(action) for action in offered)
            # Every offered action must replay; the checker owns the verdict.
            for action in offered:
                probe, current = ex.copy(), world
                try:
                    moves = probe.step(action)
                    for move in moves:
                        before = current
                        current = apply_move(inst, current, move)
                    if isinstance(action, Place):
                        assert not any(v.severity == "hard" for v in placement_violations(
                            inst, before.ship, eng._boxes[action.box], action.slot))
                except (ValueError, IllegalMoveError, AssertionError):
                    counts["false_accepts"] += 1
                counts["offered_actions"] += 1
            if expected not in offered:
                counts["false_rejects"] += 1
            assert not counts["false_accepts"], counts
            assert expected in offered, (inst.id, expected, eng.dead_end(), counts)
            for move in ex.step(expected):
                world = apply_move(inst, world, move)
            eng.push(expected)
        assert eng.is_complete() and eng.legal_actions() == ()
        assert not any(v.severity == "hard" for v in
                       check_static_rules(inst, world.ship, call_index=inst.call_index).violations)
        plan = eng.to_plan()
        result = apply_plan(inst, plan)
        assert result.legal, result.violations
        if enabled:
            assert result.d62_crossing is not None
        record_property("agreement", dict(counts))
        print(inst.id, "activated" if enabled else "legacy", dict(counts))


@pytest.mark.parametrize("screens,expected", [
    (0, "8bb71295468ebb49c860d9091e02ddf454adc7a9332db56435cd793b77effa32"),
    (1, "2adc4c1e48f3fae54a49f5858c03a23200cf5c3a66c03ed2da7e0f5e44d03dd0"),
])
def test_switch_off_matches_preorder_ordered_masks(h, screens, expected):
    """Digests captured from the original working-tree source before edits."""
    import hashlib
    import json
    from stowage.actions.samples import tiny_instance

    rows = []
    with rule_freeze_v1(enabled=False):
        for seed in (3, 11, 23):
            inst = tiny_instance(seed, n_load=4, n_other=2, p40=.2)
            inst.vessel.hull = None
            eng = h.engine(inst, {"completion_screens": screens, "global_screen": 0})
            for _ in range(6):
                actions = eng.legal_actions()
                rows.append([inst.id, eng.depth, [repr(a) for a in actions], eng.is_complete()])
                if not actions:
                    break
                eng.push(actions[seed % len(actions)])
    assert hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() == expected
