"""A content cache preserves TOML values, file freshness and caller isolation."""

import io
import os
import tomllib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from stowage import toml_cache


@pytest.fixture(autouse=True)
def isolated_cache():
    toml_cache.clear_cache()
    yield
    toml_cache.clear_cache()


def test_values_and_nested_mutation_are_identical_to_tomllib():
    payload = b'''title = "ship"
enabled = true
count = 3
fraction = 1.25
day = 2026-10-29
local_time = 11:32:00
instant = 2026-10-29T11:32:00Z
[nested]
values = [{ name = "box", mass = 2 }, { name = "other", mass = 3 }]
'''
    expected = tomllib.load(io.BytesIO(payload))
    first = toml_cache.load_bytes(payload)
    assert first == expected
    first["nested"]["values"][0]["mass"] = 999
    first["nested"]["values"].append({"name": "caller-only"})
    second = toml_cache.load_bytes(payload)
    assert second == expected
    second["nested"].clear()
    assert toml_cache.load_bytes(payload) == expected
    assert toml_cache.cache_info().hits == 2
    assert toml_cache.cache_info().misses == 1


def test_identical_contents_share_parse_across_paths(tmp_path, monkeypatch):
    calls = []
    original = tomllib.loads

    def tracked(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(tomllib, "loads", tracked)
    one, two = tmp_path / "one.toml", tmp_path / "two.toml"
    one.write_bytes(b"value = 1\n")
    two.write_bytes(one.read_bytes())
    assert toml_cache.load(one) == toml_cache.load(str(two)) == {"value": 1}
    assert len(calls) == 1


def test_same_size_same_mtime_edit_cannot_reuse_old_contents(tmp_path):
    path = tmp_path / "params.toml"
    path.write_bytes(b"value = 1\n")
    original_stat = path.stat()
    assert toml_cache.load(path) == {"value": 1}
    path.write_bytes(b"value = 2\n")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert path.stat().st_size == original_stat.st_size
    assert toml_cache.load(path) == {"value": 2}
    assert toml_cache.cache_info().misses == 2


@pytest.mark.parametrize("payload", [b"key = [", b"key = '\xff'", b"\xef\xbb\xbfkey = 1"])
def test_failed_parse_preserves_error_and_does_not_cache(payload):
    with pytest.raises(Exception) as expected:
        tomllib.load(io.BytesIO(payload))
    for _ in range(2):
        with pytest.raises(type(expected.value)) as actual:
            toml_cache.load_bytes(payload)
        assert str(actual.value) == str(expected.value)
    assert toml_cache.cache_info().currsize == 0
    assert toml_cache.cache_info().misses == 2


def test_cache_is_bounded_and_least_recently_used(monkeypatch):
    monkeypatch.setattr(toml_cache, "_MAX_ENTRIES", 2)
    for value in (1, 2, 1, 3, 1, 2):
        assert toml_cache.load_bytes(f"value = {value}".encode()) == {"value": value}
    info = toml_cache.cache_info()
    assert (info.hits, info.misses, info.maxsize, info.currsize) == (2, 4, 2, 2)


def test_concurrent_callers_parse_once_and_get_independent_trees(monkeypatch):
    barrier = Barrier(4)
    calls = []
    original = tomllib.loads

    def tracked(text):
        calls.append(text)
        return original(text)

    def read(_):
        barrier.wait()
        return toml_cache.load_bytes(b"rows = [{ value = 1 }]")

    monkeypatch.setattr(tomllib, "loads", tracked)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(read, range(4)))
    assert len(calls) == 1
    results[0]["rows"][0]["value"] = 99
    assert all(result == {"rows": [{"value": 1}]} for result in results[1:])
    assert (toml_cache.cache_info().hits, toml_cache.cache_info().misses) == (3, 1)


def test_clear_cache_resets_entries_and_counters():
    toml_cache.load_bytes(b"value = 1")
    toml_cache.clear_cache()
    assert tuple(toml_cache.cache_info()) == (0, 0, 128, 0)


def test_selected_nested_value_is_isolated_and_shares_the_full_parse():
    payload = b"kind = 'cargo'\nrows = [{ values = [1, 2] }]"
    selected = toml_cache.load_value(payload, "rows")
    selected[0]["values"].append(999)
    assert toml_cache.load_bytes(payload) == {"kind": "cargo", "rows": [{"values": [1, 2]}]}
    assert toml_cache.load_value(payload, "rows") == [{"values": [1, 2]}]
    assert toml_cache.load_value(payload, "kind") == "cargo"
    assert (toml_cache.cache_info().hits, toml_cache.cache_info().misses) == (3, 1)


def test_missing_selected_value_copies_the_callers_default():
    default = {"values": [1]}
    selected = toml_cache.load_value(b"value = 1", "missing", default)
    selected["values"].append(2)
    assert default == {"values": [1]}
    assert toml_cache.load_value(b"value = 1", "missing", default) == {"values": [1]}
    assert toml_cache.load_value(b"value = 1", "missing") is None
    assert toml_cache.load_bytes(b"value = 1") == {"value": 1}


def test_selected_value_copies_only_the_requested_value(monkeypatch):
    copied = []
    original = toml_cache.deepcopy

    def tracked(value):
        copied.append(value)
        return original(value)

    monkeypatch.setattr(toml_cache, "deepcopy", tracked)
    assert toml_cache.load_value(b"small = { value = 1 }\nlarge = [1, 2, 3]", "small") == {"value": 1}
    assert copied == [{"value": 1}]


def test_default_copy_error_does_not_corrupt_cached_parameters():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("uncopyable default")

    payload = b"present = { values = [1] }"
    default = Uncopyable()
    assert toml_cache.load_value(payload, "present", default) == {"values": [1]}
    with pytest.raises(RuntimeError, match="uncopyable default"):
        toml_cache.load_value(payload, "missing", default)
    assert toml_cache.load_bytes(payload) == {"present": {"values": [1]}}


@pytest.mark.parametrize("payload", [b"key = [", b"key = '\xff'", b"\xef\xbb\xbfkey = 1"])
def test_selected_value_preserves_parse_errors_and_does_not_cache(payload):
    with pytest.raises(Exception) as expected:
        tomllib.load(io.BytesIO(payload))
    with pytest.raises(type(expected.value)) as actual:
        toml_cache.load_value(payload, "missing", {})
    assert str(actual.value) == str(expected.value)
    assert toml_cache.cache_info().currsize == 0
