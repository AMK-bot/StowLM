"""A dispatch memo must still observe actual bytes and directory selection."""
from copy import deepcopy
import hashlib

import pytest

from stowage.linear import family_calibration as calibration


@pytest.fixture
def reference(monkeypatch, tmp_path):
    from stowage.linear import dependencies
    monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1')
    monkeypatch.delenv('STOWAGE_STABILITY_LIQUID_DIRECT', raising=False)
    monkeypatch.setattr(calibration, '_REFERENCE_INPUTS_MEMORY', {})
    epoch = ['physics-1']
    monkeypatch.setattr(dependencies, 'dependency_fingerprint', lambda artifact: epoch[0])
    manifest = tmp_path/'manifest.json'; manifest.write_text('manifest')
    grid = tmp_path/'grid-1.npz'; grid.write_bytes(b'grid-1')
    certificate = tmp_path/'certificate.json'; certificate.write_text('qualified')
    def paths(*unused):
        candidates = sorted(tmp_path.glob('grid-*.npz'))
        return (manifest, candidates[-1] if candidates else grid, certificate)
    monkeypatch.setattr(calibration, '_reference_paths', paths)
    calls = []
    def uncached(parameters):
        import os
        method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1'))
        direct = os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') == '1'
        files = {} if method == 2 or direct else {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for path in paths()}
        calls.append(deepcopy(parameters))
        return dict(method=method, direct=direct, files=files,
                    qualified=all(value is not None for value in files.values()),
                    dispatch=[dict(parameters=deepcopy(parameters), epoch=epoch[0])])
    monkeypatch.setattr(calibration, 'reference_inputs', uncached)
    return calls, epoch, manifest, grid, certificate, uncached


def test_content_memo_matches_original_and_returns_independent_copies(reference):
    calls, _, _, _, _, uncached = reference
    params = {'tank': [1., 2.]}
    expected = uncached(params)
    assert calibration.reference_inputs_cached(params) == expected
    count = len(calls)
    result = calibration.reference_inputs_cached(params)
    result['dispatch'][0]['parameters']['tank'][0] = 99.
    assert calibration.reference_inputs_cached(params) == expected
    assert len(calls) == count


@pytest.mark.parametrize('change', ['manifest', 'grid', 'certificate', 'removed',
                                  'new_candidate', 'parameters', 'source', 'method',
                                  'direct', 'callable'])
def test_all_dispatch_inputs_invalidate(reference, monkeypatch, change):
    calls, epoch, manifest, grid, certificate, original = reference
    params = {'tank': [1., 2.]}
    calibration.reference_inputs_cached(params)
    if change in ('manifest', 'grid', 'certificate'):
        path = {'manifest': manifest, 'grid': grid, 'certificate': certificate}[change]
        # Deliberately preserve length and timestamps: bytes are the identity.
        stat = path.stat(); path.write_bytes(b'x'*stat.st_size)
        import os
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == 'removed': certificate.unlink()
    elif change == 'new_candidate': grid.with_name('grid-2.npz').write_bytes(b'new-grid')
    elif change == 'parameters': params['tank'][0] = 3.
    elif change == 'source': epoch[0] = 'physics-2'
    elif change == 'method': monkeypatch.setenv('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '2')
    elif change == 'direct': monkeypatch.setenv('STOWAGE_STABILITY_LIQUID_DIRECT', '1')
    elif change == 'callable': monkeypatch.setattr(calibration, 'reference_inputs', lambda value: original(value))
    before = len(calls)
    current = calibration.reference_inputs_cached(params)
    assert len(calls) == before+1
    assert current == calibration.reference_inputs(params)


def test_racing_assets_are_rejected_without_caching(reference, monkeypatch):
    _, _, _, grid, _, original = reference
    def racing(parameters):
        result = original(parameters)
        grid.write_bytes(b'changed-after-qualification')
        return result
    monkeypatch.setattr(calibration, 'reference_inputs', racing)
    with pytest.raises(ValueError, match='assets changed'):
        calibration.reference_inputs_cached({})
    assert not calibration._REFERENCE_INPUTS_MEMORY
