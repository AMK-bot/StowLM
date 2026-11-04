"""Activated random-yard cargo integration, using the cargo owner's policies.

No sampling rule is copied here. The cargo generator owns route enrichment,
handling-port refusals, direct-delivery declarations, exact VGM and D70 profiles.
Per-call reports survive failed draws and downstream screening; raw cargo
emissions are expressly distinct from admitted random-yard instances.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from random import Random
from typing import Any

from stowage.generate import cargo
from stowage.model.port import PortCall, Rotation


def prepare_random_cargo(port, *, cargo_profile: str = "feeder_export"):
    """Select a separate profile and reuse cargo's current enriched route.

    APPROXIMATION: the chosen synthetic archetype replaces the configured
    loading call; future calls retain the profile's route and POD distribution.
    D70 is an explicit separate profile, never an implicit main-profile change.
    """
    if cargo_profile not in {"feeder_export", "dg_stress"}:
        raise ValueError("cargo_profile must be feeder_export or dg_stress")
    params = (cargo.dg_stress_profile() if cargo_profile == "dg_stress"
              else deepcopy(cargo._params("feeder_export")))
    route = deepcopy(params["rotations"][0])
    calls = list(route["port_calls"])
    calls[0] = port.locode
    route["port_calls"] = calls
    params["rotations"] = [route]
    rotation = Rotation(service=route["id"], calls=[
        PortCall(locode=locode, index=index, archetype=port.archetype if index == 0 else None)
        for index, locode in enumerate(calls)
    ])
    rotation, _ = cargo._route(rotation, params, Random(0))
    return params, rotation


def _stage_totals(reports, name):
    total: dict[str, Any] = {"kind": "actual", "n_boxes": 0, "non_dg": 0,
                             "dg_by_class": {}, "dg_by_un": {}, "uns_by_class": {},
                             "mass_bands": {}, "by_pod": {}, "reefer": 0, "oog": 0}
    mass_sum = mass_count = mass_gt = 0
    for row in reports:
        # On a failed call, requested.booking_audit retains the partial actual
        # draws; realised is empty. Never invent successful emitted inventory.
        source = row["report"]["requested"].get("booking_audit", {})
        stage = source.get(name, {})
        total["n_boxes"] += stage.get("n_boxes", 0)
        total["non_dg"] += stage.get("non_dg", 0)
        for field in ("dg_by_class", "dg_by_un", "mass_bands"):
            for key, count in stage.get(field, {}).items():
                total[field][key] = total[field].get(key, 0) + count
        for key, numbers in stage.get("uns_by_class", {}).items():
            total["uns_by_class"][key] = sorted(set(total["uns_by_class"].get(key, [])) | set(numbers))
        for pod, counts in stage.get("by_pod", {}).items():
            values = total["by_pod"].setdefault(pod, {})
            for key, count in counts.items():
                values[key] = values.get(key, 0) + count
            total["reefer"] += counts.get("reefer", 0)
            total["oog"] += counts.get("oog", 0)
        summary = stage.get("mass_summary", {})
        count, mean = summary.get("count", 0), summary.get("mean_kg")
        if count and mean is not None:
            mass_count += count
            mass_sum += count * mean
        mass_gt += summary.get("count_gt_25000kg", 0)
    total["mass_summary"] = {"count": mass_count,
        "mean_kg": mass_sum / mass_count if mass_count else None,
        "p95_kg": None, "count_gt_25000kg": mass_gt,
        "p95_note": "Per-call nearest-rank p95 is retained in calls; aggregate p95 cannot be recovered from histograms."}
    return total


def _stage_per_attempt(stage, attempts):
    """Normalize counts only; inventories, mass means and quantiles stay raw.

    These are box counts per candidate attempt, not probabilities or fractions
    of admitted cargo. In particular refused boxes per attempt may exceed one.
    """
    values = {field: stage[field] / attempts
              for field in ("n_boxes", "non_dg", "reefer", "oog")}
    for field in ("dg_by_class", "dg_by_un", "mass_bands"):
        values[field] = {key: count / attempts for key, count in stage[field].items()}
    values["by_pod"] = {pod: {key: count / attempts for key, count in counts.items()}
                        for pod, counts in stage["by_pod"].items()}
    return values


class RandomCargoDraws:
    """Per-instance raw-demand audit plus retained shipment declarations.

    Stage A currently contains exports and explicitly non-DG yard blockers.
    The onboard hook delegates the public cargo API. An import draw requires
    its actual provided rotation/handling call; no generated export is reversed.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.manifests: dict[str, Any] = {}
        self._previous_manifests: dict[str, Any] = {}

    def draw(self, rotation, call_index, n_boxes, *, seed, params, id_offset=0,
             leg="exports", vessel=None, kind="initial"):
        if kind not in {"initial", "shipment_redraw"}:
            raise ValueError("draw kind must be initial or shipment_redraw")
        if leg not in {"exports", "imports", "onboard"}:
            raise ValueError("draw leg must be exports, imports or onboard")
        if leg == "onboard" and vessel is None:
            raise ValueError("onboard draws require their actual vessel")
        report = cargo.CargoGenerationReport()
        failed = True
        try:
            options = dict(seed=seed, n_boxes=n_boxes, params=params,
                           id_offset=id_offset, report=report)
            if leg == "onboard":
                boxes = cargo.generate_onboard_cargo(vessel, rotation, call_index, **options)
            else:
                boxes = cargo.generate_load_list(rotation, call_index, **options)
            self._previous_manifests = {box.id: deepcopy(self.manifests.get(box.id)) for box in boxes}
            for box in boxes:
                self.manifests.pop(box.id, None)
            self.manifests.update(deepcopy(report.realised.get("shipment_scenarios", {})))
            failed = False
            return boxes
        finally:
            self.calls.append({"leg": leg, "kind": kind, "failed": failed, "report": asdict(report)})

    def replace_id(self, generated_id, retained_id):
        """Follow a whole-shipment redraw without retaining the old facts."""
        declaration = self.manifests.pop(generated_id, None)
        previous = self._previous_manifests.pop(generated_id, None)
        if generated_id != retained_id and previous is not None:
            self.manifests[generated_id] = previous
        self.manifests.pop(retained_id, None)
        if declaration is not None:
            self.manifests[retained_id] = declaration

    def bind(self, instance):
        """Bind retained declarations to actual scope before any cargo screen."""
        present = {box.id for box in instance.containers if box.dg is not None}
        report = cargo.CargoGenerationReport()
        report.realised = {"n_boxes": len(instance.containers), "shipment_scenarios": {
            cid: deepcopy(facts) for cid, facts in self.manifests.items() if cid in present}}
        return cargo.bind_shipment_scenarios(instance, report)

    def snapshot(self, *, attempts: int | None = None):
        """Retain raw reports plus rates using the caller's generator attempts.

        One generator attempt can make several single-shipment cargo calls, so
        the denominator cannot be inferred from calls or emitted inventories.
        Callers without the counter receive explicitly unavailable rates.
        """
        if attempts is not None and (type(attempts) is not int or attempts < 0):
            raise ValueError("attempts must be a nonnegative integer or None")
        if attempts == 0 and self.calls:
            raise ValueError("zero attempts cannot describe recorded cargo calls")
        initial = _stage_totals([row for row in self.calls if row["kind"] == "initial"], "requested")
        redraw = _stage_totals([row for row in self.calls if row["kind"] == "shipment_redraw"], "drawn")
        stages = {name: _stage_totals(self.calls, name)
                  for name in ("requested", "drawn", "redrawn", "refused", "emitted")}
        available = attempts is not None and attempts > 0
        normalized = {
            "status": ("available" if available else "no_attempts" if attempts == 0
                       else "missing_generator_attempt_count"),
            "denominator": attempts,
            "denominator_definition": "Caller-supplied full generator candidate attempts, including rejected attempts and attempts with multiple shipment-redraw calls.",
            "unit": "boxes per generator candidate attempt; not an admission rate or cargo-mix fraction",
            "initial_requested": _stage_per_attempt(initial, attempts) if available else None,
            "shipment_redraw_drawn": _stage_per_attempt(redraw, attempts) if available else None,
            "stages": ({name: _stage_per_attempt(stage, attempts) for name, stage in stages.items()}
                       if available else None),
        }
        return {"schema_version": 2,
            "profiles": sorted({row["report"]["profile"] for row in self.calls}),
            "emission_definition": "emitted counts raw cargo-generator returns, before random-yard screening; not admitted instances",
            "calls": deepcopy(self.calls),
            "cargo_call_counts": {"total": len(self.calls),
                "failed": sum(row["failed"] for row in self.calls),
                "initial": sum(row["kind"] == "initial" for row in self.calls),
                "shipment_redraw": sum(row["kind"] == "shipment_redraw" for row in self.calls)},
            "per_attempt": normalized,
            "initial_requested": initial,
            "shipment_redraw_drawn": redraw,
            "requested_definition": "stages.requested includes first candidates from every cargo call; initial_requested excludes downstream single-shipment replacement calls",
            "stages": stages}


def admitted_cargo_audit(instance, params):
    """Measure only the final instance's export load list using cargo's audit.

    This is the admitted denominator, separate from all raw cargo returns in
    RandomCargoDraws. Later-cohort yard blockers and rejected candidates never
    enter it. The caller attaches this stage only after the instance is accepted.
    """
    boxes = {box.id: box for box in instance.containers}
    selected = [boxes[container_id] for container_id in instance.load_list]
    _, route = cargo._route(instance.rotation, params, Random(0))
    future = {call.locode for call in instance.rotation.calls[instance.call_index + 1:]}
    pods = [row for row in route['pods'] if row['port'] in future]
    audit = cargo.BookingAudit(params, n_boxes=len(selected), pods=pods)
    for box in selected:
        audit.record_emit(box)
    return audit.snapshot()['emitted']
