"""Synthetic regression checks for D172 admission, coverage and publication gates."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d151
import d172_finish as finish


def fixture(tmp_path, retained=False, case='train/small/ship/family/a'):
    raw = tmp_path/'experiments/mvp/corpus'/(case+'.json')
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text('{"id":"a"}')
    gold = tmp_path/'experiments/mvp/gold'/case/'plan.json'
    gold.parent.mkdir(parents=True)
    gold.write_text('{}')
    entry = dict(case=case, instance_id='a', rung='small', ship='ship', family='family', solver='gold', stored_moves=9,
                 input=str(raw.relative_to(tmp_path)), input_sha256=d151.sha(raw), gold_plan_sha256=d151.sha(gold),
                 mode='winner_only' if retained else 'full')
    row = dict(entry, outcome_epoch='epoch', checker_epoch='checker', budget_s=120.,
               gold=dict(attempted=True, passed=True, moves=9, mask_passed=True),
               two_step=dict(attempted=True, passed=True, moves=7, mask_passed=True))
    run = tmp_path/'run'
    if retained:
        record = tmp_path/'old/record.json'
        d151.write(record, dict(provenance=dict(case=case, source='two_step', costs=dict(gold=9, two_step=7))))
        entry.update(retained_record=str(record), retained_source='two_step', retained_record_sha256=d151.sha(record))
        row.update(entry, retained=dict(attempted=True, passed=True, moves=7, mask_passed=True))
    for source, cost in [('gold', 9), ('baseline_twostep', 7), ('retained', 7)]:
        d151.write(d151.candidate_path(run, entry, source), dict(input_sha256=entry['input_sha256'], weighted_moves=cost,
                   plan=dict(source=source), report=dict(passed=True, count=dict(weighted_nonproductive=cost))))
    manifest = dict(uuid='uuid', outcome_epoch='epoch', checker_epoch='checker', budget_s=120., entries=[entry],
                    inventory_entries=[entry], excluded_val_cases=[], val_instance_ids=[])
    launch = dict(launch_id='launch', workers=1)
    receipt = dict(ok=True, uuid='uuid', launch_id='launch', shard=0, outcome_epoch='epoch', checker_epoch='checker',
                   assigned=[case], crashed=[])
    d151.write(run/'receipts/launch/shard-0.json', receipt)
    d151.write(run/'rows'/(d151.key(case)+'.json'), row)
    return entry, row, manifest, launch, run, receipt


@pytest.mark.parametrize('retained', [False, True])
def test_valid_rows_admit_and_retained_preserves_original_source(tmp_path, retained):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path, retained)
    admitted = finish.admit(manifest, launch, run, tmp_path)
    assert admitted[0]['source'] == 'two_step'
    assert admitted[0]['candidate_source'] == ('retained' if retained else 'baseline_twostep')


@pytest.mark.parametrize('field,value', [('launch_id', 'old'), ('uuid', 'old'), ('outcome_epoch', 'old'),
                                        ('checker_epoch', 'old'), ('assigned', []), ('crashed', ['a'])])
def test_receipts_bind_current_launch_epoch_and_exact_assignment(tmp_path, field, value):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    d151.write(run/'receipts/launch/shard-0.json', dict(receipt, **{field: value}))
    with pytest.raises(RuntimeError):
        finish.admit(manifest, launch, run, tmp_path)


@pytest.mark.parametrize('change', [dict(outcome_epoch='old'), dict(checker_epoch='old'), dict(gold_plan_sha256='old'),
                                   dict(case='different'), dict(crashed=True), dict(input_sha256='old'),
                                   dict(instance_id='other'), dict(input='other'), dict(mode='winner_only')])
def test_current_row_identity_is_required(tmp_path, change):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    assert finish.row_problem(dict(row, **change), entry, manifest, run)


@pytest.mark.parametrize('flag', ['finisher_timeout', 'unavailable'])
def test_operational_failure_is_not_a_teacher_verdict(tmp_path, flag):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    row['two_step'][flag] = True
    assert finish.row_problem(row, entry, manifest, run)


@pytest.mark.parametrize('cost', [float('nan'), float('inf'), -1, True, None])
def test_invalid_candidate_cost_blocks_admission(tmp_path, cost):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    row['two_step']['moves'] = cost
    assert finish.row_problem(row, entry, manifest, run) == 'two_step invalid cost'


def test_input_instance_identity_is_checked(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    raw = tmp_path/entry['input']
    raw.write_text('{"id":"different"}')
    entry['input_sha256'] = row['input_sha256'] = d151.sha(raw)
    for source in ('gold', 'baseline_twostep'):
        path = d151.candidate_path(run, entry, source)
        candidate = d151.read(path)
        candidate['input_sha256'] = entry['input_sha256']
        d151.write(path, candidate)
    d151.write(run/'rows'/(d151.key(entry['case'])+'.json'), row)
    with pytest.raises(RuntimeError, match='identity differs'):
        finish.admit(manifest, launch, run, tmp_path)


def test_mask_and_report_cost_must_agree(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    row['two_step']['mask_passed'] = False
    assert finish.row_problem(row, entry, manifest, run)
    row['two_step']['mask_passed'] = True
    path = d151.candidate_path(run, entry, 'baseline_twostep')
    candidate = d151.read(path)
    candidate['report']['count']['weighted_nonproductive'] = 8
    d151.write(path, candidate)
    assert finish.row_problem(row, entry, manifest, run)


def test_missing_map_blocks_export_instead_of_allowing_three_percent(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    (run/'rows'/(d151.key(entry['case'])+'.json')).unlink()
    with pytest.raises(RuntimeError, match='zero required'):
        finish.admit(manifest, launch, run, tmp_path)
    assert d151.read(run/'excluded.json')[0]['reason'] == 'missing row'


def test_retained_record_change_blocks_admission(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path, True)
    Path(entry['retained_record']).write_text('{}')
    assert finish.row_problem(row, entry, manifest, run) == 'retained record changed'


def test_tie_keeps_gold_and_existing_directory_is_untouched(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    row['two_step']['moves'] = 9
    path = d151.candidate_path(run, entry, 'baseline_twostep')
    candidate = d151.read(path)
    candidate.update(weighted_moves=9, report=dict(passed=True, count=dict(weighted_nonproductive=9)))
    d151.write(path, candidate)
    d151.write(run/'rows'/(d151.key(entry['case'])+'.json'), row)
    admitted = finish.admit(manifest, launch, run, tmp_path)
    assert admitted[0]['source'] == 'gold'
    target = tmp_path/'best-d172'
    temporary = finish.stage(manifest, launch, admitted, run, target)
    assert temporary.is_dir() and not target.exists()
    target.mkdir()
    (target/'sentinel').write_text('keep')
    with pytest.raises(RuntimeError, match='refusing to overwrite'):
        finish.stage(manifest, launch, admitted, run, target)
    assert (target/'sentinel').read_text() == 'keep'


def test_exact_union_and_val_exclusion(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path, True)
    old = Path(entry['retained_record']).parent.parent
    assert finish.check_coverage(manifest, old)['expected_union'] == 1
    with pytest.raises(RuntimeError, match='rung metadata'):
        finish.check_coverage(dict(manifest, entries=[dict(entry, rung='tiny')]), old)
    bad = dict(manifest, inventory_entries=[dict(entry, case='train/small/other')])
    with pytest.raises(RuntimeError, match='coverage'):
        finish.check_coverage(bad, old)
    for change in [dict(excluded_val_cases=[entry['case']]), dict(val_instance_ids=['a'])]:
        with pytest.raises(RuntimeError, match='val overlap'):
            finish.check_coverage(dict(manifest, **change), old)


@pytest.mark.parametrize('change', [dict(passed=False), dict(moves=8), dict(provenance_ok=False),
                                   dict(mask_passed=False), dict(finisher_timeout=True)])
def test_audit_blocks_any_disagreement(change):
    good = dict(case='a', passed=True, recorded=7, moves=7, provenance_ok=True, mask_passed=True)
    assert finish.audit_verdict([good], 1) == []
    assert finish.audit_verdict([dict(good, **change)], 1)
    assert finish.audit_verdict([good], 30)
    assert finish.audit_verdict([good, good], 2)


def test_summary_counts_new_comparisons_separately(tmp_path):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path)
    admitted = finish.admit(manifest, launch, run, tmp_path)
    text = finish.summary(manifest, launch, admitted, dict(eligible_small=1, d151_winners=0), dict(agree=30, seed=172))
    assert 'New two-teacher comparisons: 1; two-step wins 1.' in text
    assert '| small | ship | 1 | 0 | 1 | 1 | 0 |' in text


def test_reused_evidence_binds_original_row_and_exact_plan(tmp_path, monkeypatch):
    entry, row, manifest, launch, run, receipt = fixture(tmp_path, True)
    monkeypatch.setattr(finish, 'HERE', tmp_path)
    candidate_path = d151.candidate_path(run, entry, 'retained')
    candidate = d151.read(candidate_path)
    record = d151.read(entry['retained_record'])
    record.update(plan=candidate['plan'], report=candidate['report'])
    d151.write(entry['retained_record'], record)
    entry['retained_record_sha256'] = d151.sha(entry['retained_record'])
    old_row = tmp_path/'original-row.json'
    d151.write(old_row, dict(original=True))
    d151.write(tmp_path/'admitted.json', dict(original=True))
    row.update(entry, check_path='reused_same_epoch', reused_d151_row=str(old_row),
               reused_d151_row_sha256=d151.sha(old_row),
               reused_d151_admission_sha256=d151.sha(tmp_path/'admitted.json'))
    assert finish.row_problem(row, entry, manifest, run) is None
    changed = dict(candidate, plan={'tampered':True})
    d151.write(candidate_path, changed)
    assert 'differs from original' in finish.row_problem(row, entry, manifest, run)
    d151.write(candidate_path, candidate)
    old_row.write_text('{}')
    assert 'evidence changed' in finish.row_problem(row, entry, manifest, run)


def authorized_fixture(tmp_path):
    items = [fixture(tmp_path, case=case) for case in sorted(finish.AUTHORIZED_TIMEOUT_CASES)]
    manifest, launch, run = items[0][2:5]
    manifest['entries'] = [item[0] for item in items]
    manifest['inventory_entries'] = list(manifest['entries'])
    manifest['authorized_exclusions'] = []
    for entry, row, *_ in items:
        row['two_step']['finisher_timeout'] = True
        row['two_step']['passed'] = False
        row_path = run/'rows'/(d151.key(entry['case'])+'.json')
        d151.write(row_path, row)
        manifest['authorized_exclusions'].append(dict(case=entry['case'], authority='leader',
            reason='two_step finisher timeout after 3 launches', row_sha256=d151.sha(row_path)))
    receipt = items[0][5]
    receipt['assigned'] = [entry['case'] for entry in manifest['entries']]
    d151.write(run/'receipts/launch/shard-0.json', receipt)
    return manifest, launch, run


def test_only_the_two_explicit_timeout_exclusions_are_admitted(tmp_path):
    manifest, launch, run = authorized_fixture(tmp_path)
    assert finish.admit(manifest, launch, run, tmp_path) == []
    excluded = d151.read(run/'excluded.json')
    assert {item['case'] for item in excluded} == finish.AUTHORIZED_TIMEOUT_CASES
    assert all(item['authorized'] and item['finisher_timeout'] for item in excluded)


@pytest.mark.parametrize('change', ['third_case', 'missing_authorization', 'missing_row', 'changed_row', 'epoch', 'input'])
def test_exclusion_authority_does_not_weaken_other_gates(tmp_path, change):
    manifest, launch, run = authorized_fixture(tmp_path)
    entry = manifest['entries'][0]
    row_path = run/'rows'/(d151.key(entry['case'])+'.json')
    if change == 'third_case':
        manifest['authorized_exclusions'].append(dict(case='train/small/other'))
    elif change == 'missing_authorization':
        manifest['authorized_exclusions'].pop()
    elif change == 'missing_row':
        row_path.unlink()
    elif change == 'changed_row':
        row = d151.read(row_path)
        row['two_step']['unavailable'] = True
        d151.write(row_path, row)
    elif change == 'epoch':
        row = d151.read(row_path)
        row['outcome_epoch'] = 'different'
        d151.write(row_path, row)
        manifest['authorized_exclusions'][0]['row_sha256'] = d151.sha(row_path)
    elif change == 'input':
        (tmp_path/entry['input']).write_text('{"id":"different"}')
    with pytest.raises(RuntimeError):
        finish.admit(manifest, launch, run, tmp_path)


def test_an_additional_failed_map_still_blocks_export(tmp_path):
    manifest, launch, run = authorized_fixture(tmp_path)
    extra, row, *_ = fixture(tmp_path, case='train/small/third')
    manifest['entries'].append(extra)
    row['two_step']['finisher_timeout'] = True
    d151.write(run/'rows'/(d151.key(extra['case'])+'.json'), row)
    receipt = d151.read(run/'receipts/launch/shard-0.json')
    receipt['assigned'] = [entry['case'] for entry in manifest['entries']]
    d151.write(run/'receipts/launch/shard-0.json', receipt)
    with pytest.raises(RuntimeError, match='zero required'):
        finish.admit(manifest, launch, run, tmp_path)
