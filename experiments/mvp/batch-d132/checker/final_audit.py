"""No-physics final evidence check; never installs production code."""
from __future__ import annotations
import json
from pathlib import Path
from runtime import FENCE,LIVE_ROOT,read,save,sha,source_pins,verify_runtime


def main():
    inv=read(FENCE/'inventory.json')
    threeway=[]
    for c in inv['cases']:
        assert c['split'] in ('train','val') and c['corpus_path'].startswith(c['split']+'/')
        assert sha(c['input_path'])==c['input_sha256'] and sha(c['plan_path'])==c['plan_sha256']
        payloads={arm:(FENCE/arm/c['key']/'report.json').read_bytes()
                  for arm in ('baseline','baseline-cold','candidate')}
        threeway.append({'key':c['key'],'exact':len(set(payloads.values()))==1,
                         'report_file_sha256':{arm:sha(FENCE/arm/c['key']/'report.json') for arm in payloads}})
    processes=[]
    retired=read(FENCE/'FAILED-HARNESS-ATTEMPTS.json') if (FENCE/'FAILED-HARNESS-ATTEMPTS.json').exists() else {}
    for p in sorted(FENCE.glob('process-*/process.json')):
        r=read(p)
        relative=str(p.relative_to(FENCE))
        assert r['reaped'] and r['exit_code']==(retired[relative]['expected_exit_code'] if relative in retired else 0) and not r['source_drift'],p
        assert r['peak_waited_child_rss_bytes'] <=4*1024**3,p
        # Six-case admission incident is recorded separately, never relabelled admitted.
        processes.append({'receipt':str(p.relative_to(FENCE)),**{k:r.get(k) for k in (
            'owned_pid','wall_s','cpu_s','peak_waited_child_rss_bytes','reaped','exit_code','native_environment')},
            'retired_harness_failure':retired.get(relative)})
    runtime={name:verify_runtime(Path(read(FENCE/'runtime-pins'/f'{name}.json')['root']))
             for name in ('baseline','candidate')}
    source_correct=read(FENCE/'COMPARISON.json')
    warm=read(FENCE/'COMPARISON-WARM.json')
    assert source_correct['baseline_arm']=='baseline-cold' and source_correct['passed']
    assert source_correct['exact']==100 and source_correct['paired']==100
    assert warm['exact']==100 and warm['paired']==100
    assert sum(r['model_dump_json_exact'] for r in warm['whole_vessel_identities'])==5
    main_models=source_correct['whole_vessel_identities']
    assert len(main_models)==6 and all(r['model_dump_json_exact'] for r in main_models)
    assert all(r['exact'] for r in threeway)
    for c in read(FENCE/'WARM-CACHE-EVIDENCE.json'):
        assert sha(c['preserved_copy'])==c['file_sha256']
    audits=[]
    for arm in ('baseline','baseline-cold','candidate'):
        for p in (FENCE/arm).glob('audit-*.json'):
            r=read(p);assert r['source_unchanged'],p
            audits.append({'path':str(p.relative_to(FENCE)),
                'runtime_epoch':r['runtime_epoch'],'blocked_writes':r['blocked_writes']})
    live=source_pins(LIVE_ROOT)
    old=runtime['baseline']['files']
    live_diff=sorted(k for k in live.keys()|old.keys() if live.get(k)!=old.get(k))
    result={'passed':True,'threeway_reports_exact':sum(r['exact'] for r in threeway),'expected':100,
        'source_correct_whole_vessel_models_exact':6,'warm_whole_vessel_models_exact':5,
        'warm_whole_vessel_model_guard_passed':False,
        'warm_guard_cause':'Only feeder_500 hull embedded C2 manual-audit evidence stale; preserved exact bytes/key; unchanged baseline cold builder equals candidate',
        'all_report_fields_retained':True,'production_installed_by_lane':False,
        'processes':processes,'audits':audits,'threeway_reports':threeway,
        'live_source_matches_baseline':not live_diff,'live_source_differences':live_diff,
        'admission_incident':read(FENCE/'ADMISSION-INCIDENT.json'),
        'retired_harness_failures':retired,
        'resource_estimate_receipts':[str(p.relative_to(FENCE)) for p in sorted(FENCE.glob('ledger-*.json'))]}
    save(FENCE/'FINAL-AUDIT.json',result)
    expected_caps={'feeder_500':(3.2838356051426465,'C3'),
                   'feeder_800':(3.9584570964621637,'C2'),
                   'feeder_1700':(5.530108952747626,'C3')}
    gm_rows=[]
    for row in source_correct['actual_gm']['rows']:
        actual=row['audit']['max_gm_lashing_m'];expected,condition=expected_caps[row['ship']]
        assert actual==expected and row['governing']['condition']==condition
        assert row['governing']['phase']=='departure' and len(row['audit']['conditions'])==6
        gm_rows.append({'ship':row['ship'],'actual_cap_m':actual,'expected_cap_m':expected,
            'exact':True,'delta_m':actual-expected,'governing':row['governing'],
            'actual_six_conditions':row['audit']['conditions'],
            'wall_s':row['wall_s'],'cpu_s':row['cpu_s']})
    manual_gm={'format':'d132-manual-gm-v1','passed':True,
        'runtime_epoch':source_correct['candidate_epoch'],
        'manual':{**source_correct['manual40'],'junitxml':'candidate/manual40.xml',
                  'junitxml_sha256':sha(FENCE/'candidate/manual40.xml'),
                  'test_source':read(FENCE/'candidate/manual40-source-pins.json')},
        'gm':{'ships':gm_rows,'actual_conditions':18,'all_caps_exact':True,
              'erratum':'Actual governing C3/C2/C3 departure; historical D126 C3-every-ship assertion was wrong.',
              'raw_receipt':'candidate/actual-six-condition-gm.json',
              'raw_receipt_sha256':sha(FENCE/'candidate/actual-six-condition-gm.json')},
        'source_pins':runtime['candidate'],'processes':[p for p in processes if any(
            mode in p['receipt'] for mode in ('manual','gm'))]}
    save(FENCE/'manual-gm.json',manual_gm)
    env={arm:read(FENCE/arm/'config-checks-0-100.json') for arm in ('baseline-cold','candidate')}
    fields=('python','executable','hashseed','native_threads','singleflight','dependency_versions')
    assert all(env['baseline-cold'][f]==env['candidate'][f] for f in fields)
    summary={'format':'d132-full-checker-qualification-v1','passed':True,
        'full_reports':{'expected':100,'paired':100,'exact':100,'omitted_fields':[],
                        'tolerances_used':False,'allowed_automatic_differences':[],
                        'baseline_arm':'baseline-cold','canonical_full_json_bytes':True,
                        'baseline_passes':source_correct['baseline_passes'],
                        'candidate_passes':source_correct['candidate_passes'],
                        'threeway_bindings':threeway},
        'whole_vessel_models':{'source_correct_exact':6,'expected':6,
                               'entries':source_correct['whole_vessel_identities']},
        'timings':source_correct['timings'],
        'warm_control':{'fresh_full_reports':100,'full_reports_threeway_exact':100,
                        'whole_vessel_exact':5,'whole_vessel_guard_passed':False,
                        'explanation':result['warm_guard_cause'],
                        'cache_evidence':'WARM-CACHE-EVIDENCE.json',
                        'cache_evidence_sha256':sha(FENCE/'WARM-CACHE-EVIDENCE.json'),
                        'decoded_diff':'CANDIDATE500-VESSEL-DIFF.json',
                        'decoded_diff_sha256':sha(FENCE/'CANDIDATE500-VESSEL-DIFF.json')},
        'source_pins':runtime,'library_environment_exact':True,
        'environment':{f:env['candidate'][f] for f in fields},
        'inventory':'inventory.json','inventory_sha256':sha(FENCE/'inventory.json'),
        'manual_gm':'manual-gm.json','manual_gm_sha256':sha(FENCE/'manual-gm.json'),
        'full_comparison':'COMPARISON.json','full_comparison_sha256':sha(FENCE/'COMPARISON.json'),
        'processes':processes,'live_source_matches_baseline':not live_diff,
        'production_installed_by_lane':False,'admission_incident':result['admission_incident']}
    save(FENCE/'summary.json',summary)
    print(json.dumps({k:result[k] for k in ('passed','threeway_reports_exact',
        'source_correct_whole_vessel_models_exact','warm_whole_vessel_models_exact','live_source_matches_baseline')}))


if __name__=='__main__':main()
