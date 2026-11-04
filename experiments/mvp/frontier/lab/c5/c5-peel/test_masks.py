"""Differential mask regressions over approved quick maps and real partial states."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from masks import peeled_masks, peeled_rule_ids
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.checker import check, rules, rules_special
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility._engine import IncrementalEngine
from stowage.io import load_instance
import stowage.feasibility._engine as native

ROOT = Path(__file__).resolve().parents[6]
MAPS = [
    'experiments/mvp/corpus/val/tiny/feeder_500/random_yard/seed-020000006.json',
    'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000022.json',
    'experiments/mvp/corpus/val/small/feeder_500/simulated_yard/seed-040100034.json',
]


def engine(instance, *, screens=0):
    return make_feasibility_engine(instance, get_action_space('boxslot'),
        config=FeasibilityConfig(extra={'global_screen': 0, 'completion_screens': screens}))


def state(instance, level, prefix=(), screens=0):
    with peeled_masks(level):
        e = engine(instance, screens=screens)
        for action in prefix:
            assert e.is_legal(action), (level, action)
            e.push(action)
        actions = set(e.legal_actions())
        # Differential assertion: every retained action still passes all
        # nonpeeled exact placement rules. The checker itself is untouched.
        ship = e._ship(e._cur)
        boxes = {b.id: b for b in instance.containers}
        for a in sorted(actions, key=lambda a: (a.box, a.slot))[:64]:
            if isinstance(a, Place) and not e.forces_restow(a):
                violations = rules.placement_violations(instance, ship, boxes[a.box], a.slot)
                hard = {v.rule_id for v in violations if v.severity == 'hard'}
                assert hard <= peeled_rule_ids(level), (level, a, hard)
        return actions


@pytest.mark.parametrize('path', MAPS)
def test_relaxation_monotone_and_retains_every_nonpeeled_placement_rule(path):
    instance = load_instance(ROOT / path)
    prefix = ()
    for depth in range(2):
        previous = state(instance, 0, prefix)
        original = previous
        for level in range(1, 6):
            current = state(instance, level, prefix)
            assert previous <= current, (path, depth, level)
            previous = current
        if not original:
            break
        prefix += (min(original, key=lambda a: (a.box, a.slot)),)


@pytest.mark.parametrize('path', MAPS[:2])
def test_completion_screens_remain_monotone(path):
    instance = load_instance(ROOT / path)
    previous = state(instance, 0, screens=1)
    for level in range(1, 6):
        current = state(instance, level, screens=1)
        assert previous <= current
        previous = current


def test_restores_on_exception_and_never_patches_checker():
    names = ('__init__', '_exact_ok', '_special_ok', '_inherited_hard',
             '_e6_last', '_e6_departure_ok', '_heat_many', '_heat_ok')
    original = {name: getattr(IncrementalEngine, name) for name in names}
    build = native.build_tables
    checker_functions = (check, rules.placement_violations, rules._core,
                         rules_special.special_violations, rules_special._parameter)
    with pytest.raises(RuntimeError, match='deliberate'):
        with peeled_masks(3):
            assert checker_functions == (check, rules.placement_violations, rules._core,
                rules_special.special_violations, rules_special._parameter)
            raise RuntimeError('deliberate')
    assert native.build_tables is build
    assert all(getattr(IncrementalEngine, name) is value for name, value in original.items())
    with peeled_masks(0):
        assert native.build_tables is build
        assert all(getattr(IncrementalEngine, name) is value for name, value in original.items())


def test_tier_permission_and_unknown_fail_closed():
    assert 'expert.e6.heavy_under_deck' in peeled_rule_ids(1)
    assert 'expert.e1.heavy_bottom' not in peeled_rule_ids(1)
    assert 'expert.e1.heavy_bottom' in peeled_rule_ids(2)
    assert 'expert.e8.away_from_heat' in peeled_rule_ids(3)
    assert 'expert.physical.slot_occupancy' not in peeled_rule_ids(5)
    assert 'unknown.rule' not in peeled_rule_ids(5)
    with pytest.raises(ValueError):
        peeled_rule_ids(6)


def test_projection_preserves_input_and_missing_t0_data():
    instance = load_instance(ROOT / MAPS[0])
    overrides = dict(instance.rule_overrides)
    overrides['expert.e1.heavy_bottom'] = {'heavy_threshold': None}
    altered = instance.model_copy(update={'rule_overrides': overrides})
    before = altered.model_dump_json()
    original = engine(altered).T
    with peeled_masks(3):
        relaxed = engine(altered).T
        assert relaxed.e1_threshold is original.e1_threshold is None
        assert check.__module__ == 'stowage.checker.full'
    assert altered.model_dump_json() == before


def test_missing_heat_clearance_remains_t0():
    instance = load_instance(ROOT / MAPS[0])
    boxes = [b.model_copy(update={'special_stowage': [*b.special_stowage, 'away_from_heat']})
             for b in instance.containers]
    overrides = dict(instance.rule_overrides)
    overrides['expert.e8.away_from_heat'] = {'minimum_clearance': None}
    altered = instance.model_copy(update={'containers': boxes, 'rule_overrides': overrides})
    assert not state(altered, 0)
    assert not state(altered, 3)
