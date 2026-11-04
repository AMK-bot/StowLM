"""Cheap public lock tests, with no experiment or numerical-builder dependency."""
from __future__ import annotations

import fcntl
import os

import pytest

from stowage import cache_singleflight as locks


def test_default_off_does_not_create_cache_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("STOWAGE_VESSEL_SINGLEFLIGHT", raising=False)
    target = tmp_path / "absent" / "cache.json"
    with locks.cache_singleflight(target) as acquired:
        assert acquired is False
    assert not target.parent.exists()


def test_lock_released_after_guarded_exception(tmp_path):
    target = tmp_path / "cache.json"
    with pytest.raises(ValueError, match="builder failed"):
        with locks.cache_singleflight(target, enabled=True) as acquired:
            assert acquired
            raise ValueError("builder failed")
    descriptor = os.open(locks.lock_path(target), os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)
    assert locks.lock_path(target).is_file()  # Never remove the shared inode.


def test_unavailable_lock_falls_back_and_body_exception_propagates(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("read-only cache")

    monkeypatch.setattr(locks.os, "open", denied)
    with pytest.raises(ValueError, match="body failed"):
        with locks.cache_singleflight(tmp_path / "cache.json", enabled=True) as acquired:
            assert acquired is False
            raise ValueError("body failed")


def test_interrupted_acquisition_closes_descriptor_and_reraises(tmp_path, monkeypatch):
    descriptors = []
    original_open = os.open

    def tracked_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("cancelled lock wait")

    monkeypatch.setattr(locks.os, "open", tracked_open)
    monkeypatch.setattr(fcntl, "flock", interrupted)
    with pytest.raises(KeyboardInterrupt, match="cancelled lock wait"):
        with locks.cache_singleflight(tmp_path / "cache.json", enabled=True):
            pytest.fail("Interrupted lock wait entered guarded body")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


