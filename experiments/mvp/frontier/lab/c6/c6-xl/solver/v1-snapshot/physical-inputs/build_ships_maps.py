"""Private synthetic XL fleet and preregistered TRAIN derivative maps.

install_runtime only registers private parameter location and cache paths. It
does not construct a ship, touch checker criteria, or reuse a previous plan.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'pyproject.toml').exists())
RUNTIME = Path(os.environ.get('C6_XL_RUNTIME', HERE / 'runtime'))
SHIPS = HERE / 'ships'
MAPS = HERE / 'maps'
NAMES = ('synthetic_3600', 'synthetic_8000')
SPECS = ((3600, 242.,230.,32.2,19.,10.8,13,12,6,7,450,'bangkokmax-1700.html'),
         (8000, 346.,330.,32.2,26.,13.,22,12,8,9,900,'bangkokmax-1700.html'))

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.partial')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    temp.replace(path)

def install_runtime():
    """Register synthetic raw params, preserving unchanged checker algorithms."""
    import stowage.params as params
    if getattr(params, '_c6_xl_registered', False): return
    base = params.params_dir
    def directory(kind=None):
        return SHIPS if kind == 'fleet' else base(kind)
    params.params_dir = directory
    params._c6_xl_registered = True
    # Update imported references, only directory routing, never checker logic.
    for name, module in tuple(sys.modules.items()):
        if name.startswith('stowage.') and getattr(module, 'params_dir', None) is base:
            module.params_dir = directory
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(SHIPS / 'vessel-cache')
    from stowage.checker import stability_tables, stability_liquid, stability_liquid_tables, stability_sounding
    cache = SHIPS / 'physics-cache'
    for module in (stability_liquid, stability_liquid_tables, stability_sounding):
        module.CACHE_DIRECTORY = cache
    stability_tables.CACHE_DIR = cache
    for function in (stability_tables.load_table_geometry, stability_tables.cached_geometry, stability_tables.migrate_legacy):
        function.__kwdefaults__['cache_dir'] = cache

def freeze():
    """Audit public request/manifest metadata; never descend into sealed data."""
    specs = [dict(ship=name, seed=245100000 + i*100 + rep, fill=fill,
                  mass=mass, multiplier={'low': .65, 'high': 1.}[mass], split='train', region='medium')
             for i,name in enumerate(NAMES) for rep in range(2)
             for fill in (60,70,80) for mass in ('low','high')]
    specs += [dict(ship='synthetic_8000', seed=245100200, fill=fill, mass=mass, multiplier={'low': .65, 'high': 1.}[mass], split='train', region='full', diagnostic=True) for fill in (60,70,80) for mass in ('low','high')]
    banned = ('testv3','test-v3','testv2','test-v2','test-v2.1','v21','v2.1','valsplit','c6-maps','unseen','runtime','.git','deps','runs','test-')
    seeds=set(); scanned=[]
    def collect(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k=='seed' and type(v) is int: seeds.add(v)
                elif isinstance(v,(dict,list)): collect(v)
        elif isinstance(value,list):
            for v in value: collect(v)
    for directory, dirs, files in os.walk(ROOT / 'experiments/mvp'):
        dirs[:] = [d for d in dirs if not any(b in d.lower() for b in banned)
                   and Path(directory)/d != HERE]
        for file in files:
            low=file.lower()
            if not low.endswith('.json') or not any(w in low for w in ('request','cohort','manifest','split')): continue
            if any(b in low for b in ('testv','test-v','unseen','small-v21')): continue
            path=Path(directory)/file
            if path.stat().st_size>20_000_000: continue
            try: collect(json.loads(path.read_text()))
            except (ValueError,UnicodeError): continue
            scanned.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
    assert not seeds.intersection(s['seed'] for s in specs), 'seed collision'
    result=dict(specs=specs, planned_maps=30, independent_parents=5,
                known_seed_count=len(seeds), seed_overlap=0, metadata=scanned,
                audit_limit='All discovered nonsealed request/cohort/manifest/split JSON metadata <=20 MB. Sealed directories pruned without opening; no claim of checking hidden sealed root seeds.',
                specs_sha256=hashlib.sha256(json.dumps(specs,sort_keys=True).encode()).hexdigest())
    out=MAPS/'requests.json'
    if out.exists(): assert json.loads(out.read_text())['specs']==specs
    else: save(out,result)
    for name in ('feeder_500','feeder_800','feeder_1700'):
        dest=SHIPS/f'{name}.toml'
        if not dest.exists(): dest.symlink_to((RUNTIME/'params/fleet'/f'{name}.toml').resolve())
    print(json.dumps(dict(event='frozen',maps=30,metadata=len(scanned),seed_overlap=0)),flush=True)

def build_parameters():
    import numpy  # Keep the pinned numerical dependency before adding Shapely.
    sys.path.insert(0,str(SHIPS/'deps'))
    sys.path.insert(0,str(SHIPS/'generator'))
    import generate
    from kcs_fit import finalize
    supplement=tomllib.loads((RUNTIME/'params/fleet/feeder_1700.toml').read_text())
    for name,spec in zip(NAMES,SPECS):
        path=SHIPS/f'{name}.toml'
        if path.exists(): continue
        started=time.monotonic()
        ship=finalize(generate.build(*spec))
        ship.update(id=name,name=f'Synthetic XL {spec[0]} TEU',imdg_ship_type='closed_holds')
        ship['hull']['hull_offsets_ref']=f'params/fleet/{name}.toml#hull_form'
        ship['assumption']='APPROXIMATION: Synthetic XL KCS-scaled research design; no real ship or loading manual claim. See SHIPS.md.'
        ship['operating_limits'].update(min_trim_m=-.01*spec[2],bilge_keel_area_m2=0.)
        for key in ('min_trim_m','bilge_keel_area_m2'):
            ship['operating_limits']['provenance'][key]=dict(assumption='APPROXIMATION: Symmetric trim cap 1% Lpp; no bilge keel.')
        ship['tank_pairs']=[dict(id='HEEL',port_tank='HEEL-P',starboard_tank='HEEL-S',role='anti_heeling')]
        generate.write_toml(ship,path)
        save(SHIPS/f'{name}.generation.json',dict(cells=len(ship['cells']),hold_cells=ship['layout']['kcs_fit_audit']['hold_cells'],
             hydrostatics=ship['derived_hydrostatics'],load_line=ship['load_line'],elapsed_s=time.monotonic()-started,
             peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,parameter_sha256=sha(path)))
        print(json.dumps(dict(event='ship_parameters',ship=name,cells=len(ship['cells']),seconds=time.monotonic()-started)),flush=True)

def design_conditions(vessel, params):
    """Same C1/C2/C3 physical recipe builder with explicit XL synthetic labels."""
    from stowage.vessel import fleet_manual as manual
    name=params['id']
    # Synthetic choices made before any planner run, no passing-status filter.
    manual._RECIPES[name]={'C1':('DB+HEEL+FPT0.50',None),'C2':('DB+HEEL',None),'C3':('DB+HEEL+FPT0.50',None)}
    return manual.standard_manual_conditions(vessel,params)

def build_vessels():
    install_runtime()
    import stowage.vessel.fleet_lashing_design as design
    original=design.standard_manual_conditions
    design.standard_manual_conditions=lambda vessel,params: design_conditions(vessel,params) if params['id'] in NAMES else original(vessel,params)
    from stowage.vessel.fleet import load_vessel
    for name in NAMES:
        if os.environ.get('C6_XL_ONLY') not in (None,name): continue
        path=SHIPS/f'{name}.vessel.json'
        if path.exists(): continue
        started=time.monotonic(); vessel=load_vessel(name)
        path.write_text(vessel.model_dump_json(indent=2))
        save(SHIPS/f'{name}.assembly.json',dict(seconds=time.monotonic()-started,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             parameter_sha256=sha(SHIPS/f'{name}.toml'),vessel_sha256=sha(path),max_gm_lashing_m=vessel.hull.max_gm_lashing_m))
        print(json.dumps(dict(event='assembled',ship=name,seconds=time.monotonic()-started)),flush=True)

def validate_departure():
    """Use exactly the fleet manual test's fixture and full voyage checker."""
    install_runtime()
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.checker.full import check
    from stowage.vessel.fleet import load_vessel,load_ship_params
    spec=importlib.util.spec_from_file_location('xl_fleet_manual_test_helper',ROOT/'tests/vessel/test_fleet_manual_restore.py')
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    results=[]
    for name in NAMES:
        if os.environ.get('C6_XL_ONLY') not in (None,name): continue
        vessel=load_vessel(name)
        for recipe in design_conditions(vessel,load_ship_params(name)):
            helper._physical_recipe(vessel,recipe)
            instance=helper._manual_instance(vessel,recipe)
            plan=MovePlan(id=instance.id+'.manual',instance_id=instance.id,call_index=instance.call_index,
                ballast=BallastCondition(source='planner',tank_masses_kg={tank.id:recipe['departure_tank_masses_kg'][tank.id] for tank in vessel.tanks if tank.adjustable}))
            started=time.monotonic();report=check(instance,plan)
            physical={key:part for key,part in report.parts.items() if key in ('stability','strength') or key.endswith((':stability',':strength'))}
            evaluated=all(part.ran and not part.error for part in report.parts.values())
            physically_adequate=bool(physical) and all(part.ran and not part.error and not any(v.severity=='hard' for v in part.violations) for part in physical.values())
            path=SHIPS/f'{name}.{recipe["condition"]}.departure-check.json'
            save(path,dict(ship=name,condition=recipe['condition'],seconds=time.monotonic()-started,
                 full_report=report.model_dump(mode='json'),boxes=recipe['boxes'],
                 peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
            results.append(dict(ship=name,condition=recipe['condition'],passed=report.passed,
                                numerical_evaluation_complete=evaluated,physical_stability_and_strength_passed=physically_adequate,
                                planning_admitted=evaluated and physically_adequate,
                                report=str(path.relative_to(ROOT))))
            print(json.dumps(dict(event='departure_validation',**results[-1])),flush=True)
    save(SHIPS/'departure-validation.json',dict(conditions=results,all_evaluated=all(row['numerical_evaluation_complete'] for row in results),
         admission_by_ship={name:any(row['ship']==name and row['planning_admitted'] for row in results) for name in NAMES}))

def generate_maps():
    install_runtime()
    from stowage.generate import background_acceptance
    background_s=float(os.environ.get('C6_XL_BACKGROUND_S','10'))
    background_acceptance.evaluate_background.__kwdefaults__['time_limit_s']=background_s
    # Whole embedded hulls are rebuilt from private parameter bytes. Reusing a
    # ship's numerical tables is physical model preparation, not a warm plan.
    import stowage.vessel.fleet_lashing_design as design
    original=design.standard_manual_conditions
    design.standard_manual_conditions=lambda vessel,params: design_conditions(vessel,params) if params['id'] in NAMES else original(vessel,params)
    spec=importlib.util.spec_from_file_location('xl_original_builder',ROOT/'experiments/mvp/corpus_tools/build.py')
    build=importlib.util.module_from_spec(spec);sys.modules[spec.name]=build;spec.loader.exec_module(build)
    build.ROOT=RUNTIME
    spec=importlib.util.spec_from_file_location('xl_original_fillsweep',SHIPS/'generator/fillsweep_derive.py')
    fill=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fill;spec.loader.exec_module(fill)
    from stowage.io import load_instance,save_instance
    from stowage.frozen import current_provenance,require_clean
    from stowage.model.instance import Instance
    from stowage.rules.catalogue import load_catalogue
    requests=all_requests()
    admitted=build.source_snapshot()
    for ship,seed,region in dict.fromkeys((s['ship'],s['seed'],s['region']) for s in requests['specs']):
        if os.environ.get('C6_XL_ONLY') not in (None,ship): continue
        if os.environ.get('C6_XL_REGION') not in (None,region): continue
        parent_spec=dict(split='train',rung=region,vessel=ship,generator='random_yard',seed=seed,
                         port='singapore_like' if ship=='synthetic_8000' else 'laem_chabang_like',noise=.5,n_boxes=None,dense_20ft=False,
                         tiny_dg_enabled=True,max_attempts=100,booking_slack=0.,booking_mass_fraction=None)
        if region=='full' and ship=='synthetic_8000':
            # The generic full rung has a2000-box legacy diagnostic ceiling.
            # Use the same builder's physical export policy quantity instead.
            from stowage.vessel.fleet import load_vessel
            from stowage.generate.random_export import full_export_policy
            from stowage.params import load_param_file
            parent_spec['n_boxes']=full_export_policy(load_vessel(ship),load_param_file('cargo','feeder_export')).target_count
        parent_root=MAPS/('parents' if background_s==10 else f'parents-d58-{background_s:g}')
        checkpoint=os.environ.get('C6_XL_CHECKPOINT')=='1'
        if checkpoint:
            if region!='full': raise ValueError('Checkpoint mode is full-region only; medium D58 remains mandatory')
            parent_root=MAPS/'checkpoint-parents'
        evidence=MAPS/f'{ship}-{region}-{seed}.parent-result.json'
        if evidence.exists():
            old=json.loads(evidence.read_text())
            if old.get('status')=='failed': save(MAPS/'attempts'/f'{ship}-{region}-{seed}.previous-{time.time_ns()}.json',old)
        print(json.dumps(dict(event='parent_start',spec=parent_spec,background_s=background_s)),flush=True)
        checkpoint_instance=None
        if checkpoint:
            from stowage.generate import random_yard,instances_v2_random
            class Captured(BaseException):
                def __init__(self,instance): self.instance=instance
            def first_candidate(instance): raise Captured(instance)
            def pending_screen(*args,**kwargs): return []
            original_completion=random_yard.completion_failures
            original_screen=instances_v2_random.screen_load_list
            random_yard.completion_failures=first_candidate
            instances_v2_random.screen_load_list=pending_screen
        started=time.monotonic()
        try:
            if checkpoint and build.shard_path(parent_spec,parent_root).exists():
                checkpoint_instance=load_instance(build.shard_path(parent_spec,parent_root))
                result=dict(status='uncertified_checkpoint_resumed',path=str(build.shard_path(parent_spec,parent_root)))
            else: result=build.generate_one((parent_spec,str(parent_root),admitted))
        except BaseException as error:
            if not checkpoint or not isinstance(error,Captured): raise
            checkpoint_instance=error.instance
            path=build.shard_path(parent_spec,parent_root);path.parent.mkdir(parents=True,exist_ok=True)
            save_instance(checkpoint_instance,path)
            result=dict(status='uncertified_checkpoint',path=str(path),seconds=time.monotonic()-started,
                        c1_screen='not_run',producer_accepted=False,checkpoint='First deterministic cargo/yard candidate before necessary C1 screens; no feasibility/outcome selection')
            save(path.with_suffix('.checkpoint-audit.json'),dict(result=result,config=parent_spec,source_files=admitted,
                 derivation_code_sha256=sha(__file__),instance_sha256=sha(path),original_producer_acceptance=False))
        finally:
            if checkpoint:
                random_yard.completion_failures=original_completion
                instances_v2_random.screen_load_list=original_screen
        result['background_finisher_budget_s']=background_s
        save(MAPS/f'{ship}-{region}-{seed}.parent-result.json',result)
        print(json.dumps(dict(event='parent',ship=ship,seed=seed,region=region,**result)),flush=True)
        rows=[]
        if result['status']=='failed':
            rows=[dict(**request,generation_status='failed',error=result.get('error'),n_boxes=None)
                  for request in requests['specs'] if request['ship']==ship and request['seed']==seed and request['region']==region]
            save(MAPS/f'{ship}-{region}-{seed}.rows.json',dict(maps=rows));publish();continue
        parent_path=build.shard_path(parent_spec,parent_root)
        if checkpoint_instance is None: audit,instance=build.validate_shard(parent_path,current_sources=admitted)
        else: instance=checkpoint_instance; audit=dict(background_checks=0,accepted=False)
        parent=instance.model_dump(mode='json')
        wanted=set(parent['load_list']);cargo=[b for b in parent['containers'] if b['id'] in wanted]
        order=fill.nested_order(cargo,seed);capacity=len(parent['vessel']['cells'])
        for request in (s for s in requests['specs'] if s['ship']==ship and s['seed']==seed and s['region']==region):
            selected=fill.nearest_prefix(order,request['fill']/100*capacity)
            physical=sum(fill.footprint(box) for box in selected)
            row=dict(**request,n_boxes=len(selected),physical_cell_demand=physical,physical_cell_capacity=capacity,realised_fill=physical/capacity)
            if abs(physical/capacity-request['fill']/100)>.025:
                row.update(generation_status='failed',error='Parent cannot supply requested physical fill within 2.5pp')
                rows.append(row);continue
            identity=f"XL.{ship}.{region}.f{request['fill']}.{request['mass']}.s{seed}"
            payload=fill.derive_payload(parent,selected,identity,request['multiplier'],load_catalogue().weight_classes)
            payload['id']=identity
            payload['provenance']=current_provenance(seed=seed,created_by='experiment.c6-xl.TRAIN').merge_nested(instance,parent_ids=[instance.id]).model_dump(mode='json')
            child=Instance.model_validate(payload);require_clean(child,what='XL derivative')
            assert child.vessel==instance.vessel and child.onboard==instance.onboard
            path=MAPS/'train'/ship/region/f'f{request["fill"]}-{request["mass"]}-s{seed}.json'
            path.parent.mkdir(parents=True,exist_ok=True);save_instance(child,path);assert load_instance(path)==child
            row.update(input=str(path.relative_to(ROOT)),sha256=sha(path),generation_status='uncertified' if checkpoint else 'accepted',parent_input=str(parent_path.relative_to(ROOT)),parent_sha256=sha(parent_path),
                       parent_producer_accepted=not checkpoint,producer_c1_screen='not_run' if checkpoint else 'passed',
                       parent_background_gate_passed=(True if audit.get('background_checks',0)>0 and audit.get('background_last',{}).get('accepted') else None),
                       parent_background_checks=audit.get('background_checks',0),private_ship_parameter_sha256=sha(SHIPS/f'{ship}.toml'),
                       derivation_code_sha256=sha(__file__),plan_checker_passed=None)
            rows.append(row)
            print(json.dumps(dict(event='map',**row)),flush=True)
        save(MAPS/f'{ship}-{region}-{seed}.rows.json',dict(maps=rows))
        publish()
    publish()

def all_requests():
    requests=json.loads((MAPS/'requests.json').read_text())
    for path in sorted(MAPS.glob('amendment-*.json')):
        requests['specs'].extend(json.loads(path.read_text()).get('specs',[]))
    return requests

def validate_loaded_reference():
    """Exact full checker on the builder's loaded whole-ship D48 witness.

    This is ship preparation, not an assignment plan or solver warm start.
    """
    install_runtime()
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.checker.full import check
    from stowage.generate.background_acceptance import background_instance
    from stowage.vessel.fleet_conditions import loaded_reference_witness_vessel
    tiers=tomllib.loads((ROOT/'experiments/mvp/frontier/lab/c5/leader/tiers.toml').read_text())['tiers']
    byrule={rule:tier for tier,record in tiers.items() for rule in record['rules']}
    names=(os.environ['C6_XL_ONLY'],) if os.environ.get('C6_XL_ONLY') else NAMES
    for name in names:
        paths=[path for base in ('parents','checkpoint-parents') for path in (MAPS/base/'train/full'/name).glob('random_yard/*.json')
               if path.with_suffix('.audit.json').exists() or path.with_suffix('.checkpoint-audit.json').exists()]
        if not paths: raise ValueError('No accepted full-region parent for '+name)
        parent=load_instance(paths[0]);wanted=set(parent.load_list)
        mass=sum(box.gross_mass_kg for box in parent.containers if box.id in wanted)
        vessel=loaded_reference_witness_vessel(parent.vessel,mass)
        if vessel is None: raise ValueError('Builder did not record a loaded reference')
        instance=background_instance(parent).model_copy(update={'vessel':vessel})
        plan=MovePlan(id=instance.id+'.loaded-reference',instance_id=instance.id,call_index=instance.call_index,
             ballast=BallastCondition(source='planner',tank_masses_kg={t.id:instance.onboard.tank_masses_kg.get(t.id,0.) for t in vessel.tanks if t.adjustable}))
        started=time.monotonic();report=check(instance,plan)
        finisher=None
        if os.environ.get('C6_XL_REFERENCE_REPAIR_S') and not report.passed:
            save(SHIPS/f'{name}.loaded-reference-initial-report.json',report.model_dump(mode='json'))
            from stowage.solver.ballast import _finish_ballast
            repair_s=float(os.environ['C6_XL_REFERENCE_REPAIR_S'])
            print(json.dumps(dict(event='reference_ballast_repair',ship=name,budget_s=repair_s)),flush=True)
            # Unchanged legacy finisher implementation, in this one numerical
            # process under the independent whole-job watchdog, no spawn.
            outcome=_finish_ballast(instance,plan.model_copy(update={'ballast':None}),
                time_limit_s=repair_s,seed=0,_deadline=time.monotonic()+repair_s)
            finisher=outcome.model_dump(mode='json')
            if outcome.plan is not None:
                plan=outcome.plan;report=check(instance,plan)
        bad=[v.model_dump(mode='json') for v in report.violations if byrule.get(v.rule_id,'T0') in ('T0','T1')]
        evaluated=all(part.ran and not part.error for part in report.parts.values())
        record=dict(ship=name,parent=str(paths[0].relative_to(ROOT)),reference_type='D48 loaded whole-ship physical representative; no planner input',
              seconds=time.monotonic()-started,full_checker_passed=report.passed,numerical_evaluation_complete=evaluated,
              t0_t1_violations=bad,planning_admitted=evaluated and not bad,full_report=report.model_dump(mode='json'),
              finisher=finisher,reference_witness_plan=plan.model_dump(mode='json'),
              peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        save(SHIPS/f'{name}.loaded-reference-validation.json',record)
        print(json.dumps({key:value for key,value in record.items() if key not in ('full_report','t0_t1_violations','finisher','reference_witness_plan')}),flush=True)

def publish():
    rows=[]
    for path in sorted(MAPS.glob('*.rows.json')): rows.extend(json.loads(path.read_text())['maps'])
    # Backfill explicit failures from interrupted older generator invocations.
    requests=all_requests()
    for path in MAPS.glob('*.parent-result.json'):
        result=json.loads(path.read_text())
        if result['status']!='failed': continue
        for request in requests['specs']:
            key=f"{request['ship']}-{request['region']}-{request['seed']}.parent-result.json"
            if path.name==key and not any(all(row.get(k)==request[k] for k in ('ship','region','seed','fill','mass')) for row in rows):
                rows.append(dict(**request,generation_status='failed',n_boxes=None,error=result.get('error')))
    save(MAPS/'cohort.json',dict(planned=len(requests['specs']),maps=rows,counts=dict(Counter(row['generation_status'] for row in rows))))
    (MAPS/'xl-primary.txt').write_text(''.join(row['input']+'\n' for row in rows if row['generation_status'] in ('accepted','uncertified') and not row.get('diagnostic')))
    (MAPS/'xl-full-diagnostic.txt').write_text(''.join(row['input']+'\n' for row in rows if row['generation_status'] in ('accepted','uncertified') and row.get('diagnostic')))

def admit_ship():
    """Freeze qualified experimental inputs; a witness breach is never a pass."""
    import math
    install_runtime()
    from stowage.io import load_instance
    from stowage.frozen import require_clean
    name=os.environ['C6_XL_ONLY']
    certificate_path=SHIPS/f'{name}.loaded-reference-validation.json'
    certificate=json.loads(certificate_path.read_text());report=certificate['full_report']
    stability=[part for key,part in report['parts'].items() if key=='stability' or key.endswith(':stability')]
    excluded_loading_geometry={'iscode.loading.draft','iscode.loading.trim'}
    stability_passed=bool(stability) and all(part['ran'] and not part.get('error') and not any(
         v.get('severity','hard')=='hard' and v['rule_id'] not in excluded_loading_geometry for v in part['violations']) for part in stability)
    evaluated=all(part['ran'] and not part.get('error') for part in report['parts'].values())
    raw=tomllib.loads((SHIPS/f'{name}.toml').read_text())
    diagnostics=raw['derived_hydrostatics']
    assembled=json.loads((SHIPS/f'{name}.vessel.json').read_text())
    design_row=next(row for row in assembled['hull']['hydrostatics'] if abs(row['draught_m']-raw['hull']['design_draught_m'])<1e-8)
    hydro_valid=(0<diagnostics['block_coefficient']<1 and math.isfinite(diagnostics['displacement_kg'])
                 and diagnostics['displacement_kg']>raw['hull']['lightship_mass_kg']
                 and math.isclose(design_row['displacement_kg'],diagnostics['displacement_kg'],rel_tol=1e-6)
                 and math.isclose(design_row['lcb_m'],diagnostics['lcb_m'],rel_tol=1e-7))
    qualification=dict(ship=name,hydrostatics_valid=hydro_valid,all_parts_evaluated=evaluated,
        hydrostatic_crosscheck=dict(method='Independent generator nx96/nz128 versus production assembly grid; audit only, no checker criterion changed',
            displacement_relative_tolerance=1e-6,lcb_relative_tolerance=1e-7,
            displacement_difference_kg=design_row['displacement_kg']-diagnostics['displacement_kg'],
            lcb_difference_m=design_row['lcb_m']-diagnostics['lcb_m']),
        departure_stability_passed=stability_passed,reference_full_checker_passed=report['passed'],
        exact_reference_breaches=[dict(rule=v['rule_id'],measured=v.get('measured'),limit=v.get('limit'),message=v.get('message')) for v in report['violations']],
        experimental_planning_admitted=hydro_valid and evaluated and stability_passed,
        interpretation='Hydrostatics/departure stability checked and checker can evaluate every part. Reference draft/trim breaches remain explicit. Only a future unchanged full-checker pass counts as a solver pass.',
        certificate=str(certificate_path.relative_to(ROOT)),certificate_sha256=sha(certificate_path))
    save(SHIPS/f'{name}.admission.json',qualification)
    if not qualification['experimental_planning_admitted']: raise ValueError('Ship not qualified for experiment')
    cohort=json.loads((MAPS/'cohort.json').read_text())
    rows=[row for row in cohort['maps'] if row['ship']==name and row['generation_status'] in ('accepted','uncertified')]
    assert rows,'No maps available'
    verified=[]
    instances={}
    for row in rows:
        path=ROOT/row['input'];assert sha(path)==row['sha256'];instance=load_instance(path)
        require_clean(instance,what='XL frozen experimental input')
        parent=load_instance(ROOT/row['parent_input'])
        assert instance.vessel==parent.vessel and instance.onboard==parent.onboard and instance.yard==parent.yard
        wanted=set(instance.load_list);boxes=[b for b in instance.containers if b.id in wanted]
        demand=sum(1 if b.length_ft==20 else 2 for b in boxes)
        assert len(boxes)==row['n_boxes'] and demand==row['physical_cell_demand']
        assert abs(demand/len(instance.vessel.cells)-row['fill']/100)<=.025
        instances[(row['seed'],row['region'],row['fill'],row['mass'])]=instance
        verified.append(dict(input=row['input'],sha256=sha(path),n_boxes=len(boxes)))
    for row in rows:
        key=(row['seed'],row['region'],row['fill'])
        twins=[instances[(*key,mass)] for mass in ('low','high')]
        assert twins[0].load_list==twins[1].load_list and twins[0].yard==twins[1].yard
        boxes=[{box.id:box for box in twin.containers} for twin in twins]
        for identifier in twins[0].load_list:
            low,high=boxes[0][identifier],boxes[1][identifier]
            assert math.isclose(low.gross_mass_kg,max(low.tare_kg,.65*high.gross_mass_kg),abs_tol=1e-6)
            assert low.model_dump(exclude={'gross_mass_kg','weight_class','category'})==high.model_dump(exclude={'gross_mass_kg','weight_class','category'})
    for seed,region in {(row['seed'],row['region']) for row in rows}:
        previous=set()
        for fill in (60,70,80):
            instance=instances.get((seed,region,fill,'high'))
            if instance is None: continue
            assert previous<=set(instance.load_list);previous=set(instance.load_list)
    list_path=MAPS/f'admitted-{name}.txt'
    list_path.write_text(''.join(row['input']+'\n' for row in rows))
    immutable=dict(ship=name,maps=rows,physical_validation=qualification,list=str(list_path.relative_to(ROOT)),
        list_sha256=sha(list_path),registration_code_sha256=sha(__file__),
        raw_parameters_sha256=sha(SHIPS/f'{name}.toml'),vessel_json_sha256=sha(SHIPS/f'{name}.vessel.json'),
        verification=dict(map_hashes_schema_provenance_roundtrip_lineage_fill_and_mass_twins=True,maps=verified),
        cold='No assignment or previous solver ballast is included; physical witness is preparation evidence only.')
    save(MAPS/f'admitted-{name}.json',immutable)
    print(json.dumps(dict(event='admitted',ship=name,maps=len(rows),box_counts=sorted(set(r['n_boxes'] for r in rows)),list=str(list_path.relative_to(ROOT)),qualification=qualification)),flush=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=('freeze','ships','vessels','validate','maps','publish','validate-reference','admit'))
    parser.add_argument('--ship',choices=NAMES)
    parser.add_argument('--region',choices=('medium','full'))
    parser.add_argument('--background-s',type=float,choices=(10.,120.,300.),default=10.)
    parser.add_argument('--checkpoint',action='store_true')
    parser.add_argument('--reference-repair-s',type=float,choices=(120.,300.))
    args=parser.parse_args()
    if args.ship: os.environ['C6_XL_ONLY']=args.ship
    if args.region: os.environ['C6_XL_REGION']=args.region
    os.environ['C6_XL_BACKGROUND_S']=str(args.background_s)
    if args.checkpoint: os.environ['C6_XL_CHECKPOINT']='1'
    if args.reference_repair_s: os.environ['C6_XL_REFERENCE_REPAIR_S']=str(args.reference_repair_s)
    print(json.dumps(dict(event='owned_process',pid=os.getpid(),action=args.action)),flush=True)
    save(SHIPS/f'owned-{args.action}-{os.getpid()}.json',dict(pid=os.getpid(),action=args.action,started=time.time(),single_numerical_thread=True))
    import threading
    process_started=time.monotonic()
    def memory_guard():
        while True:
            peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            status='running'
            if peak >= 56_000_000_000: status='memory_limit'
            if time.monotonic()-process_started>=float(os.environ.get('C6_XL_HARD_S','1200')): status='timeout'
            if (SHIPS/f'stop-{os.getpid()}.json').exists(): status='requested_stop'
            save(SHIPS/f'monitor-{os.getpid()}.json',dict(pid=os.getpid(),peak_rss_bytes=peak,
                 elapsed_s=time.monotonic()-process_started,status=status))
            if status!='running': os._exit(137 if status=='memory_limit' else 124)
            time.sleep(.1)
    threading.Thread(target=memory_guard,daemon=True).start()
    def stack_sampler():
        import traceback
        while True:
            time.sleep(60)
            try:
                snapshots=['Thread '+str(ident)+'\n'+''.join(traceback.format_stack(frame)) for ident,frame in sys._current_frames().items()]
                (SHIPS/f'stacks-{os.getpid()}.txt').write_text('\n\n'.join(snapshots))
            except Exception: pass
    threading.Thread(target=stack_sampler,daemon=True).start()
    if args.action=='freeze': freeze()
    elif args.action=='ships': build_parameters()
    elif args.action=='vessels': build_vessels()
    elif args.action=='validate': validate_departure()
    elif args.action=='publish': publish()
    elif args.action=='validate-reference': validate_loaded_reference()
    elif args.action=='admit': admit_ship()
    else: generate_maps()

if __name__=='__main__': main()
