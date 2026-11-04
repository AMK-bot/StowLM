"""D50 consumable groups with D57 proportional draw-down declaration.

Actual Tank.kind supplies membership. The source layouts declare tank geometry
and contents, not service-tank roles or an observed consumption sequence. The
retained order below is legacy documentation. D57 applies one common remaining
fraction to every consumable tank; the ordering has no effect on its contents.
"""
from __future__ import annotations

import math
from numbers import Real

from stowage.model import ConsumableTankGroup, Provenance, Tank, VoyageTankPolicy

_CONSUMABLE_KINDS = ("fuel_oil", "diesel_oil", "lube_oil", "fresh_water", "stores")
_PRODUCER = "vessel.fleet_voyage.build_voyage_policy"
_ORDER_ASSUMPTION = (
    "ASSUMPTION: legacy documentation only: retained order uses descending full-sounding LCG "
    "and canonical tank ID to break ties; it does not control D57 proportional tank fills; "
    "no service-tank roles or usual operating practice are inferred."
)
_PROPORTIONAL_POLICY = (
    "D57 DECLARATION: proportional draw-down applies to every consumable tank: "
    "mass(r) = departure mass * one common remaining fraction r; symmetric pairs drain together. "
    "Default arrival r=0.10; explicit group consumption must imply the same r across all nonempty groups."
)
_LAYOUT_DECLARATION = "D27: tank geometry and explicit Tank.kind are declared synthetic design inputs"


def build_voyage_policy(tanks: list[Tank], *, parameter_source: str,
                        vessel_id: str) -> VoyageTankPolicy:
    """Declare existing consumables; never add tanks, contents, or guessed kinds.

    Full-sounding LCG is the tank's declared nominal forward location; its
    actual liquid centroid may change with fill. The retained ordering documents
    the legacy declaration; D57's pure state function scales all tanks together.
    The source parameter pin plus each actual tank ID/kind records membership.
    """
    if not isinstance(parameter_source, str) or not parameter_source.strip():
        raise ValueError("voyage policy requires a nonblank parameter source")
    if not isinstance(vessel_id, str) or not vessel_id.strip():
        raise ValueError("voyage policy requires a nonblank vessel id")
    ids = [tank.id for tank in tanks]
    if len(ids) != len(set(ids)):
        raise ValueError("voyage policy actual tank ids must be distinct")
    grouped = {kind: [] for kind in _CONSUMABLE_KINDS}
    forward_lcg = {}
    for tank in tanks:
        if tank.kind not in grouped:
            # Non-adjustable ballast, other and sludge are still not consumables.
            continue
        for field in ("capacity_m3", "content_density_kg_m3"):
            value = getattr(tank, field)
            if (isinstance(value, bool) or not isinstance(value, Real)
                    or not math.isfinite(value) or value <= 0):
                raise ValueError(f"voyage policy tank {tank.id} {field} must be finite and positive")
        if not math.isfinite(tank.capacity_m3*tank.content_density_kg_m3):
            raise ValueError(f"voyage policy tank {tank.id} mass capacity must be finite")
        if not tank.table:
            raise ValueError(f"voyage policy tank {tank.id} requires finite full-sounding LCG")
        lcg = tank.table[-1].lcg_m
        if isinstance(lcg, bool) or not isinstance(lcg, Real) or not math.isfinite(lcg):
            raise ValueError(f"voyage policy tank {tank.id} requires finite full-sounding LCG")
        forward_lcg[tank.id] = float(lcg)
        grouped[tank.kind].append(tank)

    base = [vessel_id, parameter_source, "docs/decisions.md:D50", "docs/decisions.md:D57", _LAYOUT_DECLARATION]

    def evidence(label: str, inputs: list[Tank], parents: list[str]) -> Provenance:
        return Provenance(
            created_by=f"{_PRODUCER}.{label}",
            versions={"voyage_tank_policy": "proportional_actual_consumables_v2"},
            parents=[*base, *parents],
        ).merge_nested(inputs)

    groups = []
    for kind in _CONSUMABLE_KINDS:
        members = sorted(grouped[kind], key=lambda tank: tank.id)
        if not members:
            continue
        provenance = evidence("group", members, [
            f"DECLARATION: Tank.kind={kind}",
            *(f"{parameter_source}#tanks[{tank.id}]" for tank in members),
        ])
        groups.append(ConsumableTankGroup(id=kind, kind=kind,
            tank_ids=[tank.id for tank in members], provenance=provenance))
    consumables = [tank for tank in tanks if tank.id in forward_lcg]
    order = sorted(forward_lcg, key=lambda tid: (-forward_lcg[tid], tid))
    order_provenance = evidence("order", consumables, [
        _ORDER_ASSUMPTION,
        *(f"{parameter_source}#tanks[{tid}].table[-1].lcg_m={forward_lcg[tid]}" for tid in order),
    ])
    # IS Code Part B 3.4.1.2-.3, data/raw/is-code/msc267-85.pdf, PDF p41:
    # "with 10% stores and fuel remaining". D50 extends this to declared groups.
    arrival_provenance = evidence("arrival_rule", consumables, [
        "data/raw/is-code/msc267-85.pdf: Part B 3.4.1.2 and 3.4.1.3, PDF p41",
        "D50 default arrival quantity retained by D57: 10% of departure contents",
        _PROPORTIONAL_POLICY,
        "APPROXIMATION: D57 requires departure, midpoint and arrival samples; these samples do not search the true worst continuous-voyage state",
    ])
    notes = []
    absent = [kind for kind in _CONSUMABLE_KINDS if not grouped[kind]]
    if absent:
        notes.append("DECLARATION: absent from supplied tank catalog: "+", ".join(absent))
    if not tanks:
        notes.append("DIAGNOSTIC: empty geometry-only tank catalog; no physical consumable groups or tanks invented")
    elif not consumables:
        notes.append("DECLARATION: supplied actual tanks have no consumable kinds; ballast/other/sludge are not inferred consumables")
    policy_provenance = evidence("policy", tanks, notes).merge_nested(
        groups, order_provenance, arrival_provenance)
    return VoyageTankPolicy(groups=groups, drawdown_order=order,
        default_arrival_remaining_fraction=0.1,
        drawdown_order_provenance=order_provenance,
        arrival_rule_provenance=arrival_provenance,
        provenance=policy_provenance)
