"""Synthetic shipper declarations, separated from ship-placement clearance.

Source: schema 1.2 DangerousGoods contract and the curated IMDG 42-24 DGL,
columns 4, 6, 16a and 16b (stowage, segregation and special provisions).
The DGL describes requirements; it does not establish that a shipment satisfies
them. In particular, a code saying 'protected from heat' cannot prove that an
as-yet-unplaced box is protected from heat.

APPROXIMATION: the default G1 equipment population carries packaged goods in
closed freight containers, including cylinders/drums/packages for gases/liquids.
Conditional shares are 100% closed packaged for G/R equipment, 100% tank for
explicit T equipment carrying the selected liquid UNs below, and 100% open for
U/P equipment. These are synthetic packing choices, not measured traffic or
substance-specific packing authorizations. No draw relabels a G1 box as a tank.
The default cargo profile contains no tanks; adding a nonzero tank mix requires
equipment-specific dimensions/tare/ratings and shares from the parameter owner.

The sampled DGL lacks packing/tank instructions and shipment assessments. We
therefore leave external handling/placement conditions, conditional subsidiary
hazards, voyage classification, bulk type and verification lists unknown. An
entry with no candidate SPs declares []; otherwise SP applicability stays None:
copying every candidate SP would falsely assert mutually conditional facts
(notably damaged/prototype lithium batteries). All internal guards remain.
"""

from __future__ import annotations

from copy import deepcopy
import heapq
import math
from typing import Any, Mapping

from stowage.model.cargo import PackingForm
from stowage.model.port import PortProfile, Rotation
from stowage.model.provenance import Provenance
from stowage.params import load_param_file
from stowage.ports import resolve_port_call_profile
from stowage.rules.imdg.dgl import DangerousGoodsEntry, entries_for_un
from stowage.rules.imdg.labels import hazard_division, parse_hazard_label

SHIPMENT_POLICY_VERSION = "1.0.0"
# Synthetic scenario support, identified by the DGL proper shipping names.
# Liquid identity alone grants no packing or tank authorization.
LIQUID_TANK_UNS = frozenset({1263, 1789, 2810, 3109})

MASS_AUDIT_BANDS = ("lt_10000", "10000_to_lt_20000", "20000_to_25000", "gt_25000")


def _audit_value(table: Mapping[str, Any], key: str) -> float:
    return float(table[key]["value"])


def _audit_shares(rows: list[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], float]]:
    weights = [_audit_value(row, "share") for row in rows]
    if not rows or any(not math.isfinite(weight) or weight < 0 for weight in weights) or sum(weights) <= 0:
        raise ValueError("audit sampling shares must be finite, nonnegative and have positive total")
    total = sum(weights)
    return [(row, weight / total) for row, weight in zip(rows, weights)]


def _demand_expectation(params: Mapping[str, Any], n_boxes: int | None,
                        pods: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Profile expectations before refusal, with full DGL primary identifiers."""
    equipment = _audit_shares(params["equipment"])
    dg_share = _audit_value(params["dg"], "share_of_eligible_loaded_boxes") * sum(
        share * (1 - _audit_value(row, "empty_share")) for row, share in equipment
        if row["id"] in params["dg"]["eligible_equipment_ids"]
    )
    by_class: dict[str, float] = {}
    by_un: dict[str, float] = {}
    profile_classes: dict[str, float] = {}
    unresolved = []
    for cls, class_share in (_audit_shares(params["dg"]["classes"]) if dg_share > 0 else []):
        if class_share == 0:
            continue
        profile_classes[cls["id"]] = profile_classes.get(cls["id"], 0.) + dg_share * class_share
        for un, un_share in _audit_shares(cls["un_numbers"]):
            number = int(un["un_number"])
            share = dg_share * class_share * un_share
            key = f"{number:04d}"
            by_un[key] = by_un.get(key, 0.) + share
            label = parse_hazard_label(cls["id"])
            entries = [entry for entry in entries_for_un(number)
                       if entry.hazard_class == hazard_division(cls["id"])
                       and (label is not None and (label.compatibility_group is None
                            or entry.compatibility_group == label.compatibility_group))]
            if not entries:
                # No primary compatibility group can be asserted for a missing
                # DGL row. The sampler remains responsible for explicit failure.
                unresolved.append(dict(un_number=key, configured_class=cls["id"], share=share))
            for entry in entries:
                primary = entry.hazard_class + (entry.compatibility_group or "")
                by_class[primary] = by_class.get(primary, 0.) + share / len(entries)
    reefer_share = sum(share * (1 - _audit_value(row, "empty_share"))
                       for row, share in equipment if row["reefer"])
    oog_share = _audit_value(params["oog"], "share_of_loaded_compatible_boxes") * sum(
        share * (1 - _audit_value(row, "empty_share")) for row, share in equipment
        if row["id"] in params["oog"]["eligible_equipment_ids"]
    )
    flat_rack_share = sum(share for row, share in equipment if row["iso_size_type"][2] == "P")
    by_pod: dict[str, dict[str, float]] = {}
    for row, share in _audit_shares(pods):
        values = by_pod.setdefault(row["port"], dict(n_boxes=0., reefer=0., oog=0., flat_rack=0.))
        for key, value in dict(n_boxes=share, reefer=share * reefer_share,
                               oog=share * oog_share, flat_rack=share * flat_rack_share).items():
            values[key] += value

    def expected(value: float) -> float | None:
        return None if n_boxes is None else n_boxes * value

    return {
        "kind": "expected_fractional", "reference_n_boxes": n_boxes,
        "basis": "Configured pre-refusal profile per requested box; TEU stopping and refusal conditioning are excluded.",
        "n_boxes": n_boxes,
        "dg_by_class": {key: expected(value) for key, value in sorted(by_class.items())},
        "dg_by_un": {key: expected(value) for key, value in sorted(by_un.items())},
        "by_pod": {key: {name: expected(value) for name, value in values.items()}
                   for key, values in sorted(by_pod.items())},
        "shares": {"dg_by_class": dict(sorted(by_class.items())), "dg_by_un": dict(sorted(by_un.items())),
                   "configured_dg_by_class": dict(sorted(profile_classes.items())), "by_pod": by_pod},
        "mass_bands": None,
        "mass_expectation": "Unspecified: continuous equipment/POD-transformed, clipped and D60-conditioned gross masses; actual stage counts follow below.",
        "unresolved_dgl": unresolved,
    }


def _empty_booking_audit_stage() -> dict[str, Any]:
    return dict(
        kind="actual", n_boxes=0, non_dg=0, dg_by_class={}, dg_by_un={}, uns_by_class={},
        mass_bands=dict.fromkeys(MASS_AUDIT_BANDS, 0), by_pod={},
        mass_summary=dict(count=0, mean_kg=None, p95_kg=None, count_gt_25000kg=0),
    )


class BookingAudit:
    """Empirical booking-flow audit; callers create it only after activation.

    Attach ``data`` to requested.booking_audit before drawing, so failures keep
    the audit. At successful return, record each returned box with record_emit
    and attach snapshot() to realised.booking_audit. Emitted means the raw
    generator return list, never downstream checker acceptance or yard output.
    Quantiles use the exact nearest-rank definition; two heaps keep updates
    O(log n), including on failure. Mass bands are reporting bins, not D25
    interchangeability groups or ship-placement rules.
    """

    def __init__(self, params: Mapping[str, Any], *, n_boxes: int | None,
                 pods: list[Mapping[str, Any]]) -> None:
        self.data: dict[str, Any] = {
            "schema_version": 1,
            "definitions": {
                "requested": "Actual first complete candidate for each output slot, before any replacement.",
                "drawn": "All complete candidate draws, including replacements.",
                "redrawn": "Replacement candidates, classified by their own UN, primary class and gross mass.",
                "refused": "Actual candidates rejected by a handling-port refusal rule.",
                "emitted": "Raw bookings in the successfully returned generator list; not downstream checker-admitted output.",
                "mass_bands": "kg: <10000; 10000<=mass<20000; 20000<=mass<=25000; mass>25000. Reporting only, independent of D25.",
                "p95": "Nearest-rank: sorted masses[ceil(0.95*n)-1].",
            },
            "demand_expectation": _demand_expectation(params, n_boxes, pods),
        }
        self._heaps: dict[str, tuple[list[float], list[float]]] = {}
        self._mass_sums: dict[str, float] = {}
        for stage in ("requested", "drawn", "redrawn", "refused", "emitted"):
            self.data[stage] = _empty_booking_audit_stage()
            self._heaps[stage] = ([], [])
            self._mass_sums[stage] = 0.

    @staticmethod
    def mark_emission_failed(data: dict[str, Any], reason: str) -> None:
        """Close a failed public call without erasing its generated inventory.

        The onboard wrapper can fail after internal generation succeeds. Clear
        the emitted stage on both the live requested audit and its detached
        realised snapshot in that terminal failure path; retain all drawn and
        refusal evidence. No more emissions may be recorded after this call.
        """
        data["emitted"] = _empty_booking_audit_stage()
        data["emission_failure"] = reason

    def _record(self, stage: str, *, dg: Any, mass_kg: float, pod: str,
                iso_type: str, reefer: bool, oog: Any) -> None:
        if not math.isfinite(mass_kg) or mass_kg < 0:
            raise ValueError("audit mass must be finite and nonnegative")
        values = self.data[stage]
        values["n_boxes"] += 1
        if dg is None:
            values["non_dg"] += 1
        else:
            for name, key in (("dg_by_class", dg.imdg_class), ("dg_by_un", f"{int(dg.un_number):04d}")):
                values[name][key] = values[name].get(key, 0) + 1
            numbers = values["uns_by_class"].setdefault(dg.imdg_class, [])
            number = f"{int(dg.un_number):04d}"
            if number not in numbers:
                numbers.append(number)
                numbers.sort()
        band = MASS_AUDIT_BANDS[0 if mass_kg < 10000 else 1 if mass_kg < 20000 else 2 if mass_kg <= 25000 else 3]
        values["mass_bands"][band] += 1
        pod_values = values["by_pod"].setdefault(pod, dict(n_boxes=0, reefer=0, oog=0, flat_rack=0))
        pod_values["n_boxes"] += 1
        pod_values["reefer"] += bool(reefer)
        pod_values["oog"] += bool(oog)
        pod_values["flat_rack"] += iso_type[2] == "P"
        low, high = self._heaps[stage]
        if not low or mass_kg <= -low[0]:
            heapq.heappush(low, -mass_kg)
        else:
            heapq.heappush(high, mass_kg)
        count = values["n_boxes"]
        target = (95 * count + 99) // 100
        while len(low) < target:
            heapq.heappush(low, -heapq.heappop(high))
        while len(low) > target:
            heapq.heappush(high, -heapq.heappop(low))
        self._mass_sums[stage] += mass_kg
        summary = values["mass_summary"]
        summary.update(count=count, mean_kg=self._mass_sums[stage] / count, p95_kg=-low[0])
        summary["count_gt_25000kg"] += mass_kg > 25000

    def record_draw(self, *, is_redraw: bool = False, **candidate: Any) -> None:
        self._record("drawn", **candidate)
        self._record("redrawn" if is_redraw else "requested", **candidate)

    def record_refusal(self, **candidate: Any) -> None:
        self._record("refused", **candidate)

    def record_emit(self, box: Any) -> None:
        self._record("emitted", dg=box.dg, mass_kg=box.gross_mass_kg, pod=box.pod,
                     iso_type=box.iso_type, reefer=box.reefer, oog=box.oog)

    def snapshot(self) -> dict[str, Any]:
        return deepcopy(self.data)


def refused_handling_calls(
    rotation: Rotation,
    call_index: int,
    pod: str,
    primary_class: str | None,
    *,
    onboard: bool = False,
    current_port: PortProfile | None = None,
) -> list[int]:
    """Return handling-call indexes refusing the booked primary class (C5/D66).

    Execution callers must gate this helper with ``rule_freeze_v1_enabled``.
    The generator's export POL is the current call; its synthetic onboard POL
    is call zero. POD is the first matching call strictly after the current
    call. This leg's POD can be a transshipment handling port, but the cargo
    schema records no onward itinerary, so no further stops are inferred.
    Untouched through calls are excluded, and only the supplied primary class
    is considered: subsidiary and additional DG declarations cannot trigger it.
    Unknown handling profiles or malformed rules fail explicitly rather than
    silently treating unsupported DG demand as unrestricted cargo.
    """
    if primary_class is None:
        return []
    if not 0 <= call_index < len(rotation.calls):
        raise ValueError("call_index must resolve to a rotation call")
    destination = next((call for call in rotation.calls
                        if call.index > call_index and call.locode == pod), None)
    if destination is None:
        raise ValueError("POD must resolve to a future call")
    primary = primary_class.upper()
    identifiers = {primary, primary.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), primary.partition(".")[0]}
    refused = []
    for index in (0 if onboard else call_index, destination.index):
        call = rotation.calls[index]
        profile = resolve_port_call_profile(call, current_port=current_port)
        if profile is None:
            raise ValueError(f"Cannot resolve handling port {call.locode} at call {index}")
        rule = next((rule for rule in profile.port_rules if rule.rule_id == "port.dg.refused_class"), None)
        if rule is None:
            continue
        classes = rule.params.get("classes")
        if not isinstance(classes, list) or any(
            not isinstance(value, (str, int)) or isinstance(value, bool) for value in classes
        ):
            raise ValueError(f"port.dg.refused_class requires classes at {call.locode}")
        if identifiers.intersection(str(value).upper() for value in classes):
            refused.append(index)
    return refused


def shipment_requirements(entry: DangerousGoodsEntry, *, declarations: Mapping[str, Any] | None = None,
                          placement_derivable: bool = False) -> list[str]:
    """List missing shipment facts, separately from actual placement checks.

    Activated callers can opt into SW1/SW2 geometry derivation. Their separate
    placement audit must retain these assessments; removing the legacy scalar
    requirement supplies neither geometry, sunlight facts nor permission.
    """
    fields = set()
    conditions = {
        "SW1": ("protected_from_heat",), "SW2": ("clear_living_quarters",),
        "SW6": ("ventilation_compliant",), "SW11": ("shaded", "air_circulation"),
        "H1": ("dry",), "H5": ("handling_compliant",),
        "SW19": ("special_provisions", "short_international_voyage"),
        "SW23": ("bulk_container_type",),
    }
    for code in entry.stowage_codes:
        if placement_derivable is True and code in {"SW1", "SW2"}:
            continue
        declared = declarations.get(code.lower()) if declarations else None
        if code in {"H1", "SW19"} and isinstance(declared, Mapping):
            # A declaration is available, but actual scope must still be bound
            # after the instance/vessel/rotation identities have been finalized.
            fields.add("assessment_scenarios_binding")
            if code == "SW19" and any(declared.get("special_provision_applicability", {}).values()):
                voyage = declared.get("voyage")
                source = voyage.get("provenance") if isinstance(voyage, Mapping) else None
                voyage_declared = (isinstance(voyage, Mapping)
                                   and type(voyage.get("short_international_voyage")) is bool
                                   and isinstance(source, Mapping)
                                   and all(isinstance(source.get(key), str) and source[key].strip()
                                           for key in ("document", "clause", "scenario_id")))
                if not voyage_declared:
                    fields.add("short_international_voyage")
        elif code in conditions:
            fields.update(conditions[code])
        elif code != "SW16":
            fields.add("verified_provisions")
    if entry.subsidiary_hazard_note:
        fields.add("subsidiary_hazards_verified")
    if entry.special_provisions:
        fields.add("special_provisions")
    if entry.segregation_codes:
        # Includes geometry-based requirements; no blanket segregation clearance.
        fields.add("verified_segregation_codes")
    return sorted(fields)


def _declared_scenario_record(template: Mapping[str, Any], *, name: str,
                              declarations: dict[str, Any]) -> dict[str, Any]:
    """Validate the two supported synthetic declarations, never outcome flags."""
    identifier = template.get("id")
    provenance = template.get("provenance")
    if (template.get("synthetic") is not True or template.get("scope") != "shipment_entry"
            or not isinstance(identifier, str) or not identifier.strip()
            or not isinstance(provenance, Mapping)
            or any(not isinstance(provenance.get(key), str) or not provenance[key].strip()
                   for key in ("document", "clause", "scenario_id"))
            or provenance["scenario_id"] != identifier):
        raise ValueError(f"{name} requires an explicitly synthetic shipment-entry template with provenance")
    values = template.get("declarations")
    # Equality alone cannot validate booleans: Python True == 1. Require exact
    # types recursively so malformed scenario inputs never become clearance.
    def same(actual, expected):
        if isinstance(expected, dict):
            return (isinstance(actual, Mapping) and set(actual) == set(expected)
                    and all(same(actual[key], value) for key, value in expected.items()))
        return type(actual) is type(expected) and actual == expected

    if not same(values, declarations):
        raise ValueError(f"unsupported {name} synthetic scenario declarations")
    return {**deepcopy(declarations), "provenance": {
        key: provenance[key] for key in ("document", "clause", "scenario_id")}}


def declare_entry_scenarios(entry: DangerousGoodsEntry, *, iso_type: str,
                            scenarios: Mapping[str, Any] | None = None,
                            scenario_key: str | None = None) -> dict[str, Any]:
    """Declare supported H1 and explicitly selected battery scenario facts.

    Pass templates explicitly for an I/O-free, deterministic transformation;
    None loads ``params/cargo/declared_scenarios.toml``. An empty mapping disables
    declarations. Inputs are never mutated and no RNG draw is consumed. The
    generator caller owns activation and retains these facts per entry. Their
    template provenance is deliberately unbound: an instance author must bind
    actual shipment/vessel/voyage/call/placement scope before checker use.
    ``scenario_key='sw19_recycling'`` explicitly selects intact SP377 recycling
    batteries on a declared non-short voyage; None retains the main normal-use
    scenario. Selection consumes no random draw and never changes H1 selection.

    APPROXIMATION: closed packaged G/R equipment uses synthetic weathertight
    CTUs and dry consignment packing for H1; normal intact batteries use neither
    SP376 nor SP377. These are scenario inputs, never inspections or observed
    handling outcomes. All other SP applicability and placement remain unknown.
    Explicit CTU closure/packing facts must agree with existing model declarations;
    binding must reject contradictions rather than overwrite the model.
    """
    if scenario_key not in (None, "sw19_recycling"):
        raise ValueError("unsupported shipment scenario selection")
    if not isinstance(iso_type, str) or len(iso_type) < 3 or iso_type[2] not in {"G", "R"}:
        return {}
    names = []
    if "H1" in entry.source_stowage_codes:
        names.append("h1")
    if "SW19" in entry.source_stowage_codes and entry.un_number in {3480, 3481}:
        names.append("sw19")
    if not names:
        return {}
    templates = load_param_file("cargo", "declared_scenarios")["templates"] if scenarios is None else scenarios
    if not isinstance(templates, Mapping):
        raise ValueError("shipment scenario templates must be a mapping")
    if not templates:
        return {}
    facts: dict[str, Any] = {}
    shared_declarations = {
        "ctu_closed": True,
        "packing_form": PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS.value,
    }
    for name in names:
        template_key = scenario_key if name == "sw19" and scenario_key is not None else name
        if template_key not in templates:
            if template_key != name:
                raise ValueError(f"selected shipment scenario template {template_key!r} is missing")
            continue
        template = templates[template_key]
        if not isinstance(template, Mapping):
            raise ValueError(f"{name} shipment scenario template must be a mapping")
        declarations = ({"ctu_weathertight": True, "packing_condition": "dry"} if name == "h1" else {
            "battery_condition": "intact", "transport_purpose": "normal",
            "special_provision_applicability": {"376": False, "377": False},
        })
        if template_key == "sw19_recycling":
            declarations.update(transport_purpose="recycling", transport_in_accordance=True,
                                special_provision_applicability={"376": False, "377": True})
        facts[name] = _declared_scenario_record(
            template, name=name, declarations={**shared_declarations, **declarations})
        for key in shared_declarations:
            facts[name].pop(key)
        if name == "sw19":
            facts[name]["un_number"] = f"{entry.un_number:04d}"
        if template_key == "sw19_recycling":
            voyage = template.get("voyage")
            if not isinstance(voyage, Mapping):
                raise ValueError("SP377 recycling scenario requires an explicit synthetic voyage declaration")
            facts[name]["voyage"] = _declared_scenario_record(
                voyage, name="sw19_recycling.voyage", declarations={"short_international_voyage": False})
    if facts:
        template_ids = [record["provenance"]["scenario_id"] for record in facts.values()]
        if "voyage" in facts.get("sw19", {}):
            template_ids.append(facts["sw19"]["voyage"]["provenance"]["scenario_id"])
        facts.update(synthetic=True, scope="shipment_entry",
                     template_ids=template_ids,
                     entry_id=entry.id, un_number=f"{entry.un_number:04d}",
                     packing_group=entry.packing_group,
                     **shared_declarations)
    return facts


def declare_shipment(entry: DangerousGoodsEntry, *, iso_type: str,
                     seed: int, generator_version: str) -> dict[str, Any]:
    """Declare a synthetic packing choice supported by the sampled equipment.

    No additional random draws are consumed: existing demand/weight shares and
    seeded draws remain unchanged. Unknown packing combinations remain unknown.
    """
    packing = None
    closed = None
    kind = iso_type[2]
    if kind in {"G", "R"}:
        packing = PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS
        closed = True
    elif kind == "T" and entry.un_number in LIQUID_TANK_UNS:
        packing = PackingForm.TANK_CONTAINER
        closed = True
    elif kind in {"U", "P"}:
        packing = PackingForm.OPEN_FREIGHT_CONTAINER
        closed = False
    return {
        "packing_form": packing, "ctu_closed": closed,
        "special_provisions": None if entry.special_provisions else [],
        "shipment_provenance": Provenance(
            seed=seed, created_by="generate.cargo.synthetic_shipper",
            versions={"cargo_generator": generator_version,
                      "dg_shipment_policy": SHIPMENT_POLICY_VERSION},
            parents=["synthetic-dg-shipment-policy-v1", f"imdg-42-24:{entry.id}"],
        ),
    }
