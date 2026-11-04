"""Abort before direct restart and diagnose a precise loaded-domain holdout.

Never build a cold production vessel, and never execute a direct tank envelope.
This diagnostic makes no stability verdict. Supply the current saved warm-up
qualification artifact to verify deterministic case reconstruction.
"""
from __future__ import annotations
import argparse
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np

HERE=Path(__file__).resolve().parent


class StopProbe(BaseException):
    pass


def load_cached_only(ship_name,*,with_hull,allow_assembly_key_bypass=False,events=None):
    """Read an existing validated vessel only; never invoke or populate a builder."""
    from stowage.vessel import fleet
    if events is None:
        events=[]
    from stowage.vessel import fleet_cache
    from stowage.model import Vessel
    assert with_hull is True
    params_bytes=fleet._parameter_bytes(ship_name)
    key=fleet_cache._cache_key(ship_name,'full',True,params_bytes)
    expected=fleet_cache._cache_dir()/f'{ship_name}.full.hull.{key}.json'
    if expected.is_file():
        return Vessel.model_validate_json(expected.read_text())
    if allow_assembly_key_bypass:
        source=f'params/fleet/{ship_name}.toml sha256:{hashlib.sha256(params_bytes).hexdigest()}'
        candidates=sorted(fleet_cache._cache_dir().glob(f'{ship_name}.full.hull.*.json'),
                          key=lambda path:path.stat().st_mtime,reverse=True)
        for path in candidates:
            data=path.read_bytes()
            vessel=Vessel.model_validate_json(data)
            if source in vessel.sources:
                events.append(dict(reason='assembly-key bypass authorized for numerical probe only',
                    cache_path=str(path),sha256=hashlib.sha256(data).hexdigest(),
                    exact_parameter_source=source,no_cache_write=True))
                return vessel
    events.append(dict(reason='cold vessel build forbidden',name=ship_name,expected_cache=str(expected)))
    raise StopProbe()

def probe(name,index,artifact=None,*,allow_assembly_key_bypass=False):
    spec=importlib.util.spec_from_file_location('probe_domain_qualification',HERE.parent/'method1_tables'/'qualify.py')
    qualification=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qualification)
    from stowage.vessel import fleet
    from stowage.checker import stability,stability_tables,stability_tank_transfer
    events=[]
    original_loader=qualification._domain.load_vessel
    def cached_only_loader(ship_name,*,with_hull):
        return load_cached_only(ship_name,with_hull=with_hull,
            allow_assembly_key_bypass=allow_assembly_key_bypass,events=events)
    original_builder=fleet._build_vessel
    original_checker=stability._check_stability
    original_basis=stability_tables._basis
    original_transfer=stability_tank_transfer.max_transfer
    original_condition=stability_tables.TableGeometry._condition
    def forbid_build(*args,**kwargs):
        events.append(dict(reason='cold vessel build forbidden',name=name))
        raise StopProbe()
    def checker(*args,**kwargs):
        if kwargs.get('_force_direct'):
            active=sys.exception()
            events.append(dict(reason='whole-check direct restart',exception_type=type(active).__name__ if active else None,
                exception=str(active) if active else None,traceback=traceback.format_exception(active) if active else None,
                caller_stack=traceback.format_stack(limit=10)))
            raise StopProbe()
        return original_checker(*args,**kwargs)
    def table_condition(self,*args,**kwargs):
        try:
            value=original_condition(self,*args,**kwargs)
            events.append(dict(reason='table condition solved',heel_rad=float(args[3]) if args[3] is not None else None,
                trim_m=float(value[0].trim_m),mean_draft_m=float(value[0].mean_draught_m)))
            return value
        except stability_tables.OutsideTable as error:
            events.append(dict(reason='table condition failure',heel_rad=float(args[3]) if args[3] is not None else None,
                seed=np.asarray(args[4]).tolist() if len(args)>4 and args[4] is not None else None,
                message=str(error)))
            raise
    def basis(axis,value):
        if value < axis[0]-1e-10 or value > axis[-1]+1e-10:
            events.append(dict(reason='hull interpolation basis miss',value=float(value),
                minimum=float(axis[0]),maximum=float(axis[-1])))
        return original_basis(axis,value)
    def forbid_envelope(*args,**kwargs):
        events.append(dict(reason='direct tank envelope forbidden',heel_rad=float(args[1]),
            trim_rad=float(args[2]),caller_stack=traceback.format_stack(limit=10)))
        raise StopProbe()
    qualification._domain.load_vessel=cached_only_loader
    fleet._build_vessel=forbid_build
    stability._check_stability=checker
    stability_tables._basis=basis
    stability_tables.TableGeometry._condition=table_condition
    stability_tank_transfer.max_transfer=forbid_envelope
    started=perf_counter()
    result=dict(vessel=name,index=index,events=events)
    try:
        rng=np.random.default_rng(3911)
        for current in range(index+1):
            instance,point=qualification._domain.domain_condition(name,rng,current)
        result['domain_point']=point
        result['construction_seconds']=perf_counter()-started
        if artifact:
            saved=json.loads(artifact.read_text())['cases'][index]
            result['matches_saved_domain_point']=saved['domain_point']==point
            result['matches_saved_tank_masses']=saved['tank_masses_kg']==dict(instance.onboard.tank_masses_kg)
            if not (result['matches_saved_domain_point'] and result['matches_saved_tank_masses']):
                raise ValueError('reconstructed condition differs from saved qualification')
        with qualification.offline_per_tank_qualification() as observations:
            result['fast']=qualification._benchmark._previous.run_check(instance,1,same_exemptions=False)
            result['guard_observations']=observations
    except StopProbe:
        result['stopped_before_direct_or_build']=True
    finally:
        qualification._domain.load_vessel=original_loader
        fleet._build_vessel=original_builder
        stability._check_stability=original_checker
        stability_tables._basis=original_basis
        stability_tables.TableGeometry._condition=original_condition
        stability_tank_transfer.max_transfer=original_transfer
    result['seconds']=perf_counter()-started
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vessel',required=True)
    parser.add_argument('--index',required=True,type=int)
    parser.add_argument('--artifact',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--allow-assembly-key-bypass',action='store_true',
        help='explicitly authorized probe only: exact-params cached Vessel JSON, no cache writes')
    args=parser.parse_args()
    result=probe(args.vessel,args.index,args.artifact,allow_assembly_key_bypass=args.allow_assembly_key_bypass)
    args.output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps(result,indent=2,default=str))
