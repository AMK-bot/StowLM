"""Production-builder coverage; generation is not a departure certificate.

These tests deliberately do not inherit test_random_yard's geometry fixture.
A separate bounded exact-solver pilot is recorded in the task's fix log; only
a full-checker-approved witness can close the tiny solver acceptance criterion.
"""
from __future__ import annotations

import pytest

from stowage.generate import random_yard as random
from stowage.vessel.fleet_conditions import slice_condition


def test_full_physics_tiny_booking_metadata_and_seed_determinism():
    audit = random.RandomGenerationReport()
    instance = random.generate_random_instance("tiny", seed=1, report=audit)
    repeated_audit = random.RandomGenerationReport()
    repeated = random.generate_random_instance("tiny", seed=1, report=repeated_audit)

    assert repeated.model_dump_json() == instance.model_dump_json()
    # Cache reuse and elapsed time are runtime observations, not draw identity.
    from dataclasses import asdict
    first, second = asdict(audit), asdict(repeated_audit)
    for volatile in ("background_seconds", "background_cache_hits"):
        first.pop(volatile)
        second.pop(volatile)
    assert second == first
    assert audit.background_checks > 0
    assert audit.background_last["states"]["arrival"]["b23_ratio"] >= 1.05
    assert instance.vessel.hull is not None
    assert instance.vessel.hull.hydrostatics
    assert instance.vessel.hull.cross_curves
    assert instance.vessel.tanks
    assert instance.vessel.fixed_loads
    assert not instance.provenance.tainted
    assert instance.provenance.versions["random_yard"] == random.RANDOM_YARD_VERSION

    targets = [box for box in instance.containers if box.id in instance.load_list]
    assert targets
    assert all(box.max_gross_mass_kg is not None
               and box.gross_mass_kg <= box.max_gross_mass_kg
               for box in instance.containers)
    capacity = len(instance.vessel.cells)
    teu_fraction = sum(box.length_ft / 20 for box in targets) / capacity
    assert int(instance.family["region_capacity_teu"]) == capacity
    assert instance.family["booking_policy"] == "D36_scaled_full_export_v1"
    assert float(instance.family["booking_utilisation_teu"]) == pytest.approx(teu_fraction)
    assert audit.booking_reference["region_capacity_teu"] == capacity
    assert audit.booking_utilisation["teu_fraction"] == pytest.approx(teu_fraction)
    assert float(instance.family["booking_requested_teu_fraction"]) == pytest.approx(
        audit.booking_reference["requested_teu_fraction"])
    assert float(instance.family["booking_utilisation_mass"]) == pytest.approx(
        audit.booking_utilisation["cargo_mass_fraction"])

    # The physical background must describe this actual booking, excluding
    # the later-ship yard blockers, rather than a nominal region-sized mass.
    condition = slice_condition(instance.vessel)
    assert condition is not None
    assert condition["expected_load_policy"] == "explicit_booking_mass"
    assert condition["expected_load_mass_kg"] == pytest.approx(
        sum(box.gross_mass_kg for box in targets))
