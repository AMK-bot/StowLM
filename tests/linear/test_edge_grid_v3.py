"""V3 interior faces and declared tank budgets use the original exact physics."""
import importlib.util
from itertools import product
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest


@pytest.fixture(scope="module")
def sampler():
    path = Path(__file__).resolve().parents[2]/"experiments/linear-model/epoch_requalification/edge_grid_v3.py"
    spec = importlib.util.spec_from_file_location("edge_grid_v3_design_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def profile_context(sampler):
    base = sampler.base_sampler()
    return NS(original_fractions=base.tank_fractions,
        family=NS(template=NS(tank_capacity_kg=np.array([20., 30., 50.]),
            tank_lcg_m=np.array([0., 5., 10.]),
            band=NS(displacement_min_kg=99., displacement_max_kg=300.))),
        instance=NS(vessel=NS(hull=NS(lightship_mass_kg=100.))))


def test_full_interior_mass_faces_have_800_requests(sampler):
    rows = sampler.specifications("train", sampler.base_sampler())
    assert len(rows) == len({row["state_id"] for row in rows}) == 800
    expected = set(product(tuple(i/10 for i in range(1, 11)), (-1., 1.), (-1., 1.), (0., 1.)))
    assert {(r["mass_fraction"], r["trim_fraction"], r["heel_fraction"], r["dry_kg_fraction"]) for r in rows} == expected
    for combination in expected:
        face = [r for r in rows if tuple(r[key] for key in
            ("mass_fraction", "trim_fraction", "heel_fraction", "dry_kg_fraction")) == combination]
        assert len(face) == 10
        assert sorted(r["fill_fraction"] for r in face if r["fill_pattern"] == "uniform") == [0., .5, .98, 1.]
        assert {(r["tank_budget_fraction"], r["tank_budget_pattern"]) for r in face if r["fill_pattern"] == "declared-budget"} == {
            (.2, "uniform"), (.5, "uniform"), (.8, "uniform"), (.3, "aft-weighted"),
            (.6, "forward-weighted"), (.9, "staggered")}


def test_budget_profiles_preserve_positive_dry_mass_at_interior_mass(sampler, profile_context):
    rows = sampler.specifications("train", sampler.base_sampler())
    for request in [r for r in rows if r["mass_fraction"] == .4 and r["fill_pattern"] == "declared-budget"]:
        actual = sampler.materialize(profile_context, request)
        available = actual["available_mass_above_lightship_kg"]
        realized = sum(actual["actual_all_tank_masses_kg"])
        assert 0 < realized < available
        assert realized == pytest.approx(actual["requested_tank_budget_kg"])
        assert actual["budget_capacity_limited"] is False
        fractions = np.asarray(actual["actual_tank_fractions"])
        assert np.all(fractions >= 0) and np.all(fractions <= 1)
        np.testing.assert_array_equal(fractions*profile_context.family.template.tank_capacity_kg,
                                      actual["actual_all_tank_masses_kg"])


def test_nominal_full_fill_is_never_reweighted_to_fit_mass(sampler, profile_context):
    rows = sampler.specifications("train", sampler.base_sampler())
    request = next(r for r in rows if r["mass_fraction"] == .4 and r["fill_pattern"] == "uniform" and r["fill_fraction"] == 1.)
    actual = sampler.materialize(profile_context, request)
    assert actual["actual_tank_fractions"] == [1., 1., 1.]
    assert sum(actual["actual_all_tank_masses_kg"]) > 99.+.4*(300.-99.)-100.
    assert "requested_tank_budget_kg" not in actual


def test_weighted_budget_saturates_capacity_without_losing_requested_mass(sampler):
    values = sampler.budget_masses([1., 100.], [1000., 1.], 50.)
    np.testing.assert_array_equal(values, [1., 49.])
    np.testing.assert_array_equal(sampler.budget_masses([1., 100.], [1000., 1.], 200.), [1., 100.])


def test_prescribed_dry_z_is_exact_before_new_equilibrium(sampler):
    base = sampler.base_sampler()
    for z in (0., 2.591, 37.461):
        dry, point = sampler.balanced_point_exact_z(base.balanced_point, 1.5e6,
            np.array([52., -.3, 3.]), np.array([.02, -.1, 1.]),
            1e6, np.array([49., .1, 4.]), z)
        assert dry == 5e5
        assert point[2] == z


def test_shards_cover_lattice_once_and_holdout_is_new(sampler):
    base = sampler.base_sampler()
    rows = sampler.specifications("train", base)
    for count in (2, 3):
        combined = [row["state_id"] for index in range(count) for row in rows[index::count]]
        assert len(combined) == len(set(combined)) == 800
    holdout = sampler.specifications("holdout", base)
    assert sampler.SEEDS == {"train": 54935, "holdout": 54936}
    assert len(holdout) == 256
    assert not {row["state_id"] for row in rows}.intersection(row["state_id"] for row in holdout)
