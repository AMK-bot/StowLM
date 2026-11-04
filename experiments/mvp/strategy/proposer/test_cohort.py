"""D185 consumes the external final freeze; synthetic train files only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from experiments.mvp.strategy.proposer import cohort


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root=tmp_path/'repo'
    here=root/'experiments/mvp/strategy/proposer'
    blockrule=here.parent/'blockrule'
    for key,value in [('ROOT',root),('HERE',here),('BLOCKRULE',blockrule),
                      ('MEDIUM_SOURCE',blockrule/'cohort-medium.json')]:
        monkeypatch.setattr(cohort,key,value)
    small=[]
    for i in range(20):
        path=root/f'experiments/mvp/corpus/train/small/map-{i}.json'
        small.append(dict(map_id=f'small-{i}',input=str(path.relative_to(root)),size='small',split='train',
                          sha256=write(path,{'id':f'small-{i}'})))
    write(here/'maps.json',{'maps':small})
    specs=[]
    medium=[]
    for i in range(12):
        ship=f'feeder_{i//4}'
        family='random_yard' if i%4<2 else 'simulated_yard'
        seed=185100000+i
        specs.append(dict(vessel=ship,generator=family,seed=seed,split='train',rung='medium'))
        path=blockrule/f'maps/train/medium/map-{i}.json'
        audit=path.with_suffix('.audit.json')
        medium.append(dict(map_id=f'medium-{i}',input=str(path.relative_to(root)),size='medium',split='train',
            ship=ship,family=family,seed=seed,sha256=write(path,{'id':f'medium-{i}','size_class':'medium'}),
            audit=str(audit.relative_to(root)),audit_sha256=write(audit,{'producer':'synthetic','id':i}),
            release_epoch='D185-current-epoch',source_binding_status='current_epoch_generated',generator_gate_passed=True))
    request_sha=write(blockrule/'cohort-requests.json',{'specs':specs})
    source=dict(format='D185-blockrule-frozen-medium-train-v1',request_sha256=request_sha,
                frozen_at_utc='2026-10-31T01:08:38+00:00',counts={'medium':12},maps=medium)
    write(blockrule/'cohort-medium.json',source)
    return here,blockrule,small,medium,source


def test_consumes_exact_final_medium_and_retains_small(prepared):
    here,blockrule,small,medium,_=prepared
    target=here/'maps_d185.json'
    frozen=cohort.freeze(target=target)
    assert frozen['maps']==small+medium
    assert frozen['counts']=={'small':20,'medium':12}
    assert frozen['frozen_before_proposer_runs']
    jobs=cohort.manifest_jobs(target)
    assert len(jobs)==32 and [r['input_sha256'] for r in jobs]==[r['sha256'] for r in small+medium]
    assert frozen['medium_source']['sha256']==cohort.sha(blockrule/'cohort-medium.json')


def test_missing_final_source_does_not_use_progress(prepared):
    here,blockrule,_,_,source=prepared
    (blockrule/'cohort-medium.json').unlink()
    write(blockrule/'cohort-generation-progress.json',source)
    with pytest.raises(FileNotFoundError,match='waiting'):
        cohort.freeze(target=here/'maps_d185.json')
    assert not (here/'maps_d185.json').exists()


def test_progress_filename_cannot_be_promoted(prepared):
    here,blockrule,_,_,source=prepared
    partial=blockrule/'cohort-generation-progress.json'
    write(partial,source)
    with pytest.raises(ValueError,match='only blockrule'):
        cohort.freeze(target=here/'maps_d185.json',medium_source=partial)


@pytest.mark.parametrize('change',[
    lambda x:x.update(format='generation-progress'),
    lambda x:x.update(request_sha256='0'*64),
    lambda x:x['maps'].pop(),
    lambda x:x['maps'][0].update(seed=999),
    lambda x:x['maps'][0].update(split='val'),
    lambda x:x['maps'][0].update(release_epoch='v1_pending_replacement'),
    lambda x:x['maps'][0].update(source_binding_status='drift'),
    lambda x:x['maps'][0].update(generator_gate_passed=False),
    lambda x:x['maps'][0].update(audit_sha256='0'*64),
    lambda x:x['maps'][0].update(sha256='0'*64),
])
def test_partial_stale_or_relabelled_medium_rejected(prepared,change):
    here,blockrule,_,_,source=prepared
    change(source)
    write(blockrule/'cohort-medium.json',source)
    with pytest.raises(ValueError):
        cohort.freeze(target=here/'maps_d185.json')
    assert not (here/'maps_d185.json').exists()


def test_freeze_never_overwrites_cohort(prepared):
    here,*_=prepared
    target=here/'maps_d185.json'
    cohort.freeze(target=target)
    before=target.read_bytes()
    with pytest.raises(ValueError,match='already frozen'):
        cohort.freeze(target=target)
    assert target.read_bytes()==before


def test_frozen_input_mutation_refuses_run(prepared):
    here,blockrule,_,medium,_=prepared
    target=here/'maps_d185.json'
    cohort.freeze(target=target)
    (cohort.ROOT/medium[0]['input']).write_text('{}')
    with pytest.raises(ValueError,match='SHA mismatch'):
        cohort.manifest_jobs(target)


def test_edited_subset_cohort_cannot_keep_frozen_attestation(prepared):
    here,*_=prepared
    target=here/'maps_d185.json'
    frozen=cohort.freeze(target=target)
    frozen['maps'].pop()
    frozen['counts']['medium']-=1
    write(target,frozen)
    with pytest.raises(ValueError,match='rows/order differ'):
        cohort.manifest_jobs(target)


def test_external_medium_freeze_mutation_refuses_consumption(prepared):
    here,blockrule,*_=prepared
    target=here/'maps_d185.json'
    cohort.freeze(target=target)
    data=json.loads((blockrule/'cohort-medium.json').read_text())
    data['maps'].reverse()
    write(blockrule/'cohort-medium.json',data)
    with pytest.raises(ValueError,match='external medium freeze'):
        cohort.manifest_jobs(target)


def test_legacy_archived_cohort_cannot_be_used(prepared):
    here,*_=prepared
    with pytest.raises(ValueError,match='frozen D185'):
        cohort.manifest_jobs(here/'maps.json')


def test_current_actual_cohort_is_exact_external_freeze():
    path=cohort.DEFAULT_MANIFEST
    if not path.exists():
        pytest.skip('waiting for final blockrule medium publication')
    frozen=json.loads(path.read_text())
    external=json.loads(cohort.MEDIUM_SOURCE.read_text())
    jobs=cohort.manifest_jobs(path)
    assert [r['input'] for r in jobs if r['size']=='medium']==[r['input'] for r in external['maps']]
    assert len([r for r in jobs if r['size']=='small'])==20
    assert frozen['medium_source']['sha256']==cohort.sha(cohort.MEDIUM_SOURCE)
