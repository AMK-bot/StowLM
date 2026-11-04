"""Gold campaign safety checks, independent of costly physical optimization."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


@pytest.fixture
def campaign():
    path = Path(__file__).resolve().parents[2] / 'experiments/gold-plans/campaign/gold.py'
    spec = importlib.util.spec_from_file_location('test_gold_campaign_driver', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_per_rung_budgets_are_explicit(campaign):
    result = campaign.budget_map(['4', 'tiny=1', 'small=2'], 60., ['tiny', 'small'])
    assert result == dict(tiny=1., small=2., medium=4., full=4.)


@pytest.mark.parametrize('value', ['tiny=nan', 'tiny=0', 'tiny=-2', 'other=3'])
def test_bad_budgets_fail(campaign, value):
    with pytest.raises(ValueError):
        campaign.budget_map([value], 60., ['tiny'])


def test_tiny_g1_exemption_but_large_requires_gate(campaign):
    args = SimpleNamespace(diagnostic=False, rungs=['tiny'], g1_summary=None)
    assert campaign.require_g1(args, 'epoch') is None
    args.rungs = ['small']
    with pytest.raises(ValueError, match='requires --g1-summary'):
        campaign.require_g1(args, 'epoch')


def test_g1_is_same_epoch_and_never_diagnostic(campaign,tmp_path,monkeypatch):
    # This regression isolates epoch/diagnostic checks; migration has separate integration coverage.
    monkeypatch.setattr(campaign.rule_policy(),'validate_migration_approval',lambda *args:None)
    report = tmp_path / 'g1.json'
    args = SimpleNamespace(diagnostic=False, rungs=['small'], g1_summary=report)
    report.write_text(json.dumps(dict(epoch_id='epoch',g1_met=True,diagnostic=False,min_yield={'small':.5},solve_yield={'small':.8})))
    assert campaign.require_g1(args, 'epoch')['sha256'] == campaign.sha(report)
    with pytest.raises(ValueError, match='selected frozen epoch'):
        campaign.require_g1(args, 'different')
    report.write_text(json.dumps(dict(epoch_id='epoch', g1_met=True, nested={'diagnostic': True})))
    with pytest.raises(ValueError, match='diagnostic'):
        campaign.require_g1(args, 'epoch')


def test_candidate_choice_uses_checked_objective_and_exact_tiebreak(campaign, monkeypatch):
    values = {'scalable': dict(accepted=True, objective=1), 'exact': dict(accepted=True, objective=2)}
    protocol = SimpleNamespace(_require_clean=lambda _: None, _full_hull=lambda _: True,
        _safe_judge=lambda _, kind, config: values[kind])
    monkeypatch.setattr(campaign, 'tiny', lambda: protocol)
    _, _, source, candidate = campaign._clean_candidate(object(), {'rung': 'tiny'}, object())
    assert source == 'scalable' and candidate['objective'] == 1
    values['exact']['objective'] = 1
    assert campaign._clean_candidate(object(), {'rung': 'small'}, object())[2] == 'exact'
    assert campaign._clean_candidate(object(), {'rung': 'full'}, object())[1]['status'] == 'not_scheduled'


def test_all_failures_and_timeouts_stay_in_calibration_denominator(campaign):
    rows = [dict(rung='tiny', status='timeout', accepted=False, candidate_accepted=False, process_wall_s=2.,
                 reason='search expired', scalable={'status': 'timeout'}, exact={'status': 'timeout'}),
            dict(rung='tiny', status='feasible', accepted=True, candidate_accepted=True, process_wall_s=4.,
                 scalable={'status': 'ok'}, exact={'status': 'error'})]
    summary = campaign.summarize(rows, {'epoch_id': 'epoch', 'diagnostic': False})
    measured = summary['per_rung']['tiny']
    assert measured['attempts'] == 2 and measured['solve_rate'] == .5
    assert measured['mean_attempt_s'] == 3 and measured['total_core_s'] == 6
    assert measured['solver_attempts'] == dict(scalable=2, exact=2)
    assert measured['solver_timeouts'] == dict(scalable=1, exact=1)
    assert measured['outcomes'] == dict(timeout=1, feasible=1)


def test_validated_done_resumes_without_starting_worker(campaign, monkeypatch, tmp_path):
    row = {'rung': 'tiny', 'shard': {'spec_key': 'key'}}
    identity = {'epoch_id': 'epoch'}
    output = tmp_path
    shard = output / 'shards/key'
    shard.mkdir(parents=True)
    result = shard / 'result.json'
    campaign.atomic_json(result, dict(accepted=False, status='timeout', rung='tiny', attempt_id='test-attempt'))
    campaign.machinery().write_done(shard / 'done.json', dict(campaign=identity, input=row), result)
    monkeypatch.setattr(campaign, 'freeze_check', lambda args: None)
    monkeypatch.setattr(campaign.machinery(), 'runner_check', lambda *args: None)
    monkeypatch.setattr(campaign.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('resume started a worker'))
    assert campaign.run_shard(SimpleNamespace(), identity, row, output)['resumed'] is True
    result.write_text('{}')
    with pytest.raises(ValueError, match='done marker or result drift'):
        campaign.run_shard(SimpleNamespace(), identity, row, output)


def test_select_inputs_invokes_shared_epoch_validator(campaign, monkeypatch, tmp_path):
    import eval.splits
    import eval.splits_epoch
    spec = dict(rung='tiny')
    root = tmp_path / 'instances-eepoch'
    (root / 'random_yard/tiny').mkdir(parents=True)
    path = root / 'random_yard/tiny/input.json'
    path.write_text('{}')
    frozen = dict(epoch_id='epoch', diagnostic=True, source_sha256='source', drift=['moving source'])
    row = dict(split='train', split_spec=spec, spec_key='key', instance='input.json', sha256=campaign.sha(path))
    manifest = dict(epoch_id='epoch', freeze_manifest=frozen,
        groups={'random_yard/tiny': {'shards': [row]}})
    (root / 'manifest.json').write_text(json.dumps(manifest))
    calls = []
    monkeypatch.setattr(campaign, 'load_inventory_pinned', lambda _: (object(), 'validated-split'))
    monkeypatch.setattr(eval.splits_epoch, 'validate_epoch_manifest',
        lambda *args, **kwargs: calls.append(('manifest', kwargs)))
    monkeypatch.setattr(eval.splits_epoch,'validate_epoch_instance',lambda *args:(calls.append(('instance',args)) or SimpleNamespace(containers=[])))
    args = SimpleNamespace(instance_root=root, split_manifest=None, diagnostic=True, allow_drift=True,
        rungs=['tiny'], count=1, split=['train'], shard_index=0, shard_count=1)
    selected, _, _ = campaign.select_inputs(args, {**frozen, 'drift': ['different current drift']})
    assert selected[0]['sha256'] == campaign.sha(path)
    assert calls[0] == ('manifest', dict(allow_diagnostic=True, check_source=False))
    assert calls[1][0] == 'instance'


def test_calibration_preserves_full_identity_and_resumes_first_pending(campaign, tmp_path):
    rows = [{'rung': 'tiny', 'shard': {'spec_key': 'a'}}, {'rung': 'small', 'shard': {'spec_key': 'b'}}]
    identity = {'inputs': rows}
    args = SimpleNamespace(calibrate_first_shard=True, calibration_rung='small')
    selected, completed = campaign.calibration_selection(args, identity, rows, tmp_path)
    assert selected == [rows[1]] and completed == []
    result = tmp_path / 'shards/b/result.json'
    campaign.atomic_json(result, dict(rung='small', status='timeout', accepted=False, attempt_id='test-attempt'))
    campaign.machinery().write_done(result.parent / 'done.json', dict(campaign=identity, input=rows[1]), result)
    selected, completed = campaign.calibration_selection(args, identity, rows, tmp_path)
    assert selected == [] and completed[0]['resumed']
    args.calibrate_first_shard = False
    assert campaign.calibration_selection(args, identity, rows, tmp_path) == (rows, [])


@pytest.mark.parametrize('split', ['val', 'test'])
def test_generalized_existing_writer_accepts_selected_small_split_and_blocks_diagnostics(campaign, monkeypatch, tmp_path, split):
    """Synthetic bound checker oracle tests writer policy, never physical gold."""
    admission_path = Path(__file__).with_name('test_gold_admission.py')
    helper = campaign.module_at('_gold_campaign_synthetic_fixture', admission_path)
    driver = campaign.tiny()
    instance, result, _ = helper.case.__wrapped__(driver, tmp_path, monkeypatch)
    plan = driver._structured_plan(instance, result.plan)
    report = helper.bound_report(instance, plan)
    inventory = driver._inventory()
    member = next(row for row in inventory.manifest['entries'] if row['split'] == split and row['spec']['rung'] == 'small')
    import eval.splits
    key = eval.splits.spec_key(member['spec'])
    pin = driver.inventory_pin()
    candidate = dict(accepted=True, shippable=True, mask_verified=True, global_screen='on',
        plan=plan.model_dump(mode='json'), report=report.model_dump(mode='json'))
    payload = dict(format='tiny-gold@1.0', accepted=True, split=split, split_spec=member['spec'],
        spec_key=key, input_sha256='a'*64, instance_id=instance.id, group='simulated_yard/small',
        gold_source='scalable', scalable=candidate, plan=candidate['plan'], check_report=candidate['report'],
        shippable=True, mask_verified=True, c2='on/default', component_versions=driver.component_versions(),
        relevant_source_sha256='epoch', objective=0, gold_status='feasible', **pin)
    artifact = tmp_path / 'plans' / (key+'.json')
    state = tmp_path / 'state' / (key+'.json')
    def save():
        campaign.atomic_json(artifact, payload)
        campaign.atomic_json(state, dict(accepted=True, status='feasible', gold_file=str(artifact), gold_sha256=campaign.sha(artifact)))
    save()
    assert driver.write_manifest(tmp_path)['accepted'] == 0  # old default remains TRAIN tiny only
    manifest = driver.write_manifest(tmp_path, allowed_splits=(split,), allowed_rungs=('small',))
    assert manifest['accepted'] == 1 and manifest['shards'][0]['split'] == split
    payload['diagnostic'] = True
    save()
    manifest = driver.write_manifest(tmp_path, allowed_splits=(split,), allowed_rungs=('small',))
    assert manifest['accepted'] == 0
    assert 'diagnostic gold cannot be published' in manifest['excluded'][0]['reason']


def test_finalization_failure_reason_preserves_raw_solver_status(campaign, monkeypatch, tmp_path):
    helper = campaign.module_at('_gold_campaign_synthetic_fixture', Path(__file__).with_name('test_gold_admission.py'))
    driver = campaign.tiny()
    instance, result, config = helper.case.__wrapped__(driver, tmp_path, monkeypatch)
    monkeypatch.setattr(driver, '_finalize', lambda *args: result.model_copy(update={'status': 'error', 'message': 'synthetic ballast failure'}))
    outcome = driver._judge(instance, 'exact', config)
    assert outcome['raw_status'] == 'ok'
    assert outcome['status'] == 'error'
    assert outcome['failure_stage'] == 'finalize'
    assert 'synthetic ballast failure' in outcome['message']


def test_outer_timeout_records_only_owned_pid_and_completed_timeout(campaign, monkeypatch, tmp_path):
    row = dict(rung='tiny', sha256='input', group='simulated_yard/tiny',
        shard=dict(spec_key='key', split='train'))
    identity = dict(epoch_id='epoch', split_entries_sha256='release', instance_manifest_sha256='manifest', g1_summary=None)
    args = SimpleNamespace(timeout_s=1., scalable_budgets={'tiny': 1.}, exact_budgets={'tiny': 1.},
        ballast_s=1., check_s=1., split_manifest=tmp_path / 'split.json', diagnostic=True,
        seed=0, max_nodes=10, split=['train'])
    class OwnedProcess:
        pid = 12345
        terminated = False
        killed = False
        calls = 0
        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise campaign.subprocess.TimeoutExpired('owned fake worker', timeout)
            return -15
        def terminate(self):
            self.terminated = True
        def kill(self):
            self.killed = True
    process = OwnedProcess()
    monkeypatch.setattr(campaign, 'freeze_check', lambda *args: None)
    monkeypatch.setattr(campaign.machinery(), 'runner_check', lambda *args: None)
    monkeypatch.setattr(campaign.subprocess, 'Popen', lambda *args, **kwargs: process)
    result = campaign.run_shard(args, identity, row, tmp_path)
    assert process.terminated and not process.killed
    assert result['status'] == 'timeout' and not result['accepted']
    recorded = json.loads((tmp_path / 'shards/key/pid.json').read_text())
    assert recorded['pid'] == 12345 and recorded['owner_pid'] == campaign.os.getpid()
    assert (tmp_path / 'shards/key/done.json').exists()


def test_g1_approval_requires_a_filled_leader_yield_floor(campaign, tmp_path):
    report = tmp_path/'g1.json'
    report.write_text(json.dumps(dict(epoch_id='epoch', g1_met=True, solve_yield={'small': .8})))
    args = SimpleNamespace(diagnostic=False, rungs=['small'], g1_summary=report)
    with pytest.raises(ValueError, match='min_yield'):
        campaign.require_g1(args, 'epoch')


def test_tiny_g1_exemption_never_labels_production_gold(campaign):
    assert campaign.publication_lineage(None)['data_mode'] == 'pipeline_test'
    assert campaign.publication_lineage(None)['purpose'] == 'tiny_g1_exception_pipeline_test'
    assert campaign.publication_lineage(dict(path='g1', sha256='a'*64,
        approval={'epoch_id': 'epoch', 'g1_met': True}))['data_mode'] == 'production'


def test_gold_summary_keeps_failed_family_and_unknown_demographics(campaign):
    rows = [dict(rung='small', status='timeout', accepted=False, family={'vessel':'s','generator':'g','port':'p','noise':0},
        demographics={'box_count': 12, 'dg_count': 3, 'background_b23_ratio': None, 'arrival_gm_m': None}),
        dict(rung='small', status='feasible', accepted=True, family={'vessel':'s','generator':'g','port':'p','noise':1},
        demographics={'box_count': 4, 'dg_count': 0, 'background_b23_ratio': 1.1, 'arrival_gm_m': .5})]
    summary = campaign.summarize(rows, dict(epoch_id='e', diagnostic=False))
    assert len(summary['per_rung_family']) == 2
    assert sorted(value['solve_rate'] for value in summary['per_rung_family'].values()) == [0,1]
    comparison = summary['attempted_vs_published']['box_count']
    assert comparison['attempted']['mean'] == 8 and comparison['published']['mean'] == 4
    assert summary['attempted_vs_published']['background_b23_ratio']['attempted']['unknown'] == 1
