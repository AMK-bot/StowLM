"""Warm fleet resolution checks exact bytes and returns independent trees."""
import os
from types import SimpleNamespace as NS

import pytest

from stowage.linear import physics


@pytest.fixture(autouse=True)
def clear_parsed_parameters():
    physics._parsed_linear_params.cache_clear()
    yield
    physics._parsed_linear_params.cache_clear()


def test_known_parent_name_is_resolved_first_and_returned_trees_are_independent(monkeypatch):
    from stowage.vessel import fleet
    reads=[]
    monkeypatch.setattr(physics,'list_fleet',lambda:['alpha','beta','target'])
    def raw(name):
        reads.append(name)
        return f'id="{name}"\n[tank]\nvalues=[1,2]\n'.encode()
    monkeypatch.setattr(fleet,'_parameter_bytes',raw)
    instance=NS(vessel=NS(id='target.slice',slice_of='target',sources=[]))
    name,first=physics._params_for(instance)
    first['tank']['values'][0]=99
    _,second=physics._params_for(instance)
    assert name=='target' and reads==['target','target']
    assert second['tank']['values']==[1,2]
    assert physics._parsed_linear_params.cache_info().misses==1
    assert physics._parsed_linear_params.cache_info().hits==1


def test_raw_byte_edit_misses_even_if_size_and_mtime_are_preserved(monkeypatch,tmp_path):
    from stowage.vessel import fleet
    path=tmp_path/'target.toml'
    original=b'id="target"\n[tank]\nvalues=[1,2]\n'
    path.write_bytes(original)
    state=path.stat()
    reads=[]
    monkeypatch.setattr(physics,'list_fleet',lambda:['target'])
    def raw(name):
        reads.append(name)
        return path.read_bytes()
    monkeypatch.setattr(fleet,'_parameter_bytes',raw)
    instance=NS(vessel=NS(id='target',slice_of=None,sources=[]))
    _,first=physics._params_for(instance)
    changed=original.replace(b'[1,2]',b'[9,2]')
    assert len(changed)==len(original)
    path.write_bytes(changed)
    os.utime(path,ns=(state.st_atime_ns,state.st_mtime_ns))
    assert path.stat().st_mtime_ns==state.st_mtime_ns
    _,second=physics._params_for(instance)
    assert reads==['target','target']
    assert first['tank']['values']==[1,2] and second['tank']['values']==[9,2]
    assert physics._parsed_linear_params.cache_info().misses==2


def test_nonstandard_id_retains_declared_fleet_source_resolution(monkeypatch):
    from stowage.vessel import fleet
    monkeypatch.setattr(physics,'list_fleet',lambda:['alpha','beta'])
    monkeypatch.setattr(fleet,'_parameter_bytes',lambda name:f'id="{name}"'.encode())
    instance=NS(vessel=NS(id='custom',slice_of=None,sources=['params/fleet/beta.toml']))
    assert physics._params_for(instance)==('beta',{'id':'beta'})
    instance.vessel.sources=[]
    with pytest.raises(ValueError,match='declared HullGeometry fleet inputs'):
        physics._params_for(instance)


@pytest.mark.parametrize('shared', [False, True])
def test_custom_id_resolves_pinned_legacy_source_without_changing_provenance(shared):
    from copy import deepcopy
    from hashlib import sha256
    from stowage.conventions import LEGACY_FLEET_PARAMETER_PINS
    from stowage.vessel.fleet import _parameter_bytes, load_ship_params

    old_sha = LEGACY_FLEET_PARAMETER_PINS['feeder_800'][0]
    source = f'params/fleet/feeder_1000.toml sha256:{old_sha}'
    vessel = NS(id='custom-source-only', slice_of=None, sources=[source])
    before = deepcopy(vars(vessel))
    canonical = NS(vessel=NS(id=vessel.id, slice_of=None, sources=[
        f'params/fleet/feeder_800.toml sha256:{sha256(_parameter_bytes("feeder_800")).hexdigest()}']))
    resolve = physics._prepared_params_for if shared else physics._params_for
    expected = resolve(canonical)
    actual = resolve(NS(vessel=vessel))
    assert actual == expected == ('feeder_800', load_ship_params('feeder_800'))
    assert vars(vessel) == before
    assert vessel.sources == [source]


@pytest.mark.parametrize('shared', [False, True])
def test_validated_dotted_legacy_id_without_slice_root_resolves_source(shared):
    from hashlib import sha256
    from stowage.conventions import LEGACY_FLEET_PARAMETER_PINS
    from stowage.model import Vessel
    from stowage.vessel.fleet import _parameter_bytes, load_ship_params

    old_sha = LEGACY_FLEET_PARAMETER_PINS['feeder_800'][0]
    source = f'params/fleet/feeder_1000.toml sha256:{old_sha}'
    vessel = Vessel.model_validate(dict(id='feeder_1000.slice_b02-b10',
        name='Source-only geometry lookup regression', nominal_teu=0,
        cells=[], stacks=[], hatch_covers=[], sources=[source]))
    before = vessel.model_dump(mode='json')
    canonical = Vessel.model_validate({**before, 'sources': [
        f'params/fleet/feeder_800.toml sha256:{sha256(_parameter_bytes("feeder_800")).hexdigest()}']})
    resolve = physics._prepared_params_for if shared else physics._params_for
    expected = resolve(NS(vessel=canonical))
    actual = resolve(NS(vessel=vessel))
    assert actual == expected == ('feeder_800', load_ship_params('feeder_800'))
    assert vessel.slice_of is None
    assert vessel.model_dump(mode='json') == before
    assert vessel.sources == [source]
