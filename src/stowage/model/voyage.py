"""D50 voyage identities with D57 proportional consumable draw-down.

Arrival contains the same cargo and ballast as departure; only consumable liquid
masses are derived here. This module does not evaluate stability or strength.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, TYPE_CHECKING

from pydantic import Field, model_validator

from stowage.model.base import CallIndex, Locode, StowModel
from stowage.model.provenance import Provenance

if TYPE_CHECKING:
    from stowage.model.ship import ShipState
    from stowage.model.vessel import Vessel


ConsumableKind = Literal["fuel_oil", "diesel_oil", "lube_oil", "fresh_water", "stores"]
_CONSUMABLE_KINDS = frozenset(("fuel_oil", "diesel_oil", "lube_oil", "fresh_water", "stores"))
NonblankId = Annotated[str, Field(min_length=1, pattern=r"\S")]
NonnegativeMass = Annotated[float, Field(ge=0.0, allow_inf_nan=False, strict=True)]
FiniteSignedMass = Annotated[float, Field(allow_inf_nan=False, strict=True)]


class ConsumableTankGroup(StowModel):
    """One consumable kind's tank membership; membership is not draw-down order."""

    id: NonblankId
    kind: ConsumableKind
    tank_ids: list[NonblankId] = Field(min_length=1)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def canonical_membership(self) -> ConsumableTankGroup:
        if len(self.tank_ids) != len(set(self.tank_ids)):
            raise ValueError("voyage_policy groups tank_ids must be distinct")
        self.tank_ids = sorted(self.tank_ids)
        return self


class VoyageTankPolicy(StowModel):
    """Per-vessel consumable groups and global tank draw-down order.

    ``drawdown_order`` retains the legacy TANK-ID declaration, each grouped tank
    exactly once. D57 consumes every tank proportionally at one common fraction;
    this order no longer controls fills. An empty groups/order pair is an
    explicit no-consumable policy, distinct from missing vessel policy.
    """

    groups: list[ConsumableTankGroup]
    drawdown_order: list[NonblankId]
    # IS Code Part B 3.4.1.2-.3, data/raw/is-code/msc267-85.pdf, PDF p. 41: arrival
    # "with 10 % stores and fuel remaining". D50 applies this to each declared
    # consumable group, unless instance-declared kg consumption replaces it.
    default_arrival_remaining_fraction: Literal[0.1] = 0.1
    drawdown_order_provenance: Provenance = Field(default_factory=Provenance)
    arrival_rule_provenance: Provenance = Field(default_factory=Provenance)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def validate_membership_and_order(self) -> VoyageTankPolicy:
        ids = [group.id for group in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("voyage_policy groups must have distinct ids")
        tank_ids = [tank_id for group in self.groups for tank_id in group.tank_ids]
        if len(tank_ids) != len(set(tank_ids)):
            raise ValueError("voyage_policy groups tank_ids must not overlap")
        if len(self.drawdown_order) != len(set(self.drawdown_order)) or set(self.drawdown_order) != set(tank_ids):
            raise ValueError("voyage_policy drawdown_order must contain every grouped tank id exactly once")
        self.groups = sorted(self.groups, key=lambda group: group.id)
        return self


class VoyageConditionIdentity(StowModel):
    """Stable condition, next-call, environment and water-density identity."""

    id: NonblankId
    phase: Literal["departure", "arrival"]
    call_index: Annotated[CallIndex, Field(strict=True)]
    locode: Locode
    environment_id: NonblankId
    density_id: NonblankId
    water_density_kg_m3: float = Field(gt=0.0, allow_inf_nan=False, strict=True)
    provenance: Provenance = Field(default_factory=Provenance)


class VoyageConsumption(StowModel):
    """Declared mass consumed during the leg; zero explicitly means no use."""

    group_id: NonblankId
    mass_kg: NonnegativeMass
    provenance: Provenance = Field(default_factory=Provenance)


class VoyageBallastOperation(StowModel):
    """A declared, forbidden voyage ballast change, retained for rejection.

    Positive mass_change_kg is loading; negative is discharge. Even a declared
    zero-change operation is rejected at arrival derivation under D50.
    """

    tank_id: NonblankId
    mass_change_kg: FiniteSignedMass
    provenance: Provenance = Field(default_factory=Provenance)


class VoyageSpec(StowModel):
    """One departure-to-next-call leg; arrival liquids are never an input."""

    departure: VoyageConditionIdentity
    arrival: VoyageConditionIdentity
    consumption: list[VoyageConsumption] | None = None
    ballast_operations: list[VoyageBallastOperation] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def validate_conditions(self) -> VoyageSpec:
        if self.departure.phase != "departure":
            raise ValueError("voyage departure.phase must be departure")
        if self.arrival.phase != "arrival":
            raise ValueError("voyage arrival.phase must be arrival")
        if self.departure.id == self.arrival.id:
            raise ValueError("voyage arrival.id must differ from departure.id")
        if self.arrival.call_index != self.departure.call_index + 1:
            raise ValueError("voyage arrival.call_index must be departure.call_index + 1")
        if self.consumption is not None:
            ids = [item.group_id for item in self.consumption]
            if len(ids) != len(set(ids)):
                raise ValueError("voyage consumption group_id must be distinct")
            self.consumption = sorted(self.consumption, key=lambda item: item.group_id)
        return self


class VoyageLiquidState(StowModel):
    """Derived liquid masses only, with condition and inherited evidence.

    Missing tanks mean empty, matching ShipState. This is an output value, not a
    second freely supplied arrival state on an instance.
    """

    condition: VoyageConditionIdentity
    tank_masses_kg: dict[NonblankId, NonnegativeMass]
    provenance: Provenance = Field(default_factory=Provenance)


class VoyageLiquidSample(StowModel):
    """Proportional liquids at a fraction of departure contents (D57).

    A sample is identified by its remaining fraction and input voyage; it is not
    labelled as an arrival condition when it represents a mid-voyage fill.
    """

    remaining_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False, strict=True)
    tank_masses_kg: dict[NonblankId, NonnegativeMass]
    provenance: Provenance = Field(default_factory=Provenance)


def _validated_policy(vessel: Vessel) -> VoyageTankPolicy | None:
    raw = vessel.voyage_policy
    if raw is None:
        return None
    return VoyageTankPolicy.model_validate(raw.model_dump() if isinstance(raw, VoyageTankPolicy) else raw)


def _capacity_kg(tank: object) -> float:
    for field in ("capacity_m3", "content_density_kg_m3"):
        value = getattr(tank, field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"voyage_policy tanks[{tank.id}].{field} must be finite and positive")
    capacity = tank.capacity_m3 * tank.content_density_kg_m3
    if not math.isfinite(capacity):
        raise ValueError(f"voyage_policy tanks[{tank.id}] mass capacity must be finite")
    return capacity


def validate_voyage_policy(vessel: Vessel) -> None:
    """Validate optional policy against actual tank IDs, kinds and capacities.

    A missing policy remains unknown and loads for legacy compatibility; using a
    voyage requires explicit policy. A supplied policy covers all consumables.
    """
    policy = _validated_policy(vessel)
    if policy is None:
        return
    tanks = {tank.id: tank for tank in vessel.tanks}
    if len(tanks) != len(vessel.tanks):
        raise ValueError("voyage_policy tanks must have distinct ids")
    grouped = set()
    for group in policy.groups:
        for tank_id in group.tank_ids:
            if tank_id not in tanks:
                raise ValueError(f"voyage_policy groups tank_ids references unknown tank {tank_id}")
            tank = tanks[tank_id]
            if tank.kind not in _CONSUMABLE_KINDS or tank.kind != group.kind:
                raise ValueError(f"voyage_policy groups kind does not match consumable tank {tank_id}")
            _capacity_kg(tank)
            grouped.add(tank_id)
    consumables = {tank.id for tank in vessel.tanks if tank.kind in _CONSUMABLE_KINDS}
    if grouped != consumables:
        raise ValueError("voyage_policy groups tank_ids must cover all consumable tanks")


def validate_voyage_spec(vessel: Vessel, spec: VoyageSpec) -> None:
    """Validate leg structure and exact group coverage against vessel policy.

    Ballast operations remain representable so the checker can report the
    declared forbidden operation; derive_arrival_liquid_state rejects any.
    """
    validate_voyage_policy(vessel)
    policy = _validated_policy(vessel)
    if policy is None:
        raise ValueError("voyage requires vessel voyage_policy")
    leg = VoyageSpec.model_validate(spec.model_dump() if isinstance(spec, VoyageSpec) else spec)
    if leg.consumption is not None:
        if {item.group_id for item in leg.consumption} != {group.id for group in policy.groups}:
            raise ValueError("voyage consumption must declare every voyage_policy group exactly once")


def _finite_sum(values: list[float], *, field: str) -> float:
    try:
        value = math.fsum(values)
    except OverflowError as exc:
        raise ValueError(f"{field} total must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field} total must be finite")
    return value


def _validated_departure(departure: ShipState, spec: VoyageSpec, *, vessel: Vessel):
    """Snapshot the declared leg and validate every supplied departure fill."""
    validate_voyage_spec(vessel, spec)
    leg = VoyageSpec.model_validate(spec.model_dump())
    policy = _validated_policy(vessel)
    assert policy is not None  # validated above; never an implicit fallback
    if leg.ballast_operations:
        raise ValueError("voyage ballast_operations are forbidden by D50/D57")
    if departure.vessel_id != vessel.id:
        raise ValueError("departure vessel_id must match vessel.id")
    tanks = {tank.id: tank for tank in vessel.tanks}
    masses = dict(departure.tank_masses_kg)
    for tank_id, mass in masses.items():
        if tank_id not in tanks:
            raise ValueError(f"departure tank_masses_kg references unknown tank {tank_id}")
        if isinstance(mass, bool) or not isinstance(mass, (int, float)) or not math.isfinite(mass) or mass < 0:
            raise ValueError(f"departure tank_masses_kg[{tank_id}] must be finite and nonnegative")
        if mass > _capacity_kg(tanks[tank_id]):
            raise ValueError(f"departure tank_masses_kg[{tank_id}] exceeds capacity")
    return leg, policy, masses


def _arrival_fraction(policy: VoyageTankPolicy, leg: VoyageSpec, masses: dict[str, float]) -> float:
    """Resolve one common fraction without changing any declared group total."""
    consumptions = None if leg.consumption is None else {item.group_id: item.mass_kg for item in leg.consumption}
    constraints = []
    for group in policy.groups:
        total = _finite_sum([masses.get(tank_id, 0.0) for tank_id in group.tank_ids], field="tank_masses_kg")
        if consumptions is not None:
            consumed = consumptions[group.id]
            if consumed > total:
                raise ValueError(f"voyage consumption for group {group.id} exceeds departure contents")
            if total > 0.0:
                constraints.append((group.id, total, consumed, (total - consumed) / total))
    if consumptions is None:
        return policy.default_arrival_remaining_fraction
    if not constraints:
        return 1.0  # explicit zero use of empty groups; every fill is unchanged
    fraction = constraints[0][3]
    for group_id, total, consumed, candidate in constraints:
        retained = total * fraction
        if (not math.isclose(fraction, candidate, rel_tol=1e-12, abs_tol=1e-12)
                or not math.isclose(retained, total - consumed, rel_tol=1e-9, abs_tol=1e-6)
                or not math.isclose(total - retained, consumed, rel_tol=1e-9, abs_tol=1e-6)):
            raise ValueError(f"voyage consumption for group {group_id} must imply one common remaining fraction")
    return fraction


def arrival_remaining_fraction(
    departure: ShipState, spec: VoyageSpec, *, vessel: Vessel,
) -> float:
    """Pure D57 arrival fraction: 0.10 by default, or consistent declared use.

    Every nonempty group's declared consumption must imply the same fraction.
    Empty groups require zero use and do not constrain it. Explicit zero use of
    all contents (including an entirely empty vessel) returns 1.0.
    """
    leg, policy, masses = _validated_departure(departure, spec, vessel=vessel)
    return _arrival_fraction(policy, leg, masses)


def _sample(policy: VoyageTankPolicy, leg: VoyageSpec, masses: dict[str, float], r: float) -> VoyageLiquidSample:
    consumables = {tank_id for group in policy.groups for tank_id in group.tank_ids}
    result = {tank_id: mass * r if tank_id in consumables else mass for tank_id, mass in masses.items()}
    provenance = Provenance(created_by="stowage.model.voyage.derive_voyage_liquid_state").merge_nested(
        policy, leg, parent_ids=[leg.departure.id, leg.arrival.id],
    )
    return VoyageLiquidSample(remaining_fraction=r, tank_masses_kg=result, provenance=provenance)


def derive_voyage_liquid_state(
    departure: ShipState, spec: VoyageSpec, r: float, *, vessel: Vessel,
) -> VoyageLiquidSample:
    """Pure D57 proportional sample; every consumable retains departure mass*r.

    r is finite in [0, 1]. Cargo, ballast and nonconsumables never change. Missing
    empty tanks stay missing. Policy order is validated but cannot create list
    or trim excursions by draining one side first. This derives liquids only;
    checking three samples is still D57's approximation to a continuous voyage.
    """
    if (isinstance(r, bool) or not isinstance(r, (float, int))
            or not 0.0 <= r <= 1.0 or not math.isfinite(r)):
        raise ValueError("voyage remaining fraction r must be finite and in [0, 1]")
    leg, policy, masses = _validated_departure(departure, spec, vessel=vessel)
    _arrival_fraction(policy, leg, masses)  # reject inconsistent declared use at every sample, including r=1
    return _sample(policy, leg, masses, float(r))


def derive_arrival_liquid_state(
    departure: ShipState, spec: VoyageSpec, *, vessel: Vessel,
) -> VoyageLiquidState:
    """Compatibility API: derive D57 proportional liquids at the arrival fraction."""
    leg, policy, masses = _validated_departure(departure, spec, vessel=vessel)
    fraction = _arrival_fraction(policy, leg, masses)
    sample = _sample(policy, leg, masses, fraction)
    provenance = sample.provenance.model_copy(update={
        "created_by": "stowage.model.voyage.derive_arrival_liquid_state",
    })
    return VoyageLiquidState(condition=leg.arrival, tank_masses_kg=sample.tank_masses_kg, provenance=provenance)
