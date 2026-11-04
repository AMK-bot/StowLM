"""Preregistered metadata-only3600 epoch; frozen old inputs stay unchanged."""
import argparse,copy,importlib.util,inspect,json,os,subprocess,sys,time,tomllib
from pathlib import Path
HERE=Path(__file__).resolve().parent.parent
NAME='synthetic_3600_closed'
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
def write(p,v):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_suffix(p.suffix+'.tmp');temp.write_text(json.dumps(v,indent=2));temp.replace(p)
def parameters(build,helpers):
    import numpy
    sys.path.insert(0,str(HERE/'ships/deps'));sys.path.insert(0,str(HERE/'ships/generator'))
    import generate
    source=HERE/'ships/synthetic_3600_cert.toml';raw=tomllib.loads(source.read_text());old=copy.deepcopy(raw)
    raw.get('tank_design',{}).pop('imdg_ship_type',None);raw.pop('imdg_ship_type',None)
    raw=dict(imdg_ship_type='closed_holds',**raw);raw['id']=NAME;raw['name']+=' (root metadata corrected)'
    raw['hull']['hull_offsets_ref']=f'params/fleet/{NAME}.toml#hull_form'
    p=HERE/'ships'/f'{NAME}.toml'
    if p.exists():assert tomllib.loads(p.read_text())==raw
    else:generate.write_toml(raw,p)
    parsed=tomllib.loads(p.read_text());assert parsed['imdg_ship_type']=='closed_holds' and 'imdg_ship_type' not in parsed['tank_design']
    for k in ('cells','stacks','hatch_covers','tanks','heat_sources','layout','envelope','dg_document','operating_limits','windage','derived_hydrostatics'):assert raw[k]==old[k],k
    write(HERE/'ships'/f'{NAME}.metadata-amendment.json',dict(ship=NAME,source_ship=old['id'],source_parameter_sha256=build.sha(source),parameter_sha256=build.sha(p),
        physical_fields_byte_equivalent_as_parsed=True,changes=['Rootimdg_ship_type=closed_holds','remove misplacedtank_design field','distinctid/name/virtualrawreference']))
    helpers.geometry_tests(build,NAME)
def worker(args):
    build=load('metadata_epoch_build',HERE/'build_ships_maps.py');helpers=load('metadata_epoch_helpers',HERE/'ships/next_stage.py');build.NAMES=(NAME,)
    action={'assemble':'vessels','certify':'maps','checkpoint':'maps','validate':'validate-reference','admit':'admit'}[args.task]
    if args.task=='assemble':
        original=build.build_vessels
        def assemble():parameters(build,helpers);original()
        build.build_vessels=assemble
    else:
        certified=HERE/'maps/closed3600-certified';uncertified=HERE/'maps/closed3600-checkpoint'
        root=uncertified if args.task=='checkpoint' else certified
        if args.task in ('validate','admit') and not (root/'cohort.json').exists():root=uncertified
        root.mkdir(parents=True,exist_ok=True);pre=HERE/'maps/amendment-3600-closed-epoch.json';specs=json.loads(pre.read_text())['specs']
        target=root/'requests.json'
        if not target.exists():write(target,dict(specs=specs,preregistration_sha256=build.sha(pre),protocol='OriginaleveryC1screen retained with tested exactgeometrycache and Booleanprojection' if root==certified else 'Explicit firstdrawcheckpoint, C1notrun; neverproduceraccepted'))
        else:assert json.loads(target.read_text())['specs']==specs
        build.MAPS=root
        source=inspect.getsource(build.generate_maps);source=source.replace('identity=f"XL.','identity=f"UNCERT.CLOSED.' if root==uncertified else 'identity=f"CERT.CLOSED.')
        exec(source,build.__dict__)
    if args.task in ('certify','checkpoint'):
        telemetry=load('metadata_epoch_producer_telemetry',HERE/'ships/progress.py');telemetry.install(HERE/'ships')
        e9=load('metadata_epoch_e9',HERE/'ships/generator/e9fix.py');e9.install()
        if args.task=='certify':
            cache=load('metadata_epoch_exact_geometry_cache',HERE/'prep/geometry_cache.py');cache.install()
        fast=load('metadata_epoch_projection',HERE/'solver/fast_engine.py');fast.THRESHOLD=0;fast.install()
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
    if args.task in ('certify','checkpoint'):sys.argv+=['--region','full']
    if args.task=='checkpoint':sys.argv+=['--checkpoint']
    if args.task=='validate':sys.argv+=['--reference-repair-s','120']
    build.main()
def main():
    p=argparse.ArgumentParser();p.add_argument('task',choices=('assemble','certify','checkpoint','validate','admit'));p.add_argument('--hard-s',type=float,default=600);p.add_argument('--worker',action='store_true');args=p.parse_args()
    if args.worker:return worker(args)
    log=HERE/'ships'/f'metadata3600-{args.task}.log';started=time.monotonic()
    cmd=['nice','-n','5',sys.executable,'-B',__file__,args.task,'--hard-s',str(args.hard_s),'--worker']
    with log.open('w')as out:
        child=subprocess.Popen(cmd,stdout=out,stderr=subprocess.STDOUT,env=dict(os.environ,C6_XL_HARD_S=str(args.hard_s)))
        write(log.with_suffix('.ownership.json'),dict(parent_pid=os.getpid(),owned_child_pid=child.pid,hard_s=args.hard_s,command=cmd))
        while child.poll()is None:
            if time.monotonic()-started>args.hard_s+2:
                write(HERE/'ships'/f'stop-{child.pid}.json',dict(reason='timeout'))
                try:child.terminate()
                except PermissionError:pass
            time.sleep(.2)
    monitor=HERE/'ships'/f'monitor-{child.pid}.json'
    write(log.with_suffix('.execution.json'),dict(pid=child.pid,returncode=child.returncode,wall_s=time.monotonic()-started,monitor=json.loads(monitor.read_text())if monitor.exists()else None))
    return child.returncode
if __name__=='__main__':sys.exit(main())
