"""Sampler design guards; the independent exact replay supplies physics QA."""
import importlib.util
from itertools import product
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="module")
def edge():
    path = Path(__file__).resolve().parents[2]/"experiments/linear-model/epoch_requalification/edge_grid.py"
    spec = importlib.util.spec_from_file_location("edge_grid_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grid_requests_true_mass_and_attitude_endpoints(edge):
    requests = edge.specifications("train")
    corners = [r for r in requests if r["kind"] == "corner"]
    observed = {(r["mass_fraction"], r["trim_fraction"], r["heel_fraction"],
                 r["dry_kg_fraction"], r["fill_fraction"]) for r in corners}
    assert observed == set(product((0., 1.), (-1., 1.), (-1., 1.), (0., 1.), (0., .5, .98, 1.)))
    assert len(requests) == 128
    assert len({r["state_id"] for r in requests}) == len(requests)


def test_holdouts_are_independent_deterministic_offgrid_deep_corners(edge):
    train = edge.specifications("train")
    holdout = edge.specifications("holdout")
    assert edge.SEEDS["train"] != edge.SEEDS["holdout"]
    assert holdout == edge.specifications("holdout")
    assert len(holdout) == 256
    assert not {r["state_id"] for r in train}.intersection(r["state_id"] for r in holdout)
    assert all(.9 <= abs(r["trim_fraction"]) < 1. for r in holdout)
    assert all(.8 <= abs(r["heel_fraction"]) < 1. for r in holdout)
    assert sum(r["mass_fraction"] >= .8 for r in holdout) >= 128
    assert all(r["mass_fraction"] not in {t["mass_fraction"] for t in train} for r in holdout)


def test_requested_fill_branches_are_not_clipped_to_a_mass_budget(edge):
    requests = edge.specifications("train")
    for fill in (0., .5, .98, 1.):
        request = next(r for r in requests if r["fill_pattern"] == "uniform" and r["fill_fraction"] == fill)
        assert np.array_equal(edge.tank_fractions(request, 8), np.full(8, fill))
    staggered = [edge.tank_fractions(r, 8) for r in requests if r["fill_pattern"] == "staggered"]
    values = np.concatenate(staggered)
    assert .98-1e-8 in values and .98 in values and .98+1e-8 in values


def test_balanced_point_solves_original_force_and_plane_moments(edge):
    buoyancy = np.array([52., -.3, 3.])
    normal = np.array([.02, -.1, 1.])
    normal /= np.linalg.norm(normal)
    base_mass, mass = 1e6, 1.5e6
    base_cg = np.array([49., .1, 4.])
    dry, point = edge.balanced_point(mass, buoyancy, normal, base_mass, base_cg, 5.)
    total_cg = (base_mass*base_cg+dry*point)/mass
    assert dry+base_mass == mass
    assert point[2] == pytest.approx(5.)
    assert np.linalg.norm(np.cross(total_cg-buoyancy, normal)) < 1e-12


def test_impossible_low_mass_corner_is_not_reweighted(edge):
    with pytest.raises(ValueError, match="no positive dry load"):
        edge.balanced_point(99., [1., 0., 1.], [0., 0., 1.], 100., [1., 0., 1.], 0.)


def test_coverage_distinguishes_impossible_processed_and_exact_states(edge):
    request = edge.specifications("train")[0]
    records = [{"request": request, "admitted": False, "rejection_reason": "negative dry mass"}]
    coverage = edge.coverage([request], records)
    assert coverage["requested"] == coverage["evaluated"] == 1
    assert coverage["exact_evaluated"] == coverage["retained"] == 0
    assert all(r == {"requested": 1, "evaluated": 1, "exact_evaluated": 0, "retained": 0}
               for r in coverage["strata"].values())
