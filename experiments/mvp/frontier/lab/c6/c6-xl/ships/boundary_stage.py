"""One preregistered285m supported-envelope design; no further design search."""
import argparse,importlib.util,inspect,json,os,resource,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
NAME='synthetic_boundary285'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2));tmp.replace(p)
def parameters(build,helpers):
    import numpy
    private=HERE/'ships/boundary-generator';private.mkdir(exist_ok=True)
    scale=285/330
    for filename in ('generate.py','design_tanks.py','kcs_fit.py','freeboard.py'):
        text=(HERE/'ships/generator'/filename).read_text()
        replacements=({'8000:28000000.':f'8000:{28000000*scale!r}','8000:151.8':f'8000:{151.8*scale!r}'} if filename=='generate.py' else
            {'8000:(825.,660.)':f'8000:({825*scale!r},{660*scale!r})','8000:23000.':f'8000:{23000*scale!r}','8000:4900.':f'8000:{4900*scale!r}'} if filename=='design_tanks.py' else {})
        for a,b in replacements.items():assert a in text;(None);text=text.replace(a,b)
        path=private/filename
        if path.exists():assert path.read_text()==text
        else:path.write_text(text)
    sys.path.insert(0,str(HERE/'ships/deps'));sys.path.insert(0,str(private))
    import generate
    from kcs_fit import finalize
    path=HERE/'ships/synthetic_boundary285_prototype.toml'
    if not path.exists():
        started=time.monotonic();ship=finalize(generate.build(8000,346*scale,285.,32.2,26.,13.,18,12,8,9,round(900*scale),'bangkokmax-1700.html'))
        ship['id']='synthetic_boundary285_prototype';ship['name']='Synthetic285m boundary research design'
        ship['hull']['hull_offsets_ref']='params/fleet/synthetic_boundary285_prototype.toml#hull_form'
        ship['imdg_ship_type']='closed_holds';ship['tank_pairs']=[dict(id='HEEL',port_tank='HEEL-P',starboard_tank='HEEL-S',role='anti_heeling')]
        ship['operating_limits'].update(min_trim_m=-2.85,bilge_keel_area_m2=0.)
        for key in ('min_trim_m','bilge_keel_area_m2'):ship['operating_limits']['provenance'][key]=dict(assumption='APPROXIMATION: Symmetric trim cap1%Lpp; no bilge keel.')
        ship['assumption']='APPROXIMATION: Synthetic285m supported-boundary design; achieved capacity label replaces8000 construction template key; no real ship claim.'
        for key in ('loa_m','lpp_m','beam_m','depth_m','scantling_draught_m','lightship_mass_kg','lightship_lcg_m','lightship_vcg_m'):
            ship['hull']['provenance'][key]=dict(assumption='APPROXIMATION: Fixed285m supported-envelope design; same32.2m beam,26m depth,13m draught as rejected330m prototype; lightship/tank volumes scale285/330, longitudinal CG scales with length, vertical CG14m retained.',confidence='L')
        generate.write_toml(ship,path)
        write(HERE/'ships'/f'{NAME}.generation.json',dict(seconds=time.monotonic()-started,prototype_cells=len(ship['cells']),hydrostatics=ship['derived_hydrostatics'],load_line=ship['load_line'],peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,parameter_sha256=build.sha(path),scale=scale))
    # TOML root scalars must precede tables. The generic writer preserves
    # insertion order, so appending metadata after finalize otherwise nests it.
    import tomllib
    prototype=tomllib.loads(path.read_text())
    prototype.get('tank_design',{}).pop('imdg_ship_type',None)
    prototype.pop('imdg_ship_type',None)
    prototype=dict(imdg_ship_type='closed_holds',**prototype)
    generate.write_toml(prototype,path)
    parsed=tomllib.loads(path.read_text())
    assert parsed['imdg_ship_type']=='closed_holds' and 'imdg_ship_type' not in parsed.get('tank_design',{})
    source=inspect.getsource(helpers.corrected_parameters)
    source=source.replace('name.removesuffix("_cert")',"'synthetic_boundary285_prototype'")
    source=source.replace("ship['id']=name;ship['name']+=' (paired ER-clear geometry)'","ship['nominal_teu']=len(ship['cells']);ship['id']=name;ship['name']+=' (paired ER-clear geometry)'")
    source=source.replace("assert len(ship['cells'])=={'synthetic_3600_cert':3712,'synthetic_8000_cert':8016}[name]","assert 5500<len(ship['cells'])<8000")
    namespace=dict(helpers.__dict__);exec(source,namespace);namespace['corrected_parameters'](build,NAME)
    helpers.geometry_tests(build,NAME)
def policy_diagnostics(build,certified=False):
    from stowage.io import load_instance,save_instance
    from stowage.model.instance import Instance
    from stowage.frozen import current_provenance,require_clean
    from stowage.rules.catalogue import load_catalogue
    fill=load('boundary_policy_derivation',HERE/'ships/generator/fillsweep_derive.py')
    prereg=json.loads((HERE/'maps/boundary-preregistration.json').read_text());rows=[]
    for request in prereg['policy_target_diagnostics']:
        seed=request['seed'];path=build.MAPS/('parents/train/full' if certified else 'checkpoint-parents/train/full')/NAME/'random_yard'/f'seed-{seed}.json'
        if not path.exists():
            rows.append(dict(**request,fill=None,n_boxes=None,generation_status='failed',error='No accepted producer parent'));continue
        parent=load_instance(path);payload=parent.model_dump(mode='json');wanted=set(parent.load_list)
        cargo=[box for box in payload['containers'] if box['id'] in wanted]
        identity=f"{'CERT' if certified else 'UNCERT'}.BOUNDARY.POLICY.{NAME}.{request['mass']}.s{seed}"
        derived=fill.derive_payload(payload,cargo,identity,request['multiplier'],load_catalogue().weight_classes)
        derived['id']=identity
        derived['provenance']=current_provenance(seed=seed,created_by='experiment.c6-xl.TRAIN.policy-diagnostic').merge_nested(parent,parent_ids=[parent.id]).model_dump(mode='json')
        child=Instance.model_validate(derived);require_clean(child,what='boundary policy diagnostic')
        assert child.vessel==parent.vessel and child.yard==parent.yard and child.onboard==parent.onboard
        target=build.MAPS/'train'/NAME/'full'/f'policy-{request["mass"]}-s{seed}.json';target.parent.mkdir(parents=True,exist_ok=True)
        save_instance(child,target);assert load_instance(target)==child
        demand=sum(fill.footprint(box) for box in cargo);capacity=len(parent.vessel.cells)
        rows.append(dict(**request,fill=100*demand/capacity,n_boxes=len(cargo),physical_cell_demand=demand,physical_cell_capacity=capacity,
             realised_fill=demand/capacity,input=str(target.relative_to(build.ROOT)),sha256=build.sha(target),generation_status='accepted' if certified else 'uncertified',
             parent_input=str(path.relative_to(build.ROOT)),parent_sha256=build.sha(path),parent_producer_accepted=certified,producer_c1_screen='passed' if certified else 'not_run',
             parent_background_gate_passed=None,parent_background_checks=0,private_ship_parameter_sha256=build.sha(HERE/'ships'/f'{NAME}.toml'),
             derivation_code_sha256=build.sha(__file__),plan_checker_passed=None))
    write(build.MAPS/'policy-diagnostics.rows.json',dict(maps=rows));build.publish()
    cohort=build.MAPS/'cohort.json';record=json.loads(cohort.read_text());record['planned']=16;record['primary_planned']=12;record['policy_diagnostic_planned']=4;write(cohort,record)
    print(json.dumps(dict(event='policy_diagnostics',maps=len(rows),counts=sorted({r['n_boxes'] for r in rows if r['n_boxes'] is not None}),actual_fills=sorted({r['realised_fill'] for r in rows if 'realised_fill' in r}))),flush=True)

def worker(args):
    build=load('xl_boundary_build',HERE/'build_ships_maps.py');helper=load('xl_final_helpers',HERE/'ships/next_stage.py');build.NAMES=(NAME,)
    action={'assemble':'vessels','certify':'maps','checkpoint':'maps','validate':'validate-reference','admit':'admit'}[args.task]
    if args.task=='assemble':
        original=build.build_vessels
        def assemble():parameters(build,helper);original()
        build.build_vessels=assemble
    else:
        root=HERE/('maps/boundary-certified' if args.task=='certify' or args.task=='admit' and (HERE/'maps/boundary-certified/cohort.json').exists() else 'maps/boundary-checkpoint');root.mkdir(parents=True,exist_ok=True)
        prereg=json.loads((HERE/'maps/boundary-preregistration.json').read_text())
        target=root/'requests.json'
        if not target.exists():write(target,dict(specs=prereg['specs'],protocol=('Original producer with every C1 screen retained; tested exactgeometrycache+Booleanprojection; CERT.BOUNDARY namespace.' if args.task=='certify' else 'First deterministic original builder candidate, C1 explicitly notrun, UNCERT.BOUNDARY namespace, unchanged full checker sole plan gate.'),preregistration_sha256=build.sha(HERE/'maps/boundary-preregistration.json')))
        build.MAPS=root
        source=inspect.getsource(build.generate_maps)
        assert source.count("ship=='synthetic_8000'")==2
        source=source.replace("ship=='synthetic_8000'","ship=='synthetic_boundary285'")
        source=source.replace('identity=f"XL.','identity=f"CERT.BOUNDARY.' if args.task=='certify' else 'identity=f"UNCERT.BOUNDARY.')
        exec(source,build.__dict__)
    if args.task in ('checkpoint','certify'):
        if args.task=='certify':
            cache=load('boundary_exact_geometry_cache',HERE/'prep/geometry_cache.py');cache.install()
        e9=load('xl_boundary_e9fix',HERE/'ships/generator/e9fix.py');e9.install()
        fast=load('xl_boundary_projection',HERE/'solver/fast_engine.py');fast.THRESHOLD=0;fast.install()
    if args.task=='admit':
        # Keep historical D48 and originalrecipe reports byte-for-byte. The
        # primary reference is fixed C1 of the fully validated declaredmanual.
        admission_source=inspect.getsource(build.admit_ship)
        assert admission_source.count('.loaded-reference-validation.json')==1
        admission_source=admission_source.replace('.loaded-reference-validation.json','.primary-manual-reference.json')
        exec(admission_source,build.__dict__)
        original_admit=build.admit_ship
        def gated_admit():
            design_path=HERE/'ships'/f'{NAME}.design-conditions.json'
            design=json.loads(design_path.read_text())
            declared_path=HERE/'ships'/f'{NAME}.primary-declared-manual-validation.json'
            if not declared_path.exists():declared_path=HERE/'ships'/f'{NAME}.declared-manual-validation.json'
            declared=json.loads(declared_path.read_text()) if declared_path.exists() else None
            original_full=design['all_three_conditions_checked'] and design['all_parts_evaluated'] and design['all_reference_full_checker_passed']
            repaired_full=bool(declared and declared['all_three_conditions_checked'] and declared['all_parts_evaluated'] and declared['all_declared_full_checker_passed'])
            assert original_full or repaired_full,'All3 original or explicitlydeclared ballast-onlymanualconditions mustpass unchanged fullchecker'
            reference_path=build.ROOT/next(r['report']for r in declared['conditions']if r['condition']=='C1') if repaired_full else HERE/'ships'/f'{NAME}.C1.departure-check.json'
            reference=json.loads(reference_path.read_text())
            write(HERE/'ships'/f'{NAME}.primary-manual-reference.json',dict(ship=NAME,condition='C1',
                reference_type='FixedC1 of declaredballast-onlysyntheticmanual' if repaired_full else 'FixedoriginalfleetC1',
                source_report=str(reference_path.relative_to(build.ROOT)),source_report_sha256=build.sha(reference_path),
                full_report=reference['full_report'],all_three_manual_conditions_full_checker_passed=True,
                original_manual_full_checker_passed=bool(original_full),physical_prep_only_never_solver_warm=True))
            original_admit()
            admission_path=HERE/'ships'/f'{NAME}.admission.json';admission=json.loads(admission_path.read_text())
            admission.update(all_design_conditions_checked=True,all_design_departure_stability_passed=True,all_declared_manual_full_checker_passed=bool(original_full or repaired_full),original_manual_full_checker_passed=bool(original_full),declared_manual_validation_sha256=build.sha(declared_path) if declared is not None else None,
                design_reference_full_checker_passed=design['all_reference_full_checker_passed'],design_validation_sha256=build.sha(design_path),
                ship_parameter_geometry_validation_passed=json.loads((HERE/'ships'/f'{NAME}.geometry-validation.json').read_text())['passed'])
            write(admission_path,admission)
            manifest_path=build.MAPS/f'admitted-{NAME}.json';manifest=json.loads(manifest_path.read_text());manifest['physical_validation']=admission
            manifest['all_design_conditions_report']=str(design_path.relative_to(build.ROOT));manifest['construction_driver_sha256']=build.sha(__file__);write(manifest_path,manifest)
        build.admit_ship=gated_admit
    sys.argv=[str(HERE/'build_ships_maps.py'),action,'--ship',NAME]
    if args.task in ('checkpoint','certify'):sys.argv+=['--region','full']
    if args.task=='checkpoint':sys.argv+=['--checkpoint']
    if args.task=='validate':sys.argv+=['--reference-repair-s','120']
    build.main()
    if args.task in ('checkpoint','certify'):policy_diagnostics(build,args.task=='certify')
def main():
    p=argparse.ArgumentParser();p.add_argument('task',choices=('assemble','certify','checkpoint','validate','admit'));p.add_argument('--hard-s',type=float,default=600);p.add_argument('--worker',action='store_true');args=p.parse_args()
    if args.worker:return worker(args)
    log=HERE/'ships'/f'boundary-{args.task}.log';started=time.monotonic()
    command=['nice','-n','5',sys.executable,'-B',__file__,args.task,'--hard-s',str(args.hard_s),'--worker']
    with log.open('w') as out:
        child=subprocess.Popen(command,stdout=out,stderr=subprocess.STDOUT,env=dict(os.environ,C6_XL_HARD_S=str(args.hard_s)))
        write(log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=child.pid,hard_s=args.hard_s,command=command))
        while child.poll() is None:
            if time.monotonic()-started>args.hard_s+2:
                write(HERE/'ships'/f'stop-{child.pid}.json',dict(reason='timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    monitor=HERE/'ships'/f'monitor-{child.pid}.json'
    write(log.with_suffix('.execution.json'),dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started,monitor=json.loads(monitor.read_text()) if monitor.exists() else None))
    return child.returncode
if __name__=='__main__':sys.exit(main())
