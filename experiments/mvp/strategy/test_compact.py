"""compact.py's ordering and keep rule, plus one real tiny plan through the move engine; no solver search, no GPU."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compact


def test_order_respects_precedences_and_key():
    assert compact.topological(3, {(2, 0)}, lambda i, chosen: i) == [1, 2, 0]
    with pytest.raises(ValueError):
        compact.topological(2, {(0, 1), (1, 0)}, lambda i, chosen: i)


@pytest.mark.parametrize('row,before_passed,before,kept,final', [
    (dict(passed=True, moves=20), True, 30, 'compact', 20),
    (dict(passed=True, moves=30), True, 30, 'original', 30),   # not cheaper
    (dict(passed=False), True, 30, 'original', 30),            # compacted plan failed
    (None, True, 30, 'original', 30),
    (dict(passed=True, moves=40), False, None, 'compact', 40),  # the original failed
    (dict(passed=False), False, None, 'original', None),
])
def test_keep_rule(row, before_passed, before, kept, final):
    got = compact.resolve(row, {}, before_passed, before)
    assert (got['kept'], got['final_moves']) == (kept, final)


@pytest.mark.parametrize('replay', [False, 'ValueError: rejected mask token', None, 1])
def test_a_failed_mask_replay_rejects_the_compact(replay):
    row = dict(passed=True, moves=20)
    assert compact.admit_mask(row, replay) is False
    assert compact.resolve(row, {}, True, 30)['kept'] == 'original'  # falls back to the passing original


def test_a_replayed_compact_is_admitted():
    row = dict(passed=True, moves=20)
    assert compact.admit_mask(row, True) is True
    assert compact.resolve(row, {}, True, 30)['kept'] == 'compact'


def stored(tmp_path, binding, kept='original'):
    target = tmp_path/'rows/k.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(dict(binding=binding, kept=kept)))
    return target


def test_a_stored_row_needs_the_exact_binding(tmp_path):
    binding = dict(key='k', plan_sha256='a', finisher_s=600.)
    target = stored(tmp_path, binding)
    assert compact.usable_row(target, binding)['kept'] == 'original'
    assert compact.usable_row(target, dict(binding, plan_sha256='b')) is None   # changed source plan
    assert compact.usable_row(target, dict(binding, finisher_s=120.)) is None   # changed budget
    assert compact.usable_row(target, dict(binding, key='other')) is None       # another job
    assert compact.usable_row(tmp_path/'missing.json', binding) is None
    target.write_text(json.dumps(dict(kept='original')))
    assert compact.usable_row(target, binding) is None                          # a row from before bindings


def test_a_kept_compact_needs_its_saved_plan(tmp_path):
    binding = dict(key='k')
    target = stored(tmp_path, binding, kept='compact')
    assert compact.usable_row(target, binding) is None
    (tmp_path/'plans').mkdir()
    (tmp_path/'plans/k.json').write_text('{}')
    assert compact.usable_row(target, binding)['kept'] == 'compact'


@pytest.mark.parametrize('rel', ['experiments/mvp/corpus/test/small/a.json', 'experiments/mvp/corpus/showcase/a.json', '/etc/a.json'])
def test_a_refused_input_is_refused_before_any_stored_row(rel):
    with pytest.raises(ValueError):
        compact.one_candidate(dict(target='unused', input=rel))


def test_binding_covers_the_job_and_the_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(compact, 'ROOT', tmp_path)
    (tmp_path/'i.json').write_text('{}')
    (tmp_path/'p.json').write_text('{}')
    job = dict(key='k', input='i.json', plan=str(tmp_path/'p.json'), before_passed=True, before_moves=9., verdict_sha256='v')
    first = compact.job_binding(job, dict(mask='m', outcome_epoch='e'))
    (tmp_path/'p.json').write_text('{"x": 1}')
    assert compact.job_binding(job, dict(mask='m', outcome_epoch='e'))['plan_sha256'] != first['plan_sha256']
    assert compact.job_binding(dict(job, verdict_sha256='w'), dict(mask='m', outcome_epoch='e')) != first


def test_replay_label_counts_kept_plans_only():
    import compact_report
    items = [dict(kept='compact', check=dict(mask_replay=True)), dict(kept='original', check=dict(mask_replay=None, passed=False)), dict(kept='original', check=None)]
    assert compact_report.kept_replays(items) == {'True': 1}
