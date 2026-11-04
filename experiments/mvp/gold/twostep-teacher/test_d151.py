"""Regression checks for the D151/D152 gates (rev-bestteacher.md P1s, logic-d151.md P0/P1), on synthetic files."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d151


def entry(case='train/tiny/a', mode='full', rung='tiny'):
    return dict(case=case, rung=rung, mode=mode, input='i.json', input_sha256='in', gold_plan_sha256='gp', stored_moves=9,
                solver='solver.exact', ship='s', family='f')


MANIFEST = dict(uuid='u', outcome_epoch='e', checker_epoch='c', budget_s=120., run_start=100., entries=[])


def row(gold=9., two=7., **extra):
    teacher = lambda m: dict(attempted=True, passed=m is not None, moves=m)
    return {**dict(outcome_epoch='e', checker_epoch='c', input_sha256='in', budget_s=120., gold=teacher(gold), two_step=teacher(two)), **extra}


def candidates(here, item, costs):
    for source, cost in costs.items():
        if cost is not None:
            d151.write(d151.candidate_path(here, item, source), dict(
                input_sha256=item['input_sha256'], weighted_moves=cost, plan={'p': source},
                report=dict(count=dict(weighted_nonproductive=cost))))


def setup(here, items, rows, launch_id='L', workers=1, receipts=True):
    manifest = dict(MANIFEST, entries=items)
    gold_root = here/'gold'
    for item in items:
        path = gold_root/item['case']/'plan.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('plan')
        item['gold_plan_sha256'] = d151.sha(path)
    for item, value in zip(items, rows):
        if value is not None:
            d151.write(here/'rows'/(d151.key(item['case'])+'.json'), value)
    if receipts:
        d151.write(here/'receipts'/launch_id/'shard-0.json', dict(ok=True, uuid='u', outcome_epoch='e', shard=0,
                                                                  assigned=[i['case'] for i in items]))
    return manifest, dict(launch_id=launch_id, workers=workers), gold_root


# rev-bestteacher P1 (a): only current-epoch rows; crashed or incomplete rows run again
@pytest.mark.parametrize('change,reason', [(dict(outcome_epoch='old'), 'outcome_epoch'), (dict(checker_epoch='old'), 'checker_epoch'),
                                           (dict(input_sha256='old'), 'input_sha256'), (dict(crashed='x'), 'crashed'),
                                           (dict(two_step=None), 'two_step incomplete')])
def test_a_row_from_another_epoch_or_incomplete_is_refused(tmp_path, change, reason):
    item = entry()
    candidates(tmp_path, item, dict(gold=9., baseline_twostep=7.))
    assert d151.row_problem(row(**change), item, MANIFEST, tmp_path).startswith(reason)
    assert d151.row_problem(row(), item, MANIFEST, tmp_path) is None


@pytest.mark.parametrize('flag', ['unavailable', 'finisher_timeout'])
def test_an_unavailable_checker_or_finisher_limit_is_not_a_verdict(tmp_path, flag):
    item = entry()
    candidates(tmp_path, item, dict(gold=9., baseline_twostep=7.))
    value = row()
    value['two_step'][flag] = True
    assert 'not a verdict' in d151.row_problem(value, item, MANIFEST, tmp_path)


def test_a_candidate_that_disagrees_with_its_row_is_refused(tmp_path):
    item = entry()
    candidates(tmp_path, item, dict(gold=8., baseline_twostep=7.))
    assert d151.row_problem(row(), item, MANIFEST, tmp_path) == 'gold candidate disagrees'


# rev-bestteacher P1 (b): change-and-revert proof
def proof_tree(here):
    (here/'src').mkdir()
    file = here/'src/a.py'
    file.write_text('x')
    d151.write(here/'ready.json', dict(utc='t', source_pins={'a': 1}))
    return file


def test_a_file_touched_during_the_run_fails_the_proof(tmp_path):
    file = proof_tree(tmp_path)
    good = dict(MANIFEST, run_start=file.stat().st_mtime+10)
    arguments = dict(root=tmp_path, trees=('src',), files=())
    assert d151.change_proof(good, tmp_path/'ready.json', {'a': 1}, 'e', **arguments)['passed'] is True
    bad = dict(MANIFEST, run_start=file.stat().st_mtime-10)
    assert d151.change_proof(bad, tmp_path/'ready.json', {'a': 1}, 'e', **arguments)['passed'] is False
    assert d151.change_proof(good, tmp_path/'ready.json', {'a': 2}, 'e', **arguments)['passed'] is False
    assert d151.change_proof(good, tmp_path/'ready.json', {'a': 1}, 'other', **arguments)['passed'] is False


# rev-bestteacher P1 (c): a fresh export drops what the current run didn't admit
def test_export_replaces_rather_than_merges(tmp_path):
    item = entry()
    candidates(tmp_path, item, dict(baseline_twostep=7.))
    target = tmp_path/'best-d151'
    stale = target/'stale-map/record.json'
    stale.parent.mkdir(parents=True)
    stale.write_text('{}')
    d151.export(MANIFEST, [dict(entry=item, source='two_step', costs=dict(gold=None, two_step=7.))], target, tmp_path)
    assert [p.parent.name for p in target.glob('*/record.json')] == [d151.key(item['case'])]
    assert not list(tmp_path.glob('best-d151.tmp-*'))
    assert list(tmp_path.glob('best-d151.superseded-*/stale-map/record.json'))


# rev-bestteacher P1 (d): completion binds to this launch and exact coverage
def test_a_receipt_from_another_launch_or_partial_coverage_is_refused(tmp_path):
    items = [entry(), entry('train/tiny/b')]
    candidates(tmp_path, items[0], dict(gold=9., baseline_twostep=7.))
    manifest, launch, gold = setup(tmp_path, items, [row(), None], launch_id='old')
    with pytest.raises(RuntimeError, match='no valid receipt'):
        d151.admit(manifest, dict(launch, launch_id='new'), tmp_path, gold)
    d151.write(tmp_path/'receipts/new/shard-0.json', dict(ok=True, uuid='u', outcome_epoch='e', shard=0, assigned=[items[0]['case']]))
    with pytest.raises(RuntimeError, match='cover 1 maps'):
        d151.admit(manifest, dict(launch, launch_id='new'), tmp_path, gold)


def test_missing_crashed_and_teacherless_maps_are_listed_not_kept(tmp_path):
    items = [entry('train/tiny/ok'), entry('train/tiny/missing'), entry('train/tiny/crash'), entry('train/tiny/none')]
    candidates(tmp_path, items[0], dict(gold=9., baseline_twostep=7.))
    manifest, launch, gold = setup(tmp_path, items, [row(), None, row(crashed='x'), row(gold=None, two=None)])
    admitted, excluded = d151.admit(manifest, launch, tmp_path, gold)
    assert [a['source'] for a in admitted] == ['two_step']
    assert {e['case'].split('/')[-1]: e['reason'] for e in excluded} == dict(missing='missing', crash='crashed', none='no verified teacher')


def test_a_changed_stored_gold_plan_is_refused(tmp_path):
    item = entry()
    candidates(tmp_path, item, dict(gold=9., baseline_twostep=7.))
    manifest, launch, gold = setup(tmp_path, [item], [row()])
    (gold/item['case']/'plan.json').write_text('other')
    assert d151.admit(manifest, launch, tmp_path, gold)[1][0]['reason'] == 'stored gold plan changed'


def test_winner_rule():
    assert d151.teacher(row(9., 7.), entry())[0] == 'two_step'
    assert d151.teacher(row(7., 7.), entry())[0] == 'gold'
    assert d151.teacher(row(None, 7.), entry())[0] == 'two_step'
    assert d151.teacher(row(9., 7.), entry(mode='gold_only'))[0] == 'gold'  # warm-start gold stays, whatever else exists
    assert d151.teacher(row(None, 7.), entry(mode='gold_only'))[0] is None


# logic-d151 P0-1: 8.2's val maps can't become train rows
def test_a_val_map_in_the_train_list_is_refused(tmp_path):
    gold = tmp_path/'gold'
    case = 'train/small/x'
    (gold/case).mkdir(parents=True)
    d151.write(gold/case/'result.json', dict(passed=True, rung='small', solver='solver.scalable', ship='s', family='f',
                                             input_path='i.json', objective=3))
    (gold/case/'plan.json').write_text('p')
    (tmp_path/'i.json').write_text('{}')
    rows = [dict(instance_id='x', meta=dict(gold=dict(source=f'experiments/mvp/gold/{case}/result.json')))]
    manifest = d151.make_manifest(rows, 'e', 'c', 1., set(), gold, tmp_path)
    assert manifest['entries'][0]['mode'] == 'full'
    with pytest.raises(RuntimeError, match='val maps'):
        d151.make_manifest(rows, 'e', 'c', 1., {case}, gold, tmp_path)


# rev-bestteacher P1 (e): the audit blocks on any disagreement
def test_audit_blocks_on_any_disagreement():
    source = (Path(__file__).resolve().parent/'audit_sample.py').read_text()
    verdict = source[source.index('def verdict'):source.index('def audit_one')]
    space = {}
    exec(verdict, space)
    good = dict(case='a', passed=True, moves=7., recorded=7., provenance_ok=True)
    assert space['verdict']([good], 1) == []
    assert space['verdict']([dict(good, passed=False, moves=None)], 1)
    assert space['verdict']([dict(good, moves=8.)], 1)
    assert space['verdict']([dict(good, provenance_ok=False)], 1)
    assert space['verdict']([good], 2)
    assert 'raise SystemExit(f\'audit disagreement' in source


# logic-d151 P0-1 and P1-1: counts, val, and kept-gold rows
def built(ids, rung='tiny'):
    return [dict(instance_id=i, input_ids=[1, 2], meta=dict(rung=rung)) for i in ids]


def gates(monkeypatch, train, v2_train, v2_val, admitted, excluded=()):
    import build_83
    monkeypatch.setattr(d151, 'TRAIN_RUNG', dict(tiny=1))
    monkeypatch.setattr(d151, 'VAL_RUNG', dict(tiny=1))
    return build_83.check_rows(train, v2_train, v2_val, admitted, list(excluded))


def test_build_gates(monkeypatch):
    admitted = [dict(instance_id='a', source='gold', rung='tiny')]
    val = built(['v'])
    assert gates(monkeypatch, built(['a']), built(['a']), val, admitted)['kept_gold_rows_identical'] == 1
    with pytest.raises(RuntimeError, match='kept-gold rows differ'):
        gates(monkeypatch, [dict(built(['a'])[0], input_ids=[9])], built(['a']), val, admitted)
    with pytest.raises(RuntimeError, match='train and val share'):
        gates(monkeypatch, built(['a']), built(['a']), built(['a']), admitted)
    with pytest.raises(RuntimeError, match='val rows'):
        gates(monkeypatch, built(['a']), built(['a']), built(['v', 'w']), admitted)
    with pytest.raises(RuntimeError, match='train rows'):
        gates(monkeypatch, built([]), built(['a']), val, admitted)
