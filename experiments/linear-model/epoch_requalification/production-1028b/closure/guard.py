"""Read-only source guard and exact D30 closure inventory for this run.

Run with uv run python <this-file> --capture or --check. The baseline is
immutable unless --capture is explicitly requested. No cache/table is built.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = next(path for path in HERE.parents if (path / 'pyproject.toml').exists())


def snapshot():
    from stowage.linear import dependencies as deps
    from stowage.linear.boundary_calibration import BOUNDARY_CALIBRATION_VERSION
    from stowage.linear.margin_policy import LINEAR_MARGIN_POLICY_VERSION
    from stowage.checker.stability_liquid import STABILITY_LIQUID_MANIFEST_VERSION
    from stowage.checker.stability_liquid_tables import STABILITY_LIQUID_TABLES_VERSION
    from stowage import versions

    profiles = {
        name: {
            'fingerprint': deps.dependency_fingerprint(name),
            'versions': deps.builder_versions(name),
            'selectors': {f'src/stowage/{path}': symbols for path, symbols in profile.items()},
            'semantic_hashes': deps.dependency_contents(name),
        }
        for name, profile in deps._PROFILES.items()
    }
    # Broad source guard intentionally also detects changes outside D30's
    # scoped closure. Bank JSON/evidence outputs are expected run mutations.
    source_paths = set((ROOT / 'src/stowage').rglob('*.py'))
    source_paths.update((ROOT / 'src/stowage').rglob('*.toml'))
    source_paths.update((ROOT / 'experiments/linear-model').rglob('*.py'))
    source_paths.update((ROOT / 'params/fleet').glob('*.toml'))
    source_hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source_paths) if not path.resolve().is_relative_to(HERE.parent)
    }
    return {
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'profiles': profiles,
        'policy_versions': {
            'LINEAR_MARGIN_POLICY_VERSION': LINEAR_MARGIN_POLICY_VERSION,
            'BOUNDARY_CALIBRATION_VERSION': BOUNDARY_CALIBRATION_VERSION,
            'STABILITY_LIQUID_MANIFEST_VERSION': STABILITY_LIQUID_MANIFEST_VERSION,
            'STABILITY_LIQUID_TABLES_VERSION': STABILITY_LIQUID_TABLES_VERSION,
        },
        'global_versions_audit_only': {
            name: value for name, value in vars(versions).items()
            if name.endswith('_VERSION') and isinstance(value, str)
        },
        'source_hashes': source_hashes,
    }


def changes(before, after):
    return {
        key: {'before': before.get(key), 'after': after.get(key)}
        for key in sorted(before.keys() | after.keys())
        if before.get(key) != after.get(key)
    }


def strict_sources(before, after):
    """Freeze numerical/profile implementation and qualification source only.

    Concurrent D58 fleet/generate and global version releases are deliberately
    audit-only. Actual parameter values remain strict production inputs.
    """
    paths = {
        name
        for record in (before, after)
        for profile in record['profiles'].values()
        for name in profile['selectors']
    }
    paths.update('src/stowage/linear/' + name for name in (
        'dependencies.py', 'cache.py', 'margin_policy.py', 'boundary_calibration.py'))
    for record in (before, after):
        for name in record['source_hashes']:
            path = Path(name)
            if (path.parent == Path('experiments/linear-model/epoch_requalification')
                    and path.suffix == '.py'):
                paths.add(name)
            if (path.parent == Path('experiments/linear-model')
                    and path.suffix == '.py'):
                paths.add(name)
            if path.parent == Path('params/fleet'):
                paths.add(name)
    return paths


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.capture == args.check:
        parser.error('choose exactly one of --capture or --check')
    current = json.loads(json.dumps(snapshot()))
    baseline_path = HERE / 'start.json'
    if args.capture:
        if baseline_path.exists():
            raise SystemExit('baseline exists; refuse to overwrite')
        baseline_path.write_text(json.dumps(current, indent=2) + '\n')
        print(json.dumps({'baseline': str(baseline_path.relative_to(ROOT)),
                          'source_files': len(current['source_hashes']),
                          'profiles': list(current['profiles'])}))
        return
    before = json.loads(baseline_path.read_text())
    raw_changes = changes(before['source_hashes'], current['source_hashes'])
    strict = strict_sources(before, current)
    report = {
        'start_utc': before['captured_utc'],
        'checked_utc': current['captured_utc'],
        'source_changes': raw_changes,
        'strict_source_changes': {key: value for key, value in raw_changes.items()
                                  if key in strict},
        'audit_only_source_changes': {key: value for key, value in raw_changes.items()
                                     if key not in strict},
        'profile_changes': changes(before['profiles'], current['profiles']),
        'policy_version_changes': changes(before['policy_versions'], current['policy_versions']),
        'global_version_changes_audit_only': changes(
            before['global_versions_audit_only'], current['global_versions_audit_only']),
    }
    report['passed'] = not any(report[key] for key in (
        'strict_source_changes', 'profile_changes', 'policy_version_changes'))
    (HERE / 'latest-check.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
