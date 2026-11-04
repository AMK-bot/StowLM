"""Check mix denominators, frozen-row lineage, and hash-bound fill metadata."""
import importlib.util
import json
from hashlib import sha256
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location('stowlm9_mix', Path(__file__).with_name('mix.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def entry(key):
    return dict(key=key, ship='ship', size='small', yard='random', input=f'experiments/mvp/corpus/train/small/{key}.json', input_sha256=key)


def row(key, passed, finished, manifest='M'):
    return dict(key=key, passed=passed, pid=12, started_at=finished-1, finished_at=finished,
                status='complete', manifest_id=manifest, input_sha256=key, outcome_epoch='E')


def test_retry_attempts_do_not_inflate_unique_failure_denominator(tmp_path):
    entries = {k: {**entry(k), 'fill_band': '25–<50%'} for k in ('a', 'b')}
    attempts = [row('a', False, 2), row('a', True, 4), row('b', False, 3), row('b', False, 5)]
    counts = m.grouped(entries, [attempts[1]], attempts, 'fill_band')[0]
    assert counts['attempted'] == 2
    assert counts['checked_pass'] == 1
    assert counts['failed_no_pass'] == 1
    assert counts['terminal_attempts'] == 4
    assert counts['attempted_pass_rate'] == .5


def test_physical_attempt_dedup_across_manifest_adoption(tmp_path):
    manifests = {'M': {}, 'P': {}}
    put(tmp_path/'rule'/'rows'/'a.json', row('a', True, 4))
    put(tmp_path/'history'/'P'/'rule-rows'/'a.json', row('a', True, 4, 'P'))
    put(tmp_path/'rule'/'attempts'/'a'/'failed.json', row('a', False, 2, 'P'))
    put(tmp_path/'rule'/'attempts'/'a'/'unrelated.json', row('a', False, 9, 'X'))
    assert len(m.terminal_attempts(tmp_path, manifests, {'a': entry('a')})) == 2


def test_old_published_generation_resolves_exact_history_during_handoff(tmp_path):
    oldrow = row('a', True, 4, 'P')
    path = tmp_path/'history'/'P'/'rule-rows'/'a.json'
    put(path, oldrow)
    put(tmp_path/'data-85'/'build_report.json', dict(manifest_id='P', outcome_epoch='E', rows=[dict(key='a', row_sha256=sha256(path.read_bytes()).hexdigest())]))
    (tmp_path/'data-85'/'train.jsonl').write_text(json.dumps(dict(meta=dict(gold=dict(source='/records/a/record.json', manifest_id='P', outcome_epoch='E'))))+'\n')
    rows, issues, _ = m.published_rows(tmp_path, {'M': dict(outcome_epoch='E'), 'P': dict(outcome_epoch='E')})
    assert rows == [oldrow] and issues == []
    oldrow['passed'] = False
    put(path, oldrow)
    rows, issues, _ = m.published_rows(tmp_path, {'P': dict(outcome_epoch='E')})
    assert rows == [] and len(issues) == 1


def test_render_requires_audit_hash_and_uses_half_open_fill_bands(tmp_path):
    root, repo = tmp_path/'run', tmp_path/'repo'
    entries = [entry('a'), entry('b')]
    put(root/'manifest.json', dict(manifest_id='M', outcome_epoch='E', entries=entries))
    for e, auditsha in zip(entries, ('a', 'WRONG')):
        p = repo/e['input']
        put(p.with_name(p.stem+'.audit.json'), dict(instance_sha256=auditsha, realised_teu=25, capacity_teu=100))
    result = m.render(root, repo=repo, now=10)
    assert result['audit_binding_issues'] == ['b']
    assert {r['group']: r['eligible'] for r in result['by_fill']} == {'25–<50%': 1, 'unknown': 1}
    assert m.fill_band(.5) == '50–<75%'
    assert m.fill_band(1) == '>=100%'
