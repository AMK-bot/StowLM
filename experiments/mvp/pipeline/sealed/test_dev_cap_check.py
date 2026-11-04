"""Real-development survey controls, tested with synthetic source snapshots."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.sealed import dev_cap_check as probe
from experiments.mvp.pipeline.sealed.common import HarnessError, atomic_json, sha256


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    dev=tmp_path/'dev'
    dev.mkdir()
    sources=tmp_path/'baselines'
    sources.mkdir()
    maps=[dev/f'dev-{i:02}.json' for i in range(32)]
    for path in maps:
        path.write_text('{}')
    requests=tmp_path/'requests.json'
    atomic_json(requests,dict(specs=[dict(split='dev',path=str(p)) for p in maps]+
        [dict(split='sealed',path='never-opened-sealed.json')]))
    monkeypatch.setattr(probe,'_load_development',lambda p:(SimpleNamespace(id=Path(p).stem),sha256(p))
        if p.is_file() else (_ for _ in ()).throw(FileNotFoundError(p)))
    monkeypatch.setattr(probe,'public_facts',lambda instance:dict(id=instance.id))
    monkeypatch.setattr(probe,'_current_profile',lambda:dict(name='standard',pending_measurement=True))
    calls=[]
    def analyse(instance,raw,rank):
        calls.append((instance.id,rank))
        return dict(plan_sha256=raw['tag'],by_bay=[],binding_bays=raw.get('breaks',0),physical_bays=6,
            assigned_rule=dict(broke_rule=bool(raw.get('breaks'))) if rank is not None else None)
    monkeypatch.setattr(probe,'_analyse',analyse)
    def run():
        return probe.check(dev_dir=dev,requests_path=requests,source_dirs=[sources])
    def baseline(name='base',index=0,**changes):
        row=dict(path=str(maps[index]),rules=[],plan=dict(tag=name),passed=True,
                 profile=dict(name='standard',pending_measurement=True))
        row.update(changes)
        atomic_json(sources/f'{name}.json',row)
    return maps,sources,run,baseline,calls


def test_partial_generation_never_silently_reranks_available_maps(cohort):
    maps,sources,run,baseline,calls=cohort
    maps[31].unlink()
    baseline()
    result=run()
    assert result['expected_maps']==32 and result['available_maps']==31
    assert not result['allocation_rank_final']
    assert calls==[('dev-00',None)]
    assert result['assigned_rule_diagnostic_breaks'] is None
    assert result['maps_with_baselines']==1
    assert len(result['maps'])==32 and result['maps'][31]['status']=='missing_map'


def test_complete_public_cohort_not_only_plan_subset_determines_rank(cohort):
    maps,sources,run,baseline,calls=cohort
    baseline(index=17)
    result=run()
    assert result['allocation_rank_final']
    assert calls==[('dev-17',17)]
    assert result['maps_with_baselines']==1 and len(result['maps'])==32


def test_no_cap_or_lift_based_candidate_selection_and_no_false_fresh_audit(cohort):
    maps,sources,run,baseline,calls=cohort
    baseline('a',plan=dict(tag='a',breaks=6),passed=True,audit_verified=True,audit_fresh_process=True)
    baseline('b',plan=dict(tag='b',breaks=0),passed=False)
    result=run()
    values=result['maps'][0]['baseline_candidates']
    assert len(values)==2 and [v['binding_bays'] for v in values]==[6,0]
    assert result['maps_with_baselines']==1 and result['baseline_candidates']==2
    assert not result['release_eligible'] and result['fresh_full_audits']==0
    assert all(not v['fresh_full_audit'] for v in values)


def test_constrained_plans_and_ambiguous_previews_never_become_baselines(cohort):
    maps,sources,run,baseline,calls=cohort
    baseline('constrained',rules=[dict(type='close_bay',bay=1)])
    baseline('preview',rules=None)
    result=run()
    assert not calls and result['baseline_candidates']==0


def test_mismatching_map_hash_is_explicit_and_not_checked(cohort):
    maps,sources,run,baseline,calls=cohort
    baseline(file_sha256='wrong')
    result=run()
    assert not calls and result['maps_with_baselines']==0
    assert 'byte hash differs' in result['source_errors'][0]['error']


def test_noncohort_old_maps_are_not_opened_to_find_baselines(cohort):
    maps,sources,run,baseline,calls=cohort
    baseline(path=str(maps[0].parent/'old-map-not-readable.json'))
    result=run()
    assert not calls and result['baseline_candidates']==0


def test_unregistered_dev_map_is_not_silently_added(cohort):
    maps,sources,run,baseline,calls=cohort
    (maps[0].parent/'extra.json').write_text('{}')
    with pytest.raises(HarnessError,match='Unregistered'):
        run()


def test_analyse_rejects_plan_for_different_map_before_any_recheck(monkeypatch):
    import stowage.model
    monkeypatch.setattr(stowage.model.MovePlan,'model_validate',lambda raw:
        SimpleNamespace(instance_id='wrong',call_index=0))
    with pytest.raises(HarnessError,match='identity differs'):
        probe._analyse(SimpleNamespace(id='right',call_index=0),{},None)
