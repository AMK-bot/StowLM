"""Validate final receipts and optionally seal the package; never install it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE / 'assembly'))
import batch_tool


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def xml_pass(path, count):
    root = ET.parse(path).getroot()
    assert len(root.findall('.//testcase')) == count
    assert not any(root.findall('.//' + tag) for tag in ('failure', 'error', 'skipped'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal', action='store_true')
    args = parser.parse_args()
    if (HERE / 'assembly/READY-v2.json').exists():
        raise RuntimeError('Revision 1 seal is superseded: revision 2 requires the ExIt CPU/ACK/dataset and combined-candidate guards before a new seal.')
    checker = read(HERE / 'checker/summary.json')
    assert checker['passed'] and not checker['production_installed_by_lane']
    full = checker['full_reports']
    assert full['exact'] == full['paired'] == full['baseline_passes'] == full['candidate_passes'] == 100
    assert full['canonical_full_json_bytes'] and not full['omitted_fields']
    assert not full['allowed_automatic_differences']
    assert checker['whole_vessel_models']['source_correct_exact'] == 6
    assert all(p['reaped'] and (p['exit_code'] == 0 or
        p['retired_harness_failure']['tests_executed'] == 0) for p in checker['processes'])
    for name, expected in checker['environment']['dependency_versions'].items():
        assert version(name) == expected
    manual = read(HERE / 'checker/manual-gm.json')
    assert manual['passed'] and manual['manual']['passed']
    assert manual['manual']['tests'] == 40
    assert manual['manual']['failures'] == manual['manual']['errors'] == manual['manual']['skipped'] == 0
    xml = HERE / 'checker' / manual['manual']['junitxml']
    assert digest(xml) == manual['manual']['junitxml_sha256']
    xml_pass(xml, 40)
    assert manual['gm']['actual_conditions'] == 18 and manual['gm']['all_caps_exact']
    gm = read(HERE / 'checker' / manual['gm']['raw_receipt'])
    assert digest(HERE / 'checker' / manual['gm']['raw_receipt']) == manual['gm']['raw_receipt_sha256']
    expected = {'feeder_500': (3.2838356051426465, 'C3'),
                'feeder_800': (3.9584570964621637, 'C2'),
                'feeder_1700': (5.530108952747626, 'C3')}
    assert gm['complete'] and len(gm['rows']) == 3
    for row in gm['rows']:
        cap, condition = expected[row['ship']]
        conditions = row['audit']['conditions']
        assert {(r['condition'], r['phase']) for r in conditions} == {
            (c, p) for c in ('C1','C2','C3') for p in ('departure','arrival')}
        maximum = max(conditions, key=lambda r:r['gm_m'])
        assert len(conditions) == 6 and row['cap_m'] == 1.1 * maximum['gm_m'] == cap
        assert maximum['condition'] == condition and maximum['phase'] == 'departure'
    independent = read(HERE / 'independent-results.json')
    assert independent['status'] == 'PASS' and independent['threeway_complete'] == 100
    masks = read(HERE / 'masks/comparison.json')
    assert masks['qualified'] and masks['complete214'] and not masks['errors']
    assert read(HERE / 'masks/source-qualification.json')['qualified']
    assert read(HERE / 'toml-handoff-verified.json')['toml_owner_final']
    samples = read(HERE / 'samples/SUMMARY.json')
    assert samples['passed'] and samples['all_owned_workers_reaped']
    assert samples['unique_checked_newly_reachable_plans'] == 2
    xml_pass(HERE / 'assembly/release-path-tests.xml', 55)
    rehearsal = read(HERE / 'assembly/install-rehearsal-final-release.json')
    assert rehearsal['passed'] and rehearsal['whole_source_exact_after_rollback']
    assert rehearsal['production_source_unchanged'] and len(rehearsal['commands']) == 6
    assert all(r['returncode'] == 0 for r in rehearsal['commands'])
    compute = read(HERE / 'COMPUTE.json')
    assert not compute['running_reservations']
    assert all(p['reaped'] for p in compute['unbound_process_receipts'])
    # Pure read-only precondition check; no production shell entry point runs.
    package = read(HERE / 'assembly/package/manifest.json')
    batch_tool.verify_epoch(ROOT, package, 'before')
    receipt_paths = (
        'checker/summary.json', 'checker/manual-gm.json', 'independent-results.json',
        'masks/comparison.json', 'masks/source-qualification.json',
        'samples/SUMMARY.json', 'toml-handoff-verified.json',
        'assembly/FINAL-RELEASE-AUDIT.json', 'assembly/install-rehearsal-final-release.json',
        'assembly/release-path-tests.xml', 'COMPUTE.json',
    )
    qualified = {'status':'PASS', 'combined_manifest_sha256':digest(HERE / 'assembly/combined-manifest.json'),
                 'toml_owner_final':True, 'production_installed':False,
                 'hold':'Leader schedules after generation-2 gate; sealing is not permission to install early.',
                 'test_v21_causal_heat_replay':'Not performed under D110; metadata only.',
                 'guards':[{'name':p, 'path':str((HERE / p).relative_to(ROOT)),
                            'sha256':digest(HERE / p), 'passed':True} for p in receipt_paths]}
    if args.seal:
        target = HERE / 'QUALIFIED.json'
        target.write_text(json.dumps(qualified, sort_keys=True, indent=2) + '\n')
        subprocess.run([sys.executable, str(HERE / 'assembly/package_batch.py'),
                        '--qualification-manifest', str(target)], check=True)
        sealed, package_sha = batch_tool.verify_package()
        batch_tool.verify_epoch(ROOT, sealed, 'before')
        ready = {'status':'PASS', 'checked_utc':datetime.now(timezone.utc).isoformat(),
                 'package_manifest_sha256':package_sha, 'qualification_sha256':digest(target),
                 'production_source_files':len(sealed['src_stowage_before']),
                 'candidate_source_files':len(sealed['src_stowage_after']),
                 'target_paths':len(sealed['targets']), 'production_entrypoint_invoked':False,
                 'production_prebatch_exact':True, 'no_own_workers_running':True,
                 'hold':qualified['hold']}
        (HERE / 'RELEASE-READY.json').write_text(json.dumps(ready, sort_keys=True, indent=2) + '\n')
        print(json.dumps(ready))
    else:
        print(json.dumps({'status':'PASS', 'sealed':False, 'production_entrypoint_invoked':False}))


if __name__ == '__main__':
    main()
