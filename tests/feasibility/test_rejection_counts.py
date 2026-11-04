"""Rejection diagnostics count candidate identities across query strategies."""
import pytest

from stowage.actions.expander import Expander
from stowage.actions.samples import tiny_instance
from stowage.actions.space import Place
from stowage.model.yard import YardStack
from stowage.rules.catalogue import rule_freeze_v1


RULE = 'expert.e2.weight_order'


def _plain(n=3):
    inst = tiny_instance(37, n_load=n, n_other=0, p40=0)
    inst.vessel.hull, inst.vessel.fixed_loads = None, []
    inst.yard.stacks = [YardStack(key=f'A-{i+1:03d}-01', length_ft=20, boxes=[box.id])
                        for i, box in enumerate(inst.containers)]
    for i, box in enumerate(inst.containers):
        box.gross_mass_kg = 9000 if i == 0 else 18000
    return inst


@pytest.mark.parametrize('order', [('slots', 'boxes', 'eager'), ('eager', 'slots', 'boxes'),
                                  ('boxes', 'slots', 'eager')])
def test_weight_counts_distinct_pairs_and_reset_on_pop(h, order):
    inst = _plain()
    stack = next(s for s in inst.vessel.stacks if s.id == '00101D')
    inst.vessel = inst.vessel.model_copy(update={
        'cells': [c for c in inst.vessel.cells if c.id in stack.cell_ids],
        'stacks': [stack], 'hatch_covers': [], 'bridge': None})
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        eng.push(Place(inst.load_list[0], stack.cell_ids[0]))
        for query in order:
            if query == 'slots':
                for box in inst.load_list[1:]:
                    assert eng.legal_slots_for_box(box) == ()
            elif query == 'boxes':
                assert eng.legal_box_ids() == ()
            else:
                assert eng.legal_index().size == 0
            assert eng.screen_diagnostics()['rejected'][RULE] == 2
        diag = eng.screen_diagnostics()
        assert diag['dead_end_rule_ids_label'] == 'rules active at the dead state'
        assert diag['rejected_count_label'] == 'distinct (box, slot) pairs per rule (queried domains only)'
        assert eng.dead_end().rule_ids == (RULE,)
        assert eng.copy().screen_diagnostics()['rejected'][RULE] == 2
        eng.pop()
        assert RULE not in eng.screen_diagnostics()['rejected']


@pytest.mark.parametrize('failure', ['yard', 'ship'])
def test_cached_port_failures_count_each_slot_once(h, monkeypatch, failure):
    from stowage.feasibility import _port_screen

    inst = _plain(n=1)
    slots = ('0010182', '0010282')
    rule = 'expert.moves.top_access' if failure == 'yard' else 'port.e3.quay_crossing'
    if failure == 'yard':
        def no_source(*args, **kwargs):
            raise ValueError('synthetic diagnostic yard failure')
        monkeypatch.setattr(Expander, '_dig_out', no_source)
    else:
        monkeypatch.setattr(Expander, 'can_expand', lambda *args, **kwargs: False)
        monkeypatch.setattr(_port_screen, '_expansion_rule', lambda *args: rule)
    with rule_freeze_v1():
        eng = h.engine(inst, h.DIAG)
        eng.T.static[:] = False
        for slot in slots:
            eng.T.static[0, eng.T.slot_index[slot]] = True
        for slot in slots:
            assert not eng.is_legal(Place(inst.load_list[0], slot))
        assert eng.screen_diagnostics()['rejected'][rule] == len(slots)
        assert eng.legal_slots_for_box(inst.load_list[0]) == ()
        assert eng.legal_index().size == 0
        assert eng.screen_diagnostics()['rejected'][rule] == len(slots)


def test_legacy_diagnostics_labels_stay_unchanged(h):
    with rule_freeze_v1(enabled=False):
        eng = h.engine(_plain(n=1), h.DIAG)
        eng.legal_index()
        diag = eng.screen_diagnostics()
        assert diag['rejected'] == {}
        assert 'rejected_count_label' not in diag
        assert 'dead_end_rule_ids_label' not in diag


def test_cleared_hatch_rejections_merge_by_pair(monkeypatch):
    import numpy as np
    from stowage.feasibility._port_screen import record_rejection
    from tests.feasibility.test_transactional_restow import case

    with rule_freeze_v1():
        eng, boxes, slots = case(monkeypatch)

        def rejected(state, *, allowed_boxes, allowed_slots, include_discharges):
            pairs = [(eng.T.box_ids[int(b)], eng.T.slot_ids[int(s)])
                     for b in allowed_boxes for s in allowed_slots]
            record_rejection(state, RULE, pairs=pairs)
            state.mask = np.array([], dtype=np.int64)

        monkeypatch.setattr(eng, '_evaluate', rejected)
        for _ in range(3):
            assert eng._restow_places(eng._cur, boxes, slots) == []
            assert eng.screen_diagnostics()['rejected'][RULE] == 1


def test_cached_hatch_failure_counts_each_candidate(monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    from stowage.feasibility import in_port
    from tests.feasibility.test_transactional_restow import case

    rule = 'port.stability.cargo_operations'
    with rule_freeze_v1():
        eng, boxes, slots = case(monkeypatch)
        calls = []

        def reject(ship):
            calls.append(ship)
            return SimpleNamespace(status='reject', rule_id=rule)

        monkeypatch.setattr(in_port, 'in_port_screen_enabled', lambda: True)
        monkeypatch.setattr(in_port, 'InPortPrefixScreen',
                            lambda *args: SimpleNamespace(evaluate=reject))
        assert eng._restow_places(eng._cur, boxes, slots) == []
        assert eng.screen_diagnostics()['rejected'][rule] == 1
        other = np.array([eng.T.slot_index['0010104']])
        assert eng._restow_places(eng._cur, boxes, other) == []
        assert len(calls) == 1  # cached failure; no repeated lift validation
        assert eng.screen_diagnostics()['rejected'][rule] == 2
