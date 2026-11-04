"""Per-profile independent holdout maxima versus their own D49 reserves."""
from datetime import datetime, timezone
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
SHIPS=('feeder_500','feeder_1000','feeder_1700')

def read(p): return json.loads(p.read_text())
def main():
    bank=read(ROOT.parent.parents[2]/'src/stowage/linear/margin_policy_data.json')
    result={};lines=['# Pre-final requalification: seawater and freshwater','',
        'Independent ordinary/edge maxima include reference transfer; each error is paired with its own direction/station reserve.','',
        '| Profile kg/m³ | Ship | Ordinary | Edge | Quantity | Max error | Reserve at max | Max excess |',
        '|---:|---|---:|---:|---|---:|---:|---:|']
    compact=[]
    logs=[read(p) for p in (ROOT/'compute-logs').glob('*.execution.json')]
    for profile,rho in (('seawater',1025),('freshwater',1000)):
        result[profile]={}
        for ship in SHIPS:
            raw=read(ROOT/profile/'ordinary'/ship/'raw.json')
            key=raw['diagnostics']['response_calibration_identity'];entry=bank['entries'][key]
            necessary=entry['necessary'];qs=[necessary[k] for k in ('holdout_qualification','edge_holdout_qualification')]
            assert all(q['independent_holdout_passed'] for q in qs)
            counts=[q['holdout_samples'] for q in qs];metrics={}
            for side,quantities in qs[0]['holdout_unsafe'].items():
                for metric in quantities:
                    errors=np.maximum.reduce([np.asarray(q['holdout_unsafe'][side][metric]) for q in qs])
                    reserves=[np.asarray(q['holdout_unsafe'][side][metric])-np.asarray(q['max_excess'][side][metric]) for q in qs]
                    np.testing.assert_allclose(reserves[0],reserves[1],rtol=1e-12,atol=1e-12)
                    reserve=reserves[0];excess=errors-reserve;i=int(np.argmax(errors))
                    assert np.all(excess<=0),(profile,ship,side,metric,excess)
                    value=dict(max_error=float(errors.flat[i]),reserve_at_max_error=float(reserve.flat[i]),
                        max_excess=float(np.max(excess)),reserve_min=float(np.min(reserve)),
                        station_at_max_error_m=entry['station_x_m'][i] if errors.ndim else None)
                    metrics[side+'/'+metric]=value
                    lines.append(f"|{rho}|{ship}|{counts[0]}|{counts[1]}|{side}/{metric}|{value['max_error']:.9g}|{value['reserve_at_max_error']:.9g}|{value['max_excess']:.9g}|")
            boundary=necessary['boundary_list']
            for side in ('lower_unsafe','upper_unsafe'):
                reserve=1.1*(boundary['measured_optimistic_kgm'][side]+boundary['reference_transfer_kgm'][side])
                excess=max(boundary['qualification']['max_holdout_excess_kgm'][side],boundary['interior_qualification']['max_holdout_excess_kgm'][side])
                assert excess<=0
                metrics[side+'/list_boundary_kgm']=dict(max_error=reserve+excess,reserve_at_max_error=reserve,max_excess=excess)
                lines.append(f'|{rho}|{ship}|{counts[0]}|{counts[1]}|{side}/list_boundary_kgm|{reserve+excess:.9g}|{reserve:.9g}|{excess:.9g}|')
            own=[x for x in logs if x.get('exit_code')==0 and x['name'].startswith(profile+'-') and (ship in x['name'] or (ship=='feeder_1000' and 'targeted' in x['name'])) and 'replay' not in x['name']]
            begins=[datetime.fromisoformat(x['start_utc']).timestamp() for x in own];ends=[datetime.fromisoformat(x['finished_utc']).timestamp() for x in own]
            rec=dict(density_kg_m3=rho,response_identity=key,ordinary_holdouts=counts[0],edge_holdouts=counts[1],
                boundary_holdouts_per_direction=boundary['qualification']['holdout_samples'],breaches=0,
                metrics=metrics,qualification_wall_s=max(ends)-min(begins),worker_cpu_s=sum(x['cpu_seconds'] for x in own))
            result[profile][ship]=rec
            # Report the tightest reserve fraction and all per-quantity maxima
            # in full tables, preserving physical units.
            ratios=[(v['max_error']/v['reserve_at_max_error'] if v['reserve_at_max_error'] else 0,k,v) for k,v in metrics.items()]
            ratio,quantity,value=max(ratios,key=lambda x:x[0])
            compact.append(dict(profile=profile,ship=ship,ordinary=counts[0],edge=counts[1],tightest_quantity=quantity,
                max_error=value['max_error'],reserve=value['reserve_at_max_error'],max_fraction=ratio,wall_s=rec['qualification_wall_s'],breaches=0))
    (ROOT/'results.json').write_text(json.dumps(result,indent=2)+'\n');(ROOT/'results.md').write_text('\n'.join(lines)+'\n')
    (ROOT/'compact-results.json').write_text(json.dumps(compact,indent=2)+'\n');print(json.dumps(compact))
if __name__=='__main__': main()
