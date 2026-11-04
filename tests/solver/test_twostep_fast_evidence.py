"""Evidence counts every requested row and quarantines relevant source drift."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]
HERE=ROOT/'experiments/mvp/solve47/twostep'


@pytest.mark.parametrize('previous',[None,'0','1'])
@pytest.mark.parametrize('raises',[False,True])
def test_finalization_environment_restores_exact_state(previous,raises,monkeypatch):
    m=module('benchmark')
    if previous is None:
        monkeypatch.delenv('STOWAGE_BALLAST_V4',raising=False)
    else:
        monkeypatch.setenv('STOWAGE_BALLAST_V4',previous)
    def finish():
        with m.finalization_environment(True):
            assert m.os.environ['STOWAGE_BALLAST_V4']=='1'
            if raises:
                raise ValueError('fake finalizer failure')
    if raises:
        with pytest.raises(ValueError,match='fake finalizer failure'):
            finish()
    else:
        finish()
    assert m.os.environ.get('STOWAGE_BALLAST_V4')==previous


def test_missing_producer_input_is_explicit_and_never_runs_planner(tmp_path,monkeypatch):
    from types import SimpleNamespace
    m=module('benchmark')
    monkeypatch.setattr(m,'forbidden_seeds',lambda:set())
    monkeypatch.setattr(m,'child',lambda *a,**k:pytest.fail('missing source input must not launch'))
    spec=dict(rung='small',seed=123,instance_path=str(tmp_path/'missing.json'),input_unavailable=True,
              producer_completed=True,producer_passed=False)
    m.one(SimpleNamespace(output=tmp_path/'out',planners=['twostep','scalable_legacy']),spec)
    for planner in ('twostep','scalable_legacy'):
        row=json.loads((tmp_path/f'out/rows/small-123-{planner}.json').read_text())
        assert row['status']=='input_unavailable' and row['process_started'] is False
        assert not row['passed'] and not row['qualified_pass']


def test_summary_keeps_ship_specific_demand_cells_separate(tmp_path):
    (tmp_path/'rows').mkdir()
    for index,cell in enumerate(('feeder500 TEU.25 mass.55','feeder800 TEU.25 mass.45')):
        row=dict(spec=dict(rung='small',seed=index,cohort_cell=cell),planner='twostep',
                 passed=False,qualified_pass=False,status='dead_end',stage_seconds=dict(planning=2))
        (tmp_path/f'rows/{index}.json').write_text(json.dumps(row))
    module('summarize').render(tmp_path)
    text=(tmp_path/'SUMMARY.md').read_text()
    assert '| small (feeder500 TEU.25 mass.55) | twostep | 0/1 |' in text
    assert '| small (feeder800 TEU.25 mass.45) | twostep | 0/1 |' in text
    assert '| small | twostep | 0/2 |' not in text


def test_move_comparison_uses_only_same_cell_mutual_passes(tmp_path):
    (tmp_path/'rows').mkdir()
    for seed,planner,passed,weighted in [(1,'twostep',True,10),(1,'scalable_legacy',True,20),
                                       (2,'twostep',True,900),(2,'scalable_legacy',False,0)]:
        moves=dict(rehandles=weighted,restows=0,future_restows=0,hatch_cover=0,future_hatch_cover=0,
                   weighted_nonproductive=weighted)
        row=dict(spec=dict(rung='small',seed=seed,cohort_cell='one cell'),planner=planner,
                 passed=passed,qualified_pass=passed,status='ok' if passed else 'dead_end',
                 stage_seconds=dict(planning=1),moves=moves if passed else None)
        (tmp_path/f'rows/{seed}-{planner}.json').write_text(json.dumps(row))
    module('summarize').render(tmp_path)
    text=(tmp_path/'SUMMARY.md').read_text()
    subset=text.split('Mutual-pass paired subset')[1]
    assert '| small (one cell) | legacy constructor (LNS disabled) | 1 | 10.00 / 20.00 |' in subset
    assert '455.00' not in subset and '900.00' not in subset


def test_snapshot_sources_ignore_live_source_edits_but_qualify_snapshot_params(tmp_path,monkeypatch):
    m=module('benchmark')
    monkeypatch.setattr(m,'ROOT',tmp_path)
    monkeypatch.setattr(m,'HERE',tmp_path/'evidence')
    snapshot=tmp_path/'evidence/runtime'
    for path,content in [('src/stowage/__init__.py','frozen=1'),('params/rules.toml','on=false')]:
        target=snapshot/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content)
    monkeypatch.setattr(m,'RUNTIME_ROOT',snapshot)
    before=m.sources()
    live=tmp_path/'src/stowage/__init__.py'
    live.parent.mkdir(parents=True)
    live.write_text('live=2')
    assert m.sources()==before
    changed='evidence/runtime/params/rules.toml'
    row=dict(passed=True,changed_sources=[changed],imported_source_files=[])
    assert not m.qualify(row)['qualified_pass']


def module(name):
    spec=importlib.util.spec_from_file_location(f'twostep_fast_{name}',HERE/f'{name}.py')
    result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_relevant_source_drift_quarantines_raw_checker_pass():
    m=module('benchmark')
    row=dict(passed=True,changed_sources=['src/stowage/solver/twostep.py'],
             imported_source_files=['src/stowage/solver/twostep.py'])
    assert not m.qualify(row)['qualified_pass']
    assert row['passed']


def test_unused_scalable_and_generator_edits_do_not_quarantine_twostep():
    m=module('benchmark')
    row=dict(passed=True,changed_sources=['src/stowage/solver/scalable.py','src/stowage/generate/instances.py'],
             imported_source_files=['src/stowage/solver/twostep.py'])
    assert m.qualify(row)['qualified_pass']
    row['changed_sources'].append('params/rules/catalogue.toml')
    assert not m.qualify(row)['qualified_pass']


def test_reserved_test_and_manifest_seeds_are_protected(tmp_path,monkeypatch):
    m=module('benchmark')
    monkeypatch.setattr(m,'ROOT',tmp_path)
    corpus=tmp_path/'experiments/mvp/corpus'
    (corpus/'showcase').mkdir(parents=True)
    (corpus/'test.requested.json').write_text(json.dumps(dict(specs=[dict(seed=8000001)])))
    (corpus/'showcase/showcase-manifest.json').write_text(json.dumps(dict(selections=[dict(seed=9000001)])))
    (corpus/'manifest.json').write_text(json.dumps(dict(rows=[dict(split='test',seed=8000002),dict(split='train',seed=1000001)])))
    assert m.forbidden_seeds()=={8000001,8000002,9000001}


def test_summary_keeps_failures_and_unstarted_requests_without_moves(tmp_path):
    (tmp_path/'rows').mkdir()
    specs=[dict(rung='small',seed=1),dict(rung='small',seed=2)]
    (tmp_path/'specs.json').write_text(json.dumps(specs))
    (tmp_path/'invocation.json').write_text(json.dumps(dict(planners=['twostep'])))
    row=dict(spec=specs[0],planner='twostep',passed=False,qualified_pass=False,status='rejected',
             stage_seconds=dict(planning=2),diagnostic_moves=dict(rehandles=999))
    (tmp_path/'rows/one.json').write_text(json.dumps(row))
    (tmp_path/'rows/one.ballast-outcome.json').write_text(json.dumps(dict(status='not_found',parts={})))
    module('summarize').render(tmp_path)
    text=(tmp_path/'SUMMARY.md').read_text()
    assert '| small | twostep | 0/2 |' in text
    assert '999' not in text
    assert 'not_started' in text
    unstarted=next(line for line in text.splitlines() if '| not_started |' in line)
    assert '| — | not_started |' in unstarted
    assert unstarted.endswith('| — |') and '0.000' not in text
    row['status']='planning'
    (tmp_path/'rows/one.json').write_text(json.dumps(row))
    module('summarize').render(tmp_path)
    assert '| pending | planning |' in (tmp_path/'SUMMARY.md').read_text()


def test_summary_excludes_drifted_pass_and_handles_empty_selection(tmp_path):
    (tmp_path/'rows').mkdir()
    row=dict(spec=dict(rung='medium',seed=1),planner='twostep',passed=True,qualified_pass=False,status='ok',
             stage_seconds=dict(planning=2),moves=dict(rehandles=777))
    (tmp_path/'rows/one.json').write_text(json.dumps(row))
    module('summarize').render(tmp_path)
    text=(tmp_path/'SUMMARY.md').read_text()
    assert '| medium | twostep | 0/1 |' in text
    assert '777' not in text
    (tmp_path/'rows/one.json').unlink()
    module('summarize').render(tmp_path)
    assert '# Two-step comparison evidence' in (tmp_path/'SUMMARY.md').read_text()


def test_relative_row_captures_actual_ballast_outcome_and_preserves_identity(tmp_path,monkeypatch):
    m=module('benchmark')
    monkeypatch.setattr(m,'ROOT',tmp_path)
    monkeypatch.chdir(tmp_path)
    payload=dict(status='not_found',proof='No proof of infeasibility',iterations=3,
                 parts=dict(strength=dict(metrics=dict(utilisation=1.1))))
    class Outcome:
        def model_dump(self,*,mode):
            assert mode=='json'
            return payload
    outcome=Outcome()
    instance,plan=object(),object()
    calls=[]
    def finish(actual_instance,actual_plan,**kwargs):
        calls.append((actual_instance,actual_plan,kwargs))
        return outcome
    returned,path,error=m.capture_ballast_finish(finish,instance,plan,row_path=Path('relative/row.json'),
                                         time_limit_s=90.,seed=7)
    assert returned is outcome
    assert error is None
    assert calls==[(instance,plan,dict(time_limit_s=90.,seed=7))]
    assert path=='relative/row.ballast-outcome.json'
    assert json.loads((tmp_path/path).read_text())==payload


def test_capture_serialization_failure_preserves_actual_outcome_once(tmp_path,monkeypatch):
    m=module('benchmark')
    monkeypatch.setattr(m,'ROOT',tmp_path)
    monkeypatch.chdir(tmp_path)
    class Outcome:
        def model_dump(self,*,mode):
            raise ValueError('fake serialization failure')
    outcome=Outcome()
    calls=[]
    def finish():
        calls.append(True)
        return outcome
    returned,path,error=m.capture_ballast_finish(finish,row_path=Path('relative/row.json'))
    assert returned is outcome
    assert calls==[True]
    assert path is None
    assert error=='ValueError: fake serialization failure'


def test_rejected_complete_plan_moves_have_separate_diagnostic_table(tmp_path):
    (tmp_path/'rows').mkdir()
    row=dict(spec=dict(rung='small',seed=7),planner='twostep',raw_status='ok',checker_passed=False,
             passed=False,qualified_pass=False,status='error',stage_seconds=dict(planning=1.),
             diagnostic_moves=dict(rehandles=9,restows=2,future_restows=1,hatch_cover=4,future_hatch_cover=6,
                                   weighted_nonproductive=22))
    (tmp_path/'rows/one.json').write_text(json.dumps(row))
    module('summarize').render(tmp_path)
    text=(tmp_path/'SUMMARY.md').read_text()
    quality,diagnostic=text.split('Raw-complete cargo plans rejected by the full checker',1)
    assert '| small | twostep | 0/1 | 1/1 | 1.00 | 1.00 | — | — / — | — / — |' in quality
    assert '| small | 7 | twostep | error | 9 | 2 / 1 | 4 / 6 | 22 |' in diagnostic
