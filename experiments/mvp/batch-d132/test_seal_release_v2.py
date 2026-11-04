"""seal_release_v2.py gates, on a synthetic receipt tree; never reads production or seals anything."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parent/'seal_release_v2.py'
spec = importlib.util.spec_from_file_location('seal_release_v2', SOURCE)
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)
FAST = {'fast-0.stdout': 337, 'fast-1.stdout': 407, 'fast-2.stdout': 134}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, sort_keys=True))
    return sha256(path.read_bytes()).hexdigest()


def tree(here):
    """The smallest tree that passes, built so each receipt really is bound to the next."""
    v1 = here/'revisions/v1'
    guard = put(v1/'checker/summary.json', {'x': 1})
    qualified = put(v1/'QUALIFIED.json', dict(status='PASS', guards=[dict(name='checker/summary.json', sha256=guard)]))
    release = put(v1/'RELEASE-READY.json', {'status': 'PASS'})
    v1_combined = put(v1/'assembly/combined-manifest.json', dict(src_stowage_after={'a.py': 'h'}))
    put(v1/'ARCHIVE-MANIFEST.json', dict(files={'checker/summary.json': guard, 'QUALIFIED.json': qualified}))
    manifest = put(here/'assembly/revision2-combined-manifest.json', dict(
        revision=2, src_stowage_after={'a.py': 'h'}, carried_v1_qualification_sha256=qualified,
        carried_v1_release_ready_sha256=release, carried_v1_combined_manifest_sha256=v1_combined))
    put(here/'assembly/READY-v2.json', dict(revision=2, combined_manifest_sha256=manifest))
    samples = put(here/'exit-tests/EXIT-QUALIFICATION.json', {'ok': True})
    put(here/'exit-tests/SUMMARY.json', dict(
        status='COMPLETE_CPU_QUALIFIED', source_identity=dict(assembly_v2_manifest_sha256=manifest),
        receipt_sha256={'EXIT-QUALIFICATION.json': samples}, gpu=False, hashseed=0, all_owned_workers_reaped=True,
        full_checker100=dict(plans=100, passed=100, raw_full_report_byte_identical=100, fields_removed=[]),
        pytest=dict(total_passed=138)))
    put(here/'exit-tests/candidate/STATUS.json', dict(complete=True, exact=100, mismatches=[], errors=[]))
    put(here/'revision2/masks/comparison.json', dict(qualified=True, complete214=True, errors=[], changed_queries=23))
    put(here/'revision2/masks/candidate/summary.json', dict(revision2_manifest_sha256=manifest))
    put(here/'revision2/masks/v1-candidate-identity.json', dict(qualified=True, revision=2, differences=[],
                                                                v2_manifest_sha256=manifest))
    rehearsal = put(here/'assembly/install-rehearsal-v2.json', dict(
        passed=True, whole_source_exact_after_rollback=True, exit_inventory_exact_after_rollback=True,
        production_source_unchanged=True, production_install_invocations=0, commands=[dict(returncode=0)]*6))
    cases = ''.join(f'<testcase name="t{i}"/>' for i in range(60))
    put(here/'assembly/revision2-transaction-tests.xml', f'<testsuite>{cases}</testsuite>')
    put(here/'assembly/REVISION2-ASSEMBLY-AUDIT.json', dict(passed=True, fast_counts=FAST, revision=2,
                                                            candidate_manifest_sha256=manifest,
                                                            private_rehearsal_sha256=rehearsal))
    put(here/'assembly/revision2-helper-preflight.json', dict(
        passed=True, payload_artifacts_targets_tools_fastcommands_identical_to_full_v2_rehearsal=True,
        original_full_mechanics_receipt_sha256=rehearsal))
    put(here/'toml-handoff-verified.json', dict(toml_owner_final=True))
    put(here/'COMPUTE.json', dict(running_reservations=[], unbound_process_receipts=[dict(reaped=True)]))
    return manifest


def edit(path, **changes):
    put(path, {**json.loads(path.read_text()), **changes})


def test_a_bound_tree_passes(tmp_path):
    manifest = tree(tmp_path)
    assert seal.check_receipts(tmp_path) == dict(manifest=manifest, toml_final=True)


@pytest.mark.parametrize('relative,field', [
    ('revision2/masks/candidate/summary.json', 'revision2_manifest_sha256'),
    ('revision2/masks/v1-candidate-identity.json', 'v2_manifest_sha256'),
    ('assembly/REVISION2-ASSEMBLY-AUDIT.json', 'candidate_manifest_sha256'),
    ('assembly/REVISION2-ASSEMBLY-AUDIT.json', 'private_rehearsal_sha256'),
    ('assembly/revision2-helper-preflight.json', 'original_full_mechanics_receipt_sha256'),
    ('assembly/READY-v2.json', 'combined_manifest_sha256'),
])
def test_a_receipt_from_another_revision_is_refused(tmp_path, relative, field):
    tree(tmp_path)
    edit(tmp_path/relative, **{field: 'f'*64})
    with pytest.raises(RuntimeError, match='seal refused'):
        seal.check_receipts(tmp_path)


def test_exit_summary_from_another_revision_is_refused(tmp_path):
    tree(tmp_path)
    edit(tmp_path/'exit-tests/SUMMARY.json', source_identity=dict(assembly_v2_manifest_sha256='f'*64))
    with pytest.raises(RuntimeError, match='another manifest'):
        seal.check_receipts(tmp_path)


def test_a_changed_exit_receipt_is_refused(tmp_path):
    tree(tmp_path)
    put(tmp_path/'exit-tests/EXIT-QUALIFICATION.json', {'ok': False})
    with pytest.raises(RuntimeError, match='exit receipt differs'):
        seal.check_receipts(tmp_path)


@pytest.mark.parametrize('field', ['passed', 'payload_artifacts_targets_tools_fastcommands_identical_to_full_v2_rehearsal'])
def test_a_failed_helper_preflight_is_refused(tmp_path, field):
    tree(tmp_path)
    edit(tmp_path/'assembly/revision2-helper-preflight.json', **{field: False})
    with pytest.raises(RuntimeError, match='helper preflight'):
        seal.check_receipts(tmp_path)


def test_a_damaged_v1_archive_is_refused(tmp_path):
    tree(tmp_path)
    put(tmp_path/'revisions/v1/checker/summary.json', {'x': 2})
    with pytest.raises(RuntimeError, match='v1 archive differs'):
        seal.check_receipts(tmp_path)


def test_a_missing_v1_archive_file_is_refused(tmp_path):
    tree(tmp_path)
    (tmp_path/'revisions/v1/checker/summary.json').unlink()
    with pytest.raises(RuntimeError, match='v1 archive differs'):
        seal.check_receipts(tmp_path)


def test_stowage_bytes_other_than_v1_are_refused(tmp_path):
    tree(tmp_path)
    combined = tmp_path/'assembly/revision2-combined-manifest.json'
    edit(combined, src_stowage_after={'a.py': 'other'})
    edit(tmp_path/'assembly/READY-v2.json', combined_manifest_sha256=sha256(combined.read_bytes()).hexdigest())
    with pytest.raises(RuntimeError, match='not the qualified v1 candidate'):
        seal.check_receipts(tmp_path)


def test_gates_survive_python_O(tmp_path):
    tree(tmp_path)
    edit(tmp_path/'assembly/revision2-helper-preflight.json', passed=False)
    code = (f"import importlib.util as u;s=u.spec_from_file_location('s',{str(SOURCE)!r});m=u.module_from_spec(s);"
            f"s.loader.exec_module(m);m.check_receipts(__import__('pathlib').Path({str(tmp_path)!r}))")
    done = subprocess.run([sys.executable, '-O', '-c', code], capture_output=True, text=True)
    assert done.returncode != 0 and 'seal refused: helper preflight failed' in done.stderr


def test_sealing_is_refused_under_python_O(tmp_path):
    source = SOURCE.read_text()
    assert source.index("require(not sys.flags.optimize") < source.index("target.write_text")
