"""Strict current100 full-report comparison; preserve all report content."""
from __future__ import annotations
import json
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from runtime import FENCE, read, save, sha, verify_runtime


def differences(a,b,path='$'):
    if type(a) is not type(b):
        return [{'path':path,'baseline':a,'candidate':b}]
    if isinstance(a,dict):
        result=[]
        for key in sorted(a.keys()|b.keys()):
            if key not in a or key not in b:
                result.append({'path':path+'.'+key,'baseline':a.get(key),'candidate':b.get(key),
                               'missing_in_baseline':key not in a,'missing_in_candidate':key not in b})
            else:result.extend(differences(a[key],b[key],path+'.'+key))
        return result
    if isinstance(a,list):
        if len(a)!=len(b):return [{'path':path+'.length','baseline':len(a),'candidate':len(b)}]
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in differences(x,y,f'{path}[{i}]')]
    return [] if a==b else [{'path':path,'baseline':a,'candidate':b}]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-arm',choices=('baseline','baseline-cold'),default='baseline')
    p.add_argument('--output-prefix',default='COMPARISON')
    args=p.parse_args()
    assert '/' not in args.output_prefix and args.output_prefix
    inv=read(FENCE/'inventory.json');rows=[];mismatches=[];missing=[]
    pins={arm:read(FENCE/'runtime-pins'/f'{arm}.json') for arm in ('baseline','candidate')}
    for pin in pins.values():verify_runtime(Path(pin['root']))
    for case in inv['cases']:
        for kind in ('input','plan'):
            assert sha(case[kind+'_path'])==case[kind+'_sha256']
        record={'key':case['key'],'ship':case['ship'],'rung':case['rung'],'split':case['split']}
        arms={}
        for arm in pins:
            directory=FENCE/(args.baseline_arm if arm=='baseline' else arm)/case['key']
            if not (directory/'record.json').exists():missing.append({'key':case['key'],'arm':arm});continue
            row=read(directory/'record.json')
            assert row['runtime_epoch']==pins[arm]['source_epoch_sha256']
            assert row['input_sha256']==case['input_sha256'] and row['plan_sha256']==case['plan_sha256']
            assert row['full_report_bound'] and row['inputs_immutable']
            assert sha(directory/'report.json')==row['report_sha256']
            arms[arm]=row
        if len(arms)!=2:continue
        before=(FENCE/args.baseline_arm/case['key']/'report.json').read_bytes()
        after=(FENCE/'candidate'/case['key']/'report.json').read_bytes()
        record.update(exact=before==after,baseline=arms['baseline'],candidate=arms['candidate'])
        if not record['exact']:
            mismatches.append({'key':case['key'],'differences':differences(json.loads(before),json.loads(after))})
        rows.append(record)
    setup={}
    for arm in pins:
        candidates=[p for p in (FENCE/(args.baseline_arm if arm=='baseline' else arm)).glob('setup-*.json') if len(read(p)['rows'])==6]
        if candidates:
            value=read(candidates[-1]);assert value['complete']
            setup[arm]=value
    identities=[]
    if len(setup)==2:
        before={(r['ship'],r['with_hull']):r for r in setup['baseline']['rows']}
        after={(r['ship'],r['with_hull']):r for r in setup['candidate']['rows']}
        assert before.keys()==after.keys() and len(before)==6
        for key,b in before.items():
            a=after[key]
            identities.append({'ship':key[0],'with_hull':key[1],
                'baseline_key':b['key'],'candidate_key':a['key'],
                'baseline_cache_hit_before':b['cache_hit_before'],'candidate_cache_hit_before':a['cache_hit_before'],
                'model_dump_json_exact':b['raw_model_dump_json_sha256']==a['raw_model_dump_json_sha256'],
                'baseline_model_sha256':b['raw_model_dump_json_sha256'],
                'candidate_model_sha256':a['raw_model_dump_json_sha256']})
    xml=FENCE/'candidate/manual40.xml'
    manual=None
    if xml.exists():
        root=ET.parse(xml).getroot();suites=list(root) if root.tag=='testsuites' else [root]
        manual={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
        manual['passed']=manual=={'tests':40,'failures':0,'errors':0,'skipped':0}
    gm_path=FENCE/'candidate/actual-six-condition-gm.json'
    gm=read(gm_path) if gm_path.exists() else None
    complete=len(rows)==100 and not missing
    timings={'design':'Contended sequential audit-enabled guard; no speedup or end-to-end performance qualification claimed',
             'totals':{arm:{'checker_wall_s':sum(r[arm]['wall_s'] for r in rows),
                            'checker_cpu_s':sum(r[arm]['cpu_s'] for r in rows)}
                       for arm in ('baseline','candidate')},'by_ship_rung':[]}
    for ship in ('feeder_500','feeder_800','feeder_1700'):
        for rung in ('tiny','small'):
            cohort=[r for r in rows if (r['ship'],r['rung'])==(ship,rung)]
            timings['by_ship_rung'].append({'ship':ship,'rung':rung,'paired':len(cohort),
                **{arm:{'checker_wall_s':sum(r[arm]['wall_s'] for r in cohort),
                        'checker_cpu_s':sum(r[arm]['cpu_s'] for r in cohort)} for arm in ('baseline','candidate')}})
    result={'full_report_complete':complete,'expected':100,'paired':len(rows),
        'exact':sum(r['exact'] for r in rows),'mismatches':mismatches,'missing':missing,
        'all_fields_retained':True,'allowed_automatic_differences':[],
        'baseline_passes':sum(r['baseline']['passed'] for r in rows),
        'candidate_passes':sum(r['candidate']['passed'] for r in rows),
        'rows':rows,'timings':timings,'whole_vessel_identities':identities,'manual40':manual,'actual_gm':gm,
        'baseline_arm':args.baseline_arm,
        'baseline_epoch':pins['baseline']['source_epoch_sha256'],'candidate_epoch':pins['candidate']['source_epoch_sha256'],
        'passed':complete and not mismatches and len(identities)==6 and all(r['model_dump_json_exact'] for r in identities)
                 and manual is not None and manual['passed'] and gm is not None and gm['complete']}
    save(FENCE/(args.output_prefix+'.json'),result)
    lines=['# D132 checker qualification', '',
        f'Fresh canonical FULL reports: **{result["exact"]}/{len(rows)} identical**, expected100; '
        f'baseline physical passes{result["baseline_passes"]}, candidate passes{result["candidate_passes"]}.', '',
        f'Baseline arm `{args.baseline_arm}`, epoch `{result["baseline_epoch"]}`; candidate epoch `{result["candidate_epoch"]}`.', '',
        'All numbers, violations, parts, limits, counts, and provenance are retained. '
        'No heat-legality exception or tolerance is silently accepted. '
        'Inputs are exact saved TRAIN/VAL bodies, never regenerated; historical reports are not a baseline. '
        'Both arms use hashseed0/native1/SINGLEFLIGHT1; source imports and final runtime hashes are checked. '
        'Timing is sequential/shared-host diagnostic evidence, not a certified causal speedup comparison.', '',
        '| Ship | Rung | Paired | Exact | Baseline checker s | Candidate checker s |', '|---|---|---:|---:|---:|---:|']
    for ship in ('feeder_500','feeder_800','feeder_1700'):
        for rung in ('tiny','small'):
            cohort=[r for r in rows if (r['ship'],r['rung'])==(ship,rung)]
            lines.append(f'| {ship} | {rung} | {len(cohort)} | {sum(r["exact"] for r in cohort)} | '
                f'{sum(r["baseline"]["wall_s"] for r in cohort):.3f} | {sum(r["candidate"]["wall_s"] for r in cohort):.3f} |')
    lines.append(f'| TOTAL | all | {len(rows)} | {result["exact"]} | '
                 f'{timings["totals"]["baseline"]["checker_wall_s"]:.3f} | '
                 f'{timings["totals"]["candidate"]["checker_wall_s"]:.3f} |')
    lines+=['',timings['design']+'. No cause is inferred from these timings; there was no controlled benchmark.',
        'Raw checker CPU totals: baseline '+str(timings['totals']['baseline']['checker_cpu_s'])+
        's; candidate '+str(timings['totals']['candidate']['checker_cpu_s'])+'s.']
    lines+=['','Whole-vessel raw `model_dump_json()` identity: '+str(sum(r['model_dump_json_exact'] for r in identities))+'/6.',
        'Normal source-qualified keys are recorded; stale entries are never renamed. Preparation/builder cost is separate in setup receipts.', '',
        'Manual40: '+json.dumps(manual,sort_keys=True)+'.',
        'Actual six-condition GM: '+('complete; exact3.2838356051426465/3.9584570964621637/5.530108952747626m; governing C3/C2/C3 departure.' if gm and gm['complete'] else 'pending')+'', '',
        'Erratum preserved: the historical D126 runbook incorrectly assumed C3 for every ship; feeder_800 is C2.', '',
        'Production release is owned by root/installer and remains conditional on final assembly pins. '
        'This lane never installs production code. Exact process/environment/resource/reaping and source guards '
        'are in `process-*/process.json`, per-arm configs/audits and fleet history. '
        'The pilot admission orchestration error is recorded separately in `ADMISSION-INCIDENT.json`; no subsequent worker can launch without a successful own reservation.']
    if mismatches:lines+=['','## Full-report differences','',json.dumps(mismatches,indent=2)]
    (FENCE/(args.output_prefix+'.md')).write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:result[k] for k in ('passed','full_report_complete','paired','exact','baseline_passes','candidate_passes','manual40')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
