"""Verify provenance, source freeze, and numeric report integrity (no solves)."""
import csv
import hashlib
import json
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
base=ROOT/'data/raw/kcs-largeangle-public'
manifest=(base/'MANIFEST').read_text()
primary=re.findall(r'^File: (.+)\n(?:(?!^File: ).*\n)*?^SHA256: ([0-9a-f]{64})$',manifest,re.M)
assert len(primary)==4,primary
for filename,digest in primary:
    assert sha(base/filename)==digest,filename
secondary=ROOT/'data/raw/kcs-largeangle-secondary'
entries=json.loads((secondary/'MANIFEST.json').read_text())['files']
for entry in entries:
    assert sha(secondary/entry['file'])==entry['sha256'],entry['file']
c=json.loads((HERE/'comparison.json').read_text())
for filename,digest in c['source_hashes'].items():
    assert sha(ROOT/filename)==digest,filename
for case in c['cases']:
    assert sha(ROOT/case['source_csv'])==case['source_csv_sha256'],case['case']
    for row in case['comparisons']:
        assert abs(row['error_m']-(row['compared_production_m']-row['reference_m']))<1e-12
        assert row['within_l5_diagnostic_bar']==(abs(row['error_m'])<=row['l5_diagnostic_tolerance_m'])
        assert abs(row['volume_residual_m3'])<1e-4
oracle=json.loads((HERE/'oracle-results.json').read_text())
assert oracle['source_hashes_before']==oracle['source_hashes_after']
for filename,digest in oracle['source_hashes_after'].items():
    assert sha(ROOT/filename)==digest,filename
assert oracle['max_abs_gz_error_m']<2e-6
assert oracle['max_abs_kn_error_m']<1e-6
primary_case=next(x for x in c['cases'] if x['case']=='keramati2026')
primary_oracle=json.loads((HERE/'primary-oracle-results.json').read_text())
assert primary_oracle['source_hashes_before']==primary_oracle['source_hashes_after']
assert primary_oracle['revalidated_against_current_comparison_sha256']==sha(HERE/'comparison.json')
for filename,digest in primary_oracle['source_hashes_after'].items():
    assert sha(ROOT/filename)==digest,filename
for row in primary_oracle['rows']:
    for kind in ['kn','gz']:
        compared=next(r for r in primary_case['comparisons'] if r['heel_deg']==row['heel_deg'] and r['quantity']==kind)
        assert row['production_zero_trim_'+kind+'_m']==compared['compared_production_m']
        assert row['published_'+kind+'_m']==compared['reference_m']
        assert abs(row['production_minus_independent_'+kind+'_m'])<3e-6
assert len(primary_case['comparisons'])==44
assert max(row['heel_deg'] for row in primary_case['comparisons'])==57.5
for kind in ['kn','gz']:
    rows=[row for row in primary_case['comparisons'] if row['quantity']==kind]
    assert all(row['within_l5_diagnostic_bar'] for row in rows if row['heel_deg']<=20)
    assert all(row['discrepancy_exceeds_bar_plus_reading'] for row in rows if row['heel_deg']>=25)
print(f'PASS: {len(primary)+len(entries)} raw-file SHA256 checks, frozen source/CSV hashes, 102 comparison rows, oracle residuals, and no primary-curve extrapolation')
