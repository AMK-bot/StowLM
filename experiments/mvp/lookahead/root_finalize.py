"""Independent final evidence verification; never reruns or repairs a result."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parent

def read(name):return json.loads((ROOT/name).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    summary,audit,freeze=map(read,('summary-v2-combined.json','audit-v2-combined.json','runtime-freeze-v2.json'))
    assert summary['recorded_rollouts']==summary['required_rollouts']==1400
    assert summary['contracts_passed']==summary['contracts_completed']==100
    assert not summary['contract_failures']
    for group in summary['groups']:
        assert group['attempted']==group['expected'] and not group['missing']
        assert group['errors']==group['cpu_limit']==0
    cases=summary['formal_cases'];assert len(cases)==35
    assert len({r['case'] for r in cases})==35
    assert all(r['completed'] and r['source_valid'] and not r['error'] for r in cases)
    assert audit['final'] and not audit['active_owned_children']
    assert audit['all_accepted_exact'] and not audit['archive_hash_mismatches']
    assert audit['all_raw_children_reaped_exit0']
    assert all(r['reaped'] and r['exit_code']==0 for r in audit['raw_owned_children'])
    assert len(audit['excluded_original_attempts'])==14 and audit['excluded_raw_rollouts']==80
    assert all(r['source_exact'] and r['worker_exact'] and r['helper_exact'] and r['tokenizer_exact']
               for r in audit['accepted'])
    max_rollout_cpu=0.
    for case in cases:
        row=json.loads(Path(case['artifact']).read_text())
        assert row['completed'] and row['source_valid'] and not row.get('error')
        assert row['source_epoch_before']==row['source_epoch_after']==freeze['epoch']
        assert len(row['rollouts'])==40
        for arm in ('baseline','lookahead'):
            armrows=[r for r in row['rollouts'] if r['arm']==arm]
            assert len(armrows)==20 and {r['repetition'] for r in armrows}==set(range(20))
            assert all(r['status'] in ('complete_proxy','dead_end_proxy') for r in armrows)
        max_rollout_cpu=max(max_rollout_cpu,*(r['cpu_s'] for r in row['rollouts']))
        pin=row['job']['case']['instance'];assert digest(pin['path'])==pin['sha256']
    # Later foreign live edits are not silently accepted: every retained result
    # uses the original epoch, and its archived source is checked in full.
    for path,sha in freeze['lookahead_runtime_sources'].items():
        assert digest(ROOT/'runtime-v2'/path)==sha,path
        if path.startswith('src/llm/lookahead/'):
            assert digest(path)==sha,path
    tests=read('tests-v2-final-meta.json')
    assert tests['exit_code']==0 and tests['source_before']==tests['source_after']
    for path,sha in tests['source_after'].items():assert digest(path)==sha,path
    suites=ET.parse(ROOT/'tests-v2-final.xml').getroot().findall('testsuite')
    assert sum(int(s.attrib['tests']) for s in suites)==70
    assert all(int(s.attrib[k])==0 for s in suites for k in ('failures','errors','skipped'))
    contracts=[]
    for path in sorted((ROOT/'formal-v2-contracts').glob('*/result.json')):
        row=json.loads(path.read_text())
        assert row['completed'] and row['contract_passed'] and row['source_valid']
        assert row['historical_checker_bound'] and not row['checker_run']
        for kind in ('instance','plan','checker'):
            pin=row['job']['case'][kind];assert digest(pin['path'])==pin['sha256']
        contracts.append(str(path))
    assert len(contracts)==100
    exposure=read('contract-exposure-v2.json')['groups']['all']
    assert exposure['native_executor_true']==exposure['nonzero_candidate_probes']==100
    result=dict(verified=True,contracts=100,rollouts=1400,tests=70,
        runtime_epoch=freeze['epoch'],max_rollout_cpu_s=max_rollout_cpu,
        retained_cases=[r['artifact'] for r in cases],contracts_checked=contracts,
        evidence_hashes={n:digest(ROOT/n) for n in ('summary-v2-combined.json','audit-v2-combined.json','tests-v2-final-meta.json','runtime-freeze-v2.json','primary-v2-witnesses.json','contract-results-v2.json','contract-exposure-v2.json')},
        scope='Independent accepted-result/hash/XML/input-binding verification. Original invalid attempts remain excluded. No new checker claim.')
    (ROOT/'ROOT_FINAL_VERIFICATION.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('contracts_checked','retained_cases')}))

if __name__=='__main__':main()
