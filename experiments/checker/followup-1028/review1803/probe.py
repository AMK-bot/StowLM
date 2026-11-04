"""Real loading-provider mutations; experiment-only source execution.

The old selector is the captured pre-fix dependency module. Neither variant
is written to production. Run --phase before before editing dependencies.py,
then --phase after following its freeze. No vessel builds or cache writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import types

import numpy as np

from stowage.linear import dependencies as current
from stowage.vessel.tanks import TankGeometry

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROFILES = ('liquid_reference', 'calibration', 'instance')
SOURCE = 'checker/stability_loading.py'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def execute_module(raw, name, filename):
    module = types.ModuleType(name)
    module.__file__ = str(filename)
    sys.modules[name] = module
    exec(compile(raw, str(filename), 'exec'), module.__dict__)
    return module


def centroid(raw, name):
    module = execute_module(raw, 'stowage.checker._review1803_' + name,
                            ROOT / 'src/stowage' / SOURCE)
    solid = TankGeometry(10., 30., -7., -3., 1., 4.)
    result = module.liquid_centroid(solid, .3 * solid.capacity_m3, .2, .01)
    return np.asarray(result, dtype=float)


def production_identities():
    return {
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'source_manifest': {
            str(path.relative_to(ROOT)): digest(path.read_bytes())
            for path in sorted((ROOT / 'src/stowage').rglob('*'))
            if path.is_file() and path.suffix in ('.py', '.json', '.toml')},
        'profiles': {
            profile: {'fingerprint': current.dependency_fingerprint(profile),
                      'contents': current.dependency_contents(profile),
                      'versions': current.builder_versions(profile)}
            for profile in current._PROFILES}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    args = parser.parse_args()
    started = perf_counter()
    frozen = execute_module((HERE / 'before-dependencies.py').read_bytes(),
                            'stowage.linear._review1803_before', HERE / 'before-dependencies.py')
    frozen._ROOT = current._ROOT
    raw = (HERE / 'before-stability_loading.py').read_bytes()
    needle = b'from math import cos, factorial, sin, tan'
    assert raw.count(needle) == 1
    variants = {
        'conditional_rebinding': (raw, raw.replace(needle, needle + b'\nif True:\n    sin = cos')),
        'assignment_import_order': (
            raw.replace(needle, b'from math import cos\nsin = cos\n' + needle),
            raw.replace(needle, b'from math import cos\n' + needle + b'\nsin = cos'))}
    rows = []
    for case, (left, right) in variants.items():
        a, b = centroid(left, case + '_a'), centroid(right, case + '_b')
        shift = float(np.max(np.abs(a - b)))
        assert shift > .8
        profiles = {}
        for profile in PROFILES:
            symbols = current._PROFILES[profile][SOURCE]
            old_hashes = [frozen._semantic_hash(value, symbols) for value in (left, right)]
            new_hashes = [current._semantic_hash(value, symbols) for value in (left, right)]
            assert old_hashes[0] == old_hashes[1], (case, profile, 'old reproduction failed')
            assert (new_hashes[0] == new_hashes[1]) == (args.phase == 'before'), (case, profile)
            profiles[profile] = {'old_hashes': old_hashes, 'old_equal': True,
                                 'current_hashes': new_hashes,
                                 'current_equal': new_hashes[0] == new_hashes[1]}
        rows.append({'case': case, 'variant_sha256': [digest(left), digest(right)],
                     'centroids': [a.tolist(), b.tolist()],
                     'centroids_float_hex': [[float(x).hex() for x in v] for v in (a, b)],
                     'max_absolute_centroid_shift_m': shift, 'profiles': profiles})
    identities = production_identities()
    (HERE / f'{args.phase}-production-identities.json').write_text(json.dumps(identities, indent=2) + '\n')
    if args.phase == 'after':
        before = json.loads((HERE / 'before-production-identities.json').read_text())
        source_changes = {
            name: {'before': before['source_manifest'].get(name),
                   'after': identities['source_manifest'].get(name)}
            for name in before['source_manifest'].keys() | identities['source_manifest'].keys()
            if before['source_manifest'].get(name) != identities['source_manifest'].get(name)}
        profiles = {}
        for profile, after in identities['profiles'].items():
            prior = before['profiles'][profile]
            changed_inputs = sorted(name for name in prior['contents'].keys() | after['contents'].keys()
                                    if prior['contents'].get(name) != after['contents'].get(name))
            profiles[profile] = {
                'before_fingerprint': prior['fingerprint'],
                'after_fingerprint': after['fingerprint'],
                'versions_unchanged': prior['versions'] == after['versions'],
                'versions': after['versions'], 'semantic_source_hash_changes': changed_inputs}
        delta = {'source_changes': source_changes, 'profiles': profiles,
                 'numerical_source_edits': False,
                 'source_manifest_entries_before': len(before['source_manifest']),
                 'source_manifest_entries_after': len(identities['source_manifest'])}
        (HERE / 'source-identity-delta.json').write_text(json.dumps(delta, indent=2) + '\n')
        assert set(source_changes) == {'src/stowage/linear/dependencies.py'}, source_changes
        assert all(row['versions_unchanged'] for row in profiles.values())
    result = {'phase': args.phase, 'captured_utc': datetime.now(timezone.utc).isoformat(),
              'frozen_selector_sha256': digest((HERE / 'before-dependencies.py').read_bytes()),
              'current_selector_sha256': digest(Path(current.__file__).read_bytes()),
              'cases': rows, 'seconds': perf_counter() - started}
    (HERE / f'{args.phase}-probe.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'phase': args.phase, 'seconds': result['seconds'],
                      'cases': [{k: row[k] for k in ('case', 'max_absolute_centroid_shift_m')}
                                for row in rows],
                      'all_old_hashes_equal': True,
                      'all_current_hashes_equal': args.phase == 'before'}))


if __name__ == '__main__':
    main()
