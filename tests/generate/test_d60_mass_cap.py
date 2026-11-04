"""D60 limits generated VGM while retaining each sourced CSC plate rating."""
from __future__ import annotations

import ast
from copy import deepcopy
import inspect
import math
from random import Random

import pytest

from stowage.checker import lashing
from stowage.generate.cargo import (
    _loaded_gross_mass, generate_load_list, generate_onboard_cargo,
)
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG, triangular_cdf
from stowage.model.fixtures import build_instance
from stowage.params import load_param_file


def test_generated_ceiling_equals_checker_container_mass_limit():
    """Checker-owned literal is authoritative; drift must fail this test."""
    tree = ast.parse(inspect.getsource(lashing))
    limits = [
        node.comparators[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and isinstance(node.left, ast.Attribute)
        and node.left.attr == "gross_mass_kg"
        and len(node.ops) == 1 and isinstance(node.ops[0], ast.Gt)
        and isinstance(node.comparators[0], ast.Constant)
    ]
    assert limits == [MAX_GENERATED_GROSS_MASS_KG]


@pytest.mark.parametrize("onboard", [False, True])
def test_every_equipment_profile_respects_cap_and_retains_csc(onboard):
    params = load_param_file("cargo", "feeder_export")
    vessel = build_instance().vessel
    for equipment in params["equipment"]:
        profile = deepcopy(params)
        profile["equipment"] = [deepcopy(equipment)]
        # Exercise both loaded and empty equipment without vessel plug counts
        # confusing a mass-distribution test with a vessel-support rejection.
        profile["equipment"][0]["reefer"] = False
        generate = (lambda: generate_onboard_cargo(vessel, "straits", 0, seed=60,
                     n_boxes=500, params=profile)) if onboard else (
                     lambda: generate_load_list("straits", 0, 500, seed=60, params=profile))
        boxes = generate()
        assert any(b.empty for b in boxes) and any(not b.empty for b in boxes)
        assert all(b.tare_kg <= b.gross_mass_kg <= MAX_GENERATED_GROSS_MASS_KG for b in boxes)
        assert all(b.max_gross_mass_kg == equipment["max_gross_kg"]["value"] for b in boxes)


@pytest.mark.parametrize("multiplier", [.9, 1.0, 1.1])
def test_d60_conditions_original_tail_instead_of_capping_or_rescaling(multiplier):
    """CDF probes distinguish true truncation from a capped atom/new triangle."""
    low, mode, high, tare = 6000., 20000., 55000., 4000.
    rng = Random(60)
    masses = [_loaded_gross_mass(rng, low, mode, high, tare, multiplier)
              for _ in range(10_000)]
    retained = triangular_cdf(tare + (MAX_GENERATED_GROSS_MASS_KG - tare) / multiplier,
                              low, mode, high)
    assert max(masses) <= MAX_GENERATED_GROSS_MASS_KG
    assert MAX_GENERATED_GROSS_MASS_KG not in masses
    for gross in (12_000., 20_000., 28_000., 30_000.):
        expected = triangular_cdf(tare + (gross - tare) / multiplier,
                                  low, mode, high) / retained
        observed = sum(mass <= gross for mass in masses)
        assert abs(observed - len(masses) * expected) <= 6 * math.sqrt(
            len(masses) * expected * (1 - expected)) + 2


@pytest.mark.parametrize("low,mode,high,multiplier", [
    (5000., 22000., 30480., 1.1),
    (5000., 22000., 30480., .9),
    (5000., 22000., 30480., 0.),
    (10000., 10000., 10000., 1.),
])
def test_below_cap_draws_and_boundary_atoms_are_unchanged(low, mode, high, multiplier):
    original, truncated = Random(60), Random(60)
    for _ in range(100):
        expected = min(high, max(low, 2350. + (original.triangular(low, high, mode) - 2350.) * multiplier))
        assert _loaded_gross_mass(truncated, low, mode, high, 2350., multiplier) == expected


def test_impossible_profile_fails_instead_of_altering_its_weight_support():
    with pytest.raises(ValueError, match="no mass within D60"):
        _loaded_gross_mass(Random(60), 31_000., 32_000., 34_000., 4000., 1.)


@pytest.mark.parametrize("mode", [6000., 55000.])
def test_truncation_handles_endpoint_modes(mode):
    rng = Random(60)
    masses = [_loaded_gross_mass(rng, 6000., mode, 55000., 4000., 1.)
              for _ in range(100)]
    assert all(6000. <= mass <= MAX_GENERATED_GROSS_MASS_KG for mass in masses)
