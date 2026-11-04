"""Authorized assembly shim regressions: synthetic objects, no map opens."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import VoyagePacket
from experiments.mvp.pipeline.runner import assembly, core
from experiments.mvp.pipeline.sealed import runner_seam as seam
from experiments.mvp.pipeline.sealed.common import HarnessError


@pytest.fixture
def authorized(tmp_path):
    path=tmp_path/'sealed'/'never-opened.json'
    job=dict(mode='sealed',arm='pipeline',path=str(path),sha256='a'*64)
    packet=VoyagePacket(packet_id='sealed-fixture',created_utc='2026-11-02T00:00:00Z',
        synthetic=True,base_instance=dict(path=str(path),sha256='a'*64),
        standing='',profile='standard')
    instance=SimpleNamespace(model_dump=lambda **kw:dict(synthetic=True,load_list=['box']))
    return instance,job,packet


def test_support_check_requires_actual_runner_assembly_aliases(monkeypatch):
    assert 'ordinary runner revalidation' in seam.support_check()['kind']
    monkeypatch.setattr(core,'assemble',lambda packet:None)
    with pytest.raises(HarnessError,match='aliases'):
        seam.support_check()


def test_authorized_base_uses_loaded_instance_without_file_open_and_restores_fence(authorized):
    instance,job,packet=authorized
    original=assembly._base
    calls=[]
    def loaded(actual,request):
        calls.append((actual,request))
        return actual is instance and request is job
    with seam.authorized_instance(instance,job,packet,require_loaded=loaded):
        assert assembly._base(packet)==(instance,job['sha256'])
    assert len(calls)==2 and assembly._base is original
    with pytest.raises(assembly.AssemblyError,match='Sealed maps cannot be read'):
        assembly._base(packet)


def test_real_assemble_and_metadata_keep_revalidation_after_authorized_base(authorized,monkeypatch):
    instance,job,packet=authorized
    revalidated=SimpleNamespace(model_dump=lambda **kw:dict(synthetic=True,load_list=['box']))
    calls=[]
    def assemble(actual,loaded):
        calls.append((actual,loaded))
        return revalidated
    monkeypatch.setattr(assembly,'_assemble',assemble)
    with seam.authorized_instance(instance,job,packet,require_loaded=lambda *args:True):
        assert core.assemble(packet) is revalidated
        metadata=core.assembly_metadata(packet,revalidated)
    assert calls==[(packet,instance)]
    assert metadata['base_file_sha256']==job['sha256']
    assert metadata['hash_equal'] and metadata['replacements']==dict(load_list=False,yard_state=False)


@pytest.mark.parametrize('marker',[None,False,1,'authorized',{}])
def test_no_json_or_truthy_marker_substitutes_for_authorized_worker_load(authorized,marker):
    instance,job,packet=authorized
    with pytest.raises(HarnessError,match='authorized-load provenance'):
        with seam.authorized_instance(instance,job,packet,require_loaded=lambda *args:marker):
            pytest.fail('unauthorized context entered')


@pytest.mark.parametrize('change',[dict(standing='One port per hatch.'),dict(profile='live'),
    dict(base_instance=dict(path='/different',sha256='a'*64)),
    dict(base_instance=dict(path='/different',sha256='b'*64)),
    dict(load_list=dict(origin='generator',rows=[dict(id='replacement')])),
    dict(yard_state=dict(origin='generator',state={}))])
def test_replacement_packet_refuses_before_authorization_callback(authorized,change):
    instance,job,packet=authorized
    candidate=packet.model_dump(mode='json')
    candidate.update(change)
    with pytest.raises(HarnessError,match='differs'):
        with seam.authorized_instance(instance,job,candidate,
                require_loaded=lambda *args:pytest.fail('invalid packet reached authorization')):
            pytest.fail('invalid packet entered')


def test_full_packet_pin_and_load_provenance_are_rechecked_for_each_base_call(authorized):
    instance,job,packet=authorized
    active=[True]
    with seam.authorized_instance(instance,job,packet,require_loaded=lambda *args:active[0]):
        changed=packet.model_copy(update=dict(packet_id='another-packet'))
        with pytest.raises(assembly.AssemblyError,match='changed'):
            assembly._base(changed)
        active[0]=False
        with pytest.raises(assembly.AssemblyError,match='changed'):
            assembly._base(packet)


def test_error_restores_private_loader_and_nested_context_refuses(authorized):
    instance,job,packet=authorized
    original=assembly._base
    with pytest.raises(RuntimeError,match='injected'):
        with seam.authorized_instance(instance,job,packet,require_loaded=lambda *args:True):
            with pytest.raises(HarnessError,match='Nested'):
                with seam.authorized_instance(instance,job,packet,require_loaded=lambda *args:True):
                    pytest.fail('nested context entered')
            raise RuntimeError('injected')
    assert assembly._base is original and seam._active is False
