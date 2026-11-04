"""Review regressions isolate presentation and attribution, not physical qualification."""
from copy import deepcopy
import json
import math
import runpy

import pytest


def test_repaired_checked_plan_is_attributed_but_never_an_approved_comparison(replay_case, monkeypatch):
    from app.backend import build_replay
    from app.comparison import build_comparison
    from app.tests.report_helpers import bind_report
    from stowage.model import CheckReport, PartReport, PlannerResult
    from stowage.moves import apply_plan
    instance, plan = replay_case
    report = CheckReport(passed=True, count=apply_plan(instance, plan).count,
        parts={key: PartReport(name=key) for key in
               ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')})
    bind_report(report, instance, plan)
    monkeypatch.setattr('app.backend.build_checker_payload', lambda *a, **k:
                        {'passed': True, 'bound': True, 'report': report.model_dump(mode='json')})
    source = PlannerResult(status='ok', plan=plan, planner='solver.exact', planner_version='test',
        repaired=True, repair_note='Prefix reconstructed', metrics={'fallback_needed': True, 'repair_status': 'attempted'})
    replay = build_replay(instance, plan, planner_result=source)
    assert replay['metadata']['repaired'] is True
    assert replay['metadata']['repairing_solver'] == 'solver.scalable'
    assert replay['metadata']['repair_note'] == 'Prefix reconstructed'
    assert replay['metadata']['shippable'] is True  # Physical approval is a separate canonical gate.
    compared = build_comparison(replay, replay)
    assert compared['comparable'] is False
    assert 'repair' in compared['notice'].lower()
    assert compared['left']['repaired'] is True


def test_baseline_pipeline_explicitly_disables_repair(replay_case, monkeypatch):
    from app.flow import DemoService
    from app.tests.test_flow import wait_done
    from stowage.model import PlannerResult
    instance, plan = replay_case
    calls = []
    def pipeline(planner, world, **kwargs):
        calls.append((planner.name, kwargs))
        return PlannerResult(status='error', planner=planner.name, planner_version='test'), None
    monkeypatch.setattr('app.flow.run_planner', pipeline)
    service = DemoService(instance, plan, discover=False)
    wait_done(service, service.start_solve(instance.id, compare=True)['id'])
    assert next(k for p, k in calls if p == 'baseline.twostep')['allow_repair'] is False


def test_exact_incumbent_and_all_vessels_have_honest_labels(replay_case):
    from app.backend import build_replay
    from stowage.model import PlannerResult
    instance, plan = replay_case
    source = PlannerResult(status='ok', plan=plan, planner='solver.exact', planner_version='test',
                           metrics={'optimality_status': 'FEASIBLE'})
    replay = build_replay(instance, plan, planner_result=source)
    assert replay['metadata']['optimality_status'] == 'FEASIBLE'
    assert replay['metadata']['planner_label'] == 'Exact (not proven optimal)'
    assert replay['metadata']['vessel_label'].startswith('Synthetic ')
    assert instance.vessel.name in replay['metadata']['vessel_label']


def test_projected_lift_difference_reconciles_comparison(replay_case):
    from app.backend import build_replay
    from app.comparison import build_comparison
    instance, plan = replay_case
    replay = build_replay(instance, plan)
    compared = build_comparison(replay, deepcopy(replay))
    assert compared['projected_lift_difference'] == 0
    assert any(row['key'] == 'future_total_lifts' for row in compared['rows'])


def test_weather_override_displays_raw_clearance_and_applied_minimum():
    from app.stability import _criteria
    from stowage.model import PartReport
    condition = runpy.run_path('tests/checker/test_stability_criteria.py')['condition']
    metrics, margins, _ = condition(rule_overrides={'iscode.a2_3.weather':
                                                   {'minimum_steady_heel_margin_rad': .02}})
    row = next(row for row in _criteria(PartReport(name='stability', metrics=metrics, margins=margins))
               if row['key'] == 'weather_steady_heel_margin_rad')
    raw = min(math.radians(16), .8 * metrics['weather_deck_edge_rad']) - metrics['weather_phi0_rad']
    assert row['measured'] == pytest.approx(raw)
    assert row['limit'] == pytest.approx(.02)


def test_inport_panel_pending_then_uses_only_actual_bound_report(replay_case, monkeypatch):
    from app.checker import build_checker_payload
    from app.tests.report_helpers import bind_report
    from stowage.model import CheckReport, PartReport
    instance, plan = replay_case
    report = CheckReport(passed=False)
    bind_report(report, instance, plan)
    monkeypatch.setattr('app.checker.full.check', lambda *a: report)
    assert build_checker_payload(instance, plan, evaluated_frame=0)['port_stability']['available'] is False
    report.parts['port.stability.cargo_operations'] = PartReport(
        name='port.stability.cargo_operations', metrics={'ballast_changeover_k': 2.,
            'lift.1.gm_m': .6, 'lift.2.gm_m': .8})
    bind_report(report, instance, plan)
    panel = build_checker_payload(instance, plan, evaluated_frame=0)['port_stability']
    assert panel['available'] is True and panel['ballast_changeover_k'] == 2
    assert [lift['index'] for lift in panel['per_lift']] == [1, 2]
    assert panel['metrics']['lift.2.gm_m'] == .8


def test_storyboard_selection_is_predeclared_and_sorted_by_id(replay_case, tmp_path):
    from app.selection import select_storyboard
    instance, _ = replay_case
    for name, identity, split, direct in [('a', 'z-first-file', 'val', True),
                                          ('z', 'a-first-id', 'val', True),
                                          ('b', '0-wrong-split', 'test', True),
                                          ('c', '0-no-direct', 'val', False)]:
        world = instance.model_copy(deep=True)
        world.id = identity
        world.family['split'] = split
        if direct:
            box = next(box for box in world.containers if box.id in world.load_list and box.dg is None)
            box.direct_delivery = True
            for stack in world.yard.stacks:
                stack.boxes = [cid for cid in stack.boxes if cid != box.id]
            world.yard.stacks = [stack for stack in world.yard.stacks if stack.boxes]
        (tmp_path / f'{name}.json').write_text(world.model_dump_json())
    path, metadata = select_storyboard(tmp_path, split='val', rung='tiny', rule='first-dg-direct-delivery')
    assert json.loads(path.read_text())['id'] == 'a-first-id'
    assert metadata['label'] == '1 instance, illustrative'
    assert metadata['rule'] == 'first-dg-direct-delivery'
    with pytest.raises(ValueError, match='No instance'):
        select_storyboard(tmp_path, split='train', rung='tiny', rule='first-dg-direct-delivery')


def test_comparison_retains_validated_model_identity_for_card_labels(replay_case):
    from app.backend import build_replay
    from app.comparison import build_comparison
    instance, plan = replay_case
    replay = build_replay(instance, plan)
    identity = {'verified': True, 'label': 'Base Muse Glimmer 30B + feasibility mask'}
    replay['metadata'].update(planner='base_glimmer_mask', model_run=identity)
    assert build_comparison(replay, replay)['left']['model_run'] == identity


def test_cli_instance_fingerprint_is_rechecked_after_cached_replay(replay_case, tmp_path):
    from app.flow import DemoService
    from app.inputs import load_inputs
    instance, plan = replay_case
    world = tmp_path / 'instance.json'
    artifact = tmp_path / 'plan.json'
    world.write_text(instance.model_dump_json())
    artifact.write_text(plan.model_dump_json())
    inputs = load_inputs(instance_path=world, plan_path=artifact)
    service = DemoService(inputs.instance, inputs.plan, inputs.metadata, discover=False)
    service.replay(service.active_plan_id)
    changed = json.loads(world.read_text())
    changed['seed'] = (changed.get('seed') or 0) + 1
    world.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='changed'):
        service.replay(service.active_plan_id)


def test_storyboard_uses_frozen_inventory_when_generators_have_no_split_tag(replay_case, tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    from app.selection import select_storyboard
    instance, _ = replay_case
    world = instance.model_copy(deep=True)
    world.family.pop('split', None)
    box = next(box for box in world.containers if box.id in world.load_list and box.dg is None)
    box.direct_delivery = True
    for stack in world.yard.stacks:
        stack.boxes = [cid for cid in stack.boxes if cid != box.id]
    world.yard.stacks = [stack for stack in world.yard.stacks if stack.boxes]
    path = tmp_path / 'current.json'
    path.write_text(world.model_dump_json())
    (tmp_path / 'manifest.json').write_text('{}')
    calls = []
    def select(root, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(rows=[{'path': str(path), 'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}])
    monkeypatch.setattr('eval.harness_inventory.select_frozen_instances', select)
    selected, metadata = select_storyboard(tmp_path, split='val', rung='tiny', rule='first-dg-direct-delivery')
    assert selected == path
    assert calls[0]['split'] == 'val' and calls[0]['rungs'] == ('tiny',)
    assert metadata['split_source'] == 'frozen inventory'
