"""Harness records names, missing allocations and current failed evidence honestly."""
import json
from types import SimpleNamespace

import pytest

from eval import harness
from eval.harness_inventory import select_frozen_instances
from eval.splits import DEFAULT_MANIFEST, load_inventory, spec_key
from stowage.model import CheckReport, PlannerResult
from tests.eval.test_harness import Diagnostic, campaign


def test_result_canonical_ship_nominal_and_mask_diagnostic(empty_ship_instance, monkeypatch):
    import stowage.feasibility
    instance = empty_ship_instance.model_copy(deep=True)
    instance.vessel.id = 'feeder_1000'
    instance.vessel.slice_of = None
    instance.vessel.nominal_teu = 816
    monkeypatch.setattr(harness, '_load_selected', lambda *a: instance)
    monkeypatch.setattr(stowage.feasibility, 'make_feasibility_engine', lambda *a, **k:
                        SimpleNamespace(diagnostics={'global_screen': 'unavailable: stale epoch'}))
    def pipeline(*a, **kw):
        stowage.feasibility.make_feasibility_engine(instance)
        return PlannerResult(status='dead_end', planner='test', planner_version='1'), None
    monkeypatch.setattr(harness, 'run_planner', pipeline)
    row = dict(spec_key='x', split='test', rung='tiny', input_sha256='x', spec={'family': 'heldout_slice'})
    record = harness._evaluate(Diagnostic(), row, 0, None, True, None)
    assert record['instance']['vessel_id'] == 'feeder_1000'
    assert record['instance']['vessel_class'] == 'feeder_800'
    assert record['instance']['vessel_teu'] == 800
    assert record['instance']['vessel_raw_nominal_teu'] == 816
    assert record['mask_global_screen'] == 'unavailable: stale epoch'
    assert record['mask_unavailable'] is True


def test_allocation_retains_rejected_and_unemitted_specs(tmp_path, empty_ship_instance):
    root = campaign(tmp_path, empty_ship_instance)
    inventory = load_inventory()
    entries = [e for e in inventory.manifest['entries'] if e['split'] == 'val' and e['spec']['rung'] == 'tiny']
    manifest_path = root / 'manifest.json'
    data = json.loads(manifest_path.read_text())
    emitted = {s['spec_key'] for g in data['groups'].values() for s in g['shards']}
    rejected = next(e for e in entries if spec_key(e['spec']) not in emitted)
    from hashlib import sha256
    rejection_path = root / 'random_yard/tiny/rejected.json'
    rejection_path.write_text('{"status":"rejected"}')
    data['groups']['random_yard/tiny']['rejected_shards'] = [dict(
        spec=rejected['spec'], spec_key=spec_key(rejected['spec']), split='val',
        rejection='rejected.json', sha256=sha256(rejection_path.read_bytes()).hexdigest())]
    manifest_path.write_text(json.dumps(data))
    selection = select_frozen_instances(root, split_manifest=DEFAULT_MANIFEST, split='val', rungs=['tiny'])
    assert len(selection.allocated_rows) == len(entries)
    statuses = {r['spec_key']: r['feasibility_status'] for r in selection.allocated_rows}
    assert statuses[spec_key(rejected['spec'])] == 'generation-rejected'
    assert set(statuses.values()) == {'unknown', 'generation-rejected'}


def test_resume_rejects_failed_report_from_old_checker(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw: (
        PlannerResult(status='error', planner=planner.name, planner_version=planner.version),
        CheckReport(passed=False)))
    kwargs = dict(instances_dir=root, run_dir=tmp_path / 'run', rungs=['tiny'], tiny_run=True)
    harness.run_evaluation(Diagnostic(), **kwargs)
    with pytest.raises(ValueError, match='current checker versions'):
        harness.run_evaluation(Diagnostic(), **kwargs)


def test_harness_denominator_includes_every_test_allocation(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw: (
        PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None))
    monkeypatch.setattr(harness, 'lower_bound', lambda instance: SimpleNamespace(model_dump=lambda **kw: {}))
    summary = harness.run_evaluation(Diagnostic(), instances_dir=root, run_dir=tmp_path / 'run',
                                     split='test', rungs=['tiny'], tiny_run=True)
    entries = [e for e in load_inventory().manifest['entries']
               if e['split'] == 'test' and e['spec']['rung'] == 'tiny']
    assert summary['n_instances'] == 1
    assert summary['n_allocated'] == len(entries)
    assert summary['feasibility_status_counts']['unknown'] == len(entries)
    assert summary['pass_rate'] == 0
    assert summary['pass_rate_ci95'][1] < .1
    assert set(summary['by_family']) == {e.get('test_family') or 'in_distribution' for e in entries}
    allocations = json.loads((tmp_path / 'run/allocations.json').read_text())
    assert len(allocations) == len(entries)
    assert sum(r.get('allocation_only', False) for r in allocations) == len(entries) - 1


def test_missing_emitted_instances_still_produce_allocation_summary(tmp_path, empty_ship_instance):
    root = campaign(tmp_path, empty_ship_instance)
    manifest_path = root / 'manifest.json'
    data = json.loads(manifest_path.read_text())
    for group in data['groups'].values():
        group['shards'] = []
    manifest_path.write_text(json.dumps(data))
    summary = harness.run_evaluation(Diagnostic(), instances_dir=root, run_dir=tmp_path / 'run',
                                     split='test', rungs=['tiny'], tiny_run=True)
    assert summary['n_instances'] == 0
    assert summary['n_allocated'] > 0
    assert summary['pass_rate'] == 0


def test_mask_observer_reads_final_diagnostics_and_restores_factory(monkeypatch):
    import stowage.feasibility
    from eval.mask_observation import observe_mask_diagnostics
    engine = SimpleNamespace(diagnostics={'global_screen': 'on'})
    factory = lambda: engine
    monkeypatch.setattr(stowage.feasibility, 'make_feasibility_engine', factory)
    with pytest.raises(RuntimeError):
        with observe_mask_diagnostics() as observed:
            assert stowage.feasibility.make_feasibility_engine() is engine
            engine.diagnostics['global_screen'] = 'unavailable: test'
            raise RuntimeError('planner failed')
    assert stowage.feasibility.make_feasibility_engine is factory
    assert observed == [{'global_screen': 'unavailable: test'}]


def test_instance_infeasibility_requires_physical_scope_not_fixed_stow_or_engine_model():
    result = PlannerResult(status='infeasible', planner='solver.exact', planner_version='1',
                           metrics={'ballast_status': 'infeasible_proven'})
    assert harness._feasibility_status(result, witnessed=False) == 'unknown'
    result.metrics = {'optimality_scope': 'engine_model_weighted_lifts', 'optimality_status': 'INFEASIBLE'}
    assert harness._feasibility_status(result, witnessed=False) == 'unknown'
    result.metrics = {'infeasibility_proven': True, 'infeasibility_scope': 'physical_relaxation'}
    assert harness._feasibility_status(result, witnessed=False) == 'proven-infeasible'
    assert harness._feasibility_status(result, witnessed=True) == 'feasible-witnessed'


def test_batch_mask_unavailable_diagnostic_reaches_result_row(tmp_path, empty_ship_instance, monkeypatch):
    import stowage.feasibility
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(stowage.feasibility, 'make_feasibility_engine', lambda instance:
                        SimpleNamespace(diagnostics={'global_screen': 'unavailable: batch stale epoch'}))
    monkeypatch.setattr(harness, 'lower_bound', lambda instance: SimpleNamespace(model_dump=lambda **kw: {}))
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw: (planner.plan(instance, **kw), None))
    class BatchDiagnostic(Diagnostic):
        def plan_batch(self, instances, **kw):
            for instance in instances:
                stowage.feasibility.make_feasibility_engine(instance)
            return [PlannerResult(status='dead_end', planner=self.name, planner_version=self.version)
                    for _ in instances]
    harness.run_evaluation(BatchDiagnostic(), instances_dir=root, run_dir=tmp_path / 'run',
                          split='test', rungs=['tiny'], tiny_run=True, batch_size=2)
    record = json.loads(next((tmp_path / 'run/instances').glob('*.json')).read_text())
    assert record['mask_global_screen'] == 'unavailable: batch stale epoch'
    assert record['mask_unavailable'] is True


def test_canonical_producer_joins_archived_allocation_without_rehashing_it(tmp_path):
    from copy import deepcopy
    from tests.eval.test_harness_inventory import _campaign
    inventory = load_inventory()
    archived = next(e for e in inventory.manifest['entries'] if e['split'] == 'val'
                    and e['spec']['rung'] == 'tiny' and e['spec']['vessel'] == 'feeder_1000')
    producer = deepcopy(archived)
    producer['spec']['vessel'] = 'feeder_800'
    root = _campaign(tmp_path, inventory, [producer])
    selection = select_frozen_instances(root, split_manifest=DEFAULT_MANIFEST, split='val', rungs=['tiny'])
    assert len(selection.rows) == 1
    assert selection.rows[0]['spec_key'] == spec_key(archived['spec'])
    assert selection.rows[0]['source_spec_key'] == spec_key(producer['spec'])
    assert selection.rows[0]['spec_key'] != selection.rows[0]['source_spec_key']
