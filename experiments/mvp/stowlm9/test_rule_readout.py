"""Admission, retry-denominator and conservative throughput regression checks."""
import importlib.util
import json
from hashlib import sha256
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('rule_readout', Path(__file__).with_name('rule_readout.py'))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def setup(tmp_path, n=20):
    put(tmp_path/'manifest.json', dict(manifest_id='M', outcome_epoch='E', rule_deadline='1970-01-01T01:00:00+00:00'))
    put(tmp_path/'rule'/'owner.json', dict(launch_id='L', started_at=100))
    snapshots, examples, rows, entries = [], [], [], []
    for i in range(n):
        key = f'map{i:02d}'
        row = dict(key=key, manifest_id='M', outcome_epoch='E', status='complete', passed=True,
                   source='rule', moves=10, baseline_moves=20 if i%2 == 0 else None,
                   baseline_raw_moves=30 if i%2 == 0 else None, wall_s=100+i,
                   launch_id='L', started_at=100+i, finished_at=200+i)
        entry = dict(key=key, input_sha256=f'input-{i}', baseline_record_sha256=None)
        entries.append(entry)
        row['binding'] = dict(manifest_id='M', stream='rule', input_sha256=entry['input_sha256'], baseline_record_sha256=None)
        row['record'] = dict(outcome_epoch='E', input_sha256=entry['input_sha256'], report=dict(passed=True), mask=dict(passed=True))
        path = tmp_path/'rule'/'rows'/f'{key}.json'
        put(path, row)
        snapshots.append(dict(key=key, row_sha256=sha256(path.read_bytes()).hexdigest()))
        examples.append(dict(meta=dict(gold=dict(source=f'/generation/records/{key}/record.json', manifest_id='M', outcome_epoch='E'))))
        rows.append(row)
    manifest = r.read(tmp_path/'manifest.json')
    manifest['entries'] = entries
    put(tmp_path/'manifest.json', manifest)
    put(tmp_path/'data-85'/'build_report.json', dict(manifest_id='M', outcome_epoch='E', rows=snapshots))
    (tmp_path/'data-85'/'train.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in examples))
    return rows


def test_first20_uses_published_rows_and_retry_attempt_denominator(tmp_path):
    rows = setup(tmp_path)
    failed = {**rows[0], 'passed': False, 'finished_at': 190, 'started_at': 101, 'status': 'error'}
    put(tmp_path/'rule'/'attempts'/'failed.json', failed)
    # Duplicate archive of latest row is not a second attempt.
    put(tmp_path/'rule'/'attempts'/'duplicate.json', rows[0])
    report = r.render(tmp_path, now=500)
    first = r.read(tmp_path/'rule'/'FIRST20.json')
    assert first['attempts'] == 21 and first['pass_rate'] == 20/21
    assert first['paired_canonical'] == dict(maps=10, winner_mean_moves=10, best_d172_mean_moves=20)
    assert first['paired_raw']['best_d172_mean_moves'] == 30
    assert first['wall_seconds'] == dict(mean=109.5, median=109.5)
    assert report['forecast']['workers'] == 3
    assert (tmp_path/'rule'/'FIRST20.md').exists()


def test_modified_rows_affect_published_binding_but_not_live_cadence(tmp_path):
    setup(tmp_path)
    path = tmp_path/'rule'/'rows'/'map00.json'
    row = r.read(path)
    row['moves'] = 1
    put(path, row)
    report = r.render(tmp_path, now=500)
    assert report['admitted']['labels'] == 20
    assert report['published_snapshot']['labels'] == 19
    assert report['published_binding_issues']
    assert not (tmp_path/'rule'/'FIRST20.json').exists()


def test_under400_requires_evidence_and_survives_later_improvement(tmp_path):
    setup(tmp_path, n=5)
    assert not r.render(tmp_path, now=210)['forecast']['credible']
    assert not (tmp_path/'rule'/'UNDER400.json').exists()
    report = r.render(tmp_path, now=1000)
    assert report['forecast']['credible']
    alert = (tmp_path/'rule'/'UNDER400.json').read_bytes()
    assert report['forecast']['all_future_attempts_pass_bound'] < 400
    r.render(tmp_path, now=1010)
    assert (tmp_path/'rule'/'UNDER400.json').read_bytes() == alert


def test_old_launch_does_not_pollute_new_policy_forecast(tmp_path):
    setup(tmp_path, n=5)
    put(tmp_path/'rule'/'owner.json', dict(launch_id='NEW', started_at=500))
    report = r.render(tmp_path, now=1000)
    assert report['forecast']['terminal_attempts'] == 0
    assert not report['forecast']['credible']
    assert report['admitted']['labels'] == 5


def test_manifest_rebinding_and_provenance_are_not_additional_attempts(tmp_path):
    rows = setup(tmp_path, n=1)
    # A migration may rewrite producer bindings while retaining physical execution.
    rebound_copy = {**rows[0], 'migration_provenance': {'previous_manifest_id': 'OLD'}}
    put(tmp_path/'rule'/'attempts'/'same-execution.json', rebound_copy)
    old_copy = {**rows[0], 'manifest_id': 'OLD'}
    put(tmp_path/'rule'/'attempts'/'old-manifest.json', old_copy)
    assert r.render(tmp_path, now=500)['physical_attempts']['terminal_attempts'] == 1
    # Same map and reused process remain separate attempts when execution differs.
    retry = {**rows[0], 'started_at': 300, 'finished_at': 400, 'passed': False}
    put(tmp_path/'rule'/'attempts'/'retry.json', retry)
    assert r.render(tmp_path, now=500)['physical_attempts']['terminal_attempts'] == 2


def test_parent_manifest_failed_attempts_count_but_other_history_does_not(tmp_path):
    rows = setup(tmp_path, n=1)
    manifest = r.read(tmp_path/'manifest.json')
    manifest['parent_manifest_id'] = 'PARENT'
    put(tmp_path/'manifest.json', manifest)
    parent_attempt = {**rows[0], 'manifest_id': 'PARENT', 'passed': False,
                      'launch_id': 'OLD', 'started_at': 50, 'finished_at': 80}
    put(tmp_path/'rule'/'attempts'/'parent-failed.json', parent_attempt)
    put(tmp_path/'rule'/'attempts'/'unrelated.json', {**parent_attempt, 'manifest_id': 'UNRELATED',
                                                     'started_at': 20, 'finished_at': 30})
    # Adopted current row and its parent copy refer to one execution.
    put(tmp_path/'rule'/'attempts'/'parent-success.json', {**rows[0], 'manifest_id': 'PARENT'})
    report = r.render(tmp_path, now=500)
    assert report['physical_attempts']['terminal_attempts'] == 2
    assert report['physical_attempts']['pass_rate'] == .5
    assert report['admitted']['attempts'] == 1
    assert report['admitted']['pass_rate'] == 1
    assert report['forecast']['terminal_attempts'] == 1


def test_published_label_uses_exact_archived_attempt_when_retry_replaces_live_row(tmp_path):
    setup(tmp_path, n=20)
    live = tmp_path/'rule'/'rows'/'map00.json'
    archive = tmp_path/'rule'/'attempts'/'map00'/'original.json'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(live.read_bytes())
    retry = {**r.read(live), 'passed': False, 'status': 'error', 'started_at': 400, 'finished_at': 450}
    put(live, retry)
    report = r.render(tmp_path, now=500)
    assert report['admitted']['labels'] == 19
    assert report['published_snapshot']['labels'] == 20
    assert report['published_binding_issues'] == []
    assert report['physical_attempts']['terminal_attempts'] == 21
    assert report['admitted']['attempts'] == 20
    assert r.read(tmp_path/'rule'/'FIRST20.json')['attempts'] == 20
    assert report['admitted']['maps'][0]['moves'] == 10


def test_live_readout_is_independent_of_stale_published_26(tmp_path):
    setup(tmp_path, n=127)
    train = tmp_path/'data-85'/'train.jsonl'
    train.write_text('\n'.join(train.read_text().splitlines()[:26])+'\n')
    for i in range(107, 127):
        path = tmp_path/'rule'/'rows'/f'map{i:02d}.json'
        row = r.read(path)
        row.update(passed=False, status='error')
        put(path, row)
    report = r.render(tmp_path, now=500)
    assert report['admitted']['labels'] == 107
    assert report['admitted']['terminal_maps'] == 127
    assert report['admitted']['pass_rate'] == 107/127
    assert report['published_snapshot']['labels'] == 26
    assert report['admission_binding_issues'] == []
    # No future time: checked winners are already counted in the live base.
    assert r.render(tmp_path, now=3600)['forecast']['observed_labels_by_deadline'] == 107


def test_live_row_requires_current_bound_checker_and_mask_evidence(tmp_path):
    setup(tmp_path, n=4)
    for i, mutate in enumerate((lambda row: row['binding'].update(input_sha256='wrong'),
                               lambda row: row['record']['mask'].update(passed=False),
                               lambda row: row['record']['report'].update(passed=False))):
        path = tmp_path/'rule'/'rows'/f'map{i:02d}.json'
        row = r.read(path)
        mutate(row)
        put(path, row)
    report = r.render(tmp_path, now=500)
    assert report['admitted']['labels'] == 1
    assert report['admitted']['terminal_maps'] == 3
    assert len(report['admission_binding_issues']) == 3
