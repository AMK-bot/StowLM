"""Explicit synthetic evidence for isolated static-rule behavioral tests.

The shared legacy fixture has unknown CSC ratings and an untraceable background
cargo block. Those are deliberate hard failures tested in test_rules_evidence;
other rule scenarios use a ship without that background and synthetic plate
ratings, so unrelated missing evidence cannot mask their intended outcomes.
"""

from stowage.model.fixtures import build_instance as _build_instance


def build_instance(**kwargs):
    instance = _build_instance(**kwargs)
    instance.vessel.fixed_loads = []
    for box in instance.containers:
        box.max_gross_mass_kg = 100000  # Synthetic test plate rating, not an ISO size limit.
    return instance
