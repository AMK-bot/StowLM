"""Seeded synthetic feeder bookings, without assigning ship or yard positions.

The sampling recipe and public sources are in ``params/cargo/README.md`` and
``feeder_export.toml``. A triangular weight distribution describes a bounded
minimum, most likely mass, and maximum; payload (gross minus tare) is then
multiplied by POD and clipped. SOLAS VI/2 requires verified gross mass, but
supplies no universal numerical minimum or error tolerance: these synthetic
masses are treated as exact VGM and bounded by the selected equipment rating.

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

Weight classes group verified gross masses using the catalogue's shared
boundaries (provisional leader decision D25, pending expert Q2/Q4/Q11).
Interchangeability groups ordinary bookings by POD, equipment and weight class
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
from stowage.rules.catalogue import load_catalogue
from stowage.rules.imdg.dgl import entries_for_un
from stowage.generate.cargo_shipments import (
    SHIPMENT_POLICY_VERSION, declare_shipment, shipment_requirements,
)

from stowage.versions import CARGO_GENERATOR_VERSION
Params = Mapping[str, Any]


@dataclass
class CargoGenerationReport:
    """Optional per-call audit, filled in place; no global random/report state.

    ``requested`` contains original profile probabilities; ``realised`` contains
    observed counts. Conditioning and unresolved placement checks are explicit.
    A failed call records its reason before raising; nothing is silently redrawn.
    Every call first clears the previous report. Generation failures leave
    ``realised`` empty; a later vessel-support failure retains the generated
    inventory counts with explicit rejections. Current-call partial DG audit
    entries may remain on a generation failure, never a successful output claim.
    ``unresolved_provisions`` maps retained box ids to DGL guard identifiers or
    the audit-only ``class_1_stowage_category:<category>`` marker. The latter
    describes checker 2.4's unsupported explosives arrangements, not an IMDG
    provision code. Task 4.3 must count/screen these boxes, never assume clearance.
    ``shipment_requirements`` names unknown entry-specific facts for downstream
    assessment. It is an audit aid, not a replacement for full checking.
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


def _params(params: Params | str | None) -> Params:
    return load_param_file("cargo", params or "feeder_export") if isinstance(params, (str, type(None))) else params


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
    return rotation, profile


def _dg(rng: Random, profile: Params, report: CargoGenerationReport, *, iso_type: str) -> DangerousGoods:
    cls = _pick(rng, profile["classes"])
    un = int(_pick(rng, cls["un_numbers"])["un_number"])
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
                           generator_version=CARGO_GENERATOR_VERSION),
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
    report.profile = p["profile_id"]
    _validate_probabilities(p)
    # Read the shared D25 policy once per call. No local copy of its numeric
    # boundaries or cached box labels may override the catalogue's actual VGM rule.
    weight_classes = load_catalogue().weight_classes
    rng = Random(seed)
    rotation, route = _route(rotation, p, rng)
    if isinstance(call_index, bool) or not isinstance(call_index, int) or not 0 <= call_index < len(rotation.calls):
        raise ValueError("call_index must identify a rotation call")
    audit = report if report is not None else CargoGenerationReport()
    audit.seed, audit.profile, audit.rotation, audit.call_index = seed, p["profile_id"], route["id"], call_index
    audit.requested = {"n_boxes": n_boxes, "teu": teu, "equipment_shares": {e["id"]: _v(e, "share") for e in p["equipment"]},
                       "pod_shares": {row["port"]: _v(row, "share") for row in route["pods"]},
                       "dg": _v(p["dg"], "implied_share_of_all_boxes"),
                       "oog": _v(p["oog"], "implied_share_of_all_boxes"),
                       "special_stowage": _v(p["special_stowage"], "implied_share_of_all_boxes")}
    audit.requested["weight_classes"] = {
        name: parameter.value for name, parameter in weight_classes.parameters.items()
    }
    audit.requested["dg_shipment_policy"] = SHIPMENT_POLICY_VERSION
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
    audit.unchecked = ["booking demand requires full checker validation; no placement or hard-rule acceptance is asserted",
                       "CSC ratings are synthetic individual plate assumptions from profile equipment examples, not real certification or universal ISO maxima",
                       "E8 heat/top-stowage and E9 OOG physical feasibility need checker semantics",
                       "unknown DG shipment conditions, SP applicability and conditional subsidiary hazards remain fail-closed; see shipment_requirements",
                       "default G1 equipment uses synthetic closed packaged DG; tank mixes need an explicit tank-equipment profile and packing authorization",
                       "marine-pollutant, LQ and flashpoint are not supplied by curated DGL; no LQ exemption or flashpoint exemption is assumed"]
    boxes: list[Container] = []
    total_teu = 0.0
    equipment_counts: Counter[str] = Counter()
    owner = SYNTHETIC_OWNER_CODES[1 if onboard else 0]
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
            # APPROXIMATION: parameter-prescribed payload/POD correlation and clipping.
            mass = min(high, _v(e, "max_gross_kg"), max(low, tare + (rng.triangular(low, high, mode) - tare) * _v(pod, "payload_weight_multiplier")))
        if not tare <= mass <= _v(e, "max_gross_kg"):
            raise ValueError(f"mass exceeds equipment rating or excludes tare: {e['id']}")
        reefer = bool(e["reefer"] and not empty)
        weight_class = weight_classes.classify(mass)
        category = None
        if not (dg or reefer or special or oog):
            category = f"{pod['port']}:{e['iso_size_type']}:{weight_class}:{'empty' if empty else 'loaded'}"
        box = Container(id=make_container_id(owner, id_offset + len(boxes)), iso_type=e["iso_size_type"],
                        length_ft=length, height_m=_v(e, "height_m"), tare_kg=tare, gross_mass_kg=mass,
                        max_gross_mass_kg=_v(e, "max_gross_kg"),
                        weight_class=weight_class, pol=rotation.calls[0 if onboard else call_index].locode,
                        pod=pod["port"], empty=empty, reefer=reefer, dg=dg, special_stowage=special,
                        oog=oog, category=category)
        boxes.append(box)
        if dg is not None:
            entry = next(entry for entry in entries_for_un(int(dg.un_number))
                         if entry.packing_group == dg.packing_group)
            requirements = shipment_requirements(entry)
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
