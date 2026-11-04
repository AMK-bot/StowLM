"""Validate the authoritative task2.1 freeze before the single sealed release.

Only the launcher process installs the compatibility/report hooks below;
the planner and independent-audit implementations remain unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib
import json
from pathlib import Path
from unittest.mock import patch

from experiments.mvp.pipeline.sealed.common import (
    HERE, HarnessError, atomic_json, read_json, sha256)
from experiments.mvp.pipeline.sealed import driver
from experiments.mvp.pipeline.sealed.release import release
from experiments.mvp.pipeline.sealed.preflight import check, wait_for_drain, admission_bridge

PROVENANCE = ('manifest_sha256', 'tree_digest')


def require_frozen(manifest):
    """Lazy dependency: absent verifier or drift cannot spend a sealed claim."""
    path = Path(manifest)
    if driver.is_sealed(path) or path.resolve().is_relative_to(HERE/'runs'):
        raise HarnessError('A sealed input or result cannot serve as the task2.1 manifest')
    try:
        verify = importlib.import_module('experiments.mvp.pipeline.freeze.verify')
        check = getattr(verify, 'require_frozen')
    except (ImportError, AttributeError) as error:
        raise HarnessError('Task2.1 require_frozen is unavailable; no sealed claim consumed') from error
    try:
        identity = check(manifest, full_weights=True)
    except RuntimeError as error:
        if hasattr(error, 'drifts'):
            raise HarnessError('Frozen pipeline drift: '+json.dumps(error.drifts, sort_keys=True)) from error
        raise HarnessError('Frozen pipeline verification failed: '+str(error)) from error
    if (not isinstance(identity, dict) or any(
            not isinstance(identity.get(k), str) or len(identity[k]) != 64 or
            any(c not in '0123456789abcdef' for c in identity[k]) for k in PROVENANCE)):
        raise HarnessError('Task2.1 verifier returned an invalid frozen identity')
    identity = {key: identity[key] for key in PROVENANCE}
    if sha256(manifest) != identity['manifest_sha256']:
        raise HarnessError('Freeze manifest changed after verification')
    return identity


def _manifest(manifest, identity):
    """Use p21's inventory and import closure, not the superseded sealed census."""
    name = 'experiments.mvp.pipeline.sealed.protocol'
    if not (HERE/'protocol.py').is_file():
        name = 'experiments.mvp.pipeline.sealed.fixtures.d275.protocol'
    protocol = importlib.import_module(name)
    data, files = protocol.verify_pipeline_manifest(manifest)
    if sha256(manifest) != identity['manifest_sha256'] or data.get('tree_sha256') != identity['tree_digest']:
        raise HarnessError('Frozen pipeline identity changed before release')
    return data, files


def _tag(row, identity):
    if (isinstance(row, dict) and all(key in row for key in ('map_id', 'arm', 'status', 'map_sha256'))):
        if any(key in row and row[key] != value for key, value in identity.items()):
            raise HarnessError('Result row carries a different frozen identity')
        return dict(row, **identity)
    return row


@contextmanager
def _release_context(manifest, identity):
    """Keep the active study's driver/worker/report source files unchanged."""
    from experiments.mvp.pipeline.sealed import report
    write = driver.atomic_json
    child = driver.run_child
    render = report.write_report

    def write_bound(path, value):
        if isinstance(value, list):
            value = [_tag(row, identity) for row in value]
        else:
            value = _tag(value, identity)
        return write(path, value)

    def run_bound(job, out_dir, *args, **kwargs):
        try:
            result = child(job, out_dir, *args, **kwargs)
        finally:
            # Bind raw committed bytes BEFORE the independent audit hashes them.
            # Preserve the worker's original bytes as separate provenance.
            path = Path(out_dir)/'row.json'
            if not kwargs.get('prepare') and path.is_file():
                raw = read_json(path)
                tagged = _tag(raw, identity)
                if tagged != raw:
                    original = path.with_name('worker-row.original.json')
                    with original.open('xb') as stream:
                        stream.write(path.read_bytes())
                    tagged['worker_original_sha256'] = sha256(original)
                    atomic_json(path, tagged)
        return _tag(result, identity)

    def report_bound(rows, out_dir, *, metadata=None):
        rows = [_tag(row, identity) for row in rows]
        metadata = dict(metadata or {}, **identity)
        summary = render(rows, out_dir, metadata=metadata)
        summary.update(identity)
        atomic_json(Path(out_dir)/'report.json', summary)
        markdown = Path(out_dir)/'REPORT.md'
        prefix = ('Frozen manifest SHA-256: '+identity['manifest_sha256']+'\n\n'
                  'Frozen tree digest: '+identity['tree_digest']+'\n\n')
        markdown.write_text(prefix+markdown.read_text())
        return summary

    def verify_bound(path):
        if Path(path).resolve() != manifest:
            raise HarnessError('Cannot substitute the task2.1 freeze manifest')
        return _manifest(manifest, identity)

    with patch.object(driver, 'verify_pipeline_manifest', verify_bound), \
            patch.object(driver, 'run_child', run_bound), \
            patch.object(driver, 'atomic_json', write_bound), \
            patch.object(report, 'CSV_FIELDS', report.CSV_FIELDS+tuple(
                key for key in PROVENANCE if key not in report.CSV_FIELDS)), \
            patch.object(report, 'write_report', report_bound):
        yield


def launch(manifest, *, study=None, out_dir=None, attest_idle=False):
    # P21 permits its public epoch manifests under freeze/runs/. Its verifier
    # owns symlink and private-data checks; do not reuse the dev-input runs ban.
    manifest = Path(manifest).absolute()
    if not manifest.is_file():
        raise HarnessError('Task2.1 manifest is absent; sealed launch blocked before claim')
    identity = require_frozen(manifest)  # Before study/config/release or any sealed opening.
    config_path = driver.public_path(study)/'launch-config.json' if study else HERE/'launch-config.json'
    config = read_json(config_path)
    wait_for_drain()
    blockers = check(config, operational=False)
    if blockers:
        raise HarnessError('; '.join(blockers))
    config = dict(config, pipeline_manifest=str(manifest), **identity)
    with admission_bridge(config.get('admission_path', HERE/'measurements/release-admission.json'),
                          attested=attest_idle), _release_context(manifest, identity):
        _, files = driver.verify_pipeline_manifest(manifest)
        driver.validate_release_support(config, dict(pipeline_files=files))
        return release(config, out_dir or HERE/'runs/final')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--manifest')
    cli.add_argument('--study')
    cli.add_argument('--out-dir')
    cli.add_argument('--check-only', action='store_true', help='verify2.1 before detaching the launch')
    cli.add_argument('--preflight', action='store_true', help='check public prerequisites except manifest; no GPU or sealed access')
    cli.add_argument('--attest-idle-fleet', action='store_true', help='operator confirms other fleet work stopped and GPU free')
    args = cli.parse_args()
    try:
        if args.preflight:
            blockers = check(read_json(HERE/'launch-config.json'))
            print('READY' if not blockers else 'BLOCKERS\n'+'\n'.join('- '+b for b in blockers))
            if blockers:
                cli.exit(2)
        elif not args.manifest:
            cli.error('--manifest is required for release')
        elif args.check_only:
            require_frozen(Path(args.manifest).absolute())
        else:
            launch(args.manifest, study=args.study, out_dir=args.out_dir, attest_idle=args.attest_idle_fleet)
    except (HarnessError, OSError, ValueError, KeyError) as error:
        cli.exit(2, f'Sealed launch blocked: {error}\n')


if __name__ == '__main__':
    main()
