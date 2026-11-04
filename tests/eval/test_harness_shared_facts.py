"""All method rows use one instance ledger, including repaired witnesses."""
import json

from eval import harness
from stowage.frozen import current_provenance
from stowage.model import MovePlan, PlannerResult
from tests.llm.test_rl_reward import complete_report


def make_run(root, name, instance, *, passed, repaired=False):
    folder = root / name
    folder.mkdir(parents=True)
    plan = MovePlan(id=name, instance_id=instance.id, call_index=instance.call_index)
    result = PlannerResult(status='ok', plan=plan, planner=name, planner_version='1', repaired=repaired)
    report = complete_report(instance, plan, moves=4 if passed else 100, passed=passed)
    record = dict(instance_id=instance.id, spec_key='shared-spec', input_sha256='input', split='test',
                  rung='tiny', test_family='heldout', instance={'voyage': None},
                  result=result.model_dump(mode='json'), report=report.model_dump(mode='json'),
                  report_binding_valid=True, shippable=passed, feasibility_status='unknown',
                  fallback_needed=repaired, timing={'total_s': 0, 'plan_s': 0})
    config = dict(planner=name, split='test', rungs=['tiny'], tiny_run=True, versions=current_provenance().versions)
    harness._write(folder/'config.json',config)
    harness._write(folder/'summary.json',dict(method=name,tiny_run=True,split='test'))
    harness._write(folder/'allocations.json',[record])
    harness._write(folder/'instances/shared-spec.json',record)
    return folder


def test_consolidation_joins_union_of_witnesses_into_all_method_rows(tmp_path, empty_ship_instance):
    failed = make_run(tmp_path, 'model', empty_ship_instance, passed=False)
    solved = make_run(tmp_path, 'solver', empty_ship_instance, passed=True)
    facts = harness.consolidate_evaluation_runs([failed, solved])
    assert facts['shared-spec']['feasibility_status'] == 'feasible-witnessed'
    for folder in (failed, solved):
        row = json.loads((folder/'allocations.json').read_text())[0]
        assert row['feasibility_status'] == 'feasible-witnessed'
        assert row['instance_facts'] == facts['shared-spec']
        assert json.loads((folder/'instances/shared-spec.json').read_text())['instance_facts'] == facts['shared-spec']
    summary = json.loads((failed/'summary.json').read_text())
    assert summary['n_known_feasible_failed'] == 1
    assert (tmp_path/'instance_facts.json').exists()


def test_consolidation_rejects_different_generation_epochs(tmp_path, empty_ship_instance):
    import pytest
    first = make_run(tmp_path, 'first', empty_ship_instance, passed=False)
    second = make_run(tmp_path, 'second', empty_ship_instance, passed=True)
    for folder, epoch in ((first, 'epoch-one'), (second, 'epoch-two')):
        path = folder/'config.json'
        config = json.loads(path.read_text())
        config['epoch_id'] = epoch
        harness._write(path, config)
    with pytest.raises(ValueError, match='identity'):
        harness.consolidate_evaluation_runs([first, second])
