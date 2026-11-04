"""D64 production replay orchestration; numerical criteria have separate tests."""
from types import SimpleNamespace

import pytest

from stowage.checker import full
from stowage.model import MovePlan, PartReport, Violation
from stowage.model.fixtures import build_instance
from stowage.rules.catalogue import rule_freeze_v1


def test_activation_runs_in_port_even_when_legacy_option_false(monkeypatch):
    instance = build_instance()
    instance.load_list = []
    plan = MovePlan(id='in-port-gate', instance_id=instance.id, call_index=0)
    calls = []
    monkeypatch.setattr(full, '_voyage', lambda instance, ship, parts: [])
    monkeypatch.setattr(full, '_run_part', lambda instance, ship, name, fn: PartReport(name=name))
    monkeypatch.setattr(full, '_in_port', lambda instance, plan, ship: calls.append(ship) or PartReport(name='in_port'), raising=False)
    with rule_freeze_v1(enabled=False):
        report = full.check(instance, plan)
    assert not calls
    assert 'in_port' not in report.parts
    with rule_freeze_v1(enabled=True):
        report = full.check(instance, plan, intermediate_stability=False)
    assert len(calls) == 1
    assert 'in_port' in report.parts


def _harness(monkeypatch, arrival_failures=(), departure_failures=(), *, count=5):
    from stowage.checker import in_port
    instance = build_instance()
    instance.load_list = []
    plan = MovePlan(id='in-port-replay', instance_id=instance.id, call_index=0)
    # Distinct fake lift kinds establish every-lift coverage without coupling
    # this orchestration test to physical move setup.
    kinds = ['load', 'discharge', 'restow_shift', 'hatch_cover_off', 'hatch_cover_on'][:count]
    plan.moves = [SimpleNamespace(kind=k) for k in kinds]
    ships = [instance.onboard.model_copy(deep=True) for _ in range(count+1)]
    for i, ship in enumerate(ships):
        ship.tank_masses_kg = {'state': float(i)}
    class Builder:
        def __init__(self, instance): self.index = 0
        @property
        def state(self): return SimpleNamespace(ship=ships[self.index])
        def push(self, move, *, move_index):
            self.index += 1
            return self.state
    class Context:
        max_fsm_tank_ids = frozenset()
        def for_phase(self, phase): return self
        def transferred_ship(self, ship, *, phase):
            result = ship.model_copy(deep=True)
            result.tank_masses_kg['phase'] = float(phase == 'departure')
            return result, {'heel_transfer_kg': 10.}
    calls = []
    def equilibrium_metrics(ship):
        state,phase=ship.tank_masses_kg['state'],ship.tank_masses_kg.get('phase',0.)
        return dict(draught_aft_m=4.+state*.01+phase*.1,
                    draught_fwd_m=3.+state*.01+phase*.2,list_rad=phase*.001)
    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def evaluate(self, states, *, stop_at_first_failure=False):
            reports = []
            for ship in states:
                i = int(ship.tank_masses_kg['state'])
                phase = int(ship.tank_masses_kg['phase'])
                calls.append((i, phase))
                failed = i in (departure_failures if phase else arrival_failures)
                violations = [Violation(rule_id='port.stability.cargo_operations', severity='hard', call_index=0,
                                        message='list_rad exceeds limit')] if failed else []
                reports.append(PartReport(name='stability', violations=violations,
                    metrics={'in_port': 1., **equilibrium_metrics(ship)},
                    margins={'list_rad': -.1 if failed else .1}))
            return SimpleNamespace(reports=reports, measured_state_time_s=.1,
                                   measured_state_count=min(8, len(reports)))
    monkeypatch.setattr(in_port, 'InPortStateBuilder', Builder)
    monkeypatch.setattr(in_port, 'prepare_context', lambda instance, ship: Context())
    monkeypatch.setattr(in_port, 'InPortEvaluator', Evaluator)
    monkeypatch.setattr(in_port, '_strength_context', lambda instance: instance)
    strength_calls, rule_calls = [], []
    monkeypatch.setattr(in_port, 'check_strength', lambda instance, ship, **kw:
        strength_calls.append((int(ship.tank_masses_kg['state']), kw)) or
        PartReport(name='strength',metrics=equilibrium_metrics(ship)))
    monkeypatch.setattr(in_port, 'check_static_rules', lambda instance, ship, **kw: rule_calls.append(int(ship.tank_masses_kg['state'])) or PartReport(name='rules'))
    monkeypatch.setattr(in_port, '_strength_risk', lambda instance, ship: float(ship.tank_masses_kg['state']))
    return in_port, instance, plan, ships, calls, strength_calls, rule_calls


def test_t5_changeover_is_first_arrival_failure_and_departure_reruns_suffix(monkeypatch):
    module, instance, plan, ships, calls, _, _ = _harness(monkeypatch, arrival_failures={2, 4})
    report = module.replay(instance, plan, ships[-1])
    assert report.ran and not report.violations
    assert report.metrics['k'] == report.metrics['k_arr'] == 2.
    assert calls == [(i, 0) for i in range(6)] + [(i, 1) for i in range(2, 6)]
    assert report.metrics['state:2.departure.heel_transfer_kg'] == 10.


def test_t5_suffix_failure_has_state_move_index_and_k(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch, arrival_failures={2}, departure_failures={3})
    report = module.replay(instance, plan, ships[-1])
    assert len(report.violations) == 1
    violation = report.violations[0]
    assert violation.rule_id == 'port.stability.cargo_operations'
    assert violation.move_index == 2
    assert 'state=3' in violation.message and 'k=2' in violation.message
    assert report.metrics['failure.move_index'] == 2.
    assert report.metrics['failure.k'] == 2.


def test_every_lift_including_discharge_restow_hatch_and_no_changeover(monkeypatch):
    module, instance, plan, ships, calls, strength_calls, rule_calls = _harness(monkeypatch)
    report = module.replay(instance, plan, ships[-1])
    assert report.metrics['k'] == 6.
    assert report.metrics['state_count'] == 6.
    assert calls == [(i, 0) for i in range(6)]
    assert {0, 2, 4, 5} <= {i for i, _ in strength_calls}
    assert {i for i, _ in strength_calls} == set(rule_calls)
    assert all(kw['in_port'] is False for _, kw in strength_calls)  # explicit regenerated hull


def test_internal_error_is_never_repaired_by_ballast_changeover(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch)
    class Broken:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def evaluate(self, states, **kwargs):
            return SimpleNamespace(reports=[PartReport(name='stability', ran=False, error='broken', violations=[Violation(
                rule_id='checker.internal_error', severity='hard', call_index=0, message='broken')]) for _ in states],
                measured_state_time_s=.1, measured_state_count=min(8, len(states)))
    monkeypatch.setattr(module, 'InPortEvaluator', Broken)
    report = module.replay(instance, plan, ships[-1])
    assert not report.ran
    assert any(v.rule_id == 'checker.internal_error' for v in report.violations)


def test_t8_bay_completion_tracks_both_twenty_foot_halves(monkeypatch):
    from stowage.checker import in_port
    instance = build_instance()
    moves = [SimpleNamespace(kind='load', dst=slot) for slot in
             ('0020182', '0010182', '0050182', '0070182', '0090182',
              '0110182', '0130182', '0150182', '0170182')]
    monkeypatch.setattr(in_port, '_strength_risk', lambda instance, ship: float(ship))
    samples = in_port._sample_indices(instance, moves, list(range(10)), instance)
    # s1 completes bay003 (the forward half of 002); s2 completes bay001.
    assert {1, 2} <= set(samples)


def test_t8_sample_failure_retains_k_and_other_samples(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch)
    calls = []
    def broken(instance, ship, **kwargs):
        i = int(ship.tank_masses_kg['state'])
        calls.append(i)
        if i == 0:
            raise RuntimeError('synthetic strength integration error')
        return PartReport(name='strength')
    monkeypatch.setattr(module, 'check_strength', broken)
    report = module.replay(instance, plan, ships[-1])
    assert not report.ran
    assert report.metrics['k'] == 6.
    assert any(v.rule_id == 'checker.internal_error' for v in report.violations)
    assert {0, 2, 4, 5} <= set(calls)


def test_off_serialization_matches_legacy_branch_byte_for_byte(monkeypatch):
    import inspect
    from stowage.model import Provenance
    instance = build_instance()
    instance.load_list = []
    plan = MovePlan(id='legacy-bytes', instance_id=instance.id, call_index=0)
    monkeypatch.setattr(full, '_run_part', lambda instance, ship, name, fn: PartReport(name=name))
    monkeypatch.setattr(full, 'current_provenance', lambda **kwargs: Provenance(created_by='fixed-test-producer'))
    def forbidden(*args, **kwargs):
        raise AssertionError('off called D64')
    monkeypatch.setattr(full, '_in_port', forbidden)
    # Execute the old orchestration branch against the same live dependencies.
    # This controls catalogue identity, versions and input hashes as well as the
    # informational timestamp, so equality compares the complete report bytes.
    source = inspect.getsource(full._check)
    start = source.index('    from stowage.rules.catalogue import rule_freeze_v1_enabled\n')
    end = source.index('    elif intermediate_stability:\n', start)
    legacy_source = source[:start] + '    if intermediate_stability:\n' + source[end+len('    elif intermediate_stability:\n'):]
    namespace = dict(vars(full))
    exec(compile(legacy_source, '<legacy-check-branch>', 'exec'), namespace)
    with rule_freeze_v1(enabled=False):
        legacy = namespace['_check'](instance, plan)
        current = full.check(instance, plan)
    assert current.model_dump_json().encode() == legacy.model_dump_json().encode()


def test_sampling_setup_error_preserves_changeover_evidence(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch, arrival_failures={2})
    def broken(instance):
        raise ValueError('missing strength geometry')
    monkeypatch.setattr(module, '_strength_context', broken)
    report = module.replay(instance, plan, ships[-1])
    assert not report.ran
    assert report.metrics['k'] == 2.
    assert any(v.rule_id == 'checker.internal_error' for v in report.violations)


def test_explicit_harbour_hull_is_private_and_sign_specific(monkeypatch):
    from stowage.checker import in_port, strength, strength_limits
    instance = build_instance()
    original = instance.vessel.hull
    original.hull_offsets_ref = 'params/fleet/feeder_500.toml'
    station = original.stations[0]
    harbour = station.model_copy(update={'max_bending_nm': station.max_bending_nm+123.,
                                         'min_bending_nm': station.min_bending_nm-456.})
    params, geometry = {}, object()
    monkeypatch.setattr(strength, '_geometry', lambda name: (params, geometry))
    calls = []
    monkeypatch.setattr(strength_limits, 'permissible_stations', lambda p, g, *, in_port: calls.append((p, g, in_port)) or [harbour])
    result = in_port._strength_context(instance)
    assert calls == [(params, geometry, True)]
    assert result.vessel.hull.stations == [harbour]
    assert result.vessel.hull.stations[0].max_bending_nm == station.max_bending_nm+123.
    assert result.vessel.hull.stations[0].min_bending_nm == station.min_bending_nm-456.
    assert instance.vessel.hull is original
    assert original.stations[0] is station


def test_t8_p9_restow_after_load_rechecks_and_maps_only_stack_constraints(monkeypatch):
    module, instance, plan, ships, _, _, rule_calls = _harness(monkeypatch)
    def rules(instance, ship, **kwargs):
        i = int(ship.tank_masses_kg['state'])
        rule_calls.append(i)
        return PartReport(name='rules', violations=[
            Violation(rule_id='expert.physical.stack_weight', severity='hard', call_index=0, message='stack too heavy'),
            Violation(rule_id='expert.moves.bottom_up', severity='hard', call_index=0, message='other static rule')]
            if i == 3 else [], margins={'stack_mass_kg': -10. if i == 3 else 10., 'other_margin': -1.})
    monkeypatch.setattr(module, 'check_static_rules', rules)
    # Force the post-restow state as a strength sample (top three).
    monkeypatch.setattr(module, '_strength_risk', lambda instance, ship: 100. if ship.tank_masses_kg['state'] == 3. else 0.)
    report = module.replay(instance, plan, ships[-1])
    assert 3 in rule_calls
    assert len(report.violations) == 1
    assert report.violations[0].rule_id == 'port.stability.cargo_operations'
    assert report.violations[0].move_index == 2
    assert 'stack too heavy' in report.violations[0].message
    assert report.margins['state:3.arrival.static.stack_mass_kg'] == -10.
    assert not any(key.endswith('other_margin') for key in report.margins)


@pytest.mark.parametrize('message,source_rule,expected_rule', [
    ('Strength input/equilibrium error: coupled tank/hull floating equilibrium did not converge',
     'checker.missing_vessel_data', 'checker.internal_error'),
    ('Strength input/equilibrium error: no converged floating equilibrium (scaled residual 1)',
     'checker.missing_vessel_data', 'checker.internal_error'),
    ('Strength input/equilibrium error: nonfinite load grid or nonpositive Gauss weights',
     'checker.missing_vessel_data', 'checker.internal_error'),
    ('Numerical load-curve imbalance exceeds the synthetic 0.1% closure tolerance after subtracting the physical trim lever; no strength verdict is available.',
     'checker.missing_vessel_data', 'checker.internal_error'),
    ('S1 requires finite SF/BM readouts at every prescribed station and the free bow; numerical readouts are incomplete or misaligned.',
     'iacs.s1.loading_instrument', 'checker.internal_error'),
    ('S1 required strength readouts contain masked/missing samples.',
     'iacs.s1.loading_instrument', 'checker.internal_error'),
    ('Strength requires hull geometry, lightship blocks and permissible read-out stations.',
     'checker.missing_vessel_data', 'checker.missing_vessel_data'),
    ('Strength input/equilibrium error: lightship weight blocks disagree with declared LCG',
     'checker.missing_vessel_data', 'checker.missing_vessel_data'),
])
def test_strength_numerical_diagnostics_are_internal_but_missing_data_retained(
        monkeypatch, message, source_rule, expected_rule):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch)
    monkeypatch.setattr(module, 'check_strength', lambda *args, **kwargs:
        PartReport(name='strength', ran=False, error=message, violations=[Violation(
            rule_id=source_rule, severity='hard', call_index=0, message=message)]))
    report = module.replay(instance, plan, ships[-1])
    assert not report.ran
    assert report.metrics['k'] == 6.
    assert {v.rule_id for v in report.violations} == {expected_rule}


def test_rev31_replay_uses_separate_fsm_passes_and_exposes_pass1_calibration(monkeypatch):
    module, instance, plan, ships, calls, _, _ = _harness(monkeypatch, arrival_failures={2})
    phases = []
    class PhaseContext:
        def __init__(self, phase='arrival'):
            self.phase = phase
        def for_phase(self, phase):
            return PhaseContext(phase)
        def transferred_ship(self, ship, *, phase):
            result = ship.model_copy(deep=True)
            result.tank_masses_kg['phase'] = float(phase == 'departure')
            return result, {}
    class Evaluator:
        def __init__(self, instance, context, *, workers=1, warm_start=False):
            self.phase = context.phase
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def evaluate(self, states, **kwargs):
            phases.append(self.phase)
            reports = []
            for ship in states:
                index = int(ship.tank_masses_kg['state'])
                broken = self.phase == 'arrival' and index == 2
                reports.append(PartReport(name='in_port', violations=[Violation(
                    rule_id='port.stability.cargo_operations', severity='hard',
                    call_index=0, message='gm_m exceeds limit')] if broken else []))
            return SimpleNamespace(reports=reports,
                measured_state_time_s=.75 if self.phase == 'arrival' else 99.,
                measured_state_count=min(8, len(states)))
    monkeypatch.setattr(module, 'prepare_context', lambda *args: PhaseContext())
    monkeypatch.setattr(module, 'InPortEvaluator', Evaluator)
    telemetry = {}
    result = module.replay(instance, plan, ships[-1], workers=2, telemetry=telemetry)
    assert phases == ['arrival', 'departure']
    assert result.metrics['k_arr'] == 2.
    assert telemetry['t_hat_s'] == .75
    assert telemetry['t_hat_state_count'] == 6.
    assert telemetry['remaining_state_count'] == 4.
    assert telemetry['workers'] == 2.
    assert telemetry['acceptance_budget_s'] == 10.


def test_rev31_acceptance_budget_scales_measured_cost_not_fixed_constant(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch, arrival_failures={2})
    old_evaluator = module.InPortEvaluator
    class TimedEvaluator(old_evaluator):
        def evaluate(self, *args, **kwargs):
            result = super().evaluate(*args, **kwargs)
            result.measured_state_time_s = 3.
            result.measured_state_count = min(8, len(result.reports))
            return result
    monkeypatch.setattr(module, 'InPortEvaluator', TimedEvaluator)
    telemetry = {}
    result = module.replay(instance, plan, ships[-1], workers=2, telemetry=telemetry)
    assert telemetry['acceptance_budget_s'] == 12.


def test_rev31_departure_candidate_cannot_move_arrival_changeover(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch)
    class Context:
        def __init__(self, extra_fsm, phase='departure'):
            self.extra_fsm, self.phase = extra_fsm, phase
        def for_phase(self, phase):
            return Context(self.extra_fsm if phase == 'departure' else 0., phase)
        def transferred_ship(self, ship, *, phase): return ship, {}
    class Evaluator:
        def __init__(self, instance, context, **kwargs): self.context = context
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def evaluate(self, states, **kwargs):
            reports = []
            for ship in states:
                index = int(ship.tank_masses_kg['state'])
                # A near-GM boundary would shift k if departure-only FSM
                # leaked into pass1. A later trim boundary fixes true k=2.
                failed = (index == 1 and self.context.extra_fsm > 0.) or (
                    self.context.phase == 'arrival' and index == 2)
                reports.append(PartReport(name='in_port', violations=[Violation(
                    rule_id='port.stability.cargo_operations', severity='hard',
                    call_index=0, message='synthetic boundary')] if failed else []))
            return SimpleNamespace(reports=reports, measured_state_time_s=.1,
                                   measured_state_count=min(8, len(states)))
    monkeypatch.setattr(module, 'prepare_context', lambda instance, departure:
                        Context(departure.tank_masses_kg['candidate_extra_fsm']))
    monkeypatch.setattr(module, 'InPortEvaluator', Evaluator)
    candidates = [ships[-1].model_copy(update={'tank_masses_kg': {'candidate_extra_fsm': extra}})
                  for extra in (0., .01)]
    results = [module.replay(instance, plan, candidate) for candidate in candidates]
    assert [r.metrics['k_arr'] for r in results] == [2., 2.]


def test_review_nonfinite_coupled_root_cannot_be_repaired_by_changeover(monkeypatch):
    import numpy as np
    from stowage.checker import stability, stability_equilibrium, stability_loading
    from stowage.checker.in_port_evaluator import check_state
    from stowage.checker.stability_loading import LoadingCondition
    from stowage.vessel.hydrostatics import FloatingCondition
    instance = build_instance()
    condition = LoadingCondition(1e6, 50., 0., 3., .1, 1e5)
    geometry = SimpleNamespace(equilibrium=lambda *args: FloatingCondition(2., 2., 0., None, 0.))
    context = SimpleNamespace(correct_loading=lambda c:c, max_fsm_tank_ids=())
    monkeypatch.setattr(stability, '_canonical_fleet_policy', lambda *args:None)
    monkeypatch.setattr(stability, '_context', lambda *args:(geometry, {}))
    monkeypatch.setattr(stability, 'loading_condition', lambda *args, **kwargs:condition)
    count = iter(range(1, 30))
    monkeypatch.setattr(stability_loading, 'liquid_cg', lambda *args:np.full(3, next(count)))
    monkeypatch.setattr(stability_equilibrium, 'root', lambda *args, **kwargs:
                        SimpleNamespace(x=np.full(3, np.nan)))
    monkeypatch.setenv('STOWAGE_STABILITY_DIRECT', '1')
    numerical = check_state(instance, instance.onboard, context, warm_start=False)
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch, count=1)
    class Evaluator:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def evaluate(self, states, **kwargs):
            reports = [numerical.model_copy(deep=True)
                       if ship.tank_masses_kg['phase'] == 0 and ship.tank_masses_kg['state'] == 0
                       else PartReport(name='in_port') for ship in states]
            return SimpleNamespace(reports=reports, measured_state_time_s=.1,
                                   measured_state_count=min(8, len(reports)))
    monkeypatch.setattr(module, 'InPortEvaluator', Evaluator)
    result = module.replay(instance, plan, ships[-1])
    assert not numerical.ran and numerical.error
    assert {v.rule_id for v in numerical.violations} == {'checker.internal_error'}
    assert result.metrics['k_arr'] == 0.
    assert not result.ran and result.error
    assert any(v.rule_id == 'checker.internal_error' for v in result.violations)


def test_review_cold_replay_serial_parallel_reports_identical_telemetry_separate(monkeypatch):
    module, instance, plan, ships, _, _, _ = _harness(monkeypatch, arrival_failures={2})
    original = module.InPortEvaluator
    modes = []
    class Evaluator(original):
        def __init__(self, instance, context, *, workers=1, warm_start=True):
            super().__init__(instance, context, workers=workers)
            self.workers = workers
            modes.append(warm_start)
        def evaluate(self, *args, **kwargs):
            result = super().evaluate(*args, **kwargs)
            result.measured_state_time_s = self.workers * 10.
            return result
    monkeypatch.setattr(module, 'InPortEvaluator', Evaluator)
    first, second = {}, {}
    serial = module.replay(instance, plan, ships[-1], workers=1, telemetry=first)
    parallel = module.replay(instance, plan, ships[-1], workers=2, telemetry=second)
    assert modes == [False, False, False, False]
    assert serial.model_dump_json() == parallel.model_dump_json()
    assert first['workers'] == 1 and second['workers'] == 2
    assert first['t_hat_s'] == 10. and second['t_hat_s'] == 20.
    assert not any(key in serial.metrics for key in ('t_hat_s','workers','acceptance_budget_s'))


@pytest.mark.slow
def test_review_real_cold_replay_serial_parallel_byte_identity():
    import runpy
    from pathlib import Path
    helper = runpy.run_path(str(Path(__file__).with_name('test_in_port_acceptance.py')))['saved_pilot']
    with rule_freeze_v1(enabled=True):
        instance, plan = helper('000')
        one, two = {}, {}
        serial = full._in_port(instance, plan, workers=1, telemetry=one)
        parallel = full._in_port(instance, plan, workers=2, telemetry=two)
        assert serial.ran and serial.error is None and not serial.violations, serial.model_dump()
        assert parallel.ran and parallel.error is None and not parallel.violations, parallel.model_dump()
        assert serial.model_dump_json() == parallel.model_dump_json()
        assert one['workers'] == 1 and two['workers'] == 2
        assert one['t_hat_s'] > 0. and two['t_hat_s'] > 0.
