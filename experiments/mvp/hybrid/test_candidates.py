"""Stored-start admission contracts; no decoder, solver, or finisher needed."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidates
from stowage.model import MovePlan


def plan(name, slot='0020102'):
    return MovePlan(id=name, instance_id='map', call_index=0, moves=[dict(
        call_index=0, kind='load', container_id='QZXU0000014', src='B012-001-04-01', dst=slot)])


def stored(root, sample, draft, *, instance_id='map', filename=None):
    target = root / instance_id / (filename or f'sample-{sample:02d}.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(instance_id=instance_id, sample=sample,
                                     plan=draft.model_dump(mode='json') if draft else None)))
    return target


@pytest.fixture
def instance():
    return SimpleNamespace(id='map', call_index=0)


@pytest.fixture(autouse=True)
def no_finisher(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('candidate admission must not run a finisher')
    monkeypatch.setattr(candidates.compact, 'finish_and_check', forbidden)


def test_rank_compact_deduplicate_and_take_three_replaying(tmp_path, instance, monkeypatch):
    drafts = [plan(str(i), f'00201{i:02d}') for i in range(7)]
    cheaper = plan('compact', '0040102')
    # A cheaper compact duplicate is ranked once, and replay rejects sample 2.
    choices = {str(i): (drafts[i], 10 + i, 10 + i) for i in range(7)}
    choices['0'] = (cheaper, 30, 1)
    choices['1'] = (cheaper, 31, 1)
    choices['2'] = (drafts[2], 20, 2)
    choices['3'] = (drafts[3], 10, 100)  # expensive compact must keep original
    for i, draft in enumerate(drafts):
        stored(tmp_path, i, draft)

    def compact(_instance, draft):
        chosen, before, after = choices[draft.id]
        return chosen, dict(before=dict(total=before), compact_raw=dict(total=after))
    monkeypatch.setattr(candidates.compact, 'compact_plan', compact)
    replays = []

    def replay(_instance, draft):
        replays.append(draft.id)
        return 'mask exception' if draft.id == '2' else True
    monkeypatch.setattr(candidates.compact, 'mask_ok', replay)
    starts, metadata = candidates.load_starts(instance, [tmp_path])
    assert [draft.id for _, draft in starts] == ['compact', '3', '4']
    assert replays == ['compact', '2', '3', '4']
    assert metadata['n_complete'] == 7
    assert metadata['n_distinct'] == 6
    assert metadata['candidates'][1]['reason'] == 'duplicate move signature'
    assert metadata['candidates'][3]['compacted'] is False
    assert [row['cost'] for row in metadata['admitted']] == [1, 10, 14]
    assert all(label.startswith('adapter:') for label, _ in starts)
    json.dumps(metadata)  # output contains no models or filesystem objects


def test_equal_cost_uses_sample_index_across_sources(tmp_path, instance, monkeypatch):
    first, second = tmp_path / 'one', tmp_path / 'two'
    stored(first, 5, plan('five', '0020102'))
    stored(second, 1, plan('one', '0020104'))
    monkeypatch.setattr(candidates.compact, 'compact_plan', lambda i, p: (None, dict(before=dict(total=9))))
    monkeypatch.setattr(candidates.compact, 'mask_ok', lambda i, p: True)
    starts, _ = candidates.load_starts(instance, [first, second])
    assert [draft.id for _, draft in starts] == ['one', 'five']


@pytest.mark.parametrize('replay', [False, None, 1, 'ValueError: not replayable'])
def test_only_literal_true_replays_admit(tmp_path, instance, monkeypatch, replay):
    stored(tmp_path, 0, plan('draft'))
    monkeypatch.setattr(candidates.compact, 'compact_plan', lambda i, p: (None, dict(before=dict(total=9))))
    monkeypatch.setattr(candidates.compact, 'mask_ok', lambda i, p: replay)
    starts, metadata = candidates.load_starts(instance, [tmp_path])
    assert starts == []
    assert metadata['candidates'][0]['mask_replay'] == replay


def test_paths_accept_adapter_root_candidates_root_instance_folder_and_file(tmp_path, instance):
    root = tmp_path / 'adapter'
    path = stored(root / 'candidates', 0, plan('draft'), filename='sample0.json')
    for source in (root, root / 'candidates', path.parent, path):
        assert candidates.candidate_files(instance.id, source) == [path]
    assert candidates.candidate_files(instance.id, [root, path]) == [path]
    stored(root / 'candidates', 1, plan('other'), instance_id='other')
    assert candidates.candidate_files(instance.id, root) == [path]


def test_incomplete_invalid_and_wrong_instance_records_are_not_admitted(tmp_path, instance, monkeypatch):
    stored(tmp_path, 0, None)
    wrong = plan('wrong').model_copy(update=dict(instance_id='other'))
    stored(tmp_path, 1, wrong)
    invalid = stored(tmp_path, 2, None)
    invalid.write_text('invalid json')
    monkeypatch.setattr(candidates.compact, 'compact_plan', lambda *args: pytest.fail('invalid draft compacted'))
    starts, metadata = candidates.load_starts(instance, [tmp_path])
    assert starts == []
    assert metadata['n_complete'] == 0
    assert len(metadata['candidates']) == 3
    assert all(row.get('reason') for row in metadata['candidates'])


def test_no_extra_paths_does_nothing(instance, monkeypatch):
    monkeypatch.setattr(candidates.compact, 'compact_plan', lambda *args: pytest.fail('no draft compacted'))
    assert candidates.load_starts(instance, [])[0] == []
    assert candidates.default_paths('none') == ()
    assert len(candidates.default_paths('llm')) == 2
    assert len(candidates.default_paths('random')) == 2


def test_noncheaper_compact_is_not_used(tmp_path, instance, monkeypatch):
    original, compacted = plan('original'), plan('compacted', '0020104')
    stored(tmp_path, 0, original)
    monkeypatch.setattr(candidates.compact, 'compact_plan', lambda i, p: (
        compacted, dict(before=dict(total=9), compact_raw=dict(total=9))))
    monkeypatch.setattr(candidates.compact, 'mask_ok', lambda i, p: True)
    starts, metadata = candidates.load_starts(instance, [tmp_path])
    assert starts[0][1].id == 'original'
    assert metadata['admitted'][0]['compacted'] is False


def test_source_arm_label_is_stable_for_default_roots():
    for arm in ('llm', 'random'):
        assert all(candidates._arm(root / 'map/sample-00.json') == arm
                   for root in candidates.default_paths(arm))
