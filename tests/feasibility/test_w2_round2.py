"""Review regressions and exact factorisation of the activated port screen."""
from __future__ import annotations

import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model import Instance
from stowage.rules.catalogue import rule_freeze_v1


def test_activated_kg_cap_has_no_rank_sentinel(h):
    with rule_freeze_v1():
        inst = tiny_instance(91, n_load=1, n_other=0, p40=0, max_tiers=2)
        inst.vessel.hull, inst.vessel.fixed_loads = None, []
        inst.containers[0].gross_mass_kg = inst.containers[0].max_gross_mass_kg = 1_100_000.
        for stack in inst.vessel.stacks:
            stack.max_mass_kg = stack.max_mass_20_kg = stack.max_mass_40_kg = 3_000_000.
        inst = Instance.model_validate(inst.model_dump())
        action = Place(inst.load_list[0], '0010182')
        assert Expander(inst).can_expand(action)
        assert h.engine(inst, h.DIAG).is_legal(action)


@pytest.mark.parametrize('seed,layover,crane_h', [(39, False, None), (17, False, 2),
                                               (23, False, 1), (39, True, 2)])
def test_port_factorization_matches_exact_probes_and_reuses_columns(h, monkeypatch, seed, layover, crane_h):
    import numpy as np
    from stowage.feasibility import _port_screen

    with rule_freeze_v1():
        inst = tiny_instance(seed, n_load=6, n_other=3, p40=.3, max_tiers=3, layover=layover)
        inst.vessel.hull, inst.vessel.fixed_loads = None, []
        inst.port.max_crane_height_tiers = crane_h
        for box in inst.containers:
            box.gross_mass_kg = 9000.
        eng = h.engine(inst, h.DIAG)
        st, tables = eng._cur, eng.T
        pairs = [(int(b), int(s)) for b in np.flatnonzero(st.rem)
                 for s in eng._open_slots(st) if tables.static[b, s]]
        want = [_port_screen.port_action_allowed(inst, tables, st,
                    Place(tables.box_ids[b], tables.slot_ids[s])) for b, s in pairs]
        calls = []
        original = Expander.can_expand
        def counted(self, action, **kwargs):
            calls.append(action)
            return original(self, action, **kwargs)
        monkeypatch.setattr(Expander, 'can_expand', counted)
        got = _port_screen.port_actions_allowed(inst, tables, st,
                    np.array([b for b, s in pairs]), np.array([s for b, s in pairs]))
        assert got.tolist() == want
        # Candidate identity is required by the mask's heterogeneous-box
        # correctness fix. Verify reuse on a repeated query rather than
        # requiring unsafe sharing between distinct boxes on the first one.
        assert len(calls) <= len(pairs)
        first_probes = dict(st.sp['port_probe_counts'])
        first_rejections = dict(st.sp.get('screen_rejections', {}))
        calls.clear()
        repeated = _port_screen.port_actions_allowed(inst, tables, st,
                    np.array([b for b, s in pairs]), np.array([s for b, s in pairs]))
        assert repeated.tolist() == want
        probes = st.sp['port_probe_counts']
        assert probes['yard_sources'] == first_probes['yard_sources']
        assert probes['ship_columns'] == first_probes['ship_columns']
        assert len(calls) == probes['fallback'] - first_probes['fallback']
        assert st.sp.get('screen_rejections', {}) == first_rejections
        assert st.sp['port_probe_counts']['yard_sources'] <= len(inst.load_list)


def test_plain_disjoint_fixed_cargo_uses_exact_local_arrays(h, monkeypatch):
    from tests.feasibility.w2_performance_probe import make_performance_instance
    import stowage.checker.rules as rules

    with rule_freeze_v1():
        inst = make_performance_instance('medium')
        eng = h.engine(inst, {'global_screen': 0})
        assert eng._fixed_by_stack
        assert not set(eng.T.stack_ids).intersection(eng._fixed_by_stack)
        monkeypatch.setattr(rules, 'placement_violations', lambda *a, **kw:
                            pytest.fail('plain unrelated fixed stacks retriggered the full checker'))
        assert eng.legal_actions()


def test_lazy_box_then_slot_sets_equal_eager_mask(h):
    with rule_freeze_v1():
        inst = tiny_instance(37, n_load=4, n_other=2, p40=.25)
        inst.vessel.hull, inst.vessel.fixed_loads = None, []
        lazy = h.engine(inst, h.SCREENS)
        eager = h.engine(inst, h.SCREENS)
        expected = eager.legal_actions()
        boxes = lazy.legal_box_ids()
        assert boxes == tuple(sorted({a.box for a in expected}))
        assert not lazy._cur.evaluated, 'box-token query eagerly built the full pair mask'
        for box in boxes:
            assert lazy.legal_slots_for_box(box) == tuple(a.slot for a in expected if a.box == box)
        for action in expected:
            assert lazy.is_legal(action)
        assert lazy.legal_actions() == expected


def test_crossing_dead_end_exposes_screen_rule_counts(h):
    from stowage.model import Placement
    from stowage.model.yard import YardStack

    with rule_freeze_v1():
        inst = tiny_instance(19, n_load=1, n_other=2, p40=0, max_tiers=2)
        inst.vessel.hull, inst.vessel.fixed_loads = None, []
        inst.port.max_crane_height_tiers = 2
        a, low, top = [b.id for b in inst.containers]
        inst.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=[a])]
        inst.onboard.placements = [Placement(container_id=low, slot='0010282'),
                                   Placement(container_id=top, slot='0010284')]
        eng = h.engine(inst, h.DIAG)
        eng.T.static[:] = False
        eng.T.static[eng.T.box_index[a], eng.T.slot_index['0010182']] = True
        assert eng.legal_actions() == ()
        assert 'port.e3.quay_crossing' in eng.dead_end().rule_ids
        assert eng.screen_diagnostics()['rejected']['port.e3.quay_crossing'] > 0


def test_lazy_boxes_batch_a_column_then_stop(h, monkeypatch):
    with rule_freeze_v1():
        inst = tiny_instance(37, n_load=6, n_other=0, p40=0)
        inst.vessel.hull, inst.vessel.fixed_loads = None, []
        for box in inst.containers:
            box.gross_mass_kg = 9000.
        eng = h.engine(inst, h.DIAG)
        original, calls = eng._evaluate, []
        def evaluate(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)
        monkeypatch.setattr(eng, '_evaluate', evaluate)
        assert eng.legal_box_ids() == tuple(sorted(inst.load_list))
        assert len(calls) == 1


@pytest.mark.parametrize('enabled', [False, True])
def test_empty_decision_geometry_engine_dead_end(h, enabled):
    from stowage.model.fixtures import build_instance
    payload = build_instance().model_dump()
    payload['vessel'].update(cells=[], stacks=[], hatch_covers=[], dg_doc_areas=[], hull=None)
    payload['onboard']['placements'] = []
    payload['discharge_list'] = []
    inst = Instance.model_validate(payload)
    with rule_freeze_v1(enabled=enabled):
        eng = h.engine(inst, h.DIAG)
        assert eng.legal_actions() == ()
        assert eng.dead_end() is not None
