from pathlib import Path
import json,hashlib,xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent
read=lambda p:json.loads((HERE/p).read_text())
sha=lambda p:hashlib.sha256((HERE/p).read_bytes()).hexdigest()
def junit(path):
 root=ET.parse(HERE/path).getroot();nodes=list(root.iter('testcase'));return dict(tests=len(nodes),failures=len(list(root.iter('failure'))),errors=len(list(root.iter('error'))),skips=len(list(root.iter('skipped'))),sha256=sha(path))
unit=junit('cpu-regressions-final/tests.xml');pool=junit('deferred-pool-tests/tests.xml')
assert unit['tests']==136 and pool['tests']==2 and not any(unit[k] or pool[k] for k in ('failures','errors','skips'))
ack=read('real-tiny-ack-qualified/result.json');data=read('real-dataset-pool2/result.json')
assert all(ack[k] for k in ('available','full_checker_passed','input_binding_verified','nonce_acknowledged','pre_matches','post_matches','source_unchanged'))
assert data['passed'] and data['source_unchanged'] and len(data['prime_rows'])==2 and all(r['rechecked'] for r in data['prime_rows'])
assert not ack['mlx_loaded'] and not data['mlx_loaded']
receipts={}
for phase in ('cpu-regressions-final','real-tiny-ack-qualified','real-dataset-pool2','deferred-pool-tests'):
 for name in ('result.json','process.json'):
  receipts[f'{phase}/{name}']=sha(f'{phase}/{name}')
 process=read(f'{phase}/process.json');assert process['reaped'] and process['exit_code']==0 and not process['source_drift']
summary=dict(status='EXIT_CPU_QUALIFIED_CHECKER100_PENDING',pytest=dict(total_passed=138,first_run_passed=136,first_run_deselected=2,subsequent_deferred_cases_passed=2,junit=dict(unit=unit,pool=pool)),
 actual_v2_ack=dict(pins=ack['pins'],project_imports=ack['imports'],primary_reads=ack['resource_reads'],nonce=ack['details']['epoch_ack']['request_id'],checker_s=ack['checker_s'],full_report_sha256=ack['full_report_sha256'],source_epoch=ack['source_epoch_sha256']),
 actual_dataset=dict(byte_identical=data['byte_identical'],fresh_replay_prime_rows=2,serial_s=data['results']['1']['wall_s'],pool2_s=data['results']['2']['wall_s'],pool_order_fault_probe=data['pool_order_fault_probe']),
 receipt_sha256=receipts,support_asset_manifest_sha256=sha('support-assets.json'),source_identity_guard='actual v2 module origins and exact hashes, complete src/params/support pins unchanged before/after',
 attempt_disclosure=[dict(path='cpu-regressions',result='0 tests; missing experiment driver dependency',reaped=True),dict(path='cpu-regressions-complete',result='132 passed /4 failed: missing dataset builder/config and smoke watcher in scratch',reaped=True),dict(path='real-tiny-ack',result='operational unavailable watchdog_expired120s; mistakenly nested COW cache layout; corrected scratch only, failed evidence retained',reaped=True)],
 open_boundary_requirements=['Actual host MLX/model audit/DPO smoke is not authorized by this CPU qualification and must run at the admitted fresh-process boundary.','Full100 fresh v2 candidate checker is still running separately; no overall installation approval in this receipt.'],
 resources=dict(native_threads=1,hashseed=0,gpu=False,network=False,nice_attempt='nice -n10 attempted; sandbox denied setpriority; no priority bypass',all_completed_exit_phase_workers_reaped=True))
(HERE/'EXIT-QUALIFICATION.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
print(json.dumps(dict(path=str(HERE/'EXIT-QUALIFICATION.json'),sha256=sha('EXIT-QUALIFICATION.json'),pytest_passed=138)))
