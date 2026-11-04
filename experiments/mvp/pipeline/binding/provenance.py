"""Frozen release identities independent of the engine's mutable manifests."""
import hashlib
import json
from pathlib import Path


PINS = Path(__file__).with_name('provenance-pins.json')
EXPECTED_PINS_SHA256 = 'eba0b3e076715b1b149e5f1242f56a18d6e5e9d3ab3e191af44d1ecbe012e874'
EXPECTED_ENGINE_MANIFEST_SHA256 = 'd4febd15222fd783584ddbffb8003d5e281baa7f7de95cb4aa5be7d536f0705b'
EXPECTED_NATIVE_MANIFEST_SHA256 = '55a2de7304500675dbfc1aaca14ec25e7ffe6e6ed2a04d0e88e58afc2eba6071'
EXPECTED_SOURCE_TREE_SHA256 = 'e768ee9e7a2ee2c06717c0192a4b91acc6400c29fb0808deed2c14be3ce9229a'


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(engine, root, manifest):
    """Check required files from frozen pins, never a refreshed native list."""
    errors = []
    def read(path, label):
        try:
            raw = path.read_bytes()
        except OSError as error:
            errors.append(f'{label}: {type(error).__name__}: {error}')
            return None, {}
        try:
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError('manifest must be an object')
        except (ValueError, TypeError) as error:
            errors.append(f'{label}: {type(error).__name__}: {error}')
            return raw, {}
        return raw, document

    pins_raw, pins = read(PINS, 'frozen adapter pins')
    if pins_raw is None or _digest(pins_raw) != EXPECTED_PINS_SHA256:
        errors.append('frozen adapter pin configuration digest mismatch')
        pins = {}
    engine_raw, declared = read(manifest, 'engine manifest')
    native_raw, native_declared = read(engine/'native-source-manifest.json', 'native manifest')
    engine_hash = _digest(engine_raw) if engine_raw is not None else None
    native_hash = _digest(native_raw) if native_raw is not None else None
    if engine_hash != EXPECTED_ENGINE_MANIFEST_SHA256:
        errors.append('engine manifest digest differs from frozen release')
    if native_hash != EXPECTED_NATIVE_MANIFEST_SHA256:
        errors.append('native manifest digest differs from frozen release')
    expected_sources = pins.get('engine_source_files', {})
    expected_native = pins.get('native_source_files', {})
    if declared.get('source_files') != expected_sources:
        errors.append('engine manifest required file/hash map differs from frozen release')
    if native_declared != expected_native:
        errors.append('native manifest required file/hash map differs from frozen release')

    def changed(base, expected):
        result = []
        for name, digest in expected.items():
            try:
                matches = _digest((base/name).read_bytes()) == digest
            except OSError:
                matches = False
            if not matches:
                result.append(name)
        return sorted(result)

    changed_engine = changed(engine, expected_sources)
    changed_native = changed(root, expected_native)
    try:
        from engine.provenance import source_digest
        actual = source_digest()
    except Exception as error:
        errors.append(f'engine source identity: {type(error).__name__}: {error}')
        actual = None
    identity = dict(manifest_sha256=engine_hash, native_manifest_sha256=native_hash,
        source_tree_sha256=actual, pins_sha256=_digest(pins_raw) if pins_raw is not None else None,
        engine_file_map_sha256=_digest(json.dumps(expected_sources, sort_keys=True).encode()),
        native_file_map_sha256=_digest(json.dumps(expected_native, sort_keys=True).encode()))
    return dict(identity, identity_sha256=_digest(json.dumps(identity, sort_keys=True).encode()),
        passed=bool(expected_sources and expected_native) and not errors and not changed_engine
            and not changed_native and actual == EXPECTED_SOURCE_TREE_SHA256,
        expected_manifest_sha256=EXPECTED_ENGINE_MANIFEST_SHA256,
        expected_native_manifest_sha256=EXPECTED_NATIVE_MANIFEST_SHA256,
        expected_source_tree_sha256=EXPECTED_SOURCE_TREE_SHA256,
        source_files=len(expected_sources), native_files=len(expected_native),
        observed_source_files=len(declared.get('source_files', {})) if isinstance(declared.get('source_files', {}), dict) else None,
        observed_native_files=len(native_declared), changed=changed_engine,
        changed_native=changed_native, errors=errors)


def same_identity(initial, final):
    """Comparing the whole claim also rejects changes hidden behind a pass flag."""
    fields = ('manifest_sha256', 'native_manifest_sha256', 'source_tree_sha256',
              'pins_sha256', 'engine_file_map_sha256', 'native_file_map_sha256',
              'identity_sha256', 'source_files', 'native_files')
    return bool(initial.get('passed') and final.get('passed')) and all(
        initial.get(field) == final.get(field) for field in fields)
