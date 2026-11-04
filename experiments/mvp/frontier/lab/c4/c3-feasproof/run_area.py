"""Serial, explicitly non-certifying finite-angle area sweep of every window.

A complete coarse pose cover is retained. It can be refined by the spatial
and knot APIs; no coarse numerical result may tighten a legal-stow window.
"""
from pathlib import Path
from datetime import datetime,timezone
import os,sys,json,time,math,hashlib
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[5]
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key]='1'
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(HERE/'cache')
sys.dont_write_bytecode=True
sys.path[:0]=[str(HERE/'vendor'),str(HERE),str(ROOT/'src')]
from stowage.io import load_instance
import audit,joint_model
from area_bound import AreaOracle,build_area_envelope
from native_union import NativeGeometryUnion,NativeLiquidUnion
from run_windows import atomic_json,load_physics,safe_json


def main():
    started=time.monotonic(); print('area sweep pid',os.getpid(),flush=True)
    sourcepaths=[Path(__file__),HERE/'area_bound.py',HERE/'table_bound.py',HERE/'native_union.py',
        HERE/'joint_model.py',HERE/'audit.py',HERE/'run_windows.py',HERE/'_legacy/physics.py',HERE/'_legacy/rod_bound.py']
    sourcehash={str(p.relative_to(ROOT)):audit.digest(p) for p in sourcepaths}
    path=HERE/'windows.json'; windows=json.loads(path.read_text())
    if not windows.get('complete'):raise ValueError('joint window sweep incomplete')
    input_windows_sha256=audit.digest(path)
    controls_sha256=audit.digest(HERE/'controls.json')
    controls=json.loads((HERE/'controls.json').read_text())['controls']
    physics=load_physics(); outputs={}; control_checks=[]
    for identifier,row in windows['maps'].items():
        begin=time.monotonic();instance=load_instance(ROOT/row['input'])
        phase_results={}
        for phase in joint_model.voyage_contexts(instance):
            name=phase['name']; win=row['phase_windows'][name]
            tanks={tid:[lo*phase['tank_factors'][tid],hi*phase['tank_factors'][tid]]
                   for tid,(lo,hi) in row['tanks']['shared_departure_mass_kg'].items()}
            oracle=AreaOracle(phase['context'])
            hull=NativeGeometryUnion(oracle,max_cells=16)
            liquid=NativeLiquidUnion(oracle,tuple(win['mass_kg']),tanks,max_cells=16)
            result=build_area_envelope(phase['context'],mass_kg=tuple(win['mass_kg']),
                kg_m=tuple(win['physical_kg_m']),lcg_m=tuple(win['lcg_m']),tank_masses_kg=tanks,
                oracle=oracle,integral_provider=hull,correction_lower_provider=liquid,
                max_pose_boxes=8,max_depth=0,offset_contraction_steps=0,
                heel_intervals=[(0.,math.nextafter(math.pi/2,math.inf))],
                draught_m=win['mean_draught_m'],trim_m=win['upright_trim_m'])
            result['checker_outward']=False
            result['infeasibility_proof_eligible']=False
            result['whole_heel_range_covered']=True
            result['meaning']='candidate upper enclosure; uniform numerical direct-fallback error remains uncertified'
            result['native_hull_table']=hull.identity
            identities={}
            for tank in liquid.tanks:
                table=tank['table']
                if table is None:continue
                digest=hashlib.sha256()
                for array in (table.heels,table.trims,table.fills,table.moments):
                    digest.update(str(array.shape).encode());digest.update(array.tobytes())
                digest.update(repr((table.capacity,table.prismatic)).encode())
                identities[tank['id']]=digest.hexdigest()
            result['native_liquid_table_contents_sha256']=identities
            try:
                required=physics.second_check_envelope(phase['context'],
                    draft_lower=win['mean_draught_m'][0],draft_upper=win['mean_draught_m'][1],
                    trim_lower=win['upright_trim_m'][0],trim_upper=win['upright_trim_m'][1])
            except Exception as error:
                required=dict(active=None,error=str(error))
            result['required_area_envelope']=required
            upper=result['area_upper_mrad']
            result['candidate_upper_exceeds_required_lower']=(None if not math.isfinite(upper)
                or required.get('required_total_area_lower_mrad') is None else upper>=required['required_total_area_lower_mrad'])
            phase_results[name]=result
            # A non-certified cap is never installed in the strict window.
            win['area_upper_mrad']=None
            win['required_area_lower_mrad']=None
            win['area_relaxation']=dict(candidate_area_upper_mrad=upper,
                candidate_required_lower_mrad=required.get('required_total_area_lower_mrad'),
                checker_outward=False,physical_area_cap_installed=False,
                whole_free_trim_domain=result['full_free_trim_domain'],
                whole_heel_range_covered=True,
                unresolved_pose_boxes=sum(s['surviving_boxes'] for s in result['slab_bounds']))
            for control in controls:
                if control['instance_id']!=identifier:continue
                observation=control['phases'][name]
                check=audit.audit_area_enclosure(upper,observation['measured_positive_area_mrad'])
                control_checks.append(dict(control_id=control['control_id'],phase=name,**check,
                    independent_proof=False))
        output=dict(instance_id=identifier,input=row['input'],input_sha256=row['input_sha256'],
            phases=phase_results,wall_s=time.monotonic()-begin,verdict='unknown' if row['verdict']=='unknown' else row['verdict'],
            physical_proof=False,experimental_sources=sourcehash)
        target=HERE/'area_results'/f'{identifier}.json';atomic_json(target,safe_json(output))
        row['legal_witness_targets']=[dict(control_id=c['control_id'],cargo=c['cargo'],
            phases={p:{k:o[k] for k in ('kg_physical_m','draft_mean_m','trim_m','gm_lashing_m','tank_masses_kg','measured_positive_area_mrad','required_positive_area_mrad')} for p,o in c['phases'].items()})
            for c in controls if c['instance_id']==identifier]
        row['area_results']=dict(path=str(target.relative_to(ROOT)),sha256=audit.digest(target),wall_s=output['wall_s'])
        outputs[identifier]=dict(wall_s=output['wall_s'],phase_area_upper_mrad={p:r['area_upper_mrad'] for p,r in phase_results.items()})
        print(json.dumps(dict(instance_id=identifier,area_wall_s=output['wall_s'],candidate_area_caps=outputs[identifier]['phase_area_upper_mrad'])),flush=True)
    checked={c['control_id']:c for c in controls}
    for row in windows['maps'].values():
        for projection in row['control_projections']:
            projection['window_audit']=audit.audit_window_containment(row,checked[projection['control_id']])
    windows['area_summary']=dict(maps_processed=len(outputs),phases_processed=3*len(outputs),
        control_phase_checks=control_checks,controls_enclosed=sum(c['encloses'] for c in control_checks),
        wall_s=time.monotonic()-started,pid=os.getpid(),checker_infeasibility_proofs=0,
        input_windows_sha256=input_windows_sha256,controls_sha256=controls_sha256,
        sources=sourcehash,sources_unchanged=sourcehash=={str(p.relative_to(ROOT)):audit.digest(p) for p in sourcepaths},
        candidate_cap_installed_in_strict_windows=False,
        conclusion='Unknown: complete coarse spatial cover remains loose; direct numerical fallback is not uniformly certified.')
    atomic_json(path,safe_json(windows))
    atomic_json(HERE/'area_summary.json',safe_json(windows['area_summary']))
    print(json.dumps({k:v for k,v in windows['area_summary'].items() if k!='control_phase_checks'}),flush=True)

if __name__=='__main__':main()
