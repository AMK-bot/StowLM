"""Production-hook contract using synthetic models; no numerical hull builds."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from stowage import cache_singleflight as locks
from stowage.model.fixtures import build_instance
from stowage.vessel import fleet_cache as candidate


@pytest.fixture(autouse=True)
def clear_cache():
    candidate.clear_vessel_memo()
    yield
    candidate.clear_vessel_memo()


def invoke(build, *, params=b'', hull=False):
    return candidate.cached_vessel('synthetic', slice_name='full', with_hull=hull,
                                  params_bytes=params, build=build)


@pytest.mark.parametrize('enabled', ['0', '1'])
def test_cached_json_and_mutation_isolation(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv('STOWAGE_VESSEL_SINGLEFLIGHT', enabled)
    monkeypatch.delenv('STOWAGE_VESSEL_CACHE', raising=False)
    monkeypatch.setattr(candidate, '_cache_key', lambda *args: 'synthetic-key')
    monkeypatch.setattr(candidate, '_cache_dir', lambda: tmp_path)
    vessel = build_instance().vessel
    expected = vessel.model_dump_json()
    calls = []

    def build():
        calls.append(True)
        return vessel.model_copy(deep=True)

    loaded = invoke(build)
    assert loaded.model_dump_json() == expected
    loaded.name = 'caller mutation'
    loaded.cells[0].lcg_m += 123
    assert invoke(build).model_dump_json() == expected
    candidate.clear_vessel_memo()
    assert invoke(build).model_dump_json() == expected
    assert len(calls) == 1


def test_waiter_revalidates_published_json(tmp_path, monkeypatch):
    vessel = build_instance().vessel
    monkeypatch.setattr(candidate, '_cache_key', lambda *args: 'synthetic-key')
    monkeypatch.setattr(candidate, '_cache_dir', lambda: tmp_path)

    @contextmanager
    def published(target):
        target.write_text(vessel.model_dump_json())
        yield True

    monkeypatch.setattr(locks, 'cache_singleflight', published)
    assert invoke(lambda: pytest.fail('Waiter rebuilt published entry')).model_dump_json() == vessel.model_dump_json()


@pytest.mark.parametrize('unwritable', [False, True])
def test_corrupt_or_unwritable_fallback(tmp_path, monkeypatch, unwritable):
    monkeypatch.setenv('STOWAGE_VESSEL_SINGLEFLIGHT', '1')
    monkeypatch.setattr(candidate, '_cache_key', lambda *args: 'synthetic-key')
    directory = tmp_path / 'cache'
    if unwritable:
        directory.write_text('not a directory')
    else:
        directory.mkdir()
        (directory / 'synthetic.full.geometry.synthetic-key.json').write_text('corrupt JSON')
    monkeypatch.setattr(candidate, '_cache_dir', lambda: directory)
    vessel = build_instance().vessel
    assert invoke(lambda: vessel).model_dump_json() == vessel.model_dump_json()


def test_missing_raw_before_any_lock_or_hit(tmp_path, monkeypatch):
    from stowage.vessel import hull_offsets

    def missing(*args, **kwargs):
        raise FileNotFoundError('missing pinned KCS input')

    @contextmanager
    def forbidden(target):
        pytest.fail('Missing raw input reached lock')
        yield True

    monkeypatch.setattr(hull_offsets, 'verified_offset_bytes', missing)
    monkeypatch.setattr(locks, 'cache_singleflight', forbidden)
    monkeypatch.setattr(candidate, '_cache_key', lambda *args: 'synthetic-key')
    monkeypatch.setattr(candidate, '_cache_dir', lambda: tmp_path)
    candidate._MEMO['synthetic-key'] = build_instance().vessel.model_dump_json()
    params = b'[hull_form]\nfamily="stowlm_offsets_v1"\noffsets_path="absent"\noffsets_sha256="bad"\n'
    with pytest.raises(FileNotFoundError, match='missing pinned KCS'):
        invoke(lambda: pytest.fail('must not build'), params=params, hull=True)


def test_explicit_cache_bypass(monkeypatch):
    monkeypatch.setenv('STOWAGE_VESSEL_CACHE', '0')
    monkeypatch.setenv('STOWAGE_VESSEL_SINGLEFLIGHT', '1')
    vessel = build_instance().vessel
    monkeypatch.setattr(locks, 'cache_singleflight', lambda *a: pytest.fail('Bypass locked'))
    assert invoke(lambda: vessel) is vessel


def test_both_helper_modules_are_fingerprinted():
    paths = {path.name for path in candidate._source_files()}
    assert {'toml_cache.py', 'cache_singleflight.py'} <= paths
