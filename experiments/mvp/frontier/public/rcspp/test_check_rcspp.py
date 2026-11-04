"""Hand-worked cases for check_rcspp: the worked examples printed in the suite's documentation, then one small ship per rule.

No suite data is used, so these run anywhere: PYTHONPATH=experiments/mvp/frontier/public/rcspp .venv/bin/python -m pytest <this file>
"""
from collections import Counter

import pytest

from check_rcspp import OUTSIDE_THE_PLAN, Readings, evaluate, interp
from rcspp_io import ALPHA, BETA, Bay, Box, HEIGHT, Instance, Stack, Tank, Vessel


def ship(deck_weight=150., hold_weight=240., lash=1e6, gm_limit=.1, plugs=(4,), height=20.):
    """Two bays of two rows, a hold below a deck under one hatch each. Flat hydrostatics make the arithmetic checkable by hand."""
    table = [(1000., 50., 20., .5), (100000., 50., 20., .5)]
    bonjean = [(1000., 100.), (100000., 100.)]
    bays, keyed = [], {}
    index = 0
    for name, (aft, fore), hatch in ((0, (50., 100.), 0), (2, (0., 50.), 1)):
        stacks = []
        for row in (1, 2):
            for level, start, end, vp, weight in (('BelowDeck', 2, 6, 2., hold_weight), ('OnDeck', 82, 86, 12., deck_weight)):
                stack = Stack(index, name, row, level, float(row), vp, start, end, weight, height, frozenset(plugs), name, hatch)
                index += 1
                stacks.append(stack)
                keyed[name, row, level] = stack
        bays.append(Bay(name, aft, fore, 1000., 5., [Tank(f'T{name}', 1000., 1., 3.)], bonjean, None, None, stacks))
    return Vessel('0', gm_limit, 90000., lash, table, bays, keyed)


def box(i, pol=1, pod=3, kind='40DC', weight=10., reefer=False, position=None, imdg=None):
    return Box(i, kind, weight, pol, pod, reefer, imdg is not None, imdg, 50, position)


def voyage(load=(), arrival=(), ports=4, crane=1000, mixed=8):
    return Instance('t', '0', mixed, {b.id: b for b in load}, {b.id: b for b in arrival}, [(p, crane) for p in range(ports)], {})


def faults(instance, positions, vessel=None, tanks=None, readings=None):
    return evaluate(vessel or ship(), instance, positions, tanks or {}, readings)


def stays(pol, pod, position):
    return {p: position for p in range(pol, pod)}


def only_breach(found, name):
    return [k for k in found if k not in OUTSIDE_THE_PLAN] == [name]


def test_documented_interpolation():
    """RCSPPSuite.pdf section 4.1: tfct(50000) = 0.22467 on vessel XL."""
    table = [(48835.74575, 186.92, 57.2, .220921), (50208.75375, 186.97, 56.24, .225346)]
    assert interp(table, 3, 50000) == pytest.approx(.22467, abs=1e-5)


def test_documented_lashing_value():
    """Section 4.5 prints 509.2 for GM 3.81, weight 84, hm 13.3."""
    assert 3.81**ALPHA*BETA**13.3*84 == pytest.approx(509.2, abs=.5)


def test_documented_bending_arithmetic():
    """Section 4.4 prints 79,009 after flipping one sign; the formula itself gives 79,485, which is the reading coded here."""
    forces = [(141, 1000, 83), (127, 1000, 247), (113, 1000, 621), (99, 966, 1000)]
    value = sum((mid-92)*(weight-buoyancy) for mid, weight, buoyancy in forces)
    assert value == 79009
    assert sum((mid-92)*(1000-buoyancy) for mid, _, buoyancy in [(141, 0, 83), (127, 0, 247), (113, 0, 621), (99, 0, 966)]) == 79485


def test_heights_are_the_documented_ones():
    assert HEIGHT == {'20DC': 2.591, '40DC': 2.591, '40HC': 2.896}


def test_trim_and_gm_by_hand():
    """2,000 t lightship at mid-bays 75 and 25 balances on LCB 50, so one box of 100 t at x=75 gives lcg 51.43 and trim 0.5*(50-51.43)."""
    a = box(1, weight=100.)
    found, m = faults(voyage([a]), {1: stays(1, 3, (0, 1, 2, None))})
    d = 2100.
    assert m['ports'][1]['displacement'] == pytest.approx(d)
    assert m['ports'][1]['trim'] == pytest.approx(.5*(50-(75*1100+25*1000)/d))
    # lightship at 5 m, a 2.591 m box based at 2 m
    assert m['ports'][1]['gm'] == pytest.approx(20-(2000*5+100*(2+2.591/2))/d)
    assert not [k for k in found if k not in OUTSIDE_THE_PLAN]


def test_objective_and_moves():
    a, b = box(1), box(2, pod=2)
    positions = {1: stays(1, 3, (0, 1, 2, None)), 2: stays(1, 2, (0, 1, 4, None))}
    tanks = {p: {'T0': 10.} for p in (1, 2, 3)}
    found, m = faults(voyage([a, b]), positions, tanks=tanks)
    assert m['restows'] == 0
    assert m['ballast'] == pytest.approx(3*100*1.025)
    assert m['objective_flat'] == pytest.approx(100-.05*m['ballast'])


def test_overstow_counts_as_a_restow():
    """A legal plan: the box below leaves at port 2, a new one takes its cell, and the box above keeps its slot, so it is lifted off and put back."""
    below, above, new = box(1, pod=2), box(2, pod=3), box(3, pol=2, pod=3)
    positions = {1: stays(1, 2, (0, 1, 2, None)), 2: stays(1, 3, (0, 1, 4, None)), 3: stays(2, 3, (0, 1, 2, None))}
    found, m = faults(voyage([below, above, new]), positions)
    assert m['restows'] == 1 and not [k for k in found if k not in OUTSIDE_THE_PLAN]


def test_shift_between_ports_is_a_restow():
    a = box(1)
    positions = {1: {1: (0, 1, 2, None), 2: (0, 2, 2, None)}}
    _, m = faults(voyage([a]), positions)
    assert m['restows'] == 1


def test_hatch_restow_is_counted():
    """Deck box stays through port 2 while the hold under the same cover discharges."""
    hold, deck = box(1, pod=2), box(2, pod=3)
    positions = {1: stays(1, 2, (0, 1, 2, None)), 2: stays(1, 3, (0, 2, 82, None))}
    _, m = faults(voyage([hold, deck]), positions)
    assert m['hatch_restows'] == 1 and m['restows'] == 1


def test_gap_under_a_box():
    assert only_breach(faults(voyage([box(1)]), {1: stays(1, 3, (0, 1, 4, None))})[0], '(22) gap below a box')


def test_two_boxes_in_one_slot():
    found, _ = faults(voyage([box(1), box(2)]), {i: stays(1, 3, (0, 1, 2, None)) for i in (1, 2)})
    assert '(15) two boxes in one slot' in found


def test_twenty_on_forty():
    forty, t1, t2 = box(1), box(2, kind='20DC'), box(3, kind='20DC')
    positions = {1: stays(1, 3, (0, 1, 2, None)), 2: stays(1, 3, (0, 1, 4, 'Aft')), 3: stays(1, 3, (0, 1, 4, 'Fore'))}
    assert only_breach(faults(voyage([forty, t1, t2]), positions)[0], '(23) 20 on a 40')


def test_lone_twenty():
    assert only_breach(faults(voyage([box(1, kind='20DC')]), {1: stays(1, 3, (0, 1, 2, 'Aft'))})[0], '(18)(19) lone 20')


def test_paired_twenties_must_share_ports():
    a, b = box(1, kind='20DC', pod=2), box(2, kind='20DC', pod=3)
    positions = {1: stays(1, 2, (0, 1, 2, 'Aft')), 2: stays(1, 3, (0, 1, 2, 'Fore'))}
    assert '(18)(19) paired 20s differ in load or discharge port' in faults(voyage([a, b]), positions)[0]


def test_stack_height():
    vessel = ship(height=2.)
    assert only_breach(faults(voyage([box(1)]), {1: stays(1, 3, (0, 1, 2, None))}, vessel)[0], '(24) stack height')


def test_stack_weight():
    vessel = ship(hold_weight=5.)
    assert only_breach(faults(voyage([box(1)]), {1: stays(1, 3, (0, 1, 2, None))}, vessel)[0], '(25) stack weight')


def test_reefer_needs_a_plug():
    assert only_breach(faults(voyage([box(1, reefer=True)]), {1: stays(1, 3, (0, 1, 2, None))})[0], '(26) reefer without a plug')


def test_lashing_counts_deck_stacks_only():
    vessel = ship(lash=1.)
    a = box(1)
    assert not [k for k in faults(voyage([a]), {1: stays(1, 3, (0, 1, 2, None))}, vessel)[0] if k not in OUTSIDE_THE_PLAN]
    assert '(29) lashing' in faults(voyage([a]), {1: stays(1, 3, (0, 1, 82, None))}, vessel)[0]


def test_lashing_changed_only_skips_a_settled_stack():
    vessel = ship(lash=1.)
    a = box(1, pod=4)
    found, _ = faults(voyage([a], ports=5), {1: stays(1, 4, (0, 1, 82, None))}, vessel, readings=Readings(lash_changed_only=True))
    assert found['(29) lashing'] == 1       # port 1 only: the stack doesn't change at 2 and 3


def test_long_crane_counts_a_forty_once():
    a, b = box(1), box(2)
    positions = {1: stays(1, 3, (0, 1, 2, None)), 2: stays(1, 3, (0, 2, 2, None))}
    assert '(35) long crane' not in faults(voyage([a, b], crane=2), positions)[0]
    assert faults(voyage([a, b], crane=1), positions)[0]['(35) long crane'] == 2    # port 1 loads, port 3 discharges


def test_paired_block_limit():
    a, b = box(1, pod=2), box(2, pod=3)
    positions = {1: stays(1, 2, (0, 1, 2, None)), 2: stays(1, 3, (0, 2, 2, None))}
    assert faults(voyage([a, b], mixed=0), positions)[0]['(37) too many mixed bays'] == 1
    assert '(37) too many mixed bays' not in faults(voyage([a, b], mixed=1), positions)[0]


def test_aboard_from_load_to_discharge():
    assert '(20)(21) aboard exactly from load to discharge port' in faults(voyage([box(1)]), {1: {1: (0, 1, 2, None)}})[0]


def test_arrival_breaches_are_kept_apart():
    vessel = ship(lash=1.)
    given = box(9, pol=0, pod=3, position=(0, 1, 82, None))
    found, _ = faults(voyage(arrival=[given]), {9: stays(0, 3, (0, 1, 82, None))}, vessel)
    assert found['arrival-only stack lashing'] == 2 and '(29) lashing' not in found


def test_dangerous_goods_stand_in():
    a, b = box(1, imdg=1), box(2, imdg=2)
    positions = {1: stays(1, 3, (0, 1, 2, None)), 2: stays(1, 3, (0, 2, 2, None))}
    assert faults(voyage([a, b]), positions)[0]['(27*) different IMDG types on one level of a bay'] == 2
    positions[2] = stays(1, 3, (2, 2, 2, None))
    assert not [k for k in faults(voyage([a, b]), positions)[0] if k.startswith('(27')]


def test_negative_ballast_is_a_breach():
    """Review H2: water can't be taken out of an empty tank to fix trim."""
    positions = {1: stays(1, 3, (0, 1, 2, None))}
    assert '(2) tank outside 0 to 100 %' in faults(voyage([box(1)]), positions, tanks={1: {'T0': -5.}})[0]


def test_moved_arrival_box_stays_a_fault():
    """Review H3: only a swap of sides is outside the plan, not a box in the wrong cell."""
    given = box(9, pol=0, pod=3, position=(0, 1, 2, None))
    found, _ = faults(voyage(arrival=[given]), {9: stays(0, 3, (0, 2, 2, None))})
    assert '(13) arrival position differs' in found and '(13) arrival position differs' not in OUTSIDE_THE_PLAN


def test_shift_inside_a_stack_is_two_lifts_for_the_crane():
    """Review: a box moved within one stack leaves and returns, so a limit of one lift must trip."""
    a, b = box(1), box(2)
    positions = {1: stays(1, 3, (0, 1, 2, None)), 2: {1: (0, 1, 4, None), 2: (0, 2, 2, None)}}
    assert Counter(faults(voyage([a, b], crane=1), positions)[0])['(35) long crane'] >= 2


def test_hatch_lift_counts_for_the_crane():
    """Review: a deck box lifted so the hold can be worked is two more lifts in that bay."""
    hold, deck = box(1, pod=2), box(2, pod=3)
    positions = {1: stays(1, 2, (0, 1, 2, None)), 2: stays(1, 3, (0, 2, 82, None))}
    found, m = faults(voyage([hold, deck], crane=3), positions)
    assert m['ports'][2]['long_crane'] == 3
    assert '(35) long crane' not in found
    assert '(35) long crane' in faults(voyage([hold, deck], crane=2), positions)[0]


def test_moved_arrival_box_is_not_given():
    """Review H1: a stack of arrival boxes the plan built itself is the plan's, however its boxes got there."""
    vessel = ship(hold_weight=15.)
    a = box(8, pol=0, pod=3, position=(0, 1, 2, None))
    b = box(9, pol=0, pod=3, position=(0, 2, 2, None))
    positions = {8: stays(0, 3, (0, 1, 2, None)), 9: {0: (0, 2, 2, None), 1: (0, 1, 4, None), 2: (0, 1, 4, None)}}
    assert '(25) stack weight' in faults(voyage(arrival=[a, b]), positions, vessel)[0]


def test_string_keys_are_read():
    """Review L3: a plan reloaded from JSON has string keys, and tanks must not silently go empty."""
    positions = {'1': {'1': [0, 1, 2, None], '2': [0, 1, 2, None]}}
    _, m = faults(voyage([box(1)]), positions, tanks={'1': {'T0': 10.}})
    assert m['ballast'] == pytest.approx(102.5)


def test_bad_plans_fault_instead_of_crashing():
    """Review L2: a batch of model-written plans must survive a bad one."""
    assert 'unknown box' in faults(voyage([box(1)]), {99: {1: (0, 1, 2, None)}})[0]
    assert 'side does not match box size' in faults(voyage([box(1, kind='20DC')]), {1: stays(1, 3, (0, 1, 2, None))})[0]
    assert 'unknown tank' in faults(voyage([box(1)]), {1: stays(1, 3, (0, 1, 2, None))}, tanks={1: {'nope': 5.}})[0]


def test_dangerous_goods_stand_in_sees_arrival_boxes():
    """Review M2: a load box beside an arrival box of another type counts; two arrival boxes alone are not the plan's."""
    given = box(9, pol=0, pod=3, position=(0, 1, 2, None), imdg=1)
    a = box(1, imdg=2)
    positions = {9: stays(0, 3, (0, 1, 2, None)), 1: stays(1, 3, (0, 2, 2, None))}
    assert '(27*) different IMDG types on one level of a bay' in faults(voyage([a], [given]), positions)[0]
