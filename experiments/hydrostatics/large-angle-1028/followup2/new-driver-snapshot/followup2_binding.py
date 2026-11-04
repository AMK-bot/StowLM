"""Factorial hatch-envelope probes with unchanged production stability criteria.

APPROXIMATION: cover credit is the full declared 0.6m cuboid. Cover-only is
an artificial attribution probe, not a physical recommendation. All physical
coaming dimensions in B2.3.2.6 D-prime, all masses and windage stay fixed.
Vents7.5 is D67's hold-only raise; ER inlets and closures stay fixed.
"""
import argparse
import copy
from datetime import datetime, timezone
import json
import math
import resource
import time
import numpy as np
from followup2_common import HERE, ROOT, OUT, save, sha, source_hashes
from feeder_envelope import manual_instance, evaluate
from extend19 import geometry_from_sections
from stowage.model import Instance, MovePlan, derive_voyage_liquid_state
from stowage.vessel.fleet import load_ship_params
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability import check_stability
from stowage.checker.full import departure_condition
from stowage.generate.background_acceptance import background_instance

FACTORS=((1,0),(0,0),(1,1),(0,1))
COEFFICIENTS=dict(area_0_30_mrad=.009,area_0_40_mrad=.016,area_30_40_mrad=.006,
    gz_at_or_above_30_m=.033,gz_max_m=.042,area_0_flood_mrad=.029)


def credit_geometry(native, coaming, cover, vent_height):
    sections=[]
    for section in native.sections:
        parts=[section[0].copy()]
        for polygon in section[1:]:
            if coaming: parts.append(polygon.copy())
            if cover:
                height=float(np.ptp(polygon[:,1]))
                assert abs(height-.6)<1e-8
                box=polygon.copy();box[:,1]+=.6;parts.append(box)
        sections.append(parts)
    result=geometry_from_sections(native,sections)
    result.openings=copy.deepcopy(native.openings)
    if vent_height is not None:
        for opening in result.openings:
            if opening['kind']=='hold_ventilator':
                opening['z_m']=native.depth_m+vent_height
    return result


def contexts(instance, departure, phases):
    # Same proportional-liquid and context conventions as full._voyage_conditions.
    from stowage.model import arrival_remaining_fraction
    r=arrival_remaining_fraction(departure,instance.voyage,vessel=instance.vessel)
    for phase in phases:
        fraction={'departure':1.,'mid_voyage':(1+r)/2,'arrival':r}[phase]
        sample=derive_voyage_liquid_state(departure,instance.voyage,fraction,vessel=instance.vessel)
        ship=departure.model_copy(deep=True,update={'tank_masses_kg':dict(sample.tank_masses_kg)})
        identity=instance.voyage.arrival if phase=='arrival' else instance.voyage.departure
        port=instance.port if phase=='departure' else instance.port.model_copy(update={
            'locode':identity.locode,'water_density_kg_m3':identity.water_density_kg_m3,
            'max_draught_m':None,'port_rules':[]})
        yield phase,fraction,instance.model_copy(update={'port':port,'onboard':ship,'call_index':identity.call_index}),ship


def prepare_inputs(background_set):
    for name in ('feeder_500','feeder_800','feeder_1700'):
        save(OUT/f'{name}-params.json',load_ship_params(name))
        for label in (('C1','C2') if name=='feeder_500' else ('C1',)):
            instance,_,recipe=manual_instance(name,label)
            save(OUT/f'{name}-{label}-input.json',dict(instance=instance.model_dump(mode='json'),recipe=recipe))
        if background_set=='tiny': stem=ROOT/f'experiments/background-1028/part-margins/{name}-tiny-seed1'
        else: stem=ROOT/f'experiments/background-1028/review-fixes/retry-random-{name}-seed0'
        ipath=stem.with_suffix('.instance.json');ppath=stem.with_suffix('.plan.json')
        original=Instance.model_validate_json(ipath.read_text())
        instance=background_instance(original)
        plan=MovePlan.model_validate_json(ppath.read_text())
        assert plan.moves==[]
        departure=departure_condition(instance,plan)
        instance=instance.model_copy(update={'onboard':departure})
        save(OUT/f'{name}-D58-input.json',dict(instance=instance.model_dump(mode='json'),
            recipe=dict(source_instance=str(ipath.relative_to(ROOT)),source_plan=str(ppath.relative_to(ROOT)),
                        source_instance_sha256=sha(ipath),source_plan_sha256=sha(ppath),
                        background_set=background_set,empty_plan=True)))


def run(name,label,phase,vent_height,public):
    start=time.perf_counter();before=source_hashes()
    payload=json.loads((OUT/f'{name}-{label}-input.json').read_text())
    instance=Instance.model_validate(payload['instance'])
    p=json.loads((OUT/f'{name}-params.json').read_text());native=StabilityGeometry(p)
    original_openings=copy.deepcopy(native.openings)
    _,fraction,context,ship=next(contexts(instance,instance.onboard,[phase]))
    rows=[];condition=None;wind=None;baseline=None
    for coaming,cover in FACTORS:
        g=credit_geometry(native,coaming,cover,vent_height if name=='feeder_500' else None)
        row,condition,wind=evaluate(context,ship,p,g,condition,wind)
        C=row['metrics']['containership_form_factor']
        ratios={key:row['metrics'][key]/(coef/C) for key,coef in COEFFICIENTS.items()}
        row.update(coaming_credit=bool(coaming),cover_credit=bool(cover),
            vent_height_above_deck_m=next(o['z_m']-g.depth_m for o in g.openings if o['kind']=='hold_ventilator'),
            requested_vent_argument_m=vent_height,phase=phase,remaining_fraction=fraction,
            all_b_ratios=ratios,min_b_ratio=min(ratios.values()),binding_b_term=min(ratios,key=ratios.get),
            openings=g.openings)
        if baseline is None: baseline=row
        row['delta_total_area_mrad']=row['metrics']['area_0_flood_mrad']-baseline['metrics']['area_0_flood_mrad']
        rows.append(row)
        print(name,label,phase,vent_height,coaming,cover,row['flooding_deg'],row['min_b_ratio'],row['binding_b_term'],flush=True)
    # Direct public checker validates baseline plumbing; no caches are generated.
    report=None
    if public:
        assert name!='feeder_500' or vent_height==4.5
        rep=check_stability(context,ship,call_index=context.call_index,in_port=False)
        report=rep.model_dump(mode='json')
        assert rep.ran and not rep.error,report
        for key in (*COEFFICIENTS,'containership_form_factor','containership_depth_prime_m'):
            assert abs(rep.metrics[key]-baseline['metrics'][key])<1e-8,(key,rep.metrics[key],baseline['metrics'][key])
    assert native.openings==original_openings
    result=dict(date_utc=datetime.now(timezone.utc).isoformat(),ship=name,label=label,phase=phase,
        recipe=payload['recipe'],density_kg_m3=context.port.water_density_kg_m3,
        input_sha256=sha(OUT/f'{name}-{label}-input.json'),params_sha256=sha(OUT/f'{name}-params.json'),
        physical_coamings_for_Dprime=p['envelope']['hatch_coamings'],rows=rows,public_checker=report,
        source_hashes_before=before,source_hashes_after=source_hashes(),elapsed_s=time.perf_counter()-start,
        peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    tag=str(vent_height).replace('.','p') if name=='feeder_500' else 'native'
    save(OUT/f'{name}-{label}-{phase}-v{tag}.json',result)
    assert before==result['source_hashes_after'],'Source drift during shard; result retained for audit'
    print('elapsed_s',result['elapsed_s'],'rss',result['peak_rss_bytes'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--prepare',choices=['tiny','medium'])
    parser.add_argument('--ship');parser.add_argument('--label');parser.add_argument('--phase')
    parser.add_argument('--vent',type=float,default=4.5);parser.add_argument('--public',action='store_true')
    args=parser.parse_args()
    if args.prepare: prepare_inputs(args.prepare)
    else: run(args.ship,args.label,args.phase,args.vent,args.public)
