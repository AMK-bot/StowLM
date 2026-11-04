"""C1f — the D25 E6 departure fallback as a necessary condition (plan 4.4).

A class-H box may sit on deck only if no compatible hold cell is left empty at departure (2.4
``rules_weight.e6_departure_violations``). The engine counts hold stacks that a deck H box could still use; each needs
another box, so their number may not exceed the units still to load.
These historical hard-E6 tests explicitly select legacy semantics; activated
soft-E6 behavior is covered by test_w2_tables and test_w2_weight_oog.
"""

from __future__ import annotations

import random

from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker.rules import check_static_rules
from stowage.conventions import parse_slot
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1

SPACE = get_action_space("boxslot")
CFG = FeasibilityConfig(extra={"global_screen": 0})


def _two_box_instance(*, hold_accepts_20: bool = True) -> Instance:
    """Fixture ship; load list = one H 20' (25 t) and one M 20' (15 t), plain dry boxes, each alone in the yard."""
    d = build_instance().model_dump()
    base = dict(next(c for c in d["containers"] if c["length_ft"] == 20), iso_type="22G1", height_m=2.591,
                tare_kg=2200, dg=None, oog=None, special_stowage=[], reefer=False, reefer_setpoint_c=None,
                pol="THLCH", pod="THBKK")
    from stowage.conventions import make_container_id

    heavy = dict(base, id=make_container_id("QZWU", 1), gross_mass_kg=25000.0)
    medium = dict(base, id=make_container_id("QZWU", 2), gross_mass_kg=15000.0)
    d["containers"] = [heavy, medium]
    d["load_list"] = [heavy["id"], medium["id"]]
    d["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[heavy["id"]]),
                           dict(key="A-002-01", length_ft=20, boxes=[medium["id"]])]
    if not hold_accepts_20:
        hold = {s["id"] for s in d["vessel"]["stacks"] if not s["on_deck"]}
        for c in d["vessel"]["cells"]:
            if c["stack_id"] in hold:
                c["accepts_20"] = False
    d["id"] = "e6_two_boxes" + ("" if hold_accepts_20 else "_no_hold20")
    return Instance.model_validate(d)


def _deck_places(engine, box):
    return [a for a in engine.legal_actions() if isinstance(a, Place) and a.box == box
            and parse_slot(a.slot).on_deck]


@rule_freeze_v1(enabled=False)
def test_heavy_on_deck_refused_while_hold_stacks_would_stay_empty():
    inst = _two_box_instance()
    heavy = inst.load_list[0] if inst.containers[0].gross_mass_kg >= 20000 else inst.load_list[1]
    e = make_feasibility_engine(inst, SPACE, config=CFG)
    assert not _deck_places(e, heavy)          # 4 empty hold stacks, 1 box left: E6 can't be met
    assert any(isinstance(a, Place) and a.box == heavy and not parse_slot(a.slot).on_deck
               for a in e.legal_actions())
    assert e.completion_check()["c1f"]


@rule_freeze_v1(enabled=False)
def test_heavy_on_deck_allowed_when_no_hold_cell_is_compatible():
    inst = _two_box_instance(hold_accepts_20=False)
    heavy = next(c.id for c in inst.containers if c.gross_mass_kg >= 20000)
    e = make_feasibility_engine(inst, SPACE, config=CFG)
    assert _deck_places(e, heavy)


@rule_freeze_v1(enabled=False)
def test_completed_plans_meet_e6_departure():
    """Random rollouts on random tiny yards (boxes of 9–26 t): every completed plan has no hard E6 (or other
    static) violation at departure under 2.4."""
    from stowage.actions.samples import tiny_instance

    completed = 0
    for seed in range(12):
        inst = tiny_instance(seed, n_load=6, n_other=3)
        rng = random.Random(seed)
        e = make_feasibility_engine(inst, SPACE, config=CFG)
        while not e.is_complete():
            acts = e.legal_actions()
            if not acts:
                break
            e.push(rng.choice(acts))
        if not e.is_complete():
            continue
        completed += 1
        result = apply_plan(inst, e.to_plan())
        assert result.legal
        hard = [v.rule_id for v in check_static_rules(inst, result.final_state.ship,
                                                      call_index=inst.call_index).violations
                if v.severity == "hard"]
        assert hard == [], (seed, hard)
    assert completed >= 6
