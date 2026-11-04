"""Focused tests of partial-load representation and private page relaxation."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from search import ashore_key, integer_partial, make_model, _score_subset, _single_highs, _pages


def test_partial_master_chooses_count_then_teu_then_mass():
    import numpy as np
    model = SimpleNamespace(
        boxes=[0, 1, 2], columns=[SimpleNamespace(boxes=(b,), page=0) for b in range(3)],
        pages=[SimpleNamespace(stacks=(0,))],
        T=SimpleNamespace(units=np.array([2, 1, 1]), mass=np.array([200., 100., 400.])),
        soft_breach=lambda c: 0., column_cost=lambda c, phase: 0.)
    chosen, info = integer_partial(model, allow_partial=True, seconds=1., seed=0)
    # Only one box fits. Load the 40-foot box to leave the least TEU behind.
    assert chosen == [0]
    model.T.units[:] = 1
    chosen, info = integer_partial(model, allow_partial=True, seconds=1., seed=0)
    assert chosen == [2]  # With TEU tied, leave the least mass ashore.
    assert integer_partial(model, allow_partial=False, seconds=1., seed=0)[0] is None
    fallback, info = integer_partial(model, allow_partial=True, seconds=0., seed=0)
    assert fallback is not None
    assert len(fallback) == 1
    assert info['fallback_subset']


def test_partial_actions_replay_and_unchanged_checker_names_ashore():
    from alns import Problem
    from stowage.io import load_instance
    from stowage.checker import check
    from stowage.actions.space import Place
    root = HERE.parents[5]
    instance = load_instance(root/'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json')
    problem = Problem(instance)
    model = make_model(problem, 3)
    bad = model.T.box_index['QZXU0000035']
    # Force the known heat-obstructed box ashore while testing the same real
    # action expander, sequence, replay and checker used in production.
    model.static[bad] = False
    for k, page in enumerate(model.pages):
        for b in model.boxes:
            model.add(k, (b,))
    chosen, _ = integer_partial(model, allow_partial=True, seconds=1., seed=0)
    assert chosen is not None
    scored = _score_subset(problem, model, chosen, 0)
    assert scored is not None
    assert ashore_key(instance, scored.actions)[0] == 1
    report = check(instance, scored.plan)
    missing = [v for v in report.violations if v.rule_id == 'objective.all_loaded']
    assert len(missing) == 1
    assert list(missing[0].containers) == ['QZXU0000035']
    assert not any(v.rule_id.startswith('expert.moves.') or v.rule_id == 'checker.internal_error'
                   for v in report.violations)


def test_highs_wrapper_restores_after_exception():
    pages = _pages()
    original = pages.linprog
    try:
        with _single_highs(pages):
            assert pages.linprog is not original
            raise RuntimeError('probe')
    except RuntimeError:
        pass
    assert pages.linprog is original


def test_ashore_key_orders_count_teu_mass():
    boxes = [SimpleNamespace(id='a', length_ft=40, gross_mass_kg=300),
             SimpleNamespace(id='b', length_ft=20, gross_mass_kg=400)]
    instance = SimpleNamespace(load_list=['a', 'b'], containers=boxes)
    assert ashore_key(instance, [SimpleNamespace(box='a', slot='s')]) == (1, 1, 400)
    assert ashore_key(instance, [SimpleNamespace(box='b', slot='s')]) == (1, 2, 300)


def test_soft_securing_and_weight_never_remove_height_guard():
    from dataclasses import replace
    from alns import Problem
    from stowage.io import load_instance
    instance = load_instance(HERE.parents[5]/'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000040.json')
    problem = Problem(instance)
    oracle = SimpleNamespace(ratio=lambda boxes, slots: 1.2)
    hard = make_model(problem, 3)
    soft = make_model(problem, 4, lash_limit=float('inf'))
    soft.oracle = oracle
    deck = next(p for p in soft.pages if p.deck)
    b = next(b for b in soft.boxes if soft.static[b, deck.slots[0]])
    assert soft.lash_ok(deck, [b])
    hard.oracle = oracle
    assert not hard.lash_ok(deck, [b])
    tiny_limit = replace(deck, limit_kg=1.)
    assert not soft.legal(tiny_limit, [b])
    l5 = make_model(problem, 5)
    assert l5.legal(tiny_limit, [b])
    assert not l5.legal(replace(tiny_limit, hmax=.1), [b])


def test_complete_master_never_returns_partial_fallback():
    import numpy as np
    model = SimpleNamespace(
        boxes=[0, 1], columns=[SimpleNamespace(boxes=(0,), page=0)],
        pages=[SimpleNamespace(stacks=(0,))],
        T=SimpleNamespace(units=np.array([1, 1]), mass=np.array([100., 100.])),
        soft_breach=lambda c: 0., column_cost=lambda c, phase: 0.)
    chosen, row = integer_partial(model, allow_partial=False, seconds=0., seed=0)
    assert chosen is None
    assert not row['fallback_subset']
    assert not row['allow_partial']
    model.columns.append(SimpleNamespace(boxes=(1,), page=1))
    model.pages.append(SimpleNamespace(stacks=(1,)))
    chosen, row = integer_partial(model, allow_partial=False, seconds=0., seed=0)
    assert set(chosen) == {0, 1}
    assert row['fallback_complete']
    assert not row['fallback_subset']


def test_generate_complete_only_selects_complete_master(monkeypatch):
    import search
    seen = []
    def master(model, **kwargs):
        seen.append(kwargs)
        return None, {}
    model = SimpleNamespace(boxes=[], stacks=[], pages=[], columns=[], pool={},
                            generate=lambda **kwargs: {'uncovered': 0.})
    monkeypatch.setattr(search, 'make_model', lambda *args, **kwargs: model)
    monkeypatch.setattr(search, 'integer_partial', master)
    monkeypatch.setattr(search, '_complete_seed', lambda *args, **kwargs: (None, {}))
    instance = SimpleNamespace(onboard=SimpleNamespace(placements=[]), discharge_list=[])
    assert search.generate(instance, 3, 0, 120., {'problem': object(), 'complete_only': True}) == []
    assert len(seen) == 3
    assert all(not row['allow_partial'] for row in seen)
    assert all(row['seconds'] >= 10. for row in seen)
    seen.clear()
    assert search.generate(instance, 4, 0, 120., {'problem': object()}) == []
    assert all(not row['allow_partial'] for row in seen)


def test_complete_dive_stops_at_node_cap_and_restores():
    import search
    original = lambda **kwargs: None
    model = SimpleNamespace(boxes=[0], stacks=[0], columns=[],
                            first_fit=lambda *args: None, generate=original)
    def dive(**kwargs):
        while True:
            model.generate()
    model.dive = dive
    chosen, row = search._complete_seed(model, seed=0, deadline=float('inf'), max_nodes=2)
    assert chosen is None
    assert row['nodes'] == 2
    assert model.generate is original


def test_invocation_seeds_refuse_paths_and_saved_records():
    import search
    assert search._invocation_seeds(object(), ['/tmp/a-plan.json', {'plan': {}}]) == []
