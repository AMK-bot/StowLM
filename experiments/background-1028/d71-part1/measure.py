"""D71 part1: real full-checker backgrounds plus actual berth arrival state."""
from __future__ import annotations
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import time

from stowage.generate.background_acceptance import evaluate_background
from stowage.generate.random_yard import _tiny_region
from stowage.generate.random_voyage import build_voyage_spec
from stowage.model import Instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState
from stowage.ports import load_port
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet import load_vessel, _parameter_bytes
from stowage.vessel.fleet_conditions import departure_tank_masses, slice_condition
from stowage.vessel.fleet_slices import recondition_slice, slice_preset


def draw(ship,rung,seed):
    parent=load_vessel(ship)
    v=parent if rung=='full' else _tiny_region(parent) if rung=='tiny' else slice_preset(parent,rung)
    if rung!='full':
        mass={'tiny':50_000.,'small':250_000.,'medium':{
            'feeder_500':1_917_238.3810685035,'feeder_800':3_734_513.4862812967,
            'feeder_1700':6_914_017.2082863245}[ship]}[rung]
        v=recondition_slice(v,expected_load_mass_kg=mass,seed=seed)
    port=load_port('laem_chabang_like')
    rotation=Rotation(service='d71-in-port-background',calls=[
        PortCall(locode=port.locode,archetype=port.archetype,index=i) for i in(0,1)])
    return Instance(id=f'd71.{ship}.{rung}.{seed}',stage='A',size_class=rung,seed=seed,
        vessel=v,port=port,rotation=rotation,call_index=0,containers=[],load_list=[],
        yard=YardState(yard_id=port.yard.id),onboard=ShipState(vessel_id=v.id,
            tank_masses_kg=departure_tank_masses(v)),voyage=build_voyage_spec(rotation,0,port,seed=seed))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--ships',nargs='+',default=['feeder_500','feeder_800','feeder_1700'])
    p.add_argument('--rungs',nargs='+',default=['tiny','small','medium','full'])
    p.add_argument('--draws',type=int,default=3)
    p.add_argument('--output',type=Path,default=Path('/tmp/d64-initial.jsonl'))
    args=p.parse_args()
    done=set()
    if args.output.exists():
        done={tuple(json.loads(line)[k] for k in ('ship','rung','seed')) for line in args.output.read_text().splitlines()}
    with rule_freeze_v1():
        for ship in args.ships:
            for rung in args.rungs:
                seeds=[7+i for i in range(args.draws)] if rung!='full' else [7]
                for seed in seeds:
                    if (ship,rung,seed) in done: continue
                    started=time.perf_counter()
                    row=dict(ship=ship,rung=rung,seed=seed,phase='provisional_current_parameter_pins',
                        parameter_sha256=sha256(_parameter_bytes(ship)).hexdigest())
                    try:
                        instance=draw(ship,rung,seed)
                        from stowage.vessel.fleet_berth import prepare_arrival
                        prepared=prepare_arrival(instance)
                        result=evaluate_background(instance, arrival_instance=prepared)
                        condition=slice_condition(instance.vessel)
                        tanks={t.id:t for t in instance.vessel.tanks}
                        def fills(masses):
                            return {tid:dict(mass_kg=mass,fraction=mass/(tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3))
                                    for tid,mass in masses.items() if tid in tanks and tanks[tid].adjustable}
                        row.update(result=asdict(result),arrival_fills=fills(prepared.onboard.tank_masses_kg),
                            witness_fills=fills(result.witness_tank_masses_kg),
                            heel_pairs=[p.model_dump(mode='json') for p in instance.vessel.tank_pairs],
                            heel_capacities_kg={tid:tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
                                for pair in instance.vessel.tank_pairs for tid in(pair.port_tank,pair.starboard_tank)},
                            lashing_gm_cap_m=instance.vessel.hull.max_gm_lashing_m,
                            trim_half_limit_m=instance.vessel.hull.lpp_m*__import__('math').tan(__import__('math').radians(1.))/2,
                            booking_budget=(condition or {}).get('booking_departure_budget'),
                            booking_reference_ballast_sizing=(condition or {}).get(
                                'booking_reference_ballast_sizing'),
                            condition_version=(condition or {}).get('version'),
                            preparation_notes=[s for s in prepared.vessel.sources if 'berth' in s.lower() or 'arrival' in s.lower()])
                    except Exception as error:
                        row['error']=f'{type(error).__name__}: {error}'
                    row['elapsed_s']=time.perf_counter()-started
                    with args.output.open('a') as stream: stream.write(json.dumps(row,sort_keys=True,default=str)+'\n')
                    print(json.dumps({k:row[k] for k in ('ship','rung','seed','elapsed_s')},sort_keys=True),row.get('error',row.get('result',{}).get('reasons')),flush=True)

if __name__=='__main__':main()
