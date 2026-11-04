"""Separate reusable synthetic-family calibration from physical held-out errors.

The core's signed residuals describe the actual affine solver, including its
maximum-FSM reserve and conservative inventory-height arms. Calibration belongs
to a global geometry family; it is NOT 192 physical manifest samples per case.
Each lower/upper safety limit remains a separate direction. Percentiles include
zero observations, rather than conditioning on errors of one sign.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import numpy as np
from stowage.linear.cache import source_fingerprint
HERE=Path(__file__).resolve().parent

def peak(value):return float(np.max(value))
def cell(stat,direction,metric):
 return f"{peak(stat[direction][metric]):.7g} / {peak(stat['p99'][direction][metric]):.7g}"

def main():
 p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--allow-partial',action='store_true');a=p.parse_args()
 helper=runpy.run_path(str(HERE/'narrow.py'));audit=runpy.run_path(str(HERE/'public_reference_audit.py'));source=source_fingerprint();cases={};families={};rejected=[];margin_comparisons=[]
 gm=['# Method-1 affine-solver GM: unsafe error and conservatism','',
     'Unsafe means overestimating the GM lower-bound safety margin; conservatism means underestimating it.',
     'The affine solver includes the maximum-FSM reserve and conservative known-inventory height arms.',
     'Family calibration uses synthetic total-moment states across a global domain, reused across instances.',
     'Held-out samples use the actual fixed physical manifest. These sample populations are not interchangeable.',
     'Tail cells are max / p99 in metres; absolute point estimates remain separate.','',
     '| Case | Family GM unsafe | Held-out GM unsafe | Held-out GM conservatism | Held-out absolute point GM max | Instance LinearErrors GM (incl. analytic guard) |',
     '|---|---:|---:|---:|---:|---:|']
 absolute=['# Physical held-out absolute point-prediction errors','',
           '128 actual-manifest held-out conditions per case; max / p99. SF/BM retain every station in signed-results.json.',
           'These are point estimates, separate from the affine solver safety-margin errors.','',
           '| Case | GM m | Trim m | List rad | Draught m | SF N | BM Nm |',
           '|---|---:|---:|---:|---:|---:|---:|']
 rows=['# Directional affine-solver safety-margin errors','',
       'Positive residual is unsafe for a lower bound and conservative for an upper bound; negative is the reverse.',
       'Each limit is separate. SF/BM cells show the largest per-station max and p99, which can occur at different stations.',
       'Family calibration is synthetic/global; held-out conditions use this physical manifest. No uniform bound is claimed.','',
       '| Case | Metric / bound | Family unsafe max / p99 | Held-out unsafe max / p99 | Held-out conservatism max / p99 |',
       '|---|---|---:|---:|---:|']
 usage=['# Method-1 reference provenance','',
        '| Case | Family calibration / physical held-out count | Table-query sum (held-out) | Direct-query sum (held-out) | Current manifest? |',
        '|---|---:|---:|---:|---|']
 for ship in ('feeder_500','feeder_1000','feeder_1700'):
  for level in ('light','medium','full'):
   name=ship+'/'+level;path=a.directory/f'narrow-{ship}-{level}.json'
   if not path.exists():path=a.directory/ship/path.name
   if not path.exists():rejected.append(name+':missing');continue
   d=json.loads(path.read_text())
   valid=(d.get('source_clean') and d.get('source_fingerprint_end')==source
      and d.get('stability_free_surface_method')=='1' and d.get('reference_table_inputs_clean')
      and d.get('input_hashes_end')==helper['input_snapshot'](ship)
      and d.get('liquid_table_inputs_end')==helper['liquid_table_snapshot'](ship)
      and d.get('departure_manifest_identity')==helper['current_manifest_identity'](ship,level)
      and (level!='light' or d.get('public_checker_comparison',{}).get('passed') or audit['valid_proof'](path,d))
      and d.get('signed_metrics_schema_version')==1 and d.get('signed_calibration') and d.get('signed_heldout'))
   if not valid:rejected.append(name+':stale_or_missing_signed');continue
   cal=d['signed_calibration'];held=d['signed_heldout'];cs=cal['signed_errors'];hs=held['signed_errors']
   assert cal['domain']=='geometry_family_synthetic_total_moment_states'
   assert held['domain']=='fixed_manifest_physical_loading_conditions'
   assert cal['samples']==d['calibration'].get('family_calibration_samples',d['calibration'].get('samples')) and held['samples']==d['diagnostics']['samples']
   if not a.allow_partial:assert cal['samples']==192 and held['samples']==128
   if d.get('requires_liquid_tables'):assert held['liquid_usage'].get('table',0)>0
   family_id=cal.get('family_cache_key') or ship+':'+hashlib.sha256(json.dumps(cs,sort_keys=True).encode()).hexdigest()
   families.setdefault(family_id,{'ship':ship,'samples':cal['samples'],'signed_errors':cs,
       'coverage':cal.get('coverage'),'family_metadata':{k:v for k,v in d['calibration'].items()
           if any(token in k for token in ('domain','scope','family_key','family_cache','family_band'))},'cases':[]})['cases'].append(name)
   gm.append('| '+name+' | '+' | '.join((cell(cs,'lower_unsafe','gm_m'),cell(hs,'lower_unsafe','gm_m'),
       cell(hs,'lower_conservatism','gm_m'),f"{d['sweep_errors']['gm_m']:.7g}",f"{d['model_errors']['gm_m']:.7g}"))+' |')
   known={direction:{metric:np.maximum(cs[direction][metric],hs[direction][metric]).tolist()
          for metric in hs[direction]} for direction in ('lower_unsafe','upper_unsafe','lower_conservatism','upper_conservatism')}
   d['known_family_and_heldout_directional_maxima']=known
   absolute.append('| '+name+' | '+' | '.join(f"{peak(d['diagnostics']['max'][metric]):.7g} / {peak(d['diagnostics']['p99'][metric]):.7g}" for metric in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'))+' |')
   for metric in hs['lower_unsafe']:
    directions=('lower',) if metric=='gm_m' else ('upper',) if metric=='draught_aft_m' else ('lower','upper')
    for bound in directions:
     family=np.asarray(cs[bound+'_unsafe'][metric],dtype=float);observed=np.asarray(hs[bound+'_unsafe'][metric],dtype=float)
     positive=family>0;ratios=np.divide(observed,family,out=np.zeros_like(observed),where=positive)
     margin_comparisons.append({'case':name,'metric':metric,'bound':bound,
         'max_positive_baseline_ratio':float(np.max(ratios)),
         'positive_heldout_with_zero_family':int(np.count_nonzero((~positive)&(observed>0))),
         'absolute_excess_over_scaled_family':{str(factor):float(np.max(np.maximum(observed-factor*family,0))) for factor in (1.,1.1,1.25,1.5,2.)},
         'family_list_branch_counts':cs.get('list_branch_counts') if metric=='list_rad' else None,
         'heldout_list_branch_counts':hs.get('list_branch_counts') if metric=='list_rad' else None})
     rows.append('| '+name+' | '+metric+'/'+bound+' | '+' | '.join((cell(cs,bound+'_unsafe',metric),
       cell(hs,bound+'_unsafe',metric),cell(hs,bound+'_conservatism',metric)))+' |')
   counter=held['liquid_usage'];table=counter.get('table',0);direct=counter.get('direct',0)
   usage.append(f"| {name} | {cal['samples']} / {held['samples']} | {table:g} | {direct:g} | {d['liquid_table_inputs_end'].get('metadata_matches_current',False)} |")
   cases[name]=d
 payload={'source_fingerprint':source,'reference_method':1,'geometry_families':families,
          'unique_family_calibration_samples':sum(f['samples'] for f in families.values()),
          'signed_unsafe_margin_comparisons':margin_comparisons,
          'physical_heldout_samples':sum(d['signed_heldout']['samples'] for d in cases.values()),'cases':cases}
 (a.directory/'signed-results.json').write_text(json.dumps(payload,indent=2)+'\n')
 for name,lines in (('signed-gm.md',gm),('signed-directional-errors.md',rows),('absolute-heldout-errors.md',absolute),('signed-reference-usage.md',usage)):
  (a.directory/name).write_text('\n'.join(lines)+'\n')
 print(json.dumps({'qualified_cases':len(cases),'unique_family_samples':payload['unique_family_calibration_samples'],
                   'physical_heldout_samples':payload['physical_heldout_samples'],'rejected':rejected,'source_fingerprint':source}))
 if rejected and not a.allow_partial:raise SystemExit(1)

if __name__=='__main__':main()
