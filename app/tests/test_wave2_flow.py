"""Wave-two live planning controls; physical acceptance stays with the checker."""
import json
import pytest


def test_exact_solver_is_a_live_registered_planner(replay_case, tmp_path):
    from app.flow import DemoService, _make_planner
    instance, plan = replay_case
    service = DemoService(instance, plan, discover=False)
    assert any(p['id'] == 'solver.exact' and p['live'] for p in service.catalog()['planners'])
    assert _make_planner('solver.exact').name == 'solver.exact'


def test_exact_primary_runs_against_the_frozen_two_step_baseline(replay_case, monkeypatch):
    from app.flow import DemoService
    from app.tests.test_flow import wait_done
    from stowage.model import PlannerResult
    instance, plan = replay_case
    calls = []

    def pipeline(planner, received, **kwargs):
        calls.append(planner.name)
        return PlannerResult(status='ok', plan=plan, planner=planner.name, planner_version='test'), None

    monkeypatch.setattr('app.flow.run_planner', pipeline)
    service = DemoService(instance, plan, discover=False)
    job = service.start_solve(instance.id, planner='solver.exact', compare=True)
    assert wait_done(service, job['id'])['status'] == 'done'
    assert calls == ['solver.exact', 'baseline.twostep']


@pytest.mark.parametrize('version', ['1.8', '1.9'])
def test_import_accepts_old_and_new_optional_port_fields(replay_case, tmp_path, version):
    from app.inputs import load_inputs
    instance, plan = replay_case
    data = instance.model_dump(mode='json')
    data['schema_version'] = version
    if version == '1.8':
        data['port'].pop('max_crane_height_tiers', None)
        data['port'].pop('berthing_side', None)
        for block in data['port']['yard']['blocks']:
            block.pop('truck_lane_side', None)
        for box in data['containers']:
            box.pop('direct_delivery', None)
    (tmp_path / 'instance.json').write_text(json.dumps(data))
    (tmp_path / 'plan.json').write_text(plan.model_dump_json())
    loaded = load_inputs(instance_path=tmp_path / 'instance.json', plan_path=tmp_path / 'plan.json')
    assert loaded.instance.id == instance.id
    if version == '1.8':
        assert loaded.instance.port.max_crane_height_tiers is None
    else:
        assert loaded.instance.port.max_crane_height_tiers == instance.port.max_crane_height_tiers


def test_mid_voyage_uses_its_recorded_fraction_and_retains_all_part_failures(replay_case, monkeypatch):
    from types import SimpleNamespace
    from app.checker import _condition_payloads
    from stowage.model import CheckReport, PartReport, Violation, VoyageSpec, VoyageConditionIdentity
    instance, _ = replay_case
    instance.voyage = VoyageSpec(
        departure=VoyageConditionIdentity(id='dep', phase='departure', call_index=0,
            locode=instance.port.locode, environment_id='dep-env', density_id='dep-density', water_density_kg_m3=1025.),
        arrival=VoyageConditionIdentity(id='arr', phase='arrival', call_index=1,
            locode=instance.rotation.calls[1].locode, environment_id='arr-env', density_id='arr-density', water_density_kg_m3=1000.))
    metrics = {'condition.phase: mid_voyage': 1., 'condition.remaining_fraction': .55}
    parts = {f'condition:dep:mid_voyage:{name}': PartReport(name=name, metrics=metrics)
             for name in ('stability', 'strength', 'lashing')}
    parts['condition:dep:mid_voyage:lashing'].violations = [Violation(
        rule_id='checker.missing_vessel_data', severity='hard', call_index=0, message='Mid-voyage securing unavailable')]
    fractions = []

    def derive(ship, leg, r, *, vessel):
        fractions.append(r)
        return SimpleNamespace(tank_masses_kg={'recorded-mid-consumable': 55.})

    def panel(context, ship, part):
        assert part.ran
        assert context.call_index == 0 and context.port.max_draught_m is None
        assert context.port.water_density_kg_m3 == 1025.
        assert ship.tank_masses_kg == {'recorded-mid-consumable': 55.}
        return dict(ran=True, metrics={}, gz_curve=[{'heel_rad': 0., 'gz_m': 0.}], curve_error=None)

    monkeypatch.setattr('stowage.model.derive_voyage_liquid_state', derive)
    monkeypatch.setattr('app.checker.stability_payload', panel)
    condition = _condition_payloads(instance, instance.onboard, CheckReport(passed=False, parts=parts))[0]
    assert fractions == [.55]
    assert condition['failed_parts'] == ['lashing']
    assert condition['passed'] is False
    assert condition['stability']['curve_error'] is None
