"""Explicit geometry-only generator diagnostics, without a feasibility claim.

Existing fast generator contracts deliberately replace the fleet builder with
``with_hull=False`` geometry. They test bookings/yard identities, not physics.
Only those test inputs bypass the new gate here; production fails closed and
all real-hull and direct acceptance-gate tests still execute exact acceptance.
"""
import pytest


@pytest.fixture(autouse=True)
def geometry_only_generation_diagnostic(monkeypatch):
    from stowage.generate import instances, random_yard
    from stowage.generate.background_acceptance import accept_background_candidate

    def diagnostic(instance, audit):
        if instance.vessel.hull is None:
            return instance
        return accept_background_candidate(instance, audit)

    monkeypatch.setattr(random_yard, "accept_background_candidate", diagnostic)
    monkeypatch.setattr(instances, "accept_background_candidate", diagnostic)
