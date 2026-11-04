"""Independent fresh-current gate validation; no solver/checker/model invocation."""
import hashlib,json
from pathlib import Path
import xml.etree.ElementTree as ET
root=Path(__file__).resolve().parent
folder=root/'formal-live-v2-contracts'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
summary=json.loads((folder/'summary.json').read_text())
freeze=json.loads((folder/'runtime-freeze-live.json').read_text())
assert summary['valid'] and summary['final']
assert summary['passed']==summary['completed']==summary['required']==100
assert not summary['errors'] and not summary['active_children'] and not summary['current_source_drift']
assert summary['all_token_checks_bound_to_historical_full_checker']
assert len(summary['source_checks'])==len(summary['children'])==100
assert all(c['reaped'] and c['exit_code']==0 for c in summary['children'])
assert all(c['completed'] for c in summary['controllers'])
for relative,value in freeze['lookahead_runtime_sources'].items():
    assert sha(relative)==value,relative
    assert sha(root/'runtime-live-v2-contracts'/relative)==value,relative
for row in summary['source_checks']:
    assert row['source_exact'] and row['worker_exact']
    assert row['source_epoch_before']==row['source_epoch_after']==freeze['epoch']
    assert not row['mask_mlx_imported'] and not row['mlx_imported']
    actual=json.loads(Path(row['result']).read_text())
    assert actual['contract_passed'] and actual['historical_checker_bound'] and not actual['checker_run']
    for kind in ('instance','plan','checker'):
        pin=actual['job']['case'][kind];assert sha(pin['path'])==pin['sha256']
tests=json.loads((root/'tests-live-transition-meta.json').read_text())
assert tests['exit_code']==0 and tests['source_valid'] and tests['source_before']==tests['source_after']
for relative,value in tests['source_after'].items():assert sha(relative)==value,relative
suites=ET.parse(root/'tests-live-transition.xml').getroot().findall('testsuite')
assert sum(int(s.attrib['tests']) for s in suites)==70
assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
result=dict(verified=True,current_live_contracts=100,current_live_tests=70,epoch=freeze['epoch'],summary_sha256=sha(folder/'summary.json'),freeze_sha256=sha(folder/'runtime-freeze-live.json'),tests_meta_sha256=sha(root/'tests-live-transition-meta.json'),scope='Current-default production-mask/securing/lookahead replay of historical full-checker-passed pinned plans; no new physical acceptance claim.')
(root/'ROOT_LIVE_VERIFICATION.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
