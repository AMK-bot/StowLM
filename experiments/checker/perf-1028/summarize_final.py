"""Validate matching source/report identities and summarize a complete A/B run."""
import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('prefix')
args = parser.parse_args()
reference = json.loads((HERE/f'{args.prefix}-reference.json').read_text())['rows']
optimized = json.loads((HERE/f'{args.prefix}-optimized.json').read_text())['rows']
assert len(reference) == len(optimized) == 3
rows = []
for old, new in zip(reference, optimized):
    assert old['ship'] == new['ship']
    assert old['warm_samples'] >= 5 and new['warm_samples'] >= 5
    assert len(old['samples']) >= 5 and len(new['samples']) >= 5
    samples = old['samples']+new['samples']
    assert all(sample['sources_stable'] for sample in samples)
    assert all(sample['source_sha256'] == samples[0]['source_sha256'] for sample in samples)
    assert len({sample['report_sha256'] for sample in samples}) == 1
    assert not any(sample['part_errors'] for sample in samples)
    rows.append(dict(ship=old['ship'],
        reference_cold_seconds=old['cold_median_seconds'],
        optimized_cold_seconds=new['cold_median_seconds'],
        reference_warm_seconds=old['warm_median_seconds'],
        optimized_warm_seconds=new['warm_median_seconds'],
        warm_saved_seconds=old['warm_median_seconds']-new['warm_median_seconds'],
        warm_saved_percent=100*(1-new['warm_median_seconds']/old['warm_median_seconds']),
        target_met=new['warm_median_seconds'] <= .6,
        bitwise_equal_reports=True, source_epoch_equal=True))
out = HERE/f'{args.prefix}-comparison.json'
out.write_text(json.dumps(rows, indent=2)+'\n')
print(json.dumps(rows, indent=2))
