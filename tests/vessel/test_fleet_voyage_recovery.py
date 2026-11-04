"""Recovered metadata must keep actual tanks and their matching D50 policy.

These are geometry-only diagnostic contexts with actual typed fixture Tank
models. They are not hydrostatic reconstructions or checker-approved states.
"""

from stowage.model import Vessel
from stowage.model.fixtures import build_instance
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_conditions import _parent
from stowage.vessel.fleet_slices import recover_parent, slice_preset, slice_vessel
from stowage.vessel.fleet_voyage import build_voyage_policy


def _replace_tanks_with_matching_policy(part):
    tanks = build_instance().vessel.tanks
    policy = build_voyage_policy(
        tanks, vessel_id=part.slice_of or part.id,
        parameter_source="synthetic fixture replacement Tank models; metadata diagnostic only",
    )
    payload = part.model_dump()
    payload.update(tanks=[tank.model_dump() for tank in tanks], voyage_policy=policy.model_dump())
    current = Vessel.model_validate(payload)
    assert current.hull is None
    assert current.tanks and current.voyage_policy.groups
    assert Vessel.model_validate_json(current.model_dump_json()) == current
    return current


def test_named_parent_recovery_preserves_current_tanks_and_matching_policy():
    parent = load_vessel("feeder_500", with_hull=False)
    current = _replace_tanks_with_matching_policy(slice_preset(parent, "small"))
    before = current.model_dump_json()
    full, _, _, _ = _parent(current)
    assert full.tanks == current.tanks
    assert full.voyage_policy == current.voyage_policy
    assert Vessel.model_validate_json(full.model_dump_json()) == full
    assert current.model_dump_json() == before


def test_foreign_snapshot_recovery_preserves_current_tanks_and_matching_policy():
    parent = load_vessel("feeder_500", with_hull=False).model_copy(update={"id": "foreign-policy-recovery"})
    part = slice_vessel(parent, [1, 3], name="foreign-policy-recovery.part")
    current = _replace_tanks_with_matching_policy(Vessel.model_validate_json(part.model_dump_json()))
    before = current.model_dump_json()
    full = recover_parent(current)
    assert full.tanks == current.tanks
    assert full.voyage_policy == current.voyage_policy
    assert Vessel.model_validate_json(full.model_dump_json()) == full
    assert current.model_dump_json() == before
