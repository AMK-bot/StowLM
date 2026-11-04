"""Property tests for the boxslot action space (plan 3.8 "Done when"):

1. expander plans: legal, deterministic, and ``expand(from_plan(plan)) == plan`` byte for byte;
2. foreign plans (written move by move, as a solver might: random relocation/discharge targets, extra voluntary
   relocations, eager and redundant hatch-cover lifts, covers parked on other covers): ``from_plan`` reproduces the
   container lifts exactly and never needs more hatch-cover lifts.
"""
from __future__ import annotations

import random

import pytest

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.samples import legal_slots, random_actions, tiny_instance
from stowage.actions.space import box_moves
from stowage.conventions import cells_of_slot
from stowage.model import Instance, MovePlan
from stowage.model.moves import (
    Discharge, HatchCoverOff, HatchCoverOn, Load, Relocate, RestowOff, RestowOn,
)
from stowage.moves import IllegalMoveError, apply_move, apply_plan, initial_state

SPACE = get_action_space("boxslot")
SEEDS = range(40)  # x 2 stages = 80 attempted instances per property

# The bounded foreign random planner exhausts its 300 attempts on these seeds.
# These are generator dead ends, not evidence that the instances are infeasible.
# Account for the exact seeds and RuntimeError messages; both new failures and
# recovered seeds require an explicit coverage update. Actual foreign round-trip
# coverage is 32 Stage A plans + 34 Stage B plans, while expander coverage is 40 + 40.
FOREIGN_STUCK_SEEDS = {
    False: (6, 7, 10, 11, 23, 26, 29, 39),
    True: (7, 22, 23, 27, 31, 39),
}


def _instance(seed: int, layover: bool) -> Instance:
    return tiny_instance(seed, layover=layover, n_load=4 + seed % 4, n_other=2 + seed % 3)


# ------------------------------------------------------------------ 1. expander plans
@pytest.mark.parametrize("layover", [False, True])
def test_expand_round_trip(layover):
    ok = 0
    failures = {}
    for seed in SEEDS:
        inst = _instance(seed, layover)
        try:
            acts = random_actions(inst, seed)
        except RuntimeError as error:
            failures[seed] = str(error)
            continue
        plan = SPACE.expand(inst, acts)
        result = apply_plan(inst, plan)
        assert result.legal, (seed, result.violations)
        dumped = plan.model_dump_json()
        assert SPACE.expand(inst, acts).model_dump_json() == dumped, seed
        assert SPACE.expand(inst, [SPACE.encode(a) for a in acts]).model_dump_json() == dumped, seed
        canon = SPACE.from_plan(inst, plan)
        assert [SPACE.encode(a) for a in canon] == plan.actions, seed
        again = SPACE.expand(inst, canon)
        assert again.model_dump_json() == dumped, seed
        assert apply_plan(inst, again).count == result.count
        ok += 1
    assert failures == {}, f"unexpected generation failures: {failures}"
    assert ok == len(SEEDS)


# ------------------------------------------------------------------ 2. foreign plans
class Foreign:
    """Move-level random planner validated lift by lift with ``apply_move``."""

    def __init__(self, inst: Instance, rng: random.Random) -> None:
        self.inst, self.rng, self.call = inst, rng, inst.call_index
        self.state = initial_state(inst)
        self.moves: list = []
        self.cell_stack = {c: s for s in inst.vessel.stacks for c in s.cell_ids}
        self.stacks = {s.id: s for s in inst.vessel.stacks}
        self.covers = sorted(c.id for c in inst.vessel.hatch_covers)
        self.ground = [f"{b.id}-{bay:03d}-{row:02d}" for b in inst.port.yard.blocks
                       for bay in range(1, b.n_bays + 1) for row in range(1, b.n_rows + 1)]

    # state helpers
    def snap(self):
        return self.state, len(self.moves)

    def restore(self, snap) -> None:
        self.state = snap[0]
        del self.moves[snap[1]:]

    def apply(self, move) -> bool:
        try:
            self.state = apply_move(self.inst, self.state, move)
        except IllegalMoveError:
            return False
        self.moves.append(move)
        return True

    @property
    def off(self) -> dict[str, str]:
        return self.state.ship.covers_off

    def yard(self) -> dict[str, list[str]]:
        return {s.key: list(s.boxes) for s in self.state.yard.stacks}

    def yard_loc(self, cid: str) -> tuple[str, int, bool]:
        for key, boxes in self.yard().items():
            if cid in boxes:
                return key, boxes.index(cid) + 1, boxes[-1] == cid
        raise KeyError(cid)

    def ship(self) -> dict[str, str]:
        return {p.container_id: p.slot for p in self.state.ship.placements}

    def yard_targets(self, exclude: str | None):
        y = self.yard()
        keys = sorted(set(y) | set(self.ground))
        self.rng.shuffle(keys)
        return [(k, f"{k}-{len(y.get(k, [])) + 1:02d}") for k in keys if k != exclude]

    # hatch covers
    def cover_on(self, cover: str) -> None:
        for other, rest in sorted(self.off.items()):
            if rest == cover:
                self.cover_on(other)
        assert self.apply(HatchCoverOn(call_index=self.call, cover_id=cover))

    def cover_off(self, cover: str) -> bool:
        snap = self.snap()
        for other, rest in sorted(self.off.items()):
            if rest == cover:
                self.cover_on(other)
        dsts = ["quay"] + [c for c in self.covers if c != cover and c not in self.off]
        self.rng.shuffle(dsts)
        for dst in dsts:
            if self.apply(HatchCoverOff(call_index=self.call, cover_id=cover, dst=dst)):
                return True
        self.restore(snap)
        return False

    def access(self, slot: str) -> bool:
        need: dict[str, bool] = {}
        for cell in cells_of_slot(slot):
            st = self.cell_stack[cell]
            if st.hatch_cover_id is not None:
                need[st.hatch_cover_id] = not st.on_deck
        for cover, want_off in sorted(need.items()):
            if want_off and cover not in self.off:
                if not self.cover_off(cover):
                    return False
            elif not want_off:
                if cover in self.off:
                    self.cover_on(cover)
                for other, rest in sorted(self.off.items()):
                    if rest == cover:
                        self.cover_on(other)
        return True

    def close_all(self) -> None:
        while self.off:
            free = [c for c in sorted(self.off) if c not in self.off.values()]
            self.cover_on(free[0])

    # container lifts
    def relocate(self, cid: str) -> bool:
        key, tier, top = self.yard_loc(cid)
        assert top
        src = f"{key}-{tier:02d}"
        return any(self.apply(Relocate(call_index=self.call, container_id=cid, src=src, dst=dst))
                   for _, dst in self.yard_targets(key))

    def put_aboard(self, cid: str, make) -> bool:
        slots = legal_slots(self.inst, Expander(self.inst, self.state), cid)
        self.rng.shuffle(slots)
        for slot in slots:
            snap = self.snap()
            if self.access(slot) and self.apply(make(slot)):
                return True
            self.restore(snap)
        return False

    def load(self, cid: str) -> bool:
        snap = self.snap()
        while True:
            key, tier, top = self.yard_loc(cid)
            if top:
                break
            if not self.relocate(self.yard()[key][-1]):
                self.restore(snap)
                return False
        src = f"{key}-{tier:02d}"
        if self.put_aboard(cid, lambda slot: Load(call_index=self.call, container_id=cid, src=src, dst=slot)):
            return True
        self.restore(snap)
        return False

    def above(self, cid: str) -> set[str]:
        occ = {cell: b for b, slot in self.ship().items() for cell in cells_of_slot(slot)}
        ship = self.ship()
        todo, seen = [cid], set()
        while todo:
            for cell in cells_of_slot(ship[todo.pop()]):
                st = self.cell_stack[cell]
                for upper in st.cell_ids[st.cell_ids.index(cell) + 1:]:
                    b = occ.get(upper)
                    if b is not None and b not in seen:
                        seen.add(b)
                        todo.append(b)
        return seen - {cid}

    def lift_off_ship(self, cid: str, make) -> bool:
        snap = self.snap()
        blockers = self.above(cid)
        while blockers:
            ship = self.ship()
            free = [b for b in sorted(blockers) if not (self.above(b) & blockers)]
            b = self.rng.choice(free)
            if not (self.access(ship[b]) and self.apply(RestowOff(call_index=self.call, container_id=b, src=ship[b]))):
                self.restore(snap)
                return False
            blockers.discard(b)
        src = self.ship()[cid]
        if self.access(src):
            for move in make(src):
                if self.apply(move):
                    return True
        self.restore(snap)
        return False

    def discharge(self, cid: str) -> bool:
        return self.lift_off_ship(cid, lambda src: [
            Discharge(call_index=self.call, container_id=cid, src=src, dst=dst) for _, dst in self.yard_targets(None)])


def foreign_plan(inst: Instance, seed: int) -> MovePlan:
    rng = random.Random(seed)
    f = Foreign(inst, rng)
    for cover in f.covers:  # eager opening
        if rng.random() < 0.4:
            f.cover_off(cover)
    dis = list(inst.discharge_list)
    rng.shuffle(dis)
    for cid in dis:
        assert f.discharge(cid), (inst.id, cid)
    todo = list(inst.load_list)
    for _ in range(300):
        buffer = list(f.state.yard.quay_buffer)
        if not todo and not buffer:
            break
        r = rng.random()
        if r < 0.15:  # voluntary relocation of a random top box
            tops = sorted(boxes[-1] for boxes in f.yard().values())
            f.relocate(rng.choice(tops))
        elif r < 0.22:  # redundant off/on pair
            cover = rng.choice(f.covers)
            snap = f.snap()
            if cover not in f.off and not (cover in f.off.values()) and f.cover_off(cover):
                f.cover_on(cover)
            else:
                f.restore(snap)
        elif inst.discharge_list and r < 0.27:  # extra restow of a box aboard
            aboard = sorted(f.ship())
            if aboard:
                cid = rng.choice(aboard)
                f.lift_off_ship(cid, lambda src, cid=cid: [RestowOff(call_index=f.call, container_id=cid, src=src)])
        elif buffer and (r < 0.6 or not todo):
            cid = rng.choice(sorted(buffer))
            f.put_aboard(cid, lambda slot, cid=cid: RestowOn(call_index=f.call, container_id=cid, dst=slot))
        elif todo:
            cid = rng.choice(sorted(todo))
            if f.load(cid):
                todo.remove(cid)
    else:
        raise RuntimeError(f"foreign planner stuck on {inst.id}")
    f.close_all()
    return MovePlan(id=f"{inst.id}.foreign", instance_id=inst.id, call_index=inst.call_index, moves=f.moves)


@pytest.mark.parametrize("layover", [False, True])
def test_from_plan_on_foreign_plans(layover):
    ok = fewer_covers = with_override = parked = 0
    failures = {}
    for seed in SEEDS:
        inst = _instance(seed, layover)
        try:
            foreign = foreign_plan(inst, 1000 + seed)
        except RuntimeError as error:
            failures[seed] = str(error)
            continue
        ref = apply_plan(inst, foreign)
        assert ref.legal, (seed, ref.violations)  # sanity of the generator
        acts = SPACE.from_plan(inst, foreign)
        again = SPACE.expand(inst, acts)
        res = apply_plan(inst, again)
        assert res.legal, (seed, res.violations)
        assert box_moves(again.moves) == box_moves(foreign.moves), seed
        assert res.count.hatch_cover_lifts <= ref.count.hatch_cover_lifts, seed
        assert res.count.weighted_nonproductive <= ref.count.weighted_nonproductive, seed
        for field in ("loads", "discharges", "relocations", "restow_lifts", "gate_moves", "future"):
            assert getattr(res.count, field) == getattr(ref.count, field), (seed, field)
        assert res.final_state.ship == ref.final_state.ship.model_copy(update={"covers_off": {}}), seed
        # and the expander's own plan is a fixed point
        assert SPACE.expand(inst, SPACE.from_plan(inst, again)).model_dump_json() == again.model_dump_json(), seed
        ok += 1
        fewer_covers += res.count.hatch_cover_lifts < ref.count.hatch_cover_lifts
        with_override += any(a.get("to") for a in again.actions)
        parked += any(m.kind == "hatch_cover_off" and m.dst != "quay" for m in foreign.moves)
    stage = "b" if layover else "a"
    expected_failures = {
        seed: f"foreign planner stuck on tiny_{stage}_{seed}"
        for seed in FOREIGN_STUCK_SEEDS[layover]
    }
    assert failures == expected_failures, f"unexpected generation failures: {failures}"
    assert ok == len(SEEDS) - len(expected_failures)
    assert parked > 0  # covers parked on other covers
    assert fewer_covers > 0  # the generator really exercises non-lazy cover handling
    assert with_override > 0  # and non-default relocation/discharge targets


@pytest.mark.parametrize("layover", [False, True])
@pytest.mark.parametrize(("property_test", "generator_name"), [
    (test_expand_round_trip, "random_actions"),
    (test_from_plan_on_foreign_plans, "foreign_plan"),
])
def test_properties_reject_unexpected_generator_failure(monkeypatch, layover, property_test, generator_name):
    """A newly stuck seed must fail coverage even if all other seeds still pass."""
    generate = globals()[generator_name]

    def fail_seed_zero(inst, seed):
        if inst.seed == 0:
            raise RuntimeError("injected generator regression")
        return generate(inst, seed)

    monkeypatch.setitem(globals(), generator_name, fail_seed_zero)
    with pytest.raises(AssertionError, match="unexpected generation failures"):
        property_test(layover)
