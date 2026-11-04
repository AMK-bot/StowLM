"""Audit the post-D64 canonical-policy evidence without rebuilding any physics."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    label=parser.parse_args().label
    reviewed = json.loads((HERE / f'{label}-review/canonical-policy-review.json').read_text())
    contract_path = ROOT / 'src/stowage/checker/stability_policy_contract.json'
    contract = json.loads(contract_path.read_bytes())
    assert hashlib.sha256(contract_path.read_bytes()).hexdigest() == reviewed['new_contract_sha256']
    for group in ('source_sha256', 'auxiliary_sha256'):
        assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
                   for name, expected in contract[group].items())
    assert len(reviewed['rows']) == 3
    assert all(row['five_fields_bitwise_equal'] and row['unchanged_from_prior_review_bitwise']
               and row['vessel_cache_reuse'] is False for row in reviewed['rows'])
    for name, expected in json.loads((HERE / f'{label}-owned-files.json').read_text()).items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    cold = json.loads((HERE / f'{label}-cold-proof/canonical-policy-cold-regression.json').read_text())
    assert cold['default_budget_seconds'] == 30.
    assert cold['vessel_cache_remains_empty'] and cold['cold_warm_reports_bitwise_equal']
    assert not any(Path(cold['vessel_cache']).iterdir())
    assert all(row['report']['ran'] and row['seconds'] < 30. for row in cold['runs'])
    telemetry = json.loads((HERE / f'{label}-suite.run.json').read_text())
    suites = list(ET.parse(HERE / f'{label}-suite.xml').getroot().iter('testsuite'))
    counts = {key: sum(int(row.attrib.get(key, 0)) for row in suites)
              for key in ('tests', 'errors', 'failures', 'skipped')}
    counts['passed'] = counts['tests'] - counts['errors'] - counts['failures'] - counts['skipped']
    result = dict(contract_sha256=reviewed['new_contract_sha256'], unchanged_policy_fields=15,
        construction_sources_still_current=True, owned_files_unchanged=True,
        cold_warm_seconds=[row['seconds'] for row in cold['runs']], cold_warm_reports_bitwise_equal=True,
        vessel_cache_remains_empty=True, suite=counts,
        telemetry={key: telemetry[key] for key in ('exit_code', 'wall_seconds', 'cpu_seconds', 'peak_rss_gb', 'changed')})
    (HERE / f'{label}-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    assert telemetry['exit_code'] == 0 and counts['failures'] == counts['errors'] == 0


if __name__ == '__main__':
    main()
