"""Check a frozen pipeline at launch, without loading a model or running a suite."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

try:
    from . import build, evidence, links, runtime_outputs
    from .paths import reject_symlinks
except ImportError:
    import build
    from experiments.mvp.pipeline.freeze import evidence, links, runtime_outputs
    from paths import reject_symlinks

ROOT = Path(__file__).resolve().parents[4]
SCHEMA = 'stowlm.pipeline-freeze/1'


class FreezeDrift(RuntimeError):
    """A launch refused because its pinned pipeline is unavailable or changed."""
    def __init__(self, drifts: list[dict]):
        self.drifts = drifts
        super().__init__('Frozen pipeline verification failed: ' + '; '.join(
            f"{row['path']}: {row['reason']}" for row in drifts))


def _read_manifest(path: Path, root: Path) -> bytes:
    # A manifest may be supplied separately from a relocated repository. In both
    # cases reject links before opening any bytes.
    anchor = root if path.absolute().is_relative_to(root) else Path(path.absolute().anchor)
    reject_symlinks(path, anchor)
    return path.read_bytes()


def _evidence_drift(error: str, root: Path, *, resolved=False) -> dict:
    label, separator, path = error.partition(': ')
    if label == 'unpinned fill source':
        path = path.rsplit(': ', 1)[-1]
    elif label == 'fill value has no source path':
        path = path.split(': ', 1)[0]
    if not separator:
        path = '@evidence'
    if Path(path).is_absolute() and Path(path).is_relative_to(root):
        path = Path(path).relative_to(root).as_posix()
    reason = ('evidence_error_removed' if resolved else
              'symlink' if label == 'symlink excluded' else
              'missing_evidence' if label == 'missing film source' else
              'unpinned_evidence' if label == 'unpinned fill source' else 'unsafe_evidence')
    return {'path': path, 'reason': reason, 'detail': error}


def _pipeline_layer_drifts(manifest: dict, *, root: Path, check_weights: bool,
                           verified_pipeline: dict | None = None) -> list[dict]:
    scope = manifest.get('scope', 'pipeline')
    layer = manifest.get('pipeline_layer')
    if scope == 'pipeline':
        return ([{'path': '@manifest.pipeline_layer', 'reason': 'pipeline_layer_on_pipeline'}]
                if layer is not None else [])
    if scope != 'film':
        return [{'path': '@manifest.scope', 'reason': 'unsupported_scope'}]
    try:
        if not isinstance(layer, dict) or not all(isinstance(layer.get(key), str) and layer[key]
                                                 for key in ('path', 'manifest_sha256', 'tree_digest')):
            raise ValueError('film scope requires a pipeline layer path, manifest hash and tree digest')
        name = layer['path']
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or not name.startswith(build.FREEZE + '/') or relative.suffix != '.json'):
            raise ValueError('pipeline layer must name a canonical JSON path inside freeze')
        path = root / relative
        raw = _read_manifest(path, root)
        pipeline = json.loads(raw)
        # Read and reject another film before invoking its guard, so cycles can
        # never recurse. Pipeline manifests themselves cannot contain a layer.
        if pipeline.get('scope', 'pipeline') != 'pipeline':
            return [{'path': name, 'reason': 'pipeline_layer_scope'}]
        drifts = []
        actual = hashlib.sha256(raw).hexdigest()
        if actual != layer['manifest_sha256']:
            drifts.append({'path': name, 'reason': 'pipeline_manifest_changed',
                           'expected': layer['manifest_sha256'], 'actual': actual})
        if pipeline.get('tree_sha256') != layer['tree_digest']:
            drifts.append({'path': name, 'reason': 'pipeline_tree_changed',
                           'expected': layer['tree_digest'], 'actual': pipeline.get('tree_sha256')})
        try:
            identity = require_frozen(path, root=root, full_weights=check_weights)
        except FreezeDrift as error:
            drifts.extend(error.drifts)
        else:
            # The dependency's own immutable-read check covers its verification;
            # compare that exact verified identity with the pinned reference too.
            if identity['manifest_sha256'] != actual:
                drifts.append({'path': name, 'reason': 'pipeline_manifest_changed_during_verification'})
            elif not drifts and verified_pipeline is not None:
                verified_pipeline.update(pipeline)
        return drifts
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return [{'path': '@manifest.pipeline_layer', 'reason': 'invalid_pipeline_layer', 'detail': str(error)}]


def _verify(manifest: dict, *, root: Path, check_weights: bool,
            strict_evidence: bool = False) -> list[dict]:
    files = manifest['files']
    sources = manifest['source_files']
    if not isinstance(files, dict) or not isinstance(sources, dict):
        raise ValueError('files and source_files must be objects')
    if not all(isinstance(name, str) and isinstance(digest, str) for name, digest in files.items()):
        raise ValueError('file paths and hashes must be strings')
    if not all(isinstance(name, str) and isinstance(digest, str) for name, digest in sources.items()):
        raise ValueError('source paths and hashes must be strings')
    pinned_links = manifest.get('symlinks', {})
    if not isinstance(pinned_links, dict) or not all(
            isinstance(name, str) and isinstance(record, dict) and
            all(isinstance(record.get(key), str) for key in ('link_text', 'resolved_target', 'target_sha256'))
            for name, record in pinned_links.items()):
        raise ValueError('symlinks must map paths to link text, target and target hash')
    scope = manifest.get('scope', 'pipeline')
    pipeline_pins = {}
    drifts = _pipeline_layer_drifts(manifest, root=root, check_weights=check_weights,
                                   verified_pipeline=pipeline_pins)
    pinned_errors = manifest.get('evidence', {}).get('errors', [])
    if not isinstance(pinned_errors, list) or not all(isinstance(error, str) for error in pinned_errors):
        raise ValueError('evidence errors must be a list of strings')
    product_errors = manifest.get('evidence', {}).get('product_output_bindings', {}).get('errors', [])
    if not isinstance(product_errors, list) or not all(isinstance(error, str) for error in product_errors):
        raise ValueError('product binding errors must be a list of strings')
    pinned_errors = sorted(set(pinned_errors) | set(product_errors))
    weights = set(manifest.get('model_weight_files', []))
    weights.update(manifest.get('base_model', {}).get('weight_files', {}))
    # Legacy manifests may only include adapter weights among their file hashes.
    weights.update(name for name in files if Path(name).suffix in build.WEIGHT_SUFFIXES)
    stats = manifest.get('weight_stats', {})
    if not isinstance(stats, dict):
        raise ValueError('weight_stats must be an object')
    expected = set(files) | weights
    closure = manifest.get('selection', {}).get('import_closure', {}).get('paths')
    if scope == 'pipeline' and closure is not None:
        closure = [name for name in closure if not name.startswith(('app/', 'experiments/mvp/ui/'))]
    try:
        # File hashing below still reports a changed config. Retain only an
        # independently hash-bound old snapshot, never live policy expansions.
        runtime_policy = runtime_outputs.from_manifest(root, manifest, allow_changed_config=True)
    except (OSError, ValueError, KeyError, TypeError) as error:
        drifts.append({'path': '@manifest.selection.runtime_outputs', 'reason': 'invalid_runtime_policy',
                       'detail': str(error)})
        # A malformed or changed policy never expands the names ignored by the
        # guard. In particular, legacy manifests do not inherit the live config.
        runtime_policy = {'directories': []}
    checked_dirs = set()

    def check_file(name):
        from experiments.mvp.pipeline.freeze.product_outputs import excluded_all
        if excluded_all(name):
            return {'path': name, 'reason': 'excluded_path'}
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            return {'path': name, 'reason': 'invalid_path'}
        path = root / relative
        try:
            reject_symlinks(path, root, checked=checked_dirs)
        except ValueError as error:
            return {'path': name, 'reason': 'symlink', 'detail': str(error)}
        wanted = files.get(name)
        try:
            if name in weights:
                actual_stat = path.stat()
                wanted_stat = stats.get(name)
                if not isinstance(wanted_stat, dict):
                    return {'path': name, 'reason': 'weight_stat_not_pinned'}
                actual = {'exists': True, 'size': actual_stat.st_size, 'mtime_ns': actual_stat.st_mtime_ns}
                stat_changed = any(actual[key] != wanted_stat.get(key) for key in actual)
                if not check_weights:
                    return ({'path': name, 'reason': 'changed', 'expected_stat': wanted_stat,
                             'actual_stat': actual} if stat_changed else None)
                if wanted is None:
                    return {'path': name, 'reason': 'weight_not_hashed'}
            actual = build.sha256_file(path)
            if name in weights and stat_changed:
                return {'path': name, 'reason': 'changed', 'expected_stat': wanted_stat,
                        'actual_stat': {'exists': True, 'size': actual_stat.st_size, 'mtime_ns': actual_stat.st_mtime_ns},
                        'expected': wanted, 'actual': actual}
        except FileNotFoundError:
            return {'path': name, 'reason': 'missing', 'expected': wanted}
        except OSError as error:
            return {'path': name, 'reason': 'unreadable', 'detail': str(error)}
        if actual != wanted:
            return {'path': name, 'reason': 'changed', 'expected': wanted, 'actual': actual}
        return None

    def scan_inventory():
        selection = evidence.inventory(root, scope=scope)
        if scope == 'film':
            bindings = evidence.product_output_bindings(
                root, files, manifest.get('pipeline_layer', {}).get('manifest_sha256'),
                pipeline_manifest=pipeline_pins)
            selection['errors'] = sorted(set(selection['errors']) | set(bindings['errors']))
            selection['lattice_provenance'] = bindings.get('lattice_provenance', {})
        current = set(build.inventory_paths(root, no_weights=False, import_closure=closure,
                                            evidence_selection=selection, scope=scope,
                                            runtime_dirs=runtime_policy['directories']))
        current_links = set(build.inventory_symlinks(root, import_closure=closure,
                                                     evidence_selection=selection, scope=scope,
                                                     runtime_dirs=runtime_policy['directories']))
        runtime_files = set(runtime_outputs.enumerate(root, runtime_policy))
        runtime_sources = {name for name in runtime_files if runtime_outputs.source_path(name, root=root)}
        return selection, current | current_links | runtime_files, runtime_sources

    def check_link(name):
        wanted = pinned_links[name]
        if name in expected:
            return {'path': name, 'reason': 'inconsistent_symlink_inventory'}
        if name in (closure or []):
            return {'path': name, 'reason': 'import_symlink'}
        try:
            # Inspect always hashes the actual target, including a weight whose
            # modification time was restored. Only independently pinned files
            # may be targets; ancestor and directory links remain forbidden.
            actual = links.inspect(root, name, files)
        except (OSError, ValueError) as error:
            return {'path': name, 'reason': 'unsafe_symlink', 'detail': str(error)}
        if actual != wanted:
            return {'path': name, 'reason': 'changed_symlink', 'expected': wanted, 'actual': actual}
        return None

    # Walk names concurrently with byte hashing; neither check substitutes file
    # metadata for a source hash. Full launch streams every model weight too.
    with ThreadPoolExecutor(max_workers=5) as pool:
        inventory = pool.submit(scan_inventory)
        drifts.extend(row for row in pool.map(check_file, sorted(expected)) if row is not None)
        drifts.extend(row for row in pool.map(check_link, sorted(pinned_links)) if row is not None)
        try:
            current_evidence, current, runtime_sources = inventory.result()
        except (OSError, ValueError, KeyError, TypeError) as error:
            drifts.append({'path': '@inventory', 'reason': 'unreadable', 'detail': str(error)})
            current_evidence, current, runtime_sources = {'errors': []}, set(), set()
    pinned_errors, current_errors = set(pinned_errors), set(current_evidence['errors'])
    if scope == 'film':
        pinned_lattice = manifest.get('evidence', {}).get('lattice_provenance', {})
        actual_lattice = current_evidence.get('lattice_provenance', {})
        if not isinstance(pinned_lattice, dict):
            raise ValueError('lattice_provenance must be an object')
        for name in sorted(set(pinned_lattice) | set(actual_lattice)):
            if pinned_lattice.get(name) != actual_lattice.get(name):
                drifts.append({'path': name, 'reason': 'lattice_provenance_changed',
                               'expected': pinned_lattice.get(name), 'actual': actual_lattice.get(name)})
        aliases = manifest.get('evidence', {})
        for label, recorded in (('fixture_provenance', aliases.get('fixture_provenance')),
                                ('product_output_bindings.lattice_provenance', aliases.get('product_output_bindings', {}).get('lattice_provenance'))):
            if recorded is not None:
                if not isinstance(recorded, dict):
                    raise ValueError(label + ' must be an object')
                for name, actual in sorted(actual_lattice.items()):
                    if recorded.get(name) != actual:
                        drifts.append({'path': name, 'reason': 'inconsistent_lattice_provenance', 'field': label})
    if strict_evidence or manifest.get('dry_run') is not True:
        drifts.extend(_evidence_drift(error, root) for error in sorted(pinned_errors | current_errors))
    else:
        drifts.extend(_evidence_drift(error, root) for error in sorted(current_errors - pinned_errors))
        drifts.extend(_evidence_drift(error, root, resolved=True)
                      for error in sorted(pinned_errors - current_errors))
    for name in sorted(runtime_sources):
        drifts.append({'path': name, 'reason': 'runtime_source'})
    for name in sorted(current - expected - set(pinned_links) - runtime_sources):
        if not runtime_outputs.is_ignored_addition(name, runtime_policy, root=root):
            drifts.append({'path': name, 'reason': 'added'})
    digest = build.tree_digest(sources)
    if digest != manifest.get('tree_sha256'):
        drifts.append({'path': '@manifest.tree_sha256', 'reason': 'invalid_digest',
                       'expected': manifest.get('tree_sha256'), 'actual': digest})
    for name, digest in sorted(sources.items()):
        if files.get(name) != digest:
            drifts.append({'path': name, 'reason': 'inconsistent_source_hash'})
    return sorted(drifts, key=lambda drift: (drift['path'], drift['reason']))


def verify(manifest_path: str | Path, *, check_weights: bool = False,
           root: str | Path | None = None) -> list[dict]:
    """List all drift; always stat weights, optionally stream their hashes.

    Dry manifests may be verified for inventory development. They cannot pass the
    launch guard, and a full check reports any omitted weight hash.
    """
    root = ROOT if root is None else Path(root).resolve()
    try:
        manifest = json.loads(_read_manifest(Path(manifest_path), root))
        return _verify(manifest, root=root, check_weights=check_weights)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return [{'path': str(manifest_path), 'reason': 'invalid_manifest', 'detail': str(error)}]


def require_frozen(manifest_path, *, root=None, full_weights=False) -> dict:
    """Return the verified manifest identity or raise FreezeDrift before launch."""
    root = ROOT if root is None else Path(root).resolve()
    path = Path(manifest_path)
    drifts = []
    try:
        raw = _read_manifest(path, root)
        manifest = json.loads(raw)
        if manifest.get('schema_version') != SCHEMA:
            drifts.append({'path': '@manifest.schema_version', 'reason': 'unsupported_schema'})
        if manifest.get('task') != '2.1' or manifest.get('status') != 'frozen':
            drifts.append({'path': '@manifest.status', 'reason': 'not_frozen'})
        if manifest.get('dry_run') is not False:
            drifts.append({'path': '@manifest.dry_run', 'reason': 'dry_manifest'})
        if manifest.get('readiness', {}).get('ready') is not True:
            drifts.append({'path': '@manifest.readiness', 'reason': 'not_ready'})
        drifts.extend(_verify(manifest, root=root, check_weights=full_weights, strict_evidence=True))
        # Return the digest of the exact bytes checked, and refuse if an external
        # writer replaced the manifest during the inventory check.
        if _read_manifest(path, root) != raw:
            drifts.append({'path': str(path), 'reason': 'manifest_changed_during_verification'})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        drifts.append({'path': str(path), 'reason': 'invalid_manifest', 'detail': str(error)})
    if drifts:
        raise FreezeDrift(sorted(drifts, key=lambda row: (row['path'], row['reason'])))
    return {'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'tree_digest': manifest['tree_sha256']}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?', type=Path, default=Path(__file__).with_name('manifest-pipeline.json'))
    parser.add_argument('--weights', action='store_true', help='also stream every pinned model weight')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--require-frozen', action='store_true', help='refuse dry or unready manifests')
    args = parser.parse_args()
    if args.require_frozen:
        try:
            result = require_frozen(args.manifest, root=args.root, full_weights=args.weights)
        except FreezeDrift as error:
            print(json.dumps({'ok': False, 'drifts': error.drifts}, sort_keys=True, indent=2))
            raise SystemExit(1)
        print(json.dumps({'ok': True, **result}, sort_keys=True, indent=2))
        return
    drifts = verify(args.manifest, check_weights=args.weights, root=args.root)
    print(json.dumps({'ok': not drifts, 'drifts': drifts}, sort_keys=True, indent=2))
    raise SystemExit(bool(drifts))


if __name__ == '__main__':
    main()
