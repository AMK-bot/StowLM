"""Cold reference derivation and private thread-control regressions."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location('_c4_legacy_tests', Path(__file__).with_name('legacy_cold.py'))
legacy = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = legacy
spec.loader.exec_module(legacy)


def test_native_lcg_target_is_derived_from_this_runs_original_plain(monkeypatch):
    constructed, calls, references = [], [], []
    instance = NS(onboard=NS(placements=[]), load_list=['a'])
    problem = NS(instance=instance)
    def model(p, **kwargs):
        assert p is problem
        constructed.append(kwargs)
        return NS(pages=[], columns=[])
    scores = [NS(plan='first', objective=12.), NS(plan='second', objective=10.),
              NS(plan='third', objective=11.)]
    def stow(p, m, **kwargs):
        calls.append(kwargs)
        return scores[len(calls)-1], dict(lp_proved=True)
    def fast(i, ship):
        references.append(ship)
        return 'native-motion'
    monkeypatch.setattr(legacy, '_runtime', lambda: (
        NS(Model=model), NS(one_stow=stow), NS(Fast=fast), NS(Problem=lambda i: problem)))
    ship = object()
    monkeypatch.setattr(legacy, '_ship_metrics', lambda i, s: (ship, dict(
        cargo_lcg_m=73.2 if s is scores[0] else 57.,
        c1_master_cargo_lcg_m=81.2 if s is scores[0] else 59.2,
        complete_replay_legal=True)))
    context = dict(stored=object(), stored_plan=object(), reference=object())
    records, meta = legacy.legacy_candidates(instance, seed=2, budget_s=10., context=context)
    assert constructed[0] == {}
    assert constructed[1] == dict(oracle='native-motion', deck_tiers=(4, 5, 5),
                                   lash_limit=1., lcg_target=81.2)
    assert constructed[2] == dict(oracle='native-motion', deck_tiers=(4, 5, 5),
                                   lash_limit=.97, lcg_target=81.2)
    assert [c['seed'] for c in calls] == [2, 7, 8]
    assert references == [ship]
    assert len(records) == 3
    assert [r['metadata'].get('securing_search_cap') for r in records] == [None, 1., .97]
    assert meta['stored_data_consulted'] is False
    assert meta['full_checker_passes'] is None
    assert meta['midpoint_retry']['enabled'] is False
    assert 'lp_proved' not in records[0]['metadata']
    assert records[1]['metadata']['reference_cargo_lcg_m'] == 73.2
    assert records[1]['metadata']['reference_c1_master_cargo_lcg_m'] == 81.2
    assert records[1]['metadata']['lcg_target_offset_m'] == 0.
    assert records[1]['metadata']['lcg_target_policy'] == meta['target_policy']


def test_zero_budget_never_initializes_runtime(monkeypatch):
    monkeypatch.setattr(legacy, '_runtime', lambda: (_ for _ in ()).throw(AssertionError()))
    records, meta = legacy.legacy_candidates(NS(onboard=NS(placements=[])), budget_s=0.)
    assert records == []
    assert meta['full_checker_passes'] is None


def test_private_lp_wrapper_forces_one_thread_and_keeps_other_options():
    calls = []
    module = NS(linprog=lambda *args, **kwargs: calls.append(kwargs))
    legacy._single_thread(module)
    wrapped = module.linprog
    legacy._single_thread(module)
    assert module.linprog is wrapped
    module.linprog([1.], options=dict(threads=8, time_limit=2.))
    assert calls == [dict(options=dict(threads=1, time_limit=2.))]


def test_tighter_profile_failure_keeps_previous_candidate_and_reserves_deadline(monkeypatch):
    instance = NS(onboard=NS(placements=[]), load_list=['a'])
    first, incumbent = NS(plan='plain'), NS(plan='one')
    calls = []
    def stow(*args, **kwargs):
        calls.append(kwargs)
        return [first, incumbent, None, None][len(calls)-1], {}
    monkeypatch.setattr(legacy, 'monotonic', lambda: 0.)
    monkeypatch.setattr(legacy, '_runtime', lambda: (
        NS(Model=lambda *a, **k: NS(pages=[], columns=[])), NS(one_stow=stow),
        NS(Fast=lambda *a: object()), NS(Problem=lambda i: object())))
    monkeypatch.setattr(legacy, '_ship_metrics', lambda *a: (object(), dict(cargo_lcg_m=60., c1_master_cargo_lcg_m=62.)))
    records, meta = legacy.legacy_candidates(instance, budget_s=210.)
    assert [r['scored'] for r in records] == [first, incumbent]
    assert [c['deadline'] for c in calls] == [63., 147., 210., 210.]
    assert [c['seed'] for c in calls] == [0, 5, 6, 7]
    assert meta['variants'][-1]['settings']['lash_limit'] == .985
    assert meta['variants'][-1]['full_checker_passed'] is None
    assert meta['midpoint_retry']['enabled'] is True


def test_warm_patterns_use_destination_capacity_static_and_tighter_securing(monkeypatch):
    monkeypatch.setattr(legacy, 'monotonic', lambda: 0.)
    page = NS(kind='20', stacks=(7,), slots=(1, 2))
    source = NS(pages=[page], columns=[NS(page=0, boxes=b) for b in
        ((0,), (1,), (2,), (3, 4, 5), (6,))], seed_chosen=[0], _warm_indices=[0])
    added = []
    destination = NS(pages=[page], legal=lambda p, b: b != (1,),
        lash_ok=lambda p, b: b != (2,), add=lambda p, b: added.append((p, b)) or True)
    row = legacy._warm_columns(destination, source, deadline=2., limit=4)
    assert added == [(0, (0,))]
    assert row['examined'] == 4 and row['added'] == 1
    assert not hasattr(destination, 'seed_chosen')
    assert legacy._warm_columns(destination, source, deadline=0.)['examined'] == 0


def test_midpoint_success_retains_one_and_reuses_latest_gated_pool(monkeypatch):
    instance = NS(onboard=NS(placements=[]), load_list=['a'])
    models, calls, warmed = [], [], []
    scores = [NS(plan='plain'), NS(plan='one'), None, NS(plan='midpoint')]
    def model(*args, **kwargs):
        result = NS(settings=kwargs)
        models.append(result)
        return result
    def stow(*args, **kwargs):
        calls.append(kwargs)
        return scores[len(calls)-1], {}
    def warm(new, old, **kwargs):
        warmed.append((new, old))
        return dict(examined=3, added=2)
    monkeypatch.setattr(legacy, 'monotonic', lambda: 0.)
    monkeypatch.setattr(legacy, '_warm_columns', warm)
    monkeypatch.setattr(legacy, '_runtime', lambda: (
        NS(Model=model), NS(one_stow=stow), NS(Fast=lambda *a: object()),
        NS(Problem=lambda i: object())))
    monkeypatch.setattr(legacy, '_ship_metrics', lambda *a: (object(), dict(cargo_lcg_m=60., c1_master_cargo_lcg_m=62.)))
    records, meta = legacy.legacy_candidates(instance, budget_s=210.)
    assert [r['scored'] for r in records] == [scores[0], scores[1], scores[3]]
    assert models[-1].settings['lash_limit'] == .985
    assert models[-1].settings['lcg_target'] == 62.
    assert warmed == [(models[2], models[1]), (models[3], models[2])]
    assert records[-1]['metadata']['securing_search_cap'] == .985
    assert meta['midpoint_retry']['physical_infeasibility_claim'] is False


def test_midpoint_skips_when_incomplete_tight_rung_leaves_less_than_20_seconds(monkeypatch):
    clock, calls = [0.], []
    instance = NS(onboard=NS(placements=[]), load_list=['a'])
    def stow(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 3:
            clock[0] = 191.
            return None, {}
        return NS(plan='candidate'), {}
    monkeypatch.setattr(legacy, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(legacy, '_runtime', lambda: (
        NS(Model=lambda *a, **k: NS(pages=[], columns=[])), NS(one_stow=stow),
        NS(Fast=lambda *a: object()), NS(Problem=lambda i: object())))
    monkeypatch.setattr(legacy, '_ship_metrics', lambda *a: (object(), dict(cargo_lcg_m=60., c1_master_cargo_lcg_m=62.)))
    records, meta = legacy.legacy_candidates(instance, budget_s=210.)
    assert len(calls) == 3 and len(records) == 2
    assert meta['midpoint_retry']['enabled'] is False


def test_long_slot_keeps_physical_lcg_separate_from_c1_first_support_coordinate(monkeypatch):
    import stowage.moves
    boxes = [NS(id='long', gross_mass_kg=3000., height_m=2.6),
             NS(id='short', gross_mass_kg=1000., height_m=2.6)]
    cells = [NS(id='0010102', lcg_m=30.), NS(id='0030102', lcg_m=10.),
             NS(id='0050102', lcg_m=60.)]
    instance = NS(containers=boxes, load_list=['long', 'short'], vessel=NS(
        cells=cells, stacks=[NS(base_z_m=0., cell_ids=[c.id]) for c in cells]))
    ship = NS(placements=[NS(container_id='long', slot='0020102'),
                          NS(container_id='short', slot='0050102')])
    replay = NS(legal=True, final_state=NS(ship=ship),
                count=NS(model_dump=lambda **k: {}))
    monkeypatch.setattr(stowage.moves, 'apply_plan', lambda *a: replay)
    result, metrics = legacy._ship_metrics(instance, NS(plan=object(), objective=7.))
    assert result is ship
    assert metrics['cargo_lcg_m'] == 30.
    assert metrics['c1_master_cargo_lcg_m'] == 37.5
    assert metrics['cargo_kg_m'] == 1.3
