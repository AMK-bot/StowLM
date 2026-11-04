import json

import pytest

from llm.exit import resources


@pytest.fixture
def fleet(tmp_path,monkeypatch):
    monkeypatch.setattr(resources,'ROOT',tmp_path)
    ledger=tmp_path/'experiments/fleet/ledger.md'
    ledger.parent.mkdir(parents=True)
    ledger.write_text('')
    marker=tmp_path/'experiments/mvp/runs/sft-pilot2-small/polish_summary.json'
    marker.parent.mkdir(parents=True)
    marker.write_text('{}')
    out=tmp_path/'job';out.mkdir()
    live=dict(load_1m=0,memory_free_percent=99)
    monkeypatch.setattr(resources,'probe',lambda:live.copy())
    return out,ledger,marker,live


def row(cores=0,ram=0,gpu='no',owner='other/job'):
    return f'| {owner} | job | {cores} | {ram} | {gpu} | 1s | now |\n'


@pytest.mark.parametrize('policy,load,cores,gpu,allowed',[
    ('strict',15,2,False,False),('strict',14,2,False,True),
    ('gpu_smoke',18,1,True,True),('gpu_smoke',18.01,1,True,False),
    ('gpu_generation',100,3,True,True)])
def test_load_policy_changes_only_the_load_gate(fleet,policy,load,cores,gpu,allowed):
    out,ledger,marker,live=fleet;live['load_1m']=load
    context=resources.reservation(out,cores=cores,ram_gb=66,gpu=gpu,estimate_s=1,load_policy=policy)
    if allowed:
        with context as handle:
            assert handle.cores==cores
            assert resources.OWNER in ledger.read_text()
    else:
        with pytest.raises(RuntimeError,match='capacity unavailable'):
            with context:pytest.fail('unexpected admission')
    assert resources.OWNER not in ledger.read_text()


@pytest.mark.parametrize('policy,cores,gpu',[
    ('gpu_smoke',2,True),('gpu_smoke',1,False),('gpu_generation',1,False),('unknown',1,True)])
def test_policy_misuse_fails_before_reservation(fleet,policy,cores,gpu):
    out,ledger,_,_=fleet
    with pytest.raises(ValueError):
        with resources.reservation(out,cores=cores,ram_gb=1,gpu=gpu,estimate_s=1,load_policy=policy):
            pytest.fail('invalid policy admitted')
    assert not ledger.read_text()


@pytest.mark.parametrize('blocking', ['cores','ram','gpu','free_memory','handoff','pilot'])
def test_generation_load_exception_preserves_every_other_gate(fleet,blocking):
    out,ledger,marker,live=fleet;live['load_1m']=100
    if blocking=='cores':ledger.write_text(row(14))
    elif blocking=='ram':ledger.write_text(row(ram=47))
    elif blocking=='gpu':ledger.write_text(row(gpu='yes'))
    elif blocking=='free_memory':live['memory_free_percent']=15
    elif blocking=='handoff':marker.unlink()
    elif blocking=='pilot':ledger.write_text(row(owner='fix-t8-sft/training'))
    with pytest.raises(RuntimeError):
        with resources.reservation(out,cores=3,ram_gb=66,gpu=True,estimate_s=1,
                                   load_policy='gpu_generation'):
            pytest.fail('unsafe generation admitted')
    assert resources.OWNER not in ledger.read_text()


def test_resize_excludes_own_row_preserves_others_and_logs_maximum_estimate(fleet):
    out,ledger,_,live=fleet
    other=row(8,4);ledger.write_text(other);live['load_1m']=100
    with resources.reservation(out,cores=3,ram_gb=66,gpu=True,estimate_s=1,
                               load_policy='gpu_generation') as handle:
        assert handle.resize_verifiers(6)
        assert (handle.cores,handle.ram_gb)==(8,76)
        assert resources.ledger_totals(ledger.read_text())==(16,80,True)
        assert other.strip() in ledger.read_text()
        assert handle.resize_verifiers(1)
        assert (handle.cores,handle.ram_gb)==(3,66)
        assert handle.estimate['cores']==8 and handle.estimate['peak_ram_gb']==76
        assert handle.estimate['current_cores']==3
        assert len(handle.estimate['reservation_changes'])==2
    assert ledger.read_text()==other
    compute=json.loads((out/'compute.json').read_text())
    assert compute['estimate']['cores']==8 and compute['estimate']['peak_ram_gb']==76
    assert 'estimated 8 cores/76 GB/' in ledger.with_name('history.md').read_text()
    with pytest.raises(RuntimeError,match='already been released'):
        handle.resize_verifiers(2)


@pytest.mark.parametrize('blocking', ['cores','ram','gpu','free_memory','handoff','pilot'])
def test_refused_resize_keeps_old_reservation(fleet,blocking):
    out,ledger,marker,live=fleet
    with resources.reservation(out,cores=3,ram_gb=66,gpu=True,estimate_s=1,
                               load_policy='gpu_generation') as handle:
        if blocking=='cores':ledger.write_text(ledger.read_text()+row(9))
        elif blocking=='ram':ledger.write_text(ledger.read_text()+row(ram=37))
        elif blocking=='gpu':ledger.write_text(ledger.read_text()+row(gpu='yes'))
        elif blocking=='free_memory':live['memory_free_percent']=20
        elif blocking=='handoff':marker.unlink()
        elif blocking=='pilot':ledger.write_text(ledger.read_text()+row(owner='fix-t8-sft/training'))
        before=ledger.read_text()
        assert not handle.resize_verifiers(6)
        assert ledger.read_text()==before
        assert (handle.cores,handle.ram_gb)==(3,66)
        assert handle.estimate['reservation_changes'][-1]['admitted'] is False


def test_generation_still_requires_shared_gpu_lock(fleet):
    out,_,_,_=fleet
    with resources.execution_lock():
        with pytest.raises(RuntimeError,match='shared GPU/job lock'):
            with resources.reservation(out,cores=3,ram_gb=66,gpu=True,estimate_s=1,
                                       load_policy='gpu_generation'):
                pytest.fail('concurrent GPU use admitted')


def test_downsize_releases_capacity_despite_new_other_cpu_ram_reservations(fleet):
    out,ledger,_,_=fleet
    with resources.reservation(out,cores=8,ram_gb=76,gpu=True,estimate_s=1,
                               load_policy='gpu_generation') as handle:
        other=row(16,112)
        ledger.write_text(ledger.read_text()+other)
        assert handle.resize_verifiers(1)
        assert (handle.cores,handle.ram_gb)==(3,66)
        assert other.strip() in ledger.read_text()
        assert handle.estimate['reservation_changes'][-1]['capacity_policy']=='monotone_release'
