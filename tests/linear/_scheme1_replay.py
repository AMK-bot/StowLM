"""Explicit epoch selection: historical data or strict live scheme-2 builders.

There is no availability fallback. FINAL changes the declared scheme and root
only after its fresh evidence gates; scheme 2 always uses production admission.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
OWNER=ROOT/'experiments/linear-model/epoch_requalification'
ARCHIVE=OWNER/'hash-scheme-1028-review/scheme1-history'


def _module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def open_context(evidence_root,scheme):
    evidence_root=Path(evidence_root).resolve()
    if scheme=='1':
        if evidence_root!=(OWNER/'production-1028b').resolve():
            raise ValueError('scheme-1 history must retain its original evidence root')
        return _module('scheme1_recorded_row_replay',ARCHIVE/'replay.py')
    if scheme!='2':raise ValueError('explicit supported evidence hash scheme required')
    from stowage.linear import dependencies
    if dependencies.LINEAR_DEPENDENCY_HASH_SCHEME_VERSION!='2':
        raise ValueError('live evidence requires current scheme 2')
    from stowage.linear import linear_constraints,build_linear_model
    from stowage.linear.margin_policy import qualify_policy
    from stowage.linear.reusable import assemble_instance
    from stowage.linear.operations import public_loading_policy
    from stowage.linear.bands import band_for_instance
    from stowage.model import FixedLoad,ShipState
    driver=_module('live_scheme2_replay_driver',OWNER/'driver.py')
    def prepare(ship):
        prepared=driver.prepare(ship,evidence_root/'ordinary')
        family,instance,errors,diagnostics,before=prepared
        return (*prepared,qualify_policy(family,instance,diagnostics))
    def edge_model(prepared,observed):
        family,instance,errors,diagnostics,_,certificate=prepared
        tanks=sorted(instance.vessel.tanks,key=lambda tank:tank.id)
        onboard=ShipState(vessel_id=instance.vessel.id,tank_masses_kg={tank.id:float(value)
            for tank,value in zip(tanks,observed['all_tank_masses_kg'],strict=True) if not tank.adjustable})
        state=instance.model_copy(update={'containers':[],'load_list':[],'discharge_list':[],
            'onboard':onboard,'vessel':instance.vessel.model_copy(update={
                'fixed_loads':[FixedLoad(**observed['fixed_point'])]})})
        return assemble_instance(family,state,family.template.band,errors=errors,
            diagnostics=dict(diagnostics,margin_policy=certificate,loading_policy=public_loading_policy(state)))
    def ordinary_model(ship,level,source):
        instance,_=driver.cargo_instance(ship,level)
        return build_linear_model(instance,band=band_for_instance(instance))
    def snapshot(ship,parameters):
        if parameters is None:
            from stowage.linear.physics import _prepared_params_for
            instance,_=driver.cargo_instance(ship,'light')
            _,parameters=_prepared_params_for(instance)
        return driver.snapshot(ship,parameters)
    return SimpleNamespace(prepare=prepare,edge_model=edge_model,ordinary_model=ordinary_model,
        snapshot=snapshot,constraints=linear_constraints)
