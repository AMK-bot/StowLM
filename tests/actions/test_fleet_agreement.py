"""Fleet geometry agreement, with complete fixed-manifest traces, not prefixes.

These are move/expander tests, not full-checker loading cases: geometry-only
fleet builds deliberately omit hull/tanks. Singleton target domains exhaust the
seeded manifest policy, including unsupported and hatch-blocked targets; they do
not exhaust the unrestricted feasibility mask. No mask implementation is used.
"""
from __future__ import annotations

from collections import Counter
import random

import pytest

from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.conventions import SYNTHETIC_OWNER_CODES, cells_of_slot, make_container_id, parse_slot
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_move, initial_state
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel


def _fleet_manifest(vessel_name, port_name, seed):
    """Real fleet geometry; bounded cargo deliberately reaches every usable tier."""
    rng = random.Random(seed)
    vessel = load_vessel(vessel_name, with_hull=False)
    port = load_port(port_name)
    h = port.max_crane_height_tiers
    cells = {c.id: c for c in vessel.cells}
    stacks = {s.id: s for s in vessel.stacks}
    deck = [s for s in vessel.stacks if s.on_deck]
    pairs = sorted({s.bay + 1 for s in deck if s.bay % 4 == 1})
    rng.shuffle(pairs)
    lengths = (20, 40, 45)
    groups = []
    for length, even_bay in zip(lengths, pairs, strict=False):
        candidates = [s for s in deck if s.bay == even_bay - 1]
        if length == 45:
            candidates = [s for s in candidates if all(
                cells[c].accepts_45 for c in s.cell_ids)]
        candidates.sort(key=lambda s: cells[s.cell_ids[0]].tcg_m)
        chosen = [candidates[0], candidates[len(candidates) // 2], candidates[-1]]
        columns = []
        for stack in chosen:
            slots = [f"{even_bay if length != 20 else stack.bay:03d}{stack.row:02d}{parse_slot(c).tier:02d}"
                     for c in stack.cell_ids[:h]]
            assert all(c in cells for slot in slots for c in cells_of_slot(slot))
            columns.append(slots)
        groups.append((length, columns))
    # Open and close an actual multi-row fleet cover before building deck stacks.
    hold_bay = parse_slot(groups[0][1][0][0]).bay
    hold = next(s for s in vessel.stacks if not s.on_deck and s.cell_ids and s.bay == hold_bay)
    cover = next(c for c in vessel.hatch_covers if c.id == hold.hatch_cover_id)
    assert len({stacks[sid].row for sid in cover.deck_stack_ids}) > 1
    plans = [(20, [hold.cell_ids[0]])]
    plans.extend((length, column) for length, columns in groups for column in columns)
    boxes, yard, columns, targets = [], [], [], {}
    # Keep native yard geometry: allocate disjoint singleton footprints
    # within its blocks/rows so neither long-box half overlaps.
    grounds = [(b.id, bay, row) for b in port.yard.blocks
               for row in range(1, b.n_rows + 1) for bay in range(1, b.n_bays, 2)]
    serial = 1
    for length, slots in plans:
        column = []
        for slot in slots:
            cid = make_container_id(SYNTHETIC_OWNER_CODES[1], seed * 1000 + serial)
            serial += 1
            block, bay, row = grounds.pop(0)
            boxes.append(dict(id=cid, length_ft=length, iso_type={20: "22G1", 40: "42G1", 45: "L2G1"}[length],
                              height_m=2.591, tare_kg=2200 if length == 20 else 3800,
                              gross_mass_kg=9000, pol=port.locode, pod="THSGZ"))
            yard.append(dict(key=f"{block}-{bay:03d}-{row:02d}", length_ft=length, boxes=[cid]))
            action = Place(cid, slot)
            column.append(action)
            targets[cid] = (slot,)
        columns.append(column)
    payload = build_instance().model_dump()
    payload.update(id=f"fleet-agreement-{vessel_name}-{port_name}-{seed}", stage="A", size_class="small",
                   seed=seed, family={"vessel": vessel_name, "port": port_name}, vessel=vessel.model_dump(),
                   port=port.model_dump(), containers=boxes, load_list=[b["id"] for b in boxes], discharge_list=[],
                   yard=dict(yard_id=port.yard.id, stacks=yard),
                   onboard=dict(vessel_id=vessel.id, placements=[], tank_masses_kg={}),
                   rotation=dict(service="synthetic-fleet-agreement", calls=[
                       dict(index=0, locode=port.locode, archetype=port_name),
                       dict(index=1, locode="THSGZ")]))
    return Instance.model_validate(payload), columns, targets


def _probe(instance, ex, state, action, targets, counts, *, allow_prospective_rejection=False):
    offered = ex.can_expand(action, pending_load_targets=targets)
    trial = ex.copy()
    try:
        moves = trial.step(action)
    except ValueError as error:
        counts["rejected"] += 1
        counts[f"rejection:{error}"] += 1
        assert not offered, (action, "false accept", error)
        return None
    assert allow_prospective_rejection or offered, (action, "unexpected rejection")
    if not offered:
        counts["prospective_rejected"] += 1
    replayed = state
    for move in moves:
        replayed = apply_move(instance, replayed, move)
        counts[f"move:{move.kind}"] += 1
    assert replayed == trial.world_state(), action
    counts["accepted"] += 1
    counts["moves"] += len(moves)
    return trial, replayed, offered


def _trace(instance, columns, targets, seed, *, max_depth=None):
    ex, state = Expander(instance), initial_state(instance)
    rng, counts = random.Random(seed), Counter()
    pending = [list(c) for c in columns]
    for _ in range(len(instance.load_list) if max_depth is None else max_depth):
        if not any(pending):
            break
        advances = {}
        candidates = [(i, column[0]) for i, column in enumerate(pending) if column]
        for i, action in candidates:
            result = _probe(instance, ex, state, action, targets, counts)
            if result and result[2]:
                advances[i] = result[:2]
        # Load the hold first; then independently randomise the size/bay group,
        # respecting far-row-first within a group to retain completion at H=4.
        eligible = [0] if pending[0] else [
            next(i for i in range(start, start + 3) if pending[i])
            for start in (1, 4, 7) if any(pending[start:start + 3])]
        eligible = [i for i in eligible if i in advances]
        assert eligible, (instance.id, counts)
        chosen = rng.choice(eligible)
        previous_moves = len(ex.moves)
        ex, state = advances[chosen]
        counts["committed_moves"] += len(ex.moves) - previous_moves
        pending[chosen].pop(0)
        counts["committed_actions"] += 1
    assert not any(pending), "fleet evidence must complete the manifest, not stop at a three-action prefix"
    for move in ex.finish():
        state = apply_move(instance, state, move)
        counts[f"finish:{move.kind}"] += 1
        counts["committed_moves"] += 1
    assert state == ex.world_state()
    assert all(ex.location(cid)[0] == "ship" for cid in instance.load_list)
    assert not ex.buffer and not ex.covers_off
    return counts


def _near_first_forks(instance, columns, targets):
    """Force the action D69 screens, then witness its blocked far-row lift.

    These diagnostic forks are deliberately incomplete. The main trace is the
    completion evidence; each fork tests a different 20/40/45-foot footprint.
    """
    counts = Counter()
    for start in (1, 4, 7):
        ex, state = Expander(instance), initial_state(instance)
        ex, state, _ = _probe(instance, ex, state, columns[0][0], targets, counts)
        near = columns[start + 2]
        for index, action in enumerate(near):
            result = _probe(instance, ex, state, action, targets, counts, allow_prospective_rejection=True)
            assert result is not None
            ex, state, offered = result
            assert offered == (index < len(near) - 1), (action, index)
        assert _probe(instance, ex, state, columns[start][0], targets, counts) is None
    assert counts["prospective_rejected"] == 3
    assert counts["rejected"] == 3
    assert all("port.e3.quay_crossing" in reason for reason in counts if reason.startswith("rejection:"))
    return counts


@pytest.mark.parametrize("vessel_name", ["feeder_500", "feeder_800", "feeder_1700"])
@pytest.mark.parametrize("port_name,h", [("small_feeder", 4), ("laem_chabang_like", 8)])
def test_complete_seeded_fleet_expander_engine_agreement(vessel_name, port_name, h, record_property):
    with rule_freeze_v1():
        instance, columns, targets = _fleet_manifest(vessel_name, port_name, 23)
        assert instance.port.max_crane_height_tiers == h
        counts = _trace(instance, columns, targets, 23)
        assert counts["committed_actions"] == len(instance.load_list) > 3
        assert counts["move:hatch_cover_off"] > 0
        assert counts["move:hatch_cover_on"] > 0
        native_depth = max(len(s.cell_ids) for s in instance.vessel.stacks if s.on_deck)
        assert all(len(column) == min(h, native_depth) for column in columns[1:])
        if h == 4:
            forks = _near_first_forks(instance, columns, targets)
            record_property("near_first_crossing_forks", dict(forks))
            print(instance.id, "near-first forks", dict(forks))
    record_property("fleet_agreement", dict(counts))
    print(instance.id, dict(counts))


def test_three_action_prefix_cannot_qualify_as_complete_fleet_evidence():
    """Pin the coverage failure demonstrated before replacing the old bound."""
    with rule_freeze_v1():
        instance, columns, targets = _fleet_manifest("feeder_500", "small_feeder", 23)
        with pytest.raises(AssertionError, match="must complete the manifest"):
            _trace(instance, columns, targets, 23, max_depth=3)
