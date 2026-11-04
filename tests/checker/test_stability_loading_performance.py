"""Independent table-envelope equivalence and eliminated repeated spline work."""
from dataclasses import replace
from collections import OrderedDict
from copy import copy
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import stability_liquid_batch, stability_liquid_tables, stability_loading
from stowage.checker.stability_liquid_tables import LiquidTable
from stowage.checker.stability_loading import CorrectionTank, LoadingCondition, curve_correction


def envelope_condition(*, different_fills=False, different_angles=False, prismatic=False):
    rng = np.random.default_rng(512)
    tanks, tables = [], {}
    for index in range(4):
        fills = np.linspace(0., 1., 13+int(different_fills and index % 2))
        heels = np.linspace(-.9, .9, 9+int(different_angles and index % 2))
        trims = np.linspace(-.04, .04, 7)
        reference = np.array([30.+index, -2.+index, 3.])
        moments = rng.normal(scale=.3, size=(len(heels), len(trims), len(fills), 3))
        moments += fills[None, None, :, None]*100*reference
        moments[:, :, 0] = 0.
        # Every tank is maximized over fill independently; random nodal data
        # gives different extrema, exposing an incorrect maximum of their sum.
        table = LiquidTable(fills, heels, trims, moments, 100., reference,
                            prismatic=prismatic and index == 0)
        solid = SimpleNamespace(shape='hull_sections', sounding=lambda v, r=reference:
                                SimpleNamespace(lcg_m=r[0], tcg_m=r[1], vcg_m=r[2]))
        tank = CorrectionTank(str(index), 900.+index*30., 40.+index, 0., solid, True)
        tables[tank.id] = table
        tanks.append(tank)
    return LoadingCondition(8e6, 50., 0., 6., 0., 0., tanks=tuple(tanks), liquid_tables=tables)


def independent_envelopes(condition, angles, trims):
    count = len(condition.tanks)
    result = np.zeros(len(angles))
    for tank in condition.tanks:
        tolerance = 1e-4*condition.mass_kg/(tank.density_kg_m3*count)
        for index, (heel, trim) in enumerate(zip(angles, trims, strict=True)):
            moment = condition.liquid_tables[tank.id].max_transfer(
                heel, trim, tolerance_m4=tolerance)
            result[index] += (-1 if heel < 0 else 1)*tank.density_kg_m3/condition.mass_kg*moment
    return result


def test_shared_fill_grid_constructs_one_spline_for_independent_tank_envelopes(monkeypatch):
    condition = envelope_condition()
    angles, trims = np.linspace(-.8, .8, 11), np.linspace(-.03, .03, 11)
    expected = independent_envelopes(condition, angles, trims)
    original = stability_liquid_tables.CubicSpline
    calls = []

    def counted_spline(*args, **kwargs):
        calls.append(np.asarray(args[1]).shape)
        return original(*args, **kwargs)

    monkeypatch.setattr(stability_liquid_tables, 'CubicSpline', counted_spline)
    monkeypatch.setattr(stability_liquid_batch, 'CubicSpline', counted_spline, raising=False)
    actual = curve_correction(condition, angles, trims)
    assert actual == pytest.approx(expected, abs=2e-14)
    assert len(calls) == 1  # four tanks × eleven angles formerly rebuilt 44 splines
    assert condition.liquid_usage == {'table': 44}


@pytest.mark.parametrize('different_fills', [False, True])
@pytest.mark.parametrize('different_angles', [False, True])
def test_batched_independent_maxima_match_signed_heel_trim_sweep(different_fills, different_angles):
    condition = envelope_condition(different_fills=different_fills, different_angles=different_angles)
    rng = np.random.default_rng(716)
    angles = np.r_[-.9, 0., .9, rng.uniform(-.9, .9, 30)]
    trims = np.r_[-.04, 0., .04, rng.uniform(-.04, .04, 30)]
    assert curve_correction(condition, angles, trims) == pytest.approx(
        independent_envelopes(condition, angles, trims), rel=2e-12, abs=2e-14)


@pytest.mark.parametrize('failure', ['prismatic', 'outside', 'missing'])
def test_uncovered_tank_queries_keep_independent_direct_fallback(monkeypatch, failure):
    condition = envelope_condition(prismatic=failure == 'prismatic')
    angles, trims = np.array([-.2, .3]), np.array([.01, .02])
    if failure == 'outside':
        angles[1] = 1.
    if failure == 'missing':
        tables = dict(condition.liquid_tables)
        del tables['0']
        condition = replace(condition, liquid_tables=tables)
    calls = []

    def direct(solid, heel, trim, *, tolerance_m4):
        calls.append((id(solid), heel, trim, tolerance_m4))
        return 7.

    from stowage.checker import stability_tank_transfer
    monkeypatch.setattr(stability_tank_transfer, 'max_transfer', direct)
    actual = curve_correction(condition, angles, trims)
    expected = np.zeros(2)
    expected_direct = 0
    for tank in condition.tanks:
        table = condition.liquid_tables.get(tank.id)
        for index, (heel, trim) in enumerate(zip(angles, trims, strict=True)):
            try:
                moment = table.max_transfer(heel, trim, tolerance_m4=1.) if table else None
            except ValueError:
                moment = None
            if moment is None:
                moment, expected_direct = 7., expected_direct+1
            expected[index] += (-1 if heel < 0 else 1)*tank.density_kg_m3/condition.mass_kg*moment
    assert actual == pytest.approx(expected, abs=2e-14)
    assert len(calls) == expected_direct
    assert condition.liquid_usage['direct'] == expected_direct
    assert condition.liquid_usage['table'] == len(condition.tanks)*len(angles)-expected_direct
    expected_tolerance = 1e-4*condition.mass_kg/(condition.tanks[0].density_kg_m3*len(condition.tanks))
    assert calls[0][-1] == expected_tolerance


def test_custom_table_provider_retains_its_own_maximum_and_tolerance():
    condition = envelope_condition()
    calls = []

    def custom(heel, trim, *, tolerance_m4):
        calls.append((heel, trim, tolerance_m4))
        return 13.

    tables = dict(condition.liquid_tables)
    tables['0'] = SimpleNamespace(max_transfer=custom)
    condition = replace(condition, liquid_tables=tables)
    actual = curve_correction(condition, [-.2, .3], [.01, .02])
    assert np.isfinite(actual).all()
    assert len(calls) == 2
    assert calls[0][-1] == 1e-4*condition.mass_kg/(condition.tanks[0].density_kg_m3*4)


@pytest.mark.parametrize('mass', [0., -1., float('nan'), float('inf')])
def test_invalid_transfer_tolerance_retains_hard_reference_failure(mass):
    condition = replace(envelope_condition(), mass_kg=mass)
    with pytest.raises(ValueError, match='positive.*tolerance'):
        curve_correction(condition, [.2], [.01])


def test_rejected_batch_retains_per_tank_table_and_direct_fallback(monkeypatch):
    condition = envelope_condition()
    # Simulate an unreadable moment block after certification/attachment. A
    # failed combined spline must not turn healthy peers into hard failures.
    condition.liquid_tables['0'].moments[:] = np.nan
    calls = []
    from stowage.checker import stability_tank_transfer

    def direct(*args, **kwargs):
        calls.append(args)
        return 7.

    monkeypatch.setattr(stability_tank_transfer, 'max_transfer', direct)
    actual = curve_correction(condition, [.2], [.01])
    assert np.isfinite(actual).all()
    assert len(calls) == 1
    assert condition.liquid_usage == {'direct': 1, 'table': 3}


def test_direct_liquid_physical_roots_reused_across_loading_conditions(monkeypatch):
    from stowage.checker import stability_direct_liquid_batch as direct
    from tests.checker.test_stability_direct_liquid_batch import condition
    monkeypatch.setattr(direct, '_CACHE', OrderedDict())
    monkeypatch.setattr(direct, '_CENTROID_CACHE', OrderedDict(), raising=False)
    first = condition()
    original = direct._integrate
    calls = []

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(direct, '_integrate', counted)
    before = direct.evaluate(first, .41, .02)
    assert calls
    calls.clear()
    second = copy(first)
    second.mass_kg *= 2
    second.lcg_m, second.tcg_m, second.kg_m = 2., 1., 5.
    second.upright_cache, second.liquid_usage = {}, {}
    after = direct.evaluate(second, .41, .02)
    assert len(calls) == 0  # identical physical liquid roots, new loading moments
    shift = np.asarray(before)-[first.lcg_m, first.tcg_m, first.kg_m]
    assert after == pytest.approx(np.array([2., 1., 5.])+shift/2, abs=2e-14)


@pytest.mark.parametrize('changed', ['fill', 'geometry', 'heel', 'trim'])
def test_direct_liquid_exact_root_cache_rechecks_changed_physics(monkeypatch, changed):
    from stowage.checker import stability_direct_liquid_batch as direct
    from tests.checker.test_stability_direct_liquid_batch import condition
    monkeypatch.setattr(direct, '_CACHE', OrderedDict())
    monkeypatch.setattr(direct, '_CENTROID_CACHE', OrderedDict(), raising=False)
    first = condition()
    direct.evaluate(first, .41, .02)
    second = copy(first)
    second.upright_cache, second.liquid_usage = {}, {}
    heel, trim = .41, .02
    if changed == 'fill':
        tank = replace(first.actual_tanks[0], volume_m3=first.actual_tanks[0].volume_m3+1e-8)
        second.actual_tanks = (tank, *first.actual_tanks[1:])
    elif changed == 'geometry':
        second.actual_tanks = condition().actual_tanks
    elif changed == 'heel':
        heel = np.nextafter(heel, 1.)
    else:
        trim = np.nextafter(trim, 1.)
    original = direct._integrate
    calls = []

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(direct, '_integrate', counted)
    actual = direct.evaluate(second, heel, trim)
    assert calls  # even one ULP of angular change requires new root validation
    expected = np.array([second.lcg_m, second.tcg_m, second.kg_m])
    for tank in second.actual_tanks:
        moved = np.array(tank.geometry.liquid(tank.volume_m3, heel, trim).centroid_m)
        upright = np.array(tank.geometry.liquid(tank.volume_m3).centroid_m)
        expected += tank.density_kg_m3*tank.volume_m3/second.mass_kg*(moved-upright)
    assert actual == pytest.approx(expected, abs=2e-10)


def test_direct_liquid_root_cache_keeps_budget_polls_and_immutable_results(monkeypatch):
    from stowage.checker import stability_direct_liquid_batch as direct
    from stowage.checker.stability_budget import StabilityTimeout
    from tests.checker.test_stability_direct_liquid_batch import condition
    monkeypatch.setattr(direct, '_CACHE', OrderedDict())
    monkeypatch.setattr(direct, '_CENTROID_CACHE', OrderedDict(), raising=False)
    batch = direct.prepare(condition())
    expected = direct.centroids(batch, .41, .02)
    external = direct.centroids(batch, .41, .02)
    external[:] = 123.
    assert direct.centroids(batch, .41, .02) == pytest.approx(expected)
    labels = []

    def poll(label):
        labels.append(label)
        if label.endswith('exit'):
            raise StabilityTimeout('cached query must obey deadline')

    monkeypatch.setattr(direct, 'check_budget', poll)
    with pytest.raises(StabilityTimeout, match='cached query'):
        direct.centroids(batch, .41, .02)
    assert labels[0].endswith('entry') and labels[-1].endswith('exit')
