"""The E9 lookup fix has a narrow dynamic scope and agrees with checker envelopes."""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from . import e9fix
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check, rules_special
from stowage.checker.rules import placement_violations
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility._engine import IncrementalEngine
from stowage.io import load_instance


MAPS = [
    'experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json',
    'experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-low/seed-192200101.json',
    'experiments/mvp/strategy/blockrule/maps/train/medium/feeder_500/random_yard/seed-185100000.json',
]


def test_lookup_is_scoped_to_e9_batch_and_restores_on_exception(monkeypatch):
    import stowage.checker.full as full
    checker = check
    checker_hash = hashlib.sha256(Path(full.__file__).read_bytes()).hexdigest()
    original_parameter = rules_special._parameter
    original_batch = IncrementalEngine._e9_batch
    installed = e9fix._installed
    wrapper_state = e9fix._owner, e9fix._original_batch, e9fix._wrapper
    # Exercise a fresh installation around a synthetic batch whose lookups are
    # observable. This checks normal and exceptional unwinding independently
    # of whatever order the engine's regression tests have installed the fix.
    calls = []
    sentinel, clearance = object(), object()
    def parameter(instance, rule, *names):
        calls.append(('original', rule, names))
        return sentinel
    def resolved(instance, rule, *names):
        calls.append(('clearance', rule, names))
        return clearance
    def batch(self, st, bs, ss, *, all_boxes):
        assert rules_special._parameter(None, e9fix.RULE, 'oog_clearance') is clearance
        assert rules_special._parameter(None, e9fix.RULE, 'unrelated') is sentinel
        assert rules_special._parameter(None, 'expert.e8.away_from_heat', 'oog_clearance') is sentinel
        if all_boxes:
            raise RuntimeError('batch exception')
        return sentinel
    monkeypatch.setattr(rules_special, '_parameter', parameter)
    monkeypatch.setattr(rules_special, '_clearance_parameter', resolved)
    monkeypatch.setattr(IncrementalEngine, '_e9_batch', batch)
    monkeypatch.setattr(e9fix, '_installed', False)
    try:
        e9fix.install()
        wrapped = IncrementalEngine._e9_batch
        assert rules_special._parameter is parameter
        assert wrapped(None, None, None, None, all_boxes=False) is sentinel
        assert rules_special._parameter is parameter
        with pytest.raises(RuntimeError, match='batch exception'):
            wrapped(None, None, None, None, all_boxes=True)
        assert rules_special._parameter is parameter
        e9fix.install()
        assert IncrementalEngine._e9_batch is wrapped
        assert check is checker and full.check is checker
        assert hashlib.sha256(Path(full.__file__).read_bytes()).hexdigest() == checker_hash
    finally:
        # monkeypatch also restores these; restore explicitly so identity is
        # checked while this test still owns its instrumentation.
        IncrementalEngine._e9_batch = original_batch
        e9fix._installed = installed
        e9fix._owner, e9fix._original_batch, e9fix._wrapper = wrapper_state
        rules_special._parameter = original_parameter
    assert rules_special._parameter is original_parameter


def _horizontal_oog(box):
    return box.oog is not None and any((box.oog.over_width_port_m,
        box.oog.over_width_starboard_m, box.oog.over_length_fore_m,
        box.oog.over_length_aft_m))


def _compare_e9(instance, engine):
    """Compare only the corrected E9 mask, with independent placement checks.

    Restrict to unoccupied, supported, length-compatible slots so checker slot
    geometry and the incremental next-height geometry describe the same move.
    Other mask rules (E6, stability screens) are deliberately not E9 evidence.
    """
    T, st = engine.T, engine._cur
    ship = st.ex.world_state().ship
    compared, accepted = 0, 0
    for bi, box_id in enumerate(T.box_ids):
        box = engine._boxes[box_id]
        if not st.rem[bi] or not _horizontal_oog(box):
            continue
        slots = []
        for si, slot in enumerate(T.slot_ids):
            if int(T.slot_n[si]) != (1 if box.length_ft == 20 else 2):
                continue
            cells = [int(ci) for ci in T.slot_cells[si] if ci >= 0]
            stacks = [int(sj) for sj in T.slot_stacks[si] if sj >= 0]
            if any(T.cell_ids[ci] in st.ex.occ for ci in cells):
                continue
            if any(T.stack_cells[sj][int(st.fill[sj])] not in cells
                   for sj in stacks if st.fill[sj] < len(T.stack_cells[sj])):
                continue
            if any(st.fill[sj] >= len(T.stack_cells[sj]) for sj in stacks):
                continue
            slots.append(si)
        if not slots:
            continue
        mask = engine._e9_batch(st, np.full(len(slots), bi, dtype=np.int64),
                                np.array(slots, dtype=np.int64), all_boxes=True)
        for si, actual in zip(slots, mask):
            violations = placement_violations(instance, ship, box, T.slot_ids[si],
                                               call_index=instance.call_index)
            expected = not any(v.severity == 'hard' and v.rule_id.startswith('expert.e9.')
                               for v in violations)
            assert bool(actual) == expected, (instance.id, engine.depth, box_id,
                T.slot_ids[si], bool(actual), [(v.rule_id, v.message) for v in violations])
            compared += 1
            accepted += bool(actual)
    return compared, accepted


@pytest.mark.parametrize('path', MAPS)
def test_corrected_e9_mask_matches_real_oog_maps_empty_and_partial(path):
    original_parameter, checker = rules_special._parameter, check
    e9fix.install()
    instance = load_instance(path)
    assert any(_horizontal_oog(box) for box in instance.containers)
    engine = make_feasibility_engine(instance, get_action_space('boxslot'),
        config=FeasibilityConfig(extra={'global_screen': 0, 'completion_screens': 0}))
    empty = _compare_e9(instance, engine)
    assert empty[0] > 0 and empty[1] > 0
    # Three ordinary accepted loads yield an actual reachable partial ship;
    # retain OOG boxes for the regression probes.
    for _ in range(3):
        action = next((a for a in engine.legal_actions()
                       if isinstance(a, Place) and not _horizontal_oog(engine._boxes[a.box])), None)
        assert action is not None
        engine.push(action)
    partial = _compare_e9(instance, engine)
    assert partial[0] > 0
    assert rules_special._parameter is original_parameter
    assert check is checker


def test_switch_can_disable_fix_after_previous_engine_call():
    # The switch must also work for notebook/CLI ablations in one process.
    e9fix.uninstall()
    original = IncrementalEngine._e9_batch
    parameter = rules_special._parameter
    e9fix.install()
    assert IncrementalEngine._e9_batch is not original
    e9fix.uninstall()
    assert IncrementalEngine._e9_batch is original
    assert rules_special._parameter is parameter
