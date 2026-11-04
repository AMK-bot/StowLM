"""Small structural regressions; real-map numerical runs need root admission."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location('_c4_cold_tests', Path(__file__).with_name('cold.py'))
cold = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cold
spec.loader.exec_module(cold)


def test_closed_cover_is_excluded_from_cold_port_layout():
    pages = [NS(kind='20', cover='A', slots=(1, 2)),
             NS(kind='20', cover='B', slots=(3, 4)),
             NS(kind='long', cover='A', slots=(5, 6))]
    model = NS(pages=pages, boxes=[0, 1], pod={0: 1, 1: 2}, T=NS(units=[2., 2.]))
    first = cold.layout_for(model)
    rotated = cold.layout_for(model, rotation=1)
    assert set(first) == {'A', 'B'}
    assert first != rotated
    assert cold.layout_for(model, closed=('A',)) == {'B': 1}


def test_complete_selection_rejects_shared_physical_stack_and_missing_box():
    columns = [NS(page=0, boxes=(0,)), NS(page=1, boxes=(1,)), NS(page=2, boxes=(1,))]
    model = NS(columns=columns, pages=[NS(stacks=(0,)), NS(stacks=(0,)), NS(stacks=(1,))],
               banned=set(), boxes=[0, 1], legal=lambda page, boxes: True,
               lash_ok=lambda page, boxes: True)
    assert cold._selection_valid(model, [0, 2])
    assert not cold._selection_valid(model, [0, 1])
    assert not cold._selection_valid(model, [0])
    model.lash_ok = lambda page, boxes: False
    assert not cold._selection_valid(model, [0, 2])


def test_direct_greedy_rejects_overload_before_it_can_spoil_complete_packing():
    t = NS(units=[2, 2], share=[10000., 10000.], height=[2.6, 2.6], stack_base_z=[0., 0.])
    pages = [NS(kind='long', stacks=(0,), slots=(0, 1), deck=False, limit_kg=30000.),
             NS(kind='long', stacks=(1,), slots=(2, 3), deck=True, limit_kg=30000.)]
    m = NS(T=t, stacks=[0, 1], boxes=[0, 1], order=[0, 1], special=set(),
           pages=pages, heavy={0: True, 1: True}, columns=[], pool={}, banned=set(),
           legal=lambda page, boxes: True, lash_ok=lambda page, boxes: len(boxes) <= 1)
    def add(k, boxes):
        key = (k, tuple(boxes))
        assert m.lash_ok(m.pages[k], boxes)
        if key not in m.pool:
            m.pool[key] = len(m.columns)
            m.columns.append(NS(page=k, boxes=tuple(boxes)))
    m.add = add
    chosen, row = cold._incremental_first_fit(m, rng=cold.random.Random(0),
                                              deadline=cold.monotonic()+10.)
    assert cold._selection_valid(m, chosen)
    assert len(chosen) == 2
    assert row['placed'] == 2
    assert all(len(m.columns[k].boxes) == 1 for k in chosen)


def test_mixed_column_vertical_moment_counts_each_whole_box_once():
    t = NS(stack_base_z=[0., 0.], slot_stacks=[(0, -1), (1, -1), (0, 1)],
           mass=[10000., 20000., 30000.], height=[2.6, 2.6, 2.6])
    model = NS(T=t, pages=[NS(stacks=(0, 1), slots=(0, 1, 2))])
    assert abs(cold._vertical_tm(model, NS(page=0, boxes=(0, 1, 2)))-156.) < 1e-9


def test_pure_vector_pricing_and_master_use_same_physical_moment():
    class Base:
        def height_of(self, page, tier):
            return 3.+tier*2.6
    cls = cold._model_class(Base, mixed=False)
    model = object.__new__(cls)
    model.kg_weight = model.lift_weight = .06
    model.T = NS(mass=[24000.])
    for tier in (0, 1, 4):
        vector = model.lift_weight*24.*model.height_of(None, tier)**2/10.
        assert abs(vector-model.moment_cost(None, tier, 0)) < 1e-10
        assert model.physical_height(None, tier) == 3.+tier*2.6


def test_mixed_keeps_physical_height_because_its_pricer_calls_moment_cost():
    class Base:
        def height_of(self, page, tier):
            return 6.5
    model = object.__new__(cold._model_class(Base, mixed=True))
    model.kg_weight = .06
    model.T = NS(mass=[24000.])
    assert model.height_of(None, 0) == 6.5
    assert model.moment_cost(None, 0, 0) == .06*24.*6.5


def test_zero_budget_does_not_initialize_or_consult_stored_context(monkeypatch):
    def forbidden_runtime():
        raise AssertionError('zero budget imported numerical runtime')
    monkeypatch.setattr(cold, '_runtime', forbidden_runtime)
    instance = NS(onboard=NS(placements=[]))
    context = {'stored': object(), 'stored_plan': object(), 'reference': object()}
    records, meta = cold.cold_candidates(instance, seed=3, budget_s=0, context=context)
    assert records == []
    assert meta['stored_data_consulted'] is False
    assert meta['full_checker_passes'] is None
    json.dumps(meta)


def test_cold_records_use_only_fresh_problem_and_keep_gate_distinct(monkeypatch):
    instance = NS(onboard=NS(placements=[]), load_list=['a'])
    problem = NS(instance=instance, engine=NS(T='fresh-tables'))
    stale = object()
    model = NS()
    fake_rmp = NS(Model=lambda p: model, base=NS(warm_columns=lambda *a, **k: 0))
    reference = lambda *args, **kwargs: NS(oracle='fresh-oracle', roofs={})
    runtime = (fake_rmp, NS(Model=object), NS(Reference=reference), NS(), NS(Problem=lambda i: problem))
    monkeypatch.setattr(cold, '_runtime', lambda: runtime)
    monkeypatch.setattr(cold, '_fresh_reference', lambda *a, **k: (
        NS(oracle='fresh-oracle', roofs={}), dict(source='fresh test reference')))
    monkeypatch.setattr(cold, '_instance_reference', lambda *a, **k: (
        NS(oracle='instance-oracle', roofs={}), dict(source='native test reference')))
    monkeypatch.setattr(cold, '_model_class', lambda *a, **k: lambda *a, **k: model)
    scored = NS(objective=7., plan='fresh-plan')
    monkeypatch.setattr(cold, '_pack', lambda p, *a, **k: (scored, dict(wall_s=0., complete_replay_legal=True, lifts=7.)))
    ship = NS(placements=[NS(container_id='a', slot='slot')])
    monkeypatch.setattr(cold, '_metrics', lambda *a: (ship, dict(cargo_lcg_m=50., cargo_kg_m=8.)))
    context = dict(problem=stale, stored=stale, stored_plan=stale, reference=stale,
                   cold_max_variants=1, cold_max_candidates=1, cold_refine_s=0,
                   cold_sorting_bound=False)
    records, meta = cold.cold_candidates(instance, budget_s=5., context=context)
    assert context['problem'] is problem
    assert records[0]['scored'] is scored
    assert records[0]['candidate_id'] == 'cold-000'
    assert records[0]['metadata']['complete_replay_legal']
    assert records[0]['metadata']['full_checker_passed'] is None
    assert records[0]['metadata']['model_family'] == 'unmodified_cgscale_rmp'
    assert meta['complete_stows'] == 1
    assert meta['full_checker_passes'] is None
    json.dumps(meta)


def test_plain_reference_delegates_to_native_one_stow_without_new_pricing():
    problem, model, scored = object(), object(), object()
    calls = []
    def native(p, m, **kwargs):
        calls.append((p, m, kwargs))
        return scored, dict(lp_proved=True, replay_complete=True)
    runtime = (NS(one_stow=native), None, None, None, None)
    result, row = cold._pack(problem, model, runtime, seed=8, deadline=100.,
                             max_rounds=1, plain=True)
    assert result is scored
    assert calls == [(problem, model, dict(seed=8, deadline=100.))]
    assert row['native_cgscale_reference']
    assert row['complete_replay_legal']
    assert 'lp_proved' not in row
    assert row['lp_is_physical_certificate'] is False


def test_native_recovery_prefers_mip_and_restores_private_hook(monkeypatch):
    calls = []
    def original(*args, **kwargs):
        raise AssertionError('CP must not run after a valid MIP incumbent')
    def mip(*args, **kwargs):
        calls.append(kwargs)
        return [3, 8], dict(status='FEASIBLE')
    rmp = NS(restricted_master=original)
    monkeypatch.setattr(cold, '_load', lambda *a: NS(recover=mip))
    monkeypatch.setattr(cold, '_selection_valid', lambda m, chosen: chosen == [3, 8])
    stop = cold.monotonic()+60.
    with cold._root_recovery(rmp) as history:
        chosen, row = rmp.restricted_master(object(), {}, deadline=stop)
    assert rmp.restricted_master is original
    assert chosen == [3, 8]
    assert calls[0]['seconds'] == 30.
    assert calls[0]['deadline'] == stop
    assert history == [row]
    assert row['cp'] is None
    assert row['certified_physics'] is False


def test_native_recovery_uses_cp_after_no_mip_incumbent_and_restores_on_error(monkeypatch):
    calls = []
    def original(*args, **kwargs):
        calls.append(kwargs)
        return [5], dict(status='FEASIBLE')
    rmp = NS(restricted_master=original)
    monkeypatch.setattr(cold, '_load', lambda *a: NS(recover=lambda *a, **k: (None, dict(status='UNKNOWN'))))
    monkeypatch.setattr(cold, '_selection_valid', lambda m, chosen: chosen == [5])
    stop = cold.monotonic()+60.
    try:
        with cold._root_recovery(rmp):
            chosen, row = rmp.restricted_master(object(), {}, deadline=stop)
            assert chosen == [5]
            assert calls[0]['seconds'] == 10.
            assert row['mip']['status'] == 'UNKNOWN'
            raise ValueError('caller failed after recovery')
    except ValueError:
        pass
    assert rmp.restricted_master is original


def test_compact_mode_passes_only_explicit_current_run_candidate_to_fresh_problem(monkeypatch):
    instance = NS(onboard=NS(placements=[]))
    problem, live, stale = object(), object(), object()
    runtime = (None, None, None, None, NS(Problem=lambda i: problem))
    monkeypatch.setattr(cold, '_runtime', lambda: runtime)
    calls = []
    def compact(i, p, r, **kwargs):
        calls.append((i, p, r, kwargs))
        return ['new-current-candidate'], kwargs['meta']
    monkeypatch.setattr(cold, '_compact_candidates', compact)
    context = dict(cold_compact_after_legacy=True, cold_live_scored=live,
                   stored=stale, stored_plan=stale, problem=stale)
    records, meta = cold.cold_candidates(instance, seed=9, budget_s=5., context=context)
    assert records == ['new-current-candidate']
    assert calls[0][:3] == (instance, problem, runtime)
    assert calls[0][3]['context']['cold_live_scored'] is live
    assert calls[0][3]['seed'] == 9
    assert context['problem'] is problem
    assert meta['stored_data_consulted'] is False


def test_compact_requires_explicit_matching_live_object_before_runtime_work():
    instance = NS(id='current')
    records, meta = cold._compact_candidates(instance, object(), None, seed=0,
        deadline=cold.monotonic()+1., context=dict(stored=object()), meta={})
    assert records == []
    assert meta['status'] == 'missing_or_mismatched_live_candidate'
    wrong = NS(plan=NS(instance_id='other'))
    records, meta = cold._compact_candidates(instance, object(), None, seed=0,
        deadline=cold.monotonic()+1., context=dict(cold_live_scored=wrong), meta={})
    assert records == []
    assert meta['status'] == 'missing_or_mismatched_live_candidate'


def test_large_plain_stage_has_total_120_second_cap_including_setup():
    assert cold._plain_stage_deadline(started=10., now=25., deadline=310.,
        budget_s=300., boxes=229) == 130.
    assert cold._plain_stage_deadline(started=10., now=25., deadline=310.,
        budget_s=300., boxes=428) == 130.
    assert cold._plain_stage_deadline(started=10., now=25., deadline=90.,
        budget_s=300., boxes=229) == 90.


def test_small_plain_stage_preserves_existing_allowance_and_deadline():
    assert cold._plain_stage_deadline(started=10., now=25., deadline=310.,
        budget_s=300., boxes=150) == 75.
    assert cold._plain_stage_deadline(started=10., now=25., deadline=310.,
        budget_s=100., boxes=121) == 55.
    assert cold._plain_stage_deadline(started=10., now=25., deadline=40.,
        budget_s=100., boxes=121) == 40.
