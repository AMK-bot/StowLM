"""Apply D132 only in its frozen scratch tree; emit reviewed rebased patches."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from snapshot import ROOT, WORK, manifest

HERE = Path(__file__).resolve().parent
BASE = WORK / 'baseline'
CANDIDATE = WORK / 'candidate'
PACKAGE = HERE / 'package'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(source, destination):
    shutil.copy2(source, destination)
    subprocess.run(['git', 'apply', '--check', str(destination)], cwd=CANDIDATE, check=True)
    subprocess.run(['/usr/bin/patch', '-p1', '--batch', '--forward', '-i', str(destination)],
                   cwd=CANDIDATE, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(['git', 'apply', '--reverse', '--check', str(destination)],
                   cwd=CANDIDATE, check=True)


def main():
    pins = json.loads((HERE / 'baseline-manifest.json').read_text())
    if manifest(ROOT) != pins['src_stowage'] or manifest(BASE) != pins['src_stowage']:
        raise RuntimeError('Source baseline drift; never silently rebuild against a later source')
    if manifest(CANDIDATE) != pins['src_stowage']:
        raise RuntimeError('Candidate already modified; refusing duplicate assembly')
    patch_dir = PACKAGE / 'patches'
    patch_dir.mkdir(parents=True)
    inputs = [
        (ROOT / 'experiments/mvp/fixwindow/d130/heat/feasibility-heat-tank-links.patch',
         patch_dir / '01-heat-tank-links.patch'),
        (ROOT / 'experiments/mvp/tomlcache/patches/0001-toml-content-cache.patch',
         patch_dir / '02-toml-content-cache.patch'),
        (ROOT / 'experiments/mvp/tomlcache/patches/0002-toml-cache-tests.patch',
         patch_dir / '02b-toml-tests.patch'),
    ]
    for source, destination in inputs:
        apply(source, destination)
    toml_manifest = json.loads((ROOT / 'experiments/mvp/tomlcache/patches/MANIFEST.json').read_text())
    for relative, record in toml_manifest['files'].items():
        if digest(CANDIDATE / relative) != record['candidate_sha256']:
            raise RuntimeError(f'TOML candidate does not match owner pin: {relative}')
    relative = 'src/stowage/vessel/fleet_cache.py'
    before = (CANDIDATE / relative).read_text()
    original = (ROOT / 'experiments/mvp/coldstart/patches/fleet_cache_candidate.py').read_text()
    baseline = (BASE / relative).read_text()
    cold_pins = json.loads((ROOT / 'experiments/mvp/coldstart/patches/fleet-cache-singleflight-source.json').read_text())
    if hashlib.sha256(baseline.encode()).hexdigest() != cold_pins['source_sha256']:
        raise RuntimeError('Singleflight source pin differs; needs explicit review')
    if hashlib.sha256(original.encode()).hexdigest() != cold_pins['candidate_sha256']:
        raise RuntimeError('Singleflight candidate pin mismatch')
    # The only overlap is the source fingerprint list. Retain TOML's new module
    # and append the pre-existing singleflight helper; hook behavior is unchanged.
    old_anchor = '"checker/strength_limits.py", "conventions.py", "params.py",'
    toml_anchor = old_anchor + ' "toml_cache.py",'
    single_anchor = old_anchor + ' "cache_singleflight.py",'
    if before.count(toml_anchor) != 1 or original.count(single_anchor) != 1:
        raise RuntimeError('Fingerprint conflict shape changed; manual review required')
    candidate = original.replace(single_anchor, toml_anchor + ' "cache_singleflight.py",', 1)
    # Preserve every other TOML-owner edit, namely import and selected parser API.
    baseline_start = baseline.index('        vessel = build()\n', baseline.index('    target = _cache_dir()'))
    baseline_end = baseline.index('    _remember(key, payload)', baseline_start)
    candidate_start = original.index('        from stowage.cache_singleflight import cache_singleflight')
    candidate_end = original.index('    _remember(key, payload)', candidate_start)
    new_hook = original[candidate_start:candidate_end]
    before_start = before.index('        vessel = build()\n', before.index('    target = _cache_dir()'))
    before_end = before.index('    _remember(key, payload)', before_start)
    if before[before_start:before_end] != baseline[baseline_start:baseline_end]:
        raise RuntimeError('TOML owner changed hook body; explicit review needed')
    candidate = before[:before_start] + new_hook + before[before_end:]
    candidate = candidate.replace(toml_anchor, toml_anchor + ' "cache_singleflight.py",', 1)
    ast.parse(candidate)
    patch = ''.join(difflib.unified_diff(before.splitlines(keepends=True),
                    candidate.splitlines(keepends=True), fromfile='a/' + relative, tofile='b/' + relative))
    rebased = HERE / 'fleet-cache-singleflight-rebased.patch'
    rebased.write_text(patch)
    apply(rebased, patch_dir / '03-fleet-cache-singleflight-rebased.patch')
    after = manifest(CANDIDATE)
    changed = [key for key in sorted(set(pins['src_stowage']) | set(after))
               if pins['src_stowage'].get(key) != after.get(key)]
    for path in (CANDIDATE / 'src/stowage').rglob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
    receipt = {
        'baseline': str(BASE), 'candidate': str(CANDIDATE),
        'src_stowage_before': pins['src_stowage'], 'src_stowage_after': after,
        'changed_src': changed, 'patches': {str(path.relative_to(PACKAGE)): digest(path)
                                           for path in sorted(patch_dir.glob('*.patch'))},
        'singleflight_rebase': {'overlap': 'source fingerprint list only',
                              'before_toml_sha256': hashlib.sha256(before.encode()).hexdigest(),
                              'combined_sha256': digest(CANDIDATE / relative),
                              'preserves_toml_module_and_singleflight_module': True},
        'lazy_inputs_omitted': 'app/inputs.py only, owned by mvp-ui',
        'qualification_complete': False,
    }
    (HERE / 'combined-manifest.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    if manifest(ROOT) != pins['src_stowage']:
        raise RuntimeError('Live source changed during scratch assembly; qualification cannot bind current production')
    print(json.dumps({'candidate_ready': str(CANDIDATE), 'changed_src': changed,
                      'combined_manifest_sha256': digest(HERE / 'combined-manifest.json')}))


if __name__ == '__main__':
    main()
