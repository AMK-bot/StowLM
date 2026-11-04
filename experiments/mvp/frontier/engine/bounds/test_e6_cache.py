"""Reuse only native E6 witnesses bound to the same immutable input objects."""
import json
from types import SimpleNamespace

from . import physical_capacity as physical
from . import port_capacity as ports
from .test_hatch import fixture


def test_cross_instance_or_table_identity_invalidates_witness_cache():
    cache = physical.E6WitnessCache()
    one, two = object(), object()
    tables = SimpleNamespace(e6_threshold=20000., e6_fallback=True, static=object())
    other = SimpleNamespace(e6_threshold=20000., e6_fallback=True, static=object())
    calls = []
    def native(instance, table):
        calls.append((instance, table))
        return {'box': ('cover',)}
    first, hit = cache.resolve(one, tables, native)
    assert not hit
    first.clear()  # Returned maps cannot mutate cached certificates.
    assert cache.resolve(one, tables, native) == ({'box': ('cover',)}, True)
    assert cache.resolve(two, tables, native)[1] is False
    assert cache.resolve(two, other, native)[1] is False
    assert len(calls) == 3 and cache.hits == 1 and cache.misses == 3


def test_reused_physical_witnesses_produce_same_hall_bound_as_fresh_native_query(monkeypatch):
    from stowage.feasibility.tables import build_tables
    from stowage.rules.catalogue import rule_freeze_v1
    instance = fixture()
    instance.containers[0].pod = 'THSGZ'
    for box in instance.containers:
        box.gross_mass_kg = 23000.
    native = physical._e6_witness_covers
    calls = []
    def counted(*args):
        calls.append(True)
        return native(*args)
    monkeypatch.setattr(physical, '_e6_witness_covers', counted)
    with rule_freeze_v1(enabled=False):
        tables = build_tables(instance)
        cache = physical.E6WitnessCache()
        base = physical.lower_bound(instance, budget_s=5., tables=tables, e6_cache=cache)
        assert len(calls) == 1
        reused = ports.lower_bound(instance, budget_s=5., tables=tables,
                                   base_result=base, e6_cache=cache)
        assert len(calls) == 1 and reused['e6_witness_cache_hit'] is True
        fresh = ports.lower_bound(instance, budget_s=5., tables=tables,
                                  base_result=base, e6_cache=False)
    assert len(calls) == 2
    assert reused['lower_bound'] == fresh['lower_bound']
    assert reused['mandatory_hold_cells_by_call'] == fresh['mandatory_hold_cells_by_call']
    assert reused['case_a_all_heavy_hold']['complete'] is True
    assert fresh['case_a_all_heavy_hold']['complete'] is True
    assert fresh['e6_witness_cache_hit'] is False
    json.dumps(reused)
    json.dumps(fresh)


def test_serialized_base_result_cannot_seed_e6_witness_reuse(monkeypatch):
    from stowage.feasibility.tables import build_tables
    instance = fixture()
    instance.containers[0].pod = 'THSGZ'
    tables = build_tables(instance)
    calls = []
    monkeypatch.setattr(physical, '_e6_witness_covers',
                        lambda *a: calls.append(True) or {})
    base = dict(instance_id=instance.id, lower_bound=2.,
                e6_witness_covers={'invented': ['cover']})
    result = ports.lower_bound(instance, budget_s=5., tables=tables,
                               base_result=base, e6_cache=physical.E6WitnessCache())
    assert calls == [True]
    assert result['ordinary_heavy_boxes'] == 0
    assert result['e6_witness_cache_hit'] is False
