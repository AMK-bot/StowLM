"""Package exact frozen before/after files; do not install production sources."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat

from snapshot import ROOT, WORK, manifest
from mask_inputs import closure as mask_closure

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / 'package'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def info(path):
    if not path.exists():
        return None
    return {'sha256': sha(path), 'mode': stat.S_IMODE(path.stat().st_mode)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qualification-manifest', type=Path)
    args = parser.parse_args()
    combined_path = HERE / 'combined-manifest.json'
    combined = json.loads(combined_path.read_text())
    baseline = WORK / 'baseline'
    candidate = WORK / 'candidate'
    assert manifest(baseline) == combined['src_stowage_before']
    assert manifest(candidate) == combined['src_stowage_after']
    assert manifest(ROOT) == combined['src_stowage_before'], 'Production source drift; new qualification needed'
    toml_root = ROOT / 'experiments/mvp/tomlcache/patches'
    for source, packaged in [('0001-toml-content-cache.patch', '02-toml-content-cache.patch'),
                              ('0002-toml-cache-tests.patch', '02b-toml-tests.patch')]:
        assert sha(toml_root / source) == sha(PACKAGE / 'patches' / packaged), 'TOML patch changed after assembly'
    names = set(combined['changed_src'])
    for path in sorted((candidate / 'tests').rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = str(path.relative_to(candidate))
        if info(path) != info(baseline / relative):
            names.add(relative)
    targets = {}
    for relative in sorted(names):
        target = {'before': info(baseline / relative), 'after': info(candidate / relative)}
        assert target['after'] is not None, 'This batch does not delete production files'
        targets[relative] = target
        for epoch, source in [('before', baseline), ('after', candidate)]:
            if target[epoch] is None:
                continue
            dest = PACKAGE / epoch / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, dest)
    regression = [
        'tests/test_toml_cache.py', 'tests/test_cache_singleflight.py',
        'tests/ports/test_profiles.py', 'tests/ports/test_next_call_environments.py',
        'tests/ports/test_yard_blocking_height.py', 'tests/vessel/test_fleet_cache.py',
        '{package}/guards/test_heat_tank_links.py', '{package}/guards/test_singleflight_batch.py',
    ]
    checker = [
        'tests/checker/test_stability_fallback_budget.py', 'tests/checker/test_external_watchdog.py',
        'tests/checker/test_rules_heat_ids.py', 'tests/checker/test_stability_liquid_batch.py',
        'tests/checker/test_stability_direct_liquid_batch.py', 'tests/checker/test_stability_batch.py',
        'tests/checker/test_stability_loading_policy.py', 'tests/checker/test_stability_fixture_policy.py',
        'tests/checker/test_rules_geometry_missing.py', 'tests/checker/test_rules_defaults.py',
        'tests/checker/test_full_rule_ids.py',
    ]
    qualified_names = set()
    qualified_names.update(str(p.relative_to(baseline)) for p in (baseline / 'params').rglob('*') if p.is_file())
    qualified_names.update(('pyproject.toml', 'uv.lock', 'data/raw/kcs/kcs.fix', 'data/raw/kcs/MANIFEST'))
    qualified_names.update(name for name in regression + checker if not name.startswith('{'))
    qualified_names.update(str(p.relative_to(baseline)) for p in (baseline / 'tests').rglob('conftest.py'))
    inputs = {name: info(baseline / name) for name in sorted(qualified_names) if name not in targets}
    assert all(value is not None for value in inputs.values())
    # The legal-set guard also depends on shared LLM wrappers and tokenizer
    # bytes. Bind their actual tested closure, without freezing unrelated
    # training-loop implementation files owned by other executives.
    tested_mask_epoch = json.loads((HERE.parent / 'masks/candidate/summary.json').read_text())['source_epoch']
    mask_names = mask_closure(candidate)
    for name in mask_names:
        assert info(candidate / name) == info(baseline / name) == info(ROOT / name), 'Mask dependency drift: ' + name
        assert info(candidate / name)['sha256'] == tested_mask_epoch[name], 'Not the guarded mask dependency: ' + name
        inputs[name] = info(candidate / name)
    prepared = json.loads((HERE.parent / 'masks/prepared/manifest.json').read_text())
    tokenizer = Path(prepared['tokenizer'])
    assert tokenizer.is_relative_to(ROOT)
    assert sha(tokenizer) == prepared['tokenizer_sha256'], 'Guard tokenizer drift'
    inputs[str(tokenizer.relative_to(ROOT))] = info(tokenizer)
    artifacts = {}
    for folder in ('patches', 'before', 'after', 'guards'):
        for path in sorted((PACKAGE / folder).rglob('*')):
            if path.is_file():
                artifacts[str(path.relative_to(PACKAGE))] = sha(path)
    qualification = None
    if args.qualification_manifest:
        qualification = json.loads(args.qualification_manifest.read_text())
        assert qualification['status'] == 'PASS'
        assert qualification['combined_manifest_sha256'] == sha(combined_path)
        assert qualification['toml_owner_final'] is True
        assert len(qualification['guards']) >= 3
        for guard in qualification['guards']:
            assert guard['passed'] is True
            assert sha(ROOT / guard['path']) == guard['sha256']
        target = PACKAGE / 'qualification.json'
        shutil.copy2(args.qualification_manifest, target)
        artifacts['qualification.json'] = sha(target)
    output = {
        'batch': 'D132', 'production_installed_by_packaging': False,
        'qualification_complete': qualification is not None,
        'combined_manifest_sha256': sha(combined_path),
        'src_stowage_before': combined['src_stowage_before'],
        'src_stowage_after': combined['src_stowage_after'],
        'targets': targets, 'qualified_inputs': inputs, 'artifacts': artifacts,
        'mask_dependency_closure': mask_names,
        'tooling_artifacts': {name: sha(HERE.parent / name) for name in
                              ('INSTALL.sh', 'ROLLBACK.sh', 'assembly/batch_tool.py')},
        'fast_test_commands': [['-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                                '-m', 'not slow and not gpu and not net', *regression],
                               ['-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                                '-m', 'not slow and not gpu and not net', *checker]],
        'activation': 'Operator invokes INSTALL.sh once after the generation-2 gate; this tool never schedules itself',
        'singleflight_default': 'off; explicit STOWAGE_VESSEL_SINGLEFLIGHT=1 to enable coalescing',
    }
    destination = PACKAGE / 'manifest.json'
    destination.write_text(json.dumps(output, indent=2, sort_keys=True) + '\n')
    (PACKAGE / 'manifest.sha256').write_text(sha(destination) + '\n')
    print(json.dumps({'targets': len(targets), 'source_targets': len(combined['changed_src']),
                      'qualification_complete': output['qualification_complete'],
                      'manifest_sha256': sha(destination)}))


if __name__ == '__main__':
    main()
