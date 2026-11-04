"""Regression tests for multi-call counting, closure and exact circle pricing."""
import itertools
from pathlib import Path
import random
import sys
from time import monotonic
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from graph_model import circle_mwis, closure_counts, load_chords, pattern_events


def crossing(a, b):
    i, j = sorted(a)
    k, l = sorted(b)
    return i < k < j < l or k < i < l < j


def test_circle_mwis_matches_exhaustive():
    rng = random.Random(3271)
    for n in range(1, 8):
        for repeat in range(8):
            endpoints = list(range(2*n))
            rng.shuffle(endpoints)
            chords = {i: endpoints[2*i:2*i+2] for i in range(n)}
            weights = {i: rng.randint(-2, 7) for i in range(n)}
            expected = 0
            for mask in range(1 << n):
                take = [i for i in range(n) if mask & (1 << i)]
                if not any(crossing(chords[a], chords[b]) for a, b in itertools.combinations(take, 2)):
                    expected = max(expected, sum(weights[i] for i in take))
            value, chosen = circle_mwis(chords, weights)
            assert value == expected
            assert sum(weights[i] for i in chosen) == expected


def test_free_loading_sort_removes_crossings_even_at_five_ports():
    pod = dict(enumerate((1, 2, 3, 4, 5)))
    order = sorted(pod, key=pod.get, reverse=True)
    chords = load_chords(order, pod)
    assert circle_mwis(chords)[0] == 5


def test_pattern_counts_unique_blockers_at_each_call():
    pod = dict(a=1, b=1, c=2, d=4)
    assert pattern_events(('a', 'b', 'c', 'd'), pod) == {(1, 'c'), (1, 'd'), (2, 'd')}


def test_hatch_and_stack_blockers_are_unioned_at_each_call():
    pod = dict(h=1, d=1, u=4, h2=2)
    rows = closure_counts(pod, {'h': ['h', 'h2'], 'd': ['d', 'u']}, {'h': 'C'}, {'d': 'C'})
    assert rows[0]['blockers'] == ['h2', 'u']
    assert rows[1]['blockers'] == ['u']
    assert sum(r['restowed_boxes'] for r in rows) == 3


def test_spanning_departed_box_does_not_keep_connecting_stacks():
    pod = dict(a=2, span=1, b=3)
    rows = closure_counts(pod, {'one': ['a', 'span'], 'two': ['span', 'b']}, {}, {})
    assert rows[0]['blockers'] == ['b']
    assert rows[1]['blockers'] == []


def fake_master(pods, patterns):
    from graph_method import Model
    m = Model.__new__(Model)
    m.boxes, m.pod = list(pods), pods
    m.ports = sorted(set(pods.values()))
    m.columns = [NS(page=i, boxes=tuple(boxes)) for i, (_, boxes) in enumerate(patterns)]
    m.pages = [NS(deck=deck, cover='C', stacks=(i,), slots=tuple(range(len(boxes))))
               for i, (deck, boxes) in enumerate(patterns)]
    m.T = NS(units={b: 1 for b in pods}, mass={b: 1000 for b in pods}, slot_ids=['s']*10)
    m.p = NS(instance=NS(objective=NS(future_restow=1, future_restow_lifts_per_box=2,
                                     future_hatch_cover=1, hatch_cover=1)), cells_tcg=lambda _: 0.)
    return m


def test_three_port_master_charges_deck_box_at_two_earlier_calls():
    m = fake_master(dict(a=1, b=2, c=3), [(False, ['a']), (False, ['b']), (True, ['c'])])
    result = m.graph_integer(seconds=1., seed=0)
    assert result['restricted_search_objective'] == 10  # 6 hatch + 4 restow


def test_master_does_not_double_charge_internal_and_hatch_blocker():
    m = fake_master(dict(a=1, b=1, c=3), [(False, ['a']), (True, ['b', 'c'])])
    result = m.graph_integer(seconds=1., seed=0)
    assert result['restricted_search_objective'] == 6  # 4 hatch + 2 restow


def test_tiny_enumerator_includes_all_legal_orders_not_only_global_sort():
    from graph_method import Model
    m = Model.__new__(Model)
    m.boxes = [0, 1, 2]
    m.T = NS(units=[1, 1, 1])
    m.pages = [NS(kind='20', slots=(0, 1))]
    m.legal = lambda page, boxes: not (len(boxes) == 2 and boxes[0] == 0)
    recorded = set()
    m.add = lambda page, boxes: recorded.add(tuple(boxes))
    assert m.enumerate_tiny(monotonic() + 2.)
    expected = {boxes for n in (1, 2) for boxes in itertools.permutations(m.boxes, n)
                if m.legal(m.pages[0], boxes)}
    assert recorded == expected


def heat_model(instance, lower, sensitive, slots):
    from graph_method import Model
    from frozen_engine.scout.pages import Page
    m = Model.__new__(Model)
    m._heat_pattern_cache = {}
    m.T = NS(heat=[False, True], box_ids=[lower.id, sensitive.id], slot_ids=list(slots))
    m.p = NS(instance=instance, boxes={b.id: b for b in instance.containers})
    page = Page('20', (0,), tuple(range(len(slots))), 60000., 20., False, 'test-cover')
    return m, page


def test_actual_lower_prefix_can_clear_heat_when_empty_upper_probe_fails():
    from stowage.model.fixtures import build_instance
    from stowage.checker.rules import placement_violations
    from stowage.model.ship import Placement
    instance = build_instance()
    # Synthetic isolation of E8 using the real checker and real physical
    # heights: a source just above hold base, upper support adds 2.591 metres.
    source = instance.vessel.heat_sources[0].model_copy(update={
        'x_min_m': 40., 'x_max_m': 55., 'y_min_m': -3., 'y_max_m': 3.,
        'z_min_m': 1., 'z_max_m': 2.05})
    instance = instance.model_copy(update={'vessel': instance.vessel.model_copy(update={'heat_sources': [source]})})
    lower, sensitive = instance.containers[1:3]
    slots = ('0010102', '0010104')
    m, page = heat_model(instance, lower, sensitive, slots)
    empty_probe = placement_violations(instance, instance.onboard, sensitive, slots[1])
    assert any(str(v.rule_id) == 'expert.e8.away_from_heat' for v in empty_probe)
    supported = instance.onboard.model_copy(update={'placements': [Placement(container_id=lower.id, slot=slots[0])]})
    prefix_probe = placement_violations(instance, supported, sensitive, slots[1])
    assert not any(str(v.rule_id) == 'expert.e8.away_from_heat' for v in prefix_probe)
    assert m._heat_ok(page, (0, 1))
    assert not m._heat_ok(page, (1,))


def test_real_train_heat_pattern_is_refused_by_unchanged_checker_geometry():
    from stowage.io import load_instance
    from stowage.checker.rules import placement_violations
    path = Path(__file__).resolve().parent / 'cohort/tiny.p3.s263310005.json'
    instance = load_instance(path)
    sensitive = next(b for b in instance.containers if 'away_from_heat' in b.special_stowage)
    lower = next(b for b in instance.containers if b.id in instance.load_list and b.id != sensitive.id)
    m, page = heat_model(instance, lower, sensitive, ('0010202', '0010204'))
    found = placement_violations(instance, instance.onboard, sensitive, '0010202')
    assert any(str(v.rule_id) == 'expert.e8.away_from_heat' for v in found)
    assert not m._heat_ok(page, (1,))


def test_e6_is_global_and_other_pages_can_repair_the_same_deck_pattern():
    from graph_method import e6_rejections
    from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
    from stowage.model.fixtures import build_instance
    from stowage.model.ship import Placement
    instance = build_instance()
    stacks = {s.id: s for s in instance.vessel.stacks}
    hold = [c.id for c in instance.vessel.cells if not stacks[c.stack_id].on_deck]
    template = next(b for b in instance.containers if b.length_ft == 20)
    cargo = [template.model_copy(update={
        'id': make_container_id(SYNTHETIC_OWNER_CODES[0], 900+i),
        'gross_mass_kg': 22000. if i == 0 else 4000.,
        'weight_class': 'H' if i == 0 else 'L',
        'dg': None, 'oog': None, 'special_stowage': []}) for i in range(len(hold)+1)]
    instance = instance.model_copy(update={'containers': cargo})
    deck = Placement(container_id=cargo[0].id, slot='0010182')
    empty_hold = instance.onboard.model_copy(update={'placements': [deck]})
    assert e6_rejections(instance, empty_hold)
    # Keep the exact same deck page; other selected pages fill every hold cell.
    filled_hold = empty_hold.model_copy(update={'placements': [deck] + [
        Placement(container_id=box.id, slot=slot) for box, slot in zip(cargo[1:], hold)]})
    assert not e6_rejections(instance, filled_hold)


def test_master_no_good_excludes_only_a_complete_pattern_combination():
    m = fake_master(dict(a=1, b=2, c=3), [
        (False, ['a']), (False, ['b']), (True, ['c']), (True, ['c'])])
    first = m.graph_integer(seconds=1., seed=0)
    second = m.graph_integer(seconds=1., seed=0, forbidden=[first['chosen']])
    assert second is not None
    assert set(second['chosen']) != set(first['chosen'])
    assert {0, 1} <= set(second['chosen'])  # shared pages remain allowed
