"""Reuse proves source attribution and never filters on the full verdict."""
import json
from pathlib import Path
import pytest

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from experiments.mvp.pipeline.sandbox import clear_reaudit


def test_unpinned_full_is_not_reused(tmp_path):
    source = tmp_path/'source.json'
    source.write_text('{}')
    original = {'source_sha256': sha256_file(source), 'measurement_sessions': {}}
    assert not clear_reaudit.reusable({}, original, source)


def test_reuse_checks_checker_identity_and_does_not_filter_full_failures(tmp_path, monkeypatch):
    source = tmp_path/'source.json'
    source.write_text('{}')
    here = tmp_path/'sandbox'
    here.mkdir()
    moves = here/'moves.py'
    moves.write_text('unchanged move helper')
    checker = tmp_path/'src/stowage/checker/full.py'
    checker.parent.mkdir(parents=True)
    checker.write_text('original checker')
    monkeypatch.setattr(clear_reaudit, 'HERE', here)
    monkeypatch.setattr(clear_reaudit, 'ROOT', tmp_path)
    import stowage.versions
    monkeypatch.setattr(stowage.versions, 'component_versions', lambda: {'checker': 'test'})
    original = {'source_sha256': sha256_file(source), 'measurement_sessions': {
        'pinned': {'component_versions': {'checker': 'test'},
                   'source_sha256': {'src/stowage/checker/full.py': sha256_file(checker)}}}}
    row = {'measurement_session': 'pinned', 'code_sha256': {'moves.py': sha256_file(moves)},
           'full': {'legal': False}}
    assert clear_reaudit.reusable(row, original, source)
    checker.write_text('changed checker')
    assert not clear_reaudit.reusable(row, original, source)


def test_screen_only_change_allows_reuse_but_proposal_full_or_import_change_does_not(tmp_path, monkeypatch):
    source = tmp_path/'source.json'
    source.write_text('{}')
    here = tmp_path/'sandbox'
    archived = here/'fixtures/screen_v1/moves.py.txt'
    archived.parent.mkdir(parents=True)
    old = ('import json\n'
           'def prepare_move():\n    return "proposal"\n'
           'def _screen():\n    return "old screen"\n'
           'def try_move():\n    return "full verdict"\n')
    archived.write_text(old)
    moves = here/'moves.py'
    changed_screen = old.replace('old screen', 'new screen')
    moves.write_text(changed_screen)
    checker = tmp_path/'src/stowage/checker/full.py'
    checker.parent.mkdir(parents=True)
    checker.write_text('original checker')
    monkeypatch.setattr(clear_reaudit, 'HERE', here)
    monkeypatch.setattr(clear_reaudit, 'ROOT', tmp_path)
    import stowage.versions
    monkeypatch.setattr(stowage.versions, 'component_versions', lambda: {'checker': 'test'})
    original = {'source_sha256': sha256_file(source), 'measurement_sessions': {
        'pinned': {'component_versions': {'checker': 'test'},
                   'source_sha256': {'src/stowage/checker/full.py': sha256_file(checker)}}}}
    row = {'measurement_session': 'pinned', 'code_sha256': {'moves.py': sha256_file(archived)},
           'full': {'legal': False}}
    assert clear_reaudit.reusable(row, original, source)
    for previous, changed in [('"proposal"', '"changed proposal"'),
                              ('"full verdict"', '"changed full verdict"'),
                              ('import json', 'import math')]:
        moves.write_text(changed_screen.replace(previous, changed))
        assert not clear_reaudit.reusable(row, original, source)
    moves.write_text(changed_screen)
    archived.write_text(old+'\n# altered archive\n')
    assert not clear_reaudit.reusable(row, original, source)
    second_archive = here/'fixtures/screen_securing/moves.py.txt'
    second_archive.parent.mkdir()
    second_archive.write_text(old)
    assert clear_reaudit.reusable(row, original, source)


@pytest.mark.parametrize('filename', ['clear190-current.json', 'clear190-final.json'])
def test_current_fixture_is_explicit_about_reused_and_fresh_full_results(filename):
    path = Path(__file__).with_name('fixtures')/filename
    if not path.exists():
        pytest.skip('Current-screen audit is not recorded until the original 40 completes')
    data = json.loads(path.read_text())
    assert data['complete'] and data['discovery_complete']
    rows = data['moves']
    assert len(rows) >= 40 and all(r['fast']['legal'] for r in rows)
    assert len({(r['box_id'], r['target_slot']) for r in rows}) == len(rows)
    assert len({r['requested_state_sha256'] for r in rows}) >= 40
    assert len({r['box_id'] for r in rows}) >= 20
    assert {r['kind'] for r in rows} == {'swap', 'empty'}
    assert all(sum(r['kind'] == kind for r in rows) >= 10 for kind in ('swap', 'empty'))
    assert all(r['requested_state_sha256'] == r['reached_state_sha256'] for r in rows)
    versions = {sha256_json(data['measurement_sessions'][r['measurement_session']]['component_versions'])
                for r in rows}
    assert len(versions) == 1
    for row in rows:
        assert row['fast_source_sha256'] == data['fast_source_sha256']
        assert len(row['candidate_plan_sha256']) == len(row['instance_sha256']) == 64
        assert row['full']['screen'] == 'full'
        assert row['full_origin'] == ('fresh_full_check' if row['full_measured_now'] else 'recorded_full_reuse')
        if not row['full_measured_now']:
            assert row['measurement_session'] in data['measurement_sessions']
            assert row['full_s'] == row['original_full_s']
    assert data['summary']['false_legal'] == sum(not r['full']['legal'] for r in rows)
