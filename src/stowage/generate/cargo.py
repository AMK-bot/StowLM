"""Seeded synthetic feeder bookings, without assigning ship or yard positions.

The sampling recipe and public sources are in ``params/cargo/README.md`` and
``feeder_export.toml``. A triangular weight distribution describes a bounded
minimum, most likely mass, and maximum; payload (gross minus tare) is then
multiplied by POD and clipped. SOLAS VI/2 requires verified gross mass, but
supplies no universal numerical minimum or error tolerance: these synthetic
masses are treated as exact VGM and bounded by the selected equipment rating.
APPROXIMATION: D60 conditions this distribution on gross mass at most 30.5 t,
the range covered by the synthetic securing design; CSC plate ratings stay as
sourced. The original distribution below that ceiling keeps its shape.

APPROXIMATION: equipment, demand, POD/weight correlations and stage B onboard
composition follow the synthetic parameter assumptions, not measured bookings.
This produces demand, not a feasible loading plan. Heat/top-stowage, IMDG
conditional provisions, OOG securing and physical placement need the checker.
Fail-closed DG demand is retained, including class-1 cargo and radioactive
shipments with unresolved provisions. DGL guards survive in the declaration;
``CargoGenerationReport.unresolved_provisions`` identifies boxes for task 4.3's
feasibility screen. These tags never certify or exempt a shipment.
Schema 1.2 declarations come from ``cargo_shipments``: packing choices are
labelled synthetic, and unmet entry-specific assessments remain unknown.

Weight classes remain yard gate-in labels using the catalogue's shared D25
boundaries. After rule-freeze-v1 activation, interchangeability groups ordinary
bookings by POD, equipment and exact gross mass; legacy groups use weight class
(docs/domain.md, Moves; Tanaka & Voss 2019, doi:10.1016/j.ejor.2019.06.014).
It is an advisory grouping: exact VGM is retained, and stack mass, stability
and every other hard constraint still require checker validation after a swap.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import math
from random import Random
from typing import Any, Mapping

from stowage.conventions import SYNTHETIC_OWNER_CODES, make_container_id
from stowage.model.cargo import Container, DangerousGoods, OutOfGauge
from stowage.model.port import PortCall, Rotation
from stowage.model.vessel import Vessel
from stowage.params import load_param_file
from stowage.ports import resolve_port_call_profile
from stowage.rules.catalogue import load_catalogue
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.rules.imdg.dgl import entries_for_un
from stowage.rules.imdg.labels import hazard_division, parse_hazard_label
from stowage.generate.cargo_shipments import (
    BookingAudit, SHIPMENT_POLICY_VERSION, declare_entry_scenarios, declare_shipment,
    refused_handling_calls, shipment_requirements,
)
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG, triangular_cdf

from stowage.versions import CARGO_GENERATOR_VERSION
# The registry remains leader-owned. Keep its legacy version off-state; L3/L4
# must use effective_cargo_generator_version() for activated provenance.
RULE_FREEZE_CARGO_GENERATOR_VERSION = "8.0.0"
LEGACY_CARGO_GENERATOR_VERSION = "7.0.0"
# A finite retry guard is an explicit sampling failure, never acceptance or a
# statement that a rarely supported profile has no possible valid shipment.
MAX_CONSECUTIVE_REFUSAL_REDRAWS = 256
Params = Mapping[str, Any]


def effective_cargo_generator_version() -> str:
    """Public cargo identity for generators, campaign keys and SFT admission.

    Keep the legacy epoch fixed even if the leader updates the version registry
    before the coordinated activation. Consumers must call this at operation time.
    """
    return RULE_FREEZE_CARGO_GENERATOR_VERSION if rule_freeze_v1_enabled() else LEGACY_CARGO_GENERATOR_VERSION


@dataclass
class CargoGenerationReport:
    """Optional per-call audit, filled in place; no global random/report state.

    ``requested`` contains original profile probabilities; ``realised`` contains
    observed counts. Conditioning and unresolved placement checks are explicit.
    A failed call records its reason before raising. Activated refusal redraws
    count unsupported demand by primary class and retain UN/equipment/POD facts
    in requested.refused_bookings, including on failure. No DG is relabelled.
    Every call first clears the previous report. Generation failures leave
    ``realised`` empty; a later vessel-support failure retains the generated
    inventory counts with explicit rejections. Current-call partial DG audit
    entries may remain on a generation failure, never a successful output claim.
    ``unresolved_provisions`` maps retained box ids to DGL guard identifiers or
    the audit-only ``class_1_stowage_category:<category>`` marker. The latter
    describes checker 2.4's unsupported explosives arrangements, not an IMDG
    provision code. Task 4.3 must count/screen these boxes, never assume clearance.
    ``shipment_requirements`` names unknown entry-specific facts for downstream
    assessment. Activated requested/realised ``placement_requirements`` instead
    lists SW1/SW2 checks that the checker derives for actual supported geometry.
    Those codes are pending checks, never asserted clearance or missing scalar
    shipment declarations. Neither audit replaces full checking.
    """

    seed: int | None = None
    profile: str = ""
    generator_version: str = CARGO_GENERATOR_VERSION
    rotation: str = ""
    call_index: int = 0
    requested: dict[str, Any] = field(default_factory=dict)
    realised: dict[str, Any] = field(default_factory=dict)
    conditioning: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    rejections: list[str] = field(default_factory=list)
    unresolved_provisions: dict[str, list[str]] = field(default_factory=dict)
    shipment_requirements: dict[str, list[str]] = field(default_factory=dict)


class UnsupportedCargoError(ValueError):
    """The requested profile/ship cannot represent a sampled cargo fact."""


def _v(table: Params, key: str) -> float:
    return float(table[key]["value"])


def _pick(rng: Random, rows: list[Params]) -> Params:
    weights = [_v(row, "share") for row in rows]
    if not rows or any(not math.isfinite(w) or w < 0 for w in weights) or sum(weights) <= 0:
        raise ValueError("sampling shares must be finite, nonnegative and have positive total")
    return rng.choices(rows, weights=weights, k=1)[0]


def _loaded_gross_mass(rng: Random, low: float, mode: float, high: float,
                       tare: float, multiplier: float) -> float:
    """Sample the original transformed distribution conditional on D60.

    APPROXIMATION: D60 removes only the over-30.5 t tail. Inverse-CDF sampling
    is exactly rejection sampling conditional on acceptance, using one uniform
    draw; it avoids retry loops when a custom profile retains a very small tail.
    Neither the triangular parameters nor any accepted gross mass are rescaled.
    Existing lower/upper clipping and their atoms otherwise remain unchanged.
    """
    if not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError("payload weight multiplier must be finite and nonnegative")
    if low > MAX_GENERATED_GROSS_MASS_KG:
        raise ValueError("loaded weight profile has no mass within D60 securing range")
    maximum = min(high, max(low, tare + (high - tare) * multiplier))
    if maximum <= MAX_GENERATED_GROSS_MASS_KG:
        raw = rng.triangular(low, high, mode)
        return min(high, max(low, tare + (raw - tare) * multiplier))
    cutoff = tare + (MAX_GENERATED_GROSS_MASS_KG - tare) / multiplier
    retained = triangular_cdf(cutoff, low, mode, high)
    if retained <= 0:
        raise ValueError("loaded weight profile has no mass within D60 securing range")
    u = rng.random() * retained
    c = (mode - low) / (high - low)
    if u > c:
        u, c = 1 - u, 1 - c
        raw = high + (low - high) * math.sqrt(u * c)
    else:
        raw = low + (high - low) * math.sqrt(u * c)
    return min(high, max(low, tare + (raw - tare) * multiplier))


def _params(params: Params | str | None) -> Params:
    profile = load_param_file("cargo", params or "feeder_export") if isinstance(params, (str, type(None))) else params
    pending = profile.get("dg", {}).get("pending_rule_freeze_v1")
    if not pending or not rule_freeze_v1_enabled():
        return profile
    # APPROXIMATION: D66's rare 1.4S mix is synthetic coverage demand. Apply
    # configured overrides only to retained classes, without mutating shared
    # profiles or restoring classes a custom scenario explicitly removed.
    effective = deepcopy(profile)
    overrides = {row["id"]: row for row in effective["dg"].pop("pending_rule_freeze_v1")["classes"]}
    for row in effective["dg"]["classes"]:
        if row["id"] in overrides:
            row.update(overrides[row["id"]])
    return effective


def dg_stress_profile(*, pair_id: str | None = None) -> dict[str, Any]:
    """Return D70's separate synthetic mixture or a concentrated pair cohort.

    Pair cohorts condition the configured class distribution to the two named
    classes with equal shares. Presence is demand coverage; only final checker
    reports can establish admission and a binding segregation constraint.
    Every call returns independent data; the main export profile is untouched.
    """
    profile = deepcopy(load_param_file("cargo", "dg_stress"))
    if pair_id is not None:
        pair = next((row for row in profile["dg"]["segregation_pair_panels"]
                     if row["id"] == pair_id), None)
        if pair is None:
            raise ValueError(f"unknown DG-stress pair {pair_id!r}")
        profile["dg"]["classes"] = [row for row in profile["dg"]["classes"]
                                      if row["id"] in pair["classes"]]
        if len(profile["dg"]["classes"]) != 2:
            raise ValueError("DG-stress pair must identify two supported classes")
        for row in profile["dg"]["classes"]:
            row["share"] = dict(value=.5, unit="fraction_of_dg_boxes",
                                assumption=pair["assumption"])
        profile["profile_id"] += ":" + pair_id
        profile["dg"]["selected_pair"] = deepcopy(pair)
    return profile


def _validate_probabilities(params: Params) -> None:
    """Validate Bernoulli inputs even when a branch is never sampled."""
    rows = [(f"equipment[{e['id']}]", e, "empty_share") for e in params["equipment"]]
    rows.extend((section, params[section], field) for section, field in (
        ("dg", "share_of_eligible_loaded_boxes"),
        ("oog", "share_of_loaded_compatible_boxes"),
        ("special_stowage", "share_of_loaded_boxes"),
    ))
    for label, row, field in rows:
        try:
            probability = _v(row, field)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(f"{label}.{field} must be a finite probability in [0, 1]") from error
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError(f"{label}.{field} must be a finite probability in [0, 1]")
    if "battery_recycling_share" in params["dg"]:
        probability = _v(params["dg"], "battery_recycling_share")
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("dg.battery_recycling_share must be a finite probability in [0, 1]")


def _route(rotation: Rotation | str | None, params: Params, rng: Random) -> tuple[Rotation, Params]:
    rows = params["rotations"]
    if rotation is None:
        profile = _pick(rng, rows)
    elif isinstance(rotation, str):
        profile = next((row for row in rows if row["id"] == rotation), None)
        if profile is None:
            raise ValueError(f"unsupported rotation {rotation!r}")
    else:
        # Prefer exact call sequences; arbitrary service names are informational.
        profile = next((row for row in rows if row["port_calls"] == [c.locode for c in rotation.calls]), None)
        if profile is None:
            profile = next((row for row in rows if row["id"] == rotation.service), None)
        if profile is None:
            raise ValueError("Rotation must match a configured call sequence or service id")
    if not isinstance(rotation, Rotation):
        rotation = Rotation(service=profile["id"], calls=[PortCall(locode=port, index=i) for i, port in enumerate(profile["port_calls"])])
    if rule_freeze_v1_enabled():
        # Archetypes change serialized route data, so populate them only at the
        # coordinated freeze. Never mutate a caller's shared Rotation or infer
        # crane limits: environment-only profiles intentionally have none.
        rotation = rotation.model_copy(deep=True)
        for call in rotation.calls:
            port = resolve_port_call_profile(call)
            if port is None:
                raise ValueError(f"unsupported port profile for rotation call {call.locode}")
            call.archetype = port.archetype
    return rotation, profile


def _dg(rng: Random, profile: Params, report: CargoGenerationReport, *, iso_type: str) -> DangerousGoods:
    cls = _pick(rng, profile["classes"])
    un = int(_pick(rng, cls["un_numbers"])["un_number"])
    if rule_freeze_v1_enabled():
        label = parse_hazard_label(cls["id"])
        entries = [e for e in entries_for_un(un)
                   if e.hazard_class == hazard_division(cls["id"])
                   and (label.compatibility_group is None or e.compatibility_group == label.compatibility_group)] if label else []
    else:
        entries = [e for e in entries_for_un(un) if e.hazard_class == cls["id"]]
    if not entries:
        reason = f"DGL has no supported entry for UN {un:04d}, class {cls['id']}"
        report.rejections.append(reason)
        raise UnsupportedCargoError(reason)
    # APPROXIMATION: the profile specifies no PG frequencies. Choose uniformly
    # among complete curated variants; never combine attributes across rows.
    entry = rng.choice(entries)
    imdg_class = entry.hazard_class + (entry.compatibility_group or "")
    return DangerousGoods(
        un_number=f"{un:04d}", imdg_class=imdg_class,
        subsidiary_risks=list(entry.subsidiary_risks), packing_group=entry.packing_group,
        stowage_category=entry.stowage_category,
        # Preserve internal fail-closed DGL guards as well as source SW codes.
        # H codes have their own declaration field; no guard is a clearance fact.
        stowage_codes=[c for c in entry.stowage_codes if not c.startswith("H")],
        handling_codes=[c for c in entry.stowage_codes if c.startswith("H")],
        segregation_codes=list(entry.segregation_codes),
        **declare_shipment(entry, iso_type=iso_type, seed=report.seed,
                           generator_version=report.generator_version),
    )


def generate_load_list(
    rotation: Rotation | str | None,
    call_index: int,
    n_boxes: int | None = None,
    *,
    seed: int,
    teu: float | None = None,
    params: Params | str | None = None,
    id_offset: int = 0,
    report: CargoGenerationReport | None = None,
) -> list[Container]:
    """Generate export boxes with future PODs; exactly one quantity is required.

    ``rotation=None`` draws one configured route with its call-level share;
    a string selects its id, or pass a model Rotation. Future supported POD
    shares are renormalised at later calls. ``teu`` is a positive budget: draw
    whole boxes until the next would exceed it (shortfall < 2.25 TEU). This
    stopping rule is recorded and never changes a sampled box's equipment.
    For an exact box-level distribution use ``n_boxes``.

    IDs occupy QZXU's serial namespace; set disjoint ``id_offset`` ranges when
    combining independently generated lists. Stage B uses QZYU. Up to one
    million boxes per namespace, with no wrapping. ``report`` is optional but
    recommended when constructing an instance; no checker approval is implied.
    """
    return _generate(rotation, call_index, n_boxes, seed=seed, teu=teu, params=params,
                     id_offset=id_offset, report=report, onboard=False)


def generate_onboard_cargo(
    vessel: Vessel,
    rotation: Rotation | str,
    call_index: int,
    *,
    seed: int,
    n_boxes: int | None = None,
    teu: float | None = None,
    params: Params | str | None = None,
    id_offset: int = 0,
    report: CargoGenerationReport | None = None,
) -> list[Container]:
    """Generate stage B inventory only, not placements (task 10.3).

    Specify ``n_boxes`` or ``teu``; occupancy is a scenario choice, not inferred
    from vessel capacity. Destination population is conditioned on occurrences
    strictly after the current call. POL is the first preceding call (or first
    call at index zero). Basic vessel support is checked without rejection
    sampling; full placement feasibility remains the checker's responsibility.
    """
    audit = report if report is not None else CargoGenerationReport()
    boxes = _generate(rotation, call_index, n_boxes, seed=seed, teu=teu, params=params,
                      id_offset=id_offset, report=audit, onboard=True)
    try:
        _check_vessel_support(vessel, boxes, audit)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as error:
        if not audit.rejections:
            audit.rejections.append(str(error))
        for section in (audit.requested, audit.realised):
            if "booking_audit" in section:
                BookingAudit.mark_emission_failed(section["booking_audit"], str(error))
        raise
    audit.unchecked.append("vessel pair geometry, capacities, DG Document of Compliance and all placement constraints need task 10.3/checker")
    return boxes


def _check_vessel_support(vessel, boxes, audit):
    """Retain the generated inventory audit if the vessel cannot support it."""
    cells = list(vessel.cells.values()) if isinstance(vessel.cells, dict) else list(vessel.cells)
    reasons: list[str] = []
    for length in sorted({box.length_ft for box in boxes}):
        if not any(getattr(cell, f"accepts_{length}") for cell in cells):
            reasons.append(f"vessel has no cells accepting {length}-foot cargo")
    if sum(box.reefer for box in boxes) > sum(cell.reefer_plug for cell in cells):
        reasons.append("powered reefer count exceeds vessel cell plugs")
    if reasons:
        audit.rejections.extend(reasons)
        raise UnsupportedCargoError("; ".join(reasons))


def _physical_footprints(vessel, box) -> list[str]:
    """All equipment-compatible addresses, independently of DG permission."""
    from stowage.conventions import cells_of_slot, format_slot, parse_slot

    cells = {cell.id: cell for cell in vessel.cells}
    slots = set()
    for cell in cells.values():
        address = parse_slot(cell.id)
        candidates = [cell.id] if box.length_ft == 20 else [
            format_slot(bay, address.row, address.tier)
            for bay in (address.bay - 1, address.bay + 1) if 2 <= bay <= 998]
        for slot in candidates:
            footprint = cells_of_slot(slot)
            if all(cid in cells and getattr(cells[cid], f"accepts_{box.length_ft}")
                   for cid in footprint):
                slots.add(slot)
    return sorted(slots)


def bind_design_scenarios(instance, *, container_id: str | None = None,
                          entry_path: str = "primary"):
    """Register authored SW1/SW2 design references for actual assessed contexts.

    By default include every DG primary and additional entry in the instance,
    including onboard/import cargo. An explicit container selects one entry.
    Shared authored IDs retain separate complete records: first flat, then a
    list on a distinct registration. Never splice scope fields or allowed slots
    between records, overwrite siblings, or duplicate an identical registration.

    Both map references and every authored thermal source reference are scoped
    to the actual shipment/vessel/voyage/call and ALL physical footprints. No
    geometry, completeness, thermal liability, sunlight applicability or outcome
    is inferred. Missing declarations stay missing. Inputs are never mutated.
    Off-state calls return the original without inspecting any supplied scope.
    """
    from dataclasses import asdict
    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    if not rule_freeze_v1_enabled():
        return instance
    targets = []
    if container_id is None:
        if entry_path != "primary":
            raise ValueError("entry selection requires an explicit container")

        def visit(box, entry, path):
            targets.append((box, path))
            for index, child in enumerate(entry.additional):
                visit(box, child, f"{path}.additional.{index}")

        for box in instance.containers:
            if box.dg is not None:
                visit(box, box.dg, "primary")
    else:
        box = next((box for box in instance.containers if box.id == container_id), None)
        if box is None or box.dg is None:
            raise ValueError("design binding requires an actual DG container")
        targets.append((box, entry_path))
    contexts = []
    for box, path in targets:
        slots = _physical_footprints(instance.vessel, box)
        if not slots:
            raise ValueError("design binding requires applicable physical footprints")
        context = context_for(instance, box, path, slots[0])
        if context is None:
            raise ValueError("design binding needs an actual entry and declared future voyage leg")
        contexts.append((context, slots))
    bound = instance.model_copy(deep=True)
    metadata = bound.vessel.imdg_placement_geometry
    if metadata is None or not contexts:
        return bound
    raw = metadata.model_dump(mode="python")
    sources = [raw.get("living_quarters_map_provenance"), raw.get("heat_map_provenance")]
    sources.extend(row.get("provenance") for row in raw["heat_sources"])
    if not any(source is not None for source in sources):
        return bound
    config = bound.rule_overrides.setdefault("imdg.stowage.3_2", {})
    if not isinstance(config, dict):
        raise ValueError("IMDG stowage overrides must be a mapping")
    registry = config.setdefault("assessment_scenarios", {})
    if not isinstance(registry, dict):
        raise ValueError("design scenario registry must be a mapping")
    for source in sources:
        if source is None:
            continue
        if (not isinstance(source, Mapping) or any(
                not isinstance(source.get(key), str) or not source[key].strip()
                for key in ("document", "clause", "scenario_id"))):
            raise ValueError("design reference requires its authored document, clause and scenario id")
        scenario_id = source["scenario_id"]
        for context, slots in contexts:
            record = asdict(context)
            record.pop("slot")
            record.update(document=source["document"], clause=source["clause"],
                          allowed_slots=slots, synthetic=True, scope="vessel_design")
            if scenario_id not in registry:
                registry[scenario_id] = record
            else:
                existing = registry[scenario_id]
                siblings = list(existing) if isinstance(existing, (list, tuple)) else [existing]
                # Equality alone admits True == 1 for call indices. A matching
                # sibling must independently satisfy the strict public resolver.
                identical = any(row == record and provenance_matches(
                    source, context, {scenario_id: row}) for row in siblings)
                if not identical:
                    registry[scenario_id] = [*siblings, record]
            if not provenance_matches(source, context, registry):
                raise ValueError("design provenance could not bind to the actual context")
    return bound


def bind_shipment_scenarios(instance, report: CargoGenerationReport):
    """Return a copy with shipment and authored design evidence bound to scope.

    Only a successful activated cargo report's emitted manifest is consumed.
    Every actual DG entry also receives its authored design registrations, even
    when this report contains no shipment-specific H1/SW19 declarations.
    Physical footprints establish evidence scope, never placement permission:
    the checker still assesses every provision and actual placement geometry.
    No geometry, voyage classification, or additional-entry facts are inferred.
    An explicit synthetic voyage classification can be bound to the actual leg;
    it is a declaration, never a classification inferred from route distance.
    The input instance and report are unchanged, including on binding failure.
    Off-state calls return the original instance without reading the report.
    """
    from dataclasses import asdict
    from hashlib import sha256
    import json

    from stowage.rules.imdg.scenarios import context_for, provenance_matches

    if not rule_freeze_v1_enabled():
        return instance
    if (report.rejections or type(report.realised.get("n_boxes")) is not int
            or report.realised["n_boxes"] < 0 or "shipment_scenarios" not in report.realised):
        raise ValueError("shipment binding requires a successful emitted cargo report")
    manifest = report.realised["shipment_scenarios"]
    if not isinstance(manifest, Mapping):
        raise ValueError("shipment_scenarios must map emitted container ids to entry facts")
    if len(manifest) > report.realised["n_boxes"]:
        raise ValueError("shipment manifest exceeds the emitted inventory count")
    bound = instance.model_copy(deep=True)
    if not manifest:
        return bind_design_scenarios(bound)
    config = bound.rule_overrides.setdefault("imdg.stowage.3_2", {})
    if not isinstance(config, dict):
        raise ValueError("IMDG stowage overrides must be a mapping")
    containers = config.setdefault("containers", {})
    registry = config.setdefault("assessment_scenarios", {})
    if not isinstance(containers, dict) or not isinstance(registry, dict):
        raise ValueError("shipment assessments and scenario registry must be mappings")
    boxes = {box.id: box for box in bound.containers}

    for container_id, entries in manifest.items():
        box = boxes.get(container_id)
        if box is None or box.dg is None or not isinstance(entries, Mapping):
            raise ValueError(f"shipment manifest has an unknown DG container {container_id}")
        slots = _physical_footprints(bound.vessel, box)
        if not slots:
            raise ValueError(f"shipment {container_id} has no applicable physical vessel footprints")
        legacy = containers.setdefault(container_id, {})
        if not isinstance(legacy, dict):
            raise ValueError("container shipment facts must be a mapping")
        assessments = legacy.setdefault("entry_assessments", {})
        if not isinstance(assessments, dict):
            raise ValueError("entry_assessments must be a mapping")
        for entry_path, declaration in entries.items():
            context = context_for(bound, box, entry_path, slots[0])
            if context is None:
                raise ValueError(f"shipment entry {entry_path!r} needs a valid entry and future voyage leg")
            if (not isinstance(declaration, Mapping) or declaration.get("synthetic") is not True
                    or declaration.get("scope") != "shipment_entry"):
                raise ValueError("shipment declarations must explicitly identify their synthetic entry scope")
            if set(declaration) - {"h1", "sw19", "ctu_closed", "packing_form", "synthetic", "scope",
                                   "template_ids", "entry_id", "un_number", "packing_group"}:
                raise ValueError("unsupported shipment declaration fields; no geometry or clearance is inferred")
            entry = box.dg
            for index in entry_path.split('.')[2::2]:
                entry = entry.additional[int(index)]
            closed = entry.ctu_closed if entry.ctu_closed is not None else box.dg.ctu_closed
            if "ctu_closed" in declaration and (type(declaration["ctu_closed"]) is not bool
                                                or declaration["ctu_closed"] is not closed):
                raise ValueError("shipment CTU declaration contradicts the actual DG model")
            packing = entry.packing_form if entry.packing_form is not None else box.dg.packing_form
            if "packing_form" in declaration and declaration["packing_form"] != packing:
                raise ValueError("shipment packing declaration contradicts the actual DG model")
            facts = deepcopy(dict(declaration))
            facts.pop("ctu_closed", None)  # physical classification stays on the model
            facts.pop("packing_form", None)
            if not any(key in facts for key in ("h1", "sw19")):
                raise ValueError("shipment declaration contains no supported H1 or SW19 facts")
            dgl = next((row for row in entries_for_un(int(entry.un_number))
                        if row.packing_group == entry.packing_group), None)
            if dgl is None:
                raise ValueError("shipment entry has no matching UN/packing-group DGL row")
            identity = {"entry_id": dgl.id, "un_number": entry.un_number,
                        "packing_group": entry.packing_group}
            if any(key not in declaration or declaration[key] != value for key, value in identity.items()):
                raise ValueError("shipment declaration identity must match the original UN/packing-group entry")
            for key in identity:
                facts.pop(key)
            if "h1" in facts:
                if not isinstance(facts["h1"], dict):
                    raise ValueError("H1 declaration must be a mapping")
                if "H1" not in dgl.stowage_codes or closed is not True:
                    raise ValueError("H1 scenario requires its actual H1 entry and declared closed CTU")
                if entry.dry is False and facts["h1"].get("packing_condition") == "dry":
                    raise ValueError("H1 scenario contradicts existing shipment facts")
            if "sw19" in facts:
                record = facts["sw19"]
                if ("SW19" not in dgl.stowage_codes or not isinstance(record, dict)
                        or record.get("un_number") != entry.un_number):
                    raise ValueError("SW19 scenario must match the actual battery entry")
                if "voyage" in record:
                    voyage = record["voyage"]
                    if (not isinstance(voyage, dict)
                            or set(voyage) != {"short_international_voyage", "provenance"}
                            or type(voyage["short_international_voyage"]) is not bool
                            or not isinstance(voyage["provenance"], dict)):
                        raise ValueError("synthetic voyage classification needs an explicit boolean and provenance")
                    short = voyage["short_international_voyage"]
                    for known in (entry.short_international_voyage,
                                  legacy.get("short_international_voyage")):
                        if known is not None and (type(known) is not bool or known is not short):
                            raise ValueError("synthetic voyage classification contradicts existing shipment facts")
                actual = entry.special_provisions
                applicability = record.get("special_provision_applicability", {})
                if actual is not None and any(
                    applicability.get(code) is not (code in {str(value) for value in actual})
                    for code in ("376", "377")
                ):
                    raise ValueError("SW19 scenario contradicts existing special-provision applicability")

            def scope_sources(value):
                if isinstance(value, dict):
                    source = value.get("provenance")
                    if source is not None:
                        if (not isinstance(source, dict) or any(
                            not isinstance(source.get(key), str) or not source[key].strip()
                            for key in ("document", "clause", "scenario_id")
                        )):
                            raise ValueError("shipment assessment needs explicit template provenance")
                        template_id = source["scenario_id"]
                        record = asdict(context)
                        record.pop("slot")
                        record.update(document=source["document"], clause=source["clause"],
                                      allowed_slots=slots, synthetic=True, scope="shipment_entry",
                                      template_scenario_id=template_id, entry_id=dgl.id)
                        payload = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)
                        scenario_id = "synthetic-shipment-sha256:" + sha256(payload.encode()).hexdigest()
                        if scenario_id in registry and registry[scenario_id] != record:
                            raise ValueError("conflicting existing shipment scenario registry record")
                        registry[scenario_id] = record
                        source["scenario_id"] = scenario_id
                        if not provenance_matches(source, context, registry):
                            raise ValueError("shipment provenance could not bind to actual context")
                    for key, child in value.items():
                        if key != "provenance":
                            scope_sources(child)
                elif isinstance(value, (list, tuple)):
                    for child in value:
                        scope_sources(child)

            for key in ("h1", "sw19"):
                if key in facts:
                    if not isinstance(facts[key], dict) or "provenance" not in facts[key]:
                        raise ValueError("shipment assessment requires provenance")
                    scope_sources(facts[key])
                    if entry_path == "primary" and key in legacy and legacy[key] != facts[key]:
                        raise ValueError("shipment scenario contradicts existing container assessment")
            if entry_path in assessments and assessments[entry_path] != facts:
                raise ValueError("shipment scenario contradicts an existing entry assessment")
            assessments[entry_path] = facts
    return bind_design_scenarios(bound)


def _generate(rotation, call_index, n_boxes, *, seed, teu, params, id_offset, report, onboard):
    audit = report if report is not None else CargoGenerationReport()
    # Reset before any validation or I/O so a reused report never describes an
    # earlier call. Keep the supplied object; all mutable fields are per-call.
    fresh = CargoGenerationReport(
        seed=seed, call_index=call_index,
        rotation=rotation if isinstance(rotation, str) else getattr(rotation, "service", ""),
        requested={"n_boxes": n_boxes, "teu": teu},
    )
    audit.__dict__.update(fresh.__dict__)
    try:
        audit.generator_version = effective_cargo_generator_version()
        return _generate_checked(rotation, call_index, n_boxes, seed=seed, teu=teu,
                                 params=params, id_offset=id_offset, report=audit, onboard=onboard)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, OSError) as error:
        # Include malformed custom profiles, model validation, parameter I/O,
        # exhausted IDs and sampling errors without changing exception identity.
        reason = str(error)
        if reason not in audit.rejections:
            audit.rejections.append(reason)
        raise


def _generate_checked(rotation, call_index, n_boxes, *, seed, teu, params, id_offset, report, onboard):
    if (n_boxes is None) == (teu is None):
        raise ValueError("specify exactly one of n_boxes or teu")
    if n_boxes is not None and (isinstance(n_boxes, bool) or not isinstance(n_boxes, int) or n_boxes < 0):
        raise ValueError("n_boxes must be a nonnegative integer")
    if teu is not None and (not math.isfinite(teu) or teu < 0):
        raise ValueError("teu must be finite and nonnegative")
    if isinstance(id_offset, bool) or not isinstance(id_offset, int) or not 0 <= id_offset < 1_000_000:
        raise ValueError("id_offset must be an integer in [0, 1000000)")
    p = _params(params)
    if p.get("requires_rule_freeze_v1") and not rule_freeze_v1_enabled():
        raise UnsupportedCargoError("DG-stress profile requires rule-freeze-v1 activation")
    report.profile = p["profile_id"]
    _validate_probabilities(p)
    # Read the shared D25 policy once per call. No local copy of its numeric
    # boundaries or cached box labels may override the catalogue's actual VGM rule.
    weight_classes = load_catalogue().weight_classes
    freeze_enabled = rule_freeze_v1_enabled()
    rng = Random(seed)
    rotation, route = _route(rotation, p, rng)
    if isinstance(call_index, bool) or not isinstance(call_index, int) or not 0 <= call_index < len(rotation.calls):
        raise ValueError("call_index must identify a rotation call")
    audit = report if report is not None else CargoGenerationReport()
    audit.seed, audit.profile, audit.rotation, audit.call_index = seed, p["profile_id"], route["id"], call_index
    current_port = resolve_port_call_profile(rotation.calls[call_index]) if freeze_enabled else None
    audit.requested = {"n_boxes": n_boxes, "teu": teu, "equipment_shares": {e["id"]: _v(e, "share") for e in p["equipment"]},
                       "pod_shares": {row["port"]: _v(row, "share") for row in route["pods"]},
                       "dg": _v(p["dg"], "implied_share_of_all_boxes"),
                       "oog": _v(p["oog"], "implied_share_of_all_boxes"),
                       "special_stowage": _v(p["special_stowage"], "implied_share_of_all_boxes")}
    audit.requested["weight_classes"] = {
        name: parameter.value for name, parameter in weight_classes.parameters.items()
    }
    audit.requested["dg_shipment_policy"] = SHIPMENT_POLICY_VERSION
    audit.requested["generated_gross_mass_limit_kg"] = MAX_GENERATED_GROSS_MASS_KG
    # ASSUMPTION: each synthetic box has the selected equipment example's
    # max_gross_kg as its individual CSC plate rating. This is scenario input,
    # not a universal ISO size/type limit or evidence about a real container.
    # CSC Annex I Reg. 1(2)(a), reproduced in data/raw/imdg-42-24/msc556-108.pdf
    # §1.1.2.3.1, PDF p. 21: "Maximum operating gross mass (kg and lb)".
    audit.requested["synthetic_csc_ratings"] = {
        "kind": "synthetic_assumed_individual_plate_ratings",
        "profile_id": p["profile_id"],
        "equipment": {e["id"]: {"iso_size_type": e["iso_size_type"],
                                  "max_gross_kg": deepcopy(e["max_gross_kg"])}
                      for e in p["equipment"]},
    }
    audit.realised, audit.conditioning, audit.unchecked, audit.rejections = {}, [], [], []
    audit.unresolved_provisions = {}
    audit.shipment_requirements = {}
    future = {call.locode for call in rotation.calls[call_index + 1:]}
    pods = [row for row in route["pods"] if row["port"] in future]
    if not pods:
        reason = "no supported POD occurrence strictly after current call"
        audit.rejections.append(reason)
        raise UnsupportedCargoError(reason)
    if len(pods) != len(route["pods"]) or onboard:
        audit.conditioning.append("POD shares renormalised over first subsequent occurrences: " + ",".join(row["port"] for row in pods))
    if teu is not None:
        audit.conditioning.append("TEU budget stops before first exceeding box; boundary stopping can alter equipment shares")
    audit.conditioning.append("DG packing-group variants sampled uniformly within the configured UN/class")
    audit.conditioning.append("APPROXIMATION: D60 loaded gross distribution conditioned on <=30,500 kg; sourced CSC ratings unchanged")
    audit.unchecked = ["booking demand requires full checker validation; no placement or hard-rule acceptance is asserted",
                       "CSC ratings are synthetic individual plate assumptions from profile equipment examples, not real certification or universal ISO maxima",
                       "E8 heat/top-stowage and E9 OOG physical feasibility need checker semantics",
                       "unknown DG shipment conditions, SP applicability and conditional subsidiary hazards remain fail-closed; see shipment_requirements",
                       "default G1 equipment uses synthetic closed packaged DG; tank mixes need an explicit tank-equipment profile and packing authorization",
                       "marine-pollutant, LQ and flashpoint are not supplied by curated DGL; no LQ exemption or flashpoint exemption is assumed"]
    if freeze_enabled:
        audit.requested.update(unsupported_demand=0, refused_by_class={}, refused_bookings=[],
                               shipment_scenarios={}, placement_requirements={})
        audit.conditioning.append("Port-refused bookings are counted as unsupported demand and redrawn whole; no UN, class, equipment or POD is relabelled")
        audit.conditioning.append("APPROXIMATION: direct-delivery trucks are available at their lift; no truck timing is modelled")
        audit.conditioning.append("D66 synthetic VGM rounded to nearest whole kg (ties to even), constrained by tare, individual CSC plate and D60 cap; exact-kg categories are strict swap-safety keys")
        audit.unchecked.append("POD includes transshipment handling on this leg; no onward transshipment itinerary or planned restows are supplied")
        audit.unchecked.append("Synthetic H1/SW19 entry declarations require bind_shipment_scenarios after final instance/vessel/rotation setup; no SW1/SW2 geometry or observed outcomes supplied")
        audit.unchecked.append("SW1/SW2 are pending checker placement assessments from actual geometry and scoped design evidence; absent or unsafe evidence remains fail-closed, never a shipment clearance flag")
        if "battery_recycling_share" in p["dg"]:
            audit.requested["dg_stress"] = {
                "synthetic": True, "separate_panel": True,
                "battery_recycling_share": _v(p["dg"], "battery_recycling_share"),
                "selected_pair": deepcopy(p["dg"].get("selected_pair")),
            }
            audit.conditioning.append("APPROXIMATION: D70 separate DG-stress panel; SP377 intact recycling minority has an explicit synthetic non-short voyage declaration, bound to the actual leg before checking")
    scenario_templates = load_param_file("cargo", "declared_scenarios")["templates"] if freeze_enabled else None
    booking_audit = BookingAudit(p, n_boxes=n_boxes, pods=pods) if freeze_enabled else None
    if booking_audit is not None:
        audit.requested["booking_audit"] = booking_audit.data
    boxes: list[Container] = []
    total_teu = 0.0
    equipment_counts: Counter[str] = Counter()
    owner = SYNTHETIC_OWNER_CODES[1 if onboard else 0]
    consecutive_refusals = 0
    while n_boxes is None or len(boxes) < n_boxes:
        e = _pick(rng, p["equipment"])
        length = {"2": 20, "4": 40, "L": 45}[e["iso_size_type"][0]]
        box_teu = length / 20
        if teu is not None and total_teu + box_teu > teu:
            break
        if id_offset + len(boxes) >= 1_000_000:
            raise ValueError("container ID serial namespace exhausted; use disjoint smaller lists")
        empty = rng.random() < _v(e, "empty_share")
        pod = _pick(rng, pods)
        oog = None
        dg = None
        special = []
        if not empty:
            if e["id"] in p["oog"]["eligible_equipment_ids"] and rng.random() < _v(p["oog"], "share_of_loaded_compatible_boxes"):
                profile = _pick(rng, p["oog"]["profiles"])
                oog = OutOfGauge(over_height_m=_v(profile, "over_height_m"),
                                 over_width_port_m=_v(profile, "over_width_each_side_m"),
                                 over_width_starboard_m=_v(profile, "over_width_each_side_m"),
                                 over_length_fore_m=_v(profile, "over_length_each_end_m"),
                                 over_length_aft_m=_v(profile, "over_length_each_end_m"))
            if e["id"] in p["dg"]["eligible_equipment_ids"] and rng.random() < _v(p["dg"], "share_of_eligible_loaded_boxes"):
                dg = _dg(rng, p["dg"], audit, iso_type=e["iso_size_type"])
            if oog is None and rng.random() < _v(p["special_stowage"], "share_of_loaded_boxes"):
                special = [_pick(rng, p["special_stowage"]["requests"])["id"]]
        tare = _v(e, "tare_kg")
        if empty:
            mass = _v(e["empty_weight_kg"], "value")
        else:
            weights = e["loaded_weight_kg"]
            low, mode, high = (_v(weights, key) for key in ("min", "mode", "max"))
            if weights["family"] != "triangular" or not tare <= low <= mode <= high <= _v(e, "max_gross_kg"):
                raise ValueError(f"invalid triangular weight/rating parameters: {e['id']}")
            # APPROXIMATION: parameter-prescribed payload/POD correlation and
            # clipping, followed by D60 tail truncation (not a capped atom).
            mass = _loaded_gross_mass(rng, low, mode, high, tare,
                                      _v(pod, "payload_weight_multiplier"))
        if not tare <= mass <= _v(e, "max_gross_kg"):
            raise ValueError(f"mass exceeds equipment rating or excludes tare: {e['id']}")
        if mass > MAX_GENERATED_GROSS_MASS_KG:
            raise ValueError(f"mass exceeds D60 securing range: {e['id']}")
        if freeze_enabled:
            # D66 declares synthetic VGM at whole-kg resolution. Bound rounding
            # to valid integer masses, never round below tare or above a plate.
            integer_low = math.ceil(tare)
            integer_high = math.floor(min(_v(e, "max_gross_kg"), MAX_GENERATED_GROSS_MASS_KG))
            if integer_low > integer_high:
                raise UnsupportedCargoError(f"no whole-kg gross mass within tare/plate/D60 bounds: {e['id']}")
            mass = float(min(integer_high, max(integer_low, round(mass))))
        if not tare <= mass <= _v(e, "max_gross_kg"):
            raise ValueError(f"mass exceeds equipment rating or excludes tare: {e['id']}")
        if mass > MAX_GENERATED_GROSS_MASS_KG:
            raise ValueError(f"mass exceeds D60 securing range: {e['id']}")
        if booking_audit is not None:
            candidate = dict(dg=dg, mass_kg=mass, pod=pod["port"], iso_type=e["iso_size_type"],
                             reefer=bool(e["reefer"] and not empty), oog=oog)
            booking_audit.record_draw(**candidate, is_redraw=consecutive_refusals > 0)
        if freeze_enabled and dg is not None:
            refused_calls = refused_handling_calls(rotation, call_index, pod["port"],
                                                   dg.imdg_class, onboard=onboard)
            if refused_calls:
                booking_audit.record_refusal(**candidate)
                # No alternative handling scenario exists for this booked leg.
                # Preserve its demand facts in the audit, then redraw a booking
                # instead of silently converting its DG/equipment/destination.
                audit.requested["unsupported_demand"] += 1
                losses = audit.requested["refused_by_class"]
                losses[dg.imdg_class] = losses.get(dg.imdg_class, 0) + 1
                audit.requested["refused_bookings"].append({
                    "un_number": dg.un_number, "primary_class": dg.imdg_class,
                    "iso_type": e["iso_size_type"], "equipment_id": e["id"],
                    "pol": rotation.calls[0 if onboard else call_index].locode,
                    "pod": pod["port"], "refused_call_indexes": refused_calls,
                })
                consecutive_refusals += 1
                if consecutive_refusals >= MAX_CONSECUTIVE_REFUSAL_REDRAWS:
                    raise UnsupportedCargoError("unsupported demand: port refusal redraw limit exhausted; see refused_by_class and refused_bookings (sampling failure, not proof of impossible demand)")
                continue
        consecutive_refusals = 0
        reefer = bool(e["reefer"] and not empty)
        weight_class = weight_classes.classify(mass)
        category = None
        if not (dg or reefer or special or oog):
            mass_key = f"mass_kg={mass.hex()}" if freeze_enabled else weight_class
            category = f"{pod['port']}:{e['iso_size_type']}:{mass_key}:{'empty' if empty else 'loaded'}"
        box = Container(id=make_container_id(owner, id_offset + len(boxes)), iso_type=e["iso_size_type"],
                        length_ft=length, height_m=_v(e, "height_m"), tare_kg=tare, gross_mass_kg=mass,
                        max_gross_mass_kg=_v(e, "max_gross_kg"),
                        weight_class=weight_class, pol=rotation.calls[0 if onboard else call_index].locode,
                        pod=pod["port"], empty=empty, reefer=reefer, dg=dg, special_stowage=special,
                        oog=oog, category=category,
                        direct_delivery=bool(current_port and current_port.requires_direct_delivery(dg.imdg_class if dg else None)))
        boxes.append(box)
        if dg is not None:
            entry = next(entry for entry in entries_for_un(int(dg.un_number))
                         if entry.packing_group == dg.packing_group)
            scenario_key = None
            if (freeze_enabled and "SW19" in entry.source_stowage_codes
                    and entry.un_number in {3480, 3481} and "battery_recycling_share" in p["dg"]
                    and rng.random() < _v(p["dg"], "battery_recycling_share")):
                scenario_key = "sw19_recycling"
            declarations = declare_entry_scenarios(entry, iso_type=e["iso_size_type"],
                                                   scenarios=scenario_templates,
                                                   scenario_key=scenario_key) if freeze_enabled else None
            if declarations:
                audit.requested["shipment_scenarios"][box.id] = {"primary": declarations}
            requirements = shipment_requirements(entry, declarations=declarations,
                                                 placement_derivable=freeze_enabled)
            if freeze_enabled:
                placement_codes = sorted(set(entry.source_stowage_codes) & {"SW1", "SW2"})
                if placement_codes:
                    audit.requested["placement_requirements"][box.id] = placement_codes
            if dg.packing_form is None:
                requirements.append("packing_form")
            if requirements:
                audit.shipment_requirements[box.id] = sorted(requirements)
            markers = [c for c in dg.stowage_codes if not c.startswith("SW")]
            if dg.stowage_category not in {"A", "B", "C", "D", "E"}:
                markers.append(f"class_1_stowage_category:{dg.stowage_category}")
            if markers:
                audit.unresolved_provisions[box.id] = sorted(set(markers))
        equipment_counts[e["id"]] += 1
        total_teu += box_teu
    audit.realised = {"n_boxes": len(boxes), "teu": total_teu, "equipment": dict(sorted(equipment_counts.items())),
                      "pods": dict(sorted(Counter(box.pod for box in boxes).items())),
                      "empty": sum(box.empty for box in boxes), "reefer": sum(box.reefer for box in boxes),
                      "dg": sum(box.dg is not None for box in boxes), "oog": sum(box.oog is not None for box in boxes),
                      "unresolved_provisions": len(audit.unresolved_provisions),
                      "special_stowage": sum(bool(box.special_stowage) for box in boxes)}
    if freeze_enabled:
        audit.realised["unsupported_demand"] = audit.requested["unsupported_demand"]
        audit.realised["refused_by_class"] = dict(sorted(audit.requested["refused_by_class"].items()))
        for box in boxes:
            booking_audit.record_emit(box)
        audit.realised["booking_audit"] = booking_audit.snapshot()
        audit.realised["shipment_scenarios"] = deepcopy(audit.requested["shipment_scenarios"])
        audit.realised["placement_requirements"] = deepcopy(audit.requested["placement_requirements"])
    labels = weight_classes.parameters["labels"].value
    audit.realised["dg_packing_forms"] = dict(sorted(Counter(
        box.dg.packing_form.value if box.dg.packing_form is not None else "unknown"
        for box in boxes if box.dg is not None
    ).items()))
    audit.realised["dg_packing_forms_by_un"] = {
        un: dict(sorted(Counter(
            box.dg.packing_form.value if box.dg.packing_form is not None else "unknown"
            for box in boxes if box.dg is not None and box.dg.un_number == un
        ).items()))
        for un in sorted({box.dg.un_number for box in boxes if box.dg is not None})
    }
    audit.realised["weight_classes_by_size"] = {
        str(length): {label: sum(box.length_ft == length and box.weight_class == label for box in boxes)
                      for label in labels}
        for length in sorted({box.length_ft for box in boxes})
    }
    return boxes
