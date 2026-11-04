"""Validate the revision-2 D132 receipts and optionally seal the package; never installs anything.

seal_release.py is the v1 path and deliberately refuses once assembly/READY-v2.json exists. This is its v2 counterpart
(fix-t8-sft, leader's D132 v2 order, after the owning session was lost). It adds no numerical work: it reads the final
receipts, and only with --seal writes QUALIFIED.json, rebuilds the package with package_batch.py --revision 2, verifies
the sealed package against production's pre-batch bytes, and writes RELEASE-READY.json. Any failed check seals nothing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE / 'assembly'))
import batch_tool  # noqa: E402

HOLD = 'Leader schedules after the generation-2 gate; sealing is not permission to install early.'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def require(condition, label):
    """Every gate goes through here, so ``python -O`` can't remove one."""
    if not condition:
        raise RuntimeError(f'seal refused: {label}')


def xml_pass(path, count):
    root = ET.parse(path).getroot()
    require(len(root.findall('.//testcase')) == count, f'{path}: expected {count} test cases')
    require(not any(root.findall('.//' + tag) for tag in ('failure', 'error', 'skipped')), f'{path}: not all passed')


def check_v1_archive(here, combined):
    """The v1 qualification v2 carries over: its archive is intact, file by file, and v2's stowage bytes are v1's."""
    archive = here / 'revisions/v1'
    qualified_path = archive / 'QUALIFIED.json'
    require(digest(qualified_path) == combined['carried_v1_qualification_sha256'], 'v1 QUALIFIED.json is not the carried one')
    require(digest(archive / 'RELEASE-READY.json') == combined['carried_v1_release_ready_sha256'], 'v1 release receipt differs')
    qualified = read(qualified_path)
    require(qualified['status'] == 'PASS', 'v1 qualification did not pass')
    files = read(archive / 'ARCHIVE-MANIFEST.json')['files']
    require(files, 'v1 archive manifest lists no files')
    for name, expected in files.items():
        require((archive / name).is_file() and digest(archive / name) == expected, f'v1 archive differs: {name}')
    for guard in qualified['guards']:
        require(digest(archive / guard['name']) == guard['sha256'], f"v1 guard differs: {guard['name']}")
    v1_combined_path = archive / 'assembly/combined-manifest.json'
    require(digest(v1_combined_path) == combined['carried_v1_combined_manifest_sha256'], 'v1 combined manifest differs')
    require(read(v1_combined_path)['src_stowage_after'] == combined['src_stowage_after'],
            'v2 stowage bytes are not the qualified v1 candidate')


def check_receipts(here):
    """All receipt gates, read-only. Every receipt must name the revision-2 manifest, not just say it passed."""
    ready = read(here / 'assembly/READY-v2.json')
    combined_path = here / 'assembly/revision2-combined-manifest.json'
    combined = read(combined_path)
    manifest = digest(combined_path)
    require(ready['revision'] == combined['revision'] == 2, 'READY-v2 and the manifest are not revision 2')
    require(manifest == ready['combined_manifest_sha256'], 'READY-v2 names another manifest')
    check_v1_archive(here, combined)
    # v2 candidate: 100 full reports, ACK, dataset, 138 CPU tests, two complete samples (exit-tests/finalize.py).
    exit_summary = read(here / 'exit-tests/SUMMARY.json')
    require(exit_summary['status'] == 'COMPLETE_CPU_QUALIFIED', 'exit summary not qualified')
    require(exit_summary['source_identity']['assembly_v2_manifest_sha256'] == manifest, 'exit summary names another manifest')
    require(exit_summary['receipt_sha256'], 'exit summary lists no receipts')
    for name, expected in exit_summary['receipt_sha256'].items():
        require(digest(here / 'exit-tests' / name) == expected, f'exit receipt differs: {name}')
    full = exit_summary['full_checker100']
    require(full['plans'] == full['passed'] == full['raw_full_report_byte_identical'] == 100
            and not full['fields_removed'], 'full checker 100 not exact')
    require(exit_summary['pytest']['total_passed'] == 138 and exit_summary['all_owned_workers_reaped'], 'exit tests incomplete')
    require(not exit_summary['gpu'] and exit_summary['hashseed'] == 0, 'exit run not CPU with hash seed 0')
    status = read(here / 'exit-tests/candidate/STATUS.json')
    require(status['complete'] and status['exact'] == 100 and not status['mismatches'] and not status['errors'],
            'exit candidate status not exact')
    # v2 mask214: complete, no errors, every v2 event equal to the v1 candidate. comparison.json names no revision, so
    # the candidate run and the v1 identity check beside it carry the binding.
    masks = read(here / 'revision2/masks/comparison.json')
    require(masks['qualified'] and masks['complete214'] and not masks['errors'] and masks['changed_queries'] == 23,
            'mask comparison not qualified')
    require(read(here / 'revision2/masks/candidate/summary.json')['revision2_manifest_sha256'] == manifest,
            'mask candidate names another manifest')
    identity = read(here / 'revision2/masks/v1-candidate-identity.json')
    require(identity['v2_manifest_sha256'] == manifest, 'mask identity names another manifest')
    require(identity['qualified'] and identity['revision'] == 2 and not identity['differences'], 'mask identity not exact')
    # Transaction rehearsal and path tests on the v2 package.
    rehearsal_path = here / 'assembly/install-rehearsal-v2.json'
    rehearsal = read(rehearsal_path)
    require(rehearsal['passed'] and rehearsal['whole_source_exact_after_rollback'], 'rehearsal failed')
    require(rehearsal['exit_inventory_exact_after_rollback'] and rehearsal['production_source_unchanged'], 'rehearsal not clean')
    require(rehearsal['production_install_invocations'] == 0 and len(rehearsal['commands']) == 6, 'rehearsal commands differ')
    require(all(r['returncode'] == 0 for r in rehearsal['commands']), 'a rehearsal command failed')
    xml_pass(here / 'assembly/revision2-transaction-tests.xml', 60)
    audit = read(here / 'assembly/REVISION2-ASSEMBLY-AUDIT.json')
    require(audit['passed'] and audit['fast_counts'] == {'fast-0.stdout': 337, 'fast-1.stdout': 407, 'fast-2.stdout': 134},
            'assembly audit failed')
    require(audit['revision'] == 2 and audit['candidate_manifest_sha256'] == manifest, 'assembly audit names another manifest')
    require(audit['private_rehearsal_sha256'] == digest(rehearsal_path), 'assembly audit names another rehearsal')
    preflight = read(here / 'assembly/revision2-helper-preflight.json')
    require(preflight['passed'] is True, 'helper preflight failed')
    require(preflight['payload_artifacts_targets_tools_fastcommands_identical_to_full_v2_rehearsal'] is True,
            'helper preflight differs from the v2 rehearsal')
    require(preflight['original_full_mechanics_receipt_sha256'] == digest(rehearsal_path), 'helper preflight names another rehearsal')
    toml_final = read(here / 'toml-handoff-verified.json')['toml_owner_final']
    require(toml_final is True, 'TOML owner not final')  # v2 stowage bytes are the v1 candidate's, so this handoff binds them
    compute = read(here / 'COMPUTE.json')
    require(not compute['running_reservations'], 'reservations still running')
    require(all(p['reaped'] for p in compute['unbound_process_receipts']), 'a worker was not reaped')
    return dict(manifest=manifest, toml_final=toml_final)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seal', action='store_true')
    args = parser.parse_args()
    checked = check_receipts(HERE)
    # Pure read-only precondition: production still holds the exact pre-batch bytes.
    package = read(HERE / 'assembly/package/manifest.json')
    batch_tool.verify_epoch(ROOT, package, 'before')
    receipts = ('exit-tests/SUMMARY.json', 'exit-tests/candidate/STATUS.json', 'revision2/masks/comparison.json',
                'revision2/masks/candidate/summary.json', 'revision2/masks/v1-candidate-identity.json',
                'assembly/READY-v2.json', 'assembly/revision2-combined-manifest.json',
                'assembly/install-rehearsal-v2.json', 'assembly/revision2-transaction-tests.xml',
                'assembly/REVISION2-ASSEMBLY-AUDIT.json', 'assembly/revision2-helper-preflight.json',
                'revisions/v1/QUALIFIED.json', 'revisions/v1/ARCHIVE-MANIFEST.json',
                'toml-handoff-verified.json', 'COMPUTE.json')
    qualified = {'status': 'PASS', 'revision': 2, 'combined_manifest_sha256': checked['manifest'],
                 'toml_owner_final': checked['toml_final'], 'production_installed': False, 'hold': HOLD,
                 'test_v21_causal_heat_replay': 'Not performed under D110; metadata only.',
                 'model_gpu_smoke': 'Not run under the no-GPU limit; boundary task after install.',
                 'guards': [{'name': p, 'path': str((HERE / p).relative_to(ROOT)), 'sha256': digest(HERE / p),
                             'passed': True} for p in receipts]}
    if not args.seal:
        print(json.dumps({'status': 'PASS', 'sealed': False, 'production_entrypoint_invoked': False}))
        return
    # package_batch.py still has bare asserts, so an optimized run must not reach it.
    require(not sys.flags.optimize, 'sealing under python -O would remove package_batch.py checks')
    target = HERE / 'QUALIFIED.json'
    target.write_text(json.dumps(qualified, sort_keys=True, indent=2) + '\n')
    subprocess.run([sys.executable, str(HERE / 'assembly/package_batch.py'), '--revision', '2',
                    '--qualification-manifest', str(target)], check=True)
    sealed, package_sha = batch_tool.verify_package()
    batch_tool.verify_epoch(ROOT, sealed, 'before')
    release = {'status': 'PASS', 'revision': 2, 'qualification_complete': sealed['qualification_complete'],
               'checked_utc': datetime.now(timezone.utc).isoformat(), 'package_manifest_sha256': package_sha,
               'qualification_sha256': digest(target), 'production_source_files': len(sealed['src_stowage_before']),
               'candidate_source_files': len(sealed['src_stowage_after']), 'exit_files_before': len(sealed['src_exit_before']),
               'exit_files_after': len(sealed['src_exit_after']), 'target_paths': len(sealed['targets']),
               'production_entrypoint_invoked': False, 'production_prebatch_exact': True,
               'no_own_workers_running': True, 'hold': HOLD}
    (HERE / 'RELEASE-READY.json').write_text(json.dumps(release, sort_keys=True, indent=2) + '\n')
    print(json.dumps(release))


if __name__ == '__main__':
    main()
