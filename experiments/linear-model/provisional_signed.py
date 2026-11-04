"""Read-only interim statistics, explicitly outside current-epoch qualification.

Adds the directional gap of each captured public-checker condition to held-out
unsafe maxima. This triangle inequality is conservative for that measured gap;
it does not measure interpolation gaps for every held-out condition.
"""
import argparse,json
from pathlib import Path
import numpy as np

def peak(x):return float(np.max(x))
def main():
 p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--prefix',default='provisional-eight');a=p.parse_args()
 cases={};summary={};public_gaps={};absolute_rows=[];directional_rows=[]
 for path in sorted(a.directory.glob('*/narrow-*.json')):
  if any(t in path.name for t in ('header','audit')):continue
  d=json.loads(path.read_text())
  if not all(key in d for key in ('ship','level','signed_calibration','signed_heldout')):continue
  name=d['ship']+'/'+d['level'];cal=d['signed_calibration']['signed_errors'];held=d['signed_heldout']['signed_errors']
  c=d.get('public_checker_comparison');gaps={}
  if c:
   for metric in cal['lower_unsafe']:
    exact=c['exact_condition'];public=c['stability_metrics']
    if metric in ('sf_n','bm_nm'):
     core=np.array(exact[metric]);actual=np.array([c['strength_metrics'][f'{metric}@{float(x):.12g}'] for x in d['station_x_m']])
    elif metric in ('draught_aft_m','draught_forward_m'):
     sign=1 if metric=='draught_aft_m' else -1
     core=np.array(exact['draught_m']+sign*.5*exact['trim_m']);actual=np.array(public['draught_aft_m' if sign==1 else 'draught_fwd_m'])
    else:core=np.array(exact[metric]);actual=np.array(public['draught_mean_m' if metric=='draught_m' else metric])
    gaps[metric]={'lower':np.maximum(core-actual,0),'upper':np.maximum(actual-core,0)}
   public_gaps[name]={'original_passed':c['passed'],'production_usage':c['production_usage'],'absolute_differences':c['absolute_differences']}
  comparisons=[]
  for metric in cal['lower_unsafe']:
   for bound in (('lower',) if metric=='gm_m' else ('lower','upper')):
    baseline=np.array(cal[bound+'_unsafe'][metric]);observed=np.array(held[bound+'_unsafe'][metric]);gap=gaps.get(metric,{}).get(bound,np.zeros_like(observed));required=observed+gap
    ratio=np.divide(required,baseline,out=np.zeros_like(required),where=baseline>0)
    zero=(baseline==0)&(required>0)
    row={'metric':metric,'bound':bound,'max_ratio_with_positive_baseline':peak(ratio),'zero_baseline_positive_count':int(np.count_nonzero(zero)),
         'zero_baseline_absolute_required':peak(np.where(zero,required,0)),
         'max_observed_public_directional_gap':peak(gap),
         'absolute_excess_at_scale':{str(f):peak(np.maximum(required-f*baseline,0)) for f in (1.1,1.5,3.5)}}
    comparisons.append(row);key=metric+'/'+bound;acc=summary.setdefault(key,{'max_ratio':0.,'case':None,'zero_baseline_positive_count':0,'zero_baseline_absolute_required':0.,'absolute_excess_at_scale':{'1.1':0.,'1.5':0.,'3.5':0.}})
    if row['max_ratio_with_positive_baseline']>acc['max_ratio']:acc.update(max_ratio=row['max_ratio_with_positive_baseline'],case=name)
    acc['zero_baseline_positive_count']+=row['zero_baseline_positive_count'];acc['zero_baseline_absolute_required']=max(acc['zero_baseline_absolute_required'],row['zero_baseline_absolute_required'])
    for f,value in row['absolute_excess_at_scale'].items():acc['absolute_excess_at_scale'][f]=max(acc['absolute_excess_at_scale'][f],value)
  absolute_rows.append('| '+name+' | '+' | '.join(f"{peak(d['diagnostics']['max'][metric]):.7g} / {peak(d['diagnostics']['p99'][metric]):.7g}" for metric in ('gm_m','trim_m','list_rad','draught_m','sf_n','bm_nm'))+' |')
  for metric in cal['lower_unsafe']:
   for bound in (('lower',) if metric=='gm_m' else ('lower','upper')):
    direction=bound+'_unsafe';conservative=bound+'_conservatism'
    directional_rows.append('| '+name+' | '+metric+'/'+bound+' | '+ ' | '.join(f"{peak(values[which][metric]):.7g} / {peak(values['p99'][which][metric]):.7g}" for values,which in ((cal,direction),(held,direction),(held,conservative)))+' |')
  cases[name]={'original_record_path':str(path),'original_record_sha256':__import__('hashlib').sha256(path.read_bytes()).hexdigest(),
    'virtual_prior_metadata_epoch':d.get('virtual_prior_metadata_epoch',False),
    'family_calibration_cache_key':d['calibration']['family_calibration_cache_key'],
    'family_signed_errors':cal,'physical_signed_errors':held,'absolute_point_max':d['diagnostics']['max'],'absolute_point_p99':d['diagnostics']['p99'],'station_x_m':d['station_x_m'],
    'source_start':d['source_fingerprint_start'],'source_end':d['source_fingerprint_end'],'source_clean':d['source_clean'],
    'gm_unsafe_max':held['lower_unsafe']['gm_m'],'gm_unsafe_p99':held['p99']['lower_unsafe']['gm_m'],
    'gm_conservatism_max':held['lower_conservatism']['gm_m'],'gm_conservatism_p99':held['p99']['lower_conservatism']['gm_m'],
    'absolute_point_gm_max':d['sweep_errors']['gm_m'],'comparisons':comparisons,'list_branches':held['list_branch_counts']}
 out={'status':'PROVISIONAL_NOT_CURRENT_EPOCH_QUALIFICATION','note':f'{len(cases)}-case observations; original version drift and strict public failures preserved. Directional public gaps measured for captured light conditions only; numerical closure floors are NOT implemented by current LP.',
      'cases':cases,'per_metric_direction':summary,'unique_family_calibration_samples':192*len({r['family_calibration_cache_key'] for r in cases.values()}),'physical_heldout_samples':128*len(cases),'public_gaps':public_gaps}
 (a.directory/(a.prefix+'.json')).write_text(json.dumps(out,indent=2)+'\n')
 lines=['# Provisional observations: not current-epoch qualification','',out['note'],'',
 '| Case | GM unsafe max / p99 m | GM conservatism max / p99 m | Absolute point GM max m |','|---|---:|---:|---:|']
 for name,r in cases.items():lines.append(f"| {name} | {r['gm_unsafe_max']:.7g} / {r['gm_unsafe_p99']:.7g} | {r['gm_conservatism_max']:.7g} / {r['gm_conservatism_p99']:.7g} | {r['absolute_point_gm_max']:.7g} |")
 lines+=['','The following ratios include the captured public dispatch gap, added directionally to held-out maxima. Zero-baseline closure errors require a stated absolute reserve; no finite multiplicative factor covers them.','',
 '| Metric / bound | Max positive-baseline ratio | Case | Zero-baseline absolute requirement | Excess at scale 3.5 |','|---|---:|---|---:|---:|']
 for key,r in summary.items():lines.append(f"| {key} | {r['max_ratio']:.7g} | {r['case']} | {r['zero_baseline_absolute_required']:.7g} | {r['absolute_excess_at_scale']['3.5']:.7g} |")
 lines+=['','No universal validated scalar policy is established. A factor 3.5 exceeds the observed positive-baseline ratios but can exhaust trim/list intervals; positive zero-baseline requirements cannot be covered by a multiplier. Proposed empirical caller budgets are 1.1×max(family unsafe, physical unsafe + captured public directional gap), per direction/station. This triangle bound can overstate actual unsafe error when conservative bias absorbs a public gap. It includes tiny closure reserves and the measured public GM gap; these are explicit caller budgets, not numerical floors currently installed by the LP. Using these observations as budgets turns them into calibration data and requires fresh independent validation. Current certificate-epoch qualification remains pending.']
 (a.directory/(a.prefix+'.md')).write_text('\n'.join(lines)+'\n')
 (a.directory/(a.prefix+'-absolute-errors.md')).write_text('\n'.join(['# Pinned historical physical held-out errors; not current qualification','', 'Max / p99; SF/BM largest station statistics. Every station remains in original JSON.','', '| Case | GM m | Trim m | List rad | Draught m | SF N | BM Nm |','|---|---:|---:|---:|---:|---:|---:|',*absolute_rows])+'\n')
 (a.directory/(a.prefix+'-directional-errors.md')).write_text('\n'.join(['# Pinned historical signed affine-solver errors; not current qualification','', 'Max / p99. Family192 total-moment states; physical128 actual-manifest conditions. Populations remain separate.','', '| Case | Metric / bound | Family unsafe | Physical unsafe | Physical conservatism |','|---|---|---:|---:|---:|',*directional_rows])+'\n')
 print(json.dumps({'cases':len(cases),'maximum_ratio':max((r['max_ratio'],key,r['case']) for key,r in summary.items())}))
if __name__=='__main__':main()
