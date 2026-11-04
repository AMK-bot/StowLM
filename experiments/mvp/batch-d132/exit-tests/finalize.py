"""Seal only completed raw v2 receipts; no numerical computation."""
from pathlib import Path
import json,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
read=lambda p:json.loads(Path(p).read_text())
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
inventory=read(HERE/'inventory.json');v2=HERE/'candidate';base=ROOT/'experiments/mvp/batch-d132/checker/baseline-cold';old=ROOT/'experiments/mvp/batch-d132/checker/candidate'
assert read(v2/'STATUS.json')['complete']
rows=[];timing={}
for case in inventory['cases']:
 key=case['key'];record=read(v2/key/'record.json');payload=(v2/key/'report.json').read_bytes()
 assert record['passed'] and record['full_report_bound'] and record['inputs_immutable'] and record['exact']
 assert payload==(base/key/'report.json').read_bytes()==(old/key/'report.json').read_bytes()
 assert sha(v2/key/'report.json')==record['report_sha256']
 assert sha(case['input_path'])==case['input_sha256'] and sha(case['plan_path'])==case['plan_sha256']
 assert case['split'] in ('train','val') and case['input_sha256'] not in inventory['exclusion']['test_input_hashes']
 rows.append(dict(key=key,report_sha256=record['report_sha256'],input_sha256=case['input_sha256'],plan_sha256=case['plan_sha256'],passed=True,full_bytes_identical=True))
 group=case['rung']+'/'+case['ship'];t=timing.setdefault(group,dict(plans=0,wall_s=0.,cpu_s=0.));t['plans']+=1;t['wall_s']+=record['wall_s'];t['cpu_s']+=record['cpu_s']
status=read(v2/'STATUS.json');samples=read(HERE/'fresh-two-samples/result.json');assert samples['complete'] and samples['source_unchanged'] and len(samples['rows'])==2
processes={}
for directory in HERE.iterdir():
 p=directory/'process.json'
 if p.is_file():
  record=read(p);assert record['reaped'],str(p)
  processes[directory.name]={k:record.get(k) for k in ('owned_pid','reaped','exit_code','wall_s','cpu_s','peak_waited_child_rss_bytes','source_drift')}
ledger=(ROOT/'experiments/fleet/ledger.md').read_text();assert not any(line.startswith('| mvp-construct/D132-exit') for line in ledger.splitlines()),'owned ledger row still active'
proof=read(HERE/'EXIT-QUALIFICATION.json');assert proof['pytest']['total_passed']==138
receipts=['EXIT-QUALIFICATION.json','EXIT-RECEIPT-PINS.json','ACTUAL-NONEXIT-HELPERS.json','support-assets.json','candidate/STATUS.json','candidate/audit-checks-0-100-v2.json','fresh-two-samples/result.json','v2-full-checker100-process/process.json','fresh-two-samples-process/process.json']
result=dict(status='COMPLETE_CPU_QUALIFIED',pytest=proof['pytest'],real_ack=proof['actual_v2_ack'],real_dataset=proof['actual_dataset'],
 full_checker100=dict(plans=100,passed=100,raw_full_report_byte_identical=100,fields_removed=[],baseline=str(base),v1_candidate=str(old),candidate=str(v2),per_case=rows,by_rung_ship=timing,total_wall_s=status['wall_s'],total_cpu_s=status['cpu_s'],canonical_full_json='sort_keys=True,separators=(comma,colon),allow_nan=False,newline; no numbers/provenance/fields stripped'),
 fresh_complete_samples=samples,source_identity=dict(actual_combined_runtime='/private/tmp/stowlm-d132-exit-qualified-runtime-20261030',assembly_v2_candidate='/private/tmp/stowlm-d132-assembly-v2-20261030/candidate',assembly_v2_manifest_sha256=sha(ROOT/'experiments/mvp/batch-d132/assembly/revision2-combined-manifest.json'),
 checked_every_report_source_pins=True,source_unchanged_all_phases=True,whole_vessel_six_models='v1 source-correct6/6 identical guard carried by exact unchanged stowage/params/KCS dependency proof; no relabeled cache keys'),
 processes=processes,all_owned_workers_reaped=True,all_own_ledger_rows_released=True,receipt_sha256={p:sha(HERE/p) for p in receipts},
 timing_limit='Contended sequential audit-enabled qualification. No controlled speedup/end-to-end performance claim; model/GPU smoke not run.',
 open_boundary_requirements=proof['open_boundary_requirements'][:1],attempt_disclosure=proof['attempt_disclosure'],gpu=False,network=False,native_threads=1,hashseed=0)
(HERE/'SUMMARY.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(path=str(HERE/'SUMMARY.json'),sha256=sha(HERE/'SUMMARY.json'),reports100_identical=True,samples=2,pytest_passed=138)))
