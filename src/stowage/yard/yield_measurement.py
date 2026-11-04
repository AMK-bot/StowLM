"""No-planner D72 measurements for the pre-freeze yield matrix.

``measure_snapshot(instance)`` consumes an already generated instance: no cargo
redraw, background finishing, planner or checker is invoked. ``measure_calls``
replays a caller's fixed ShipCalls twice, with rule freeze enabled for BOTH arms;
D72-off means only congestion_mix_share=0, not legacy model semantics. Its
snapshot metrics use the shared bounds.components.yard_rehandle_bound.

The CLI accepts saved Instance JSONs (``--snapshot``), a fixed ShipCall JSON
bundle (``--calls``), or an explicitly synthetic small-demand smoke probe on
unmodified production load_port layouts. A call bundle has keys ``calls``
(dataclass ShipCall fields, with model_dump JSON for each Instance), optional
``initial_yard``, and optional ``until_s``. It never invokes the production
instance generator. The full production-demand yield matrix remains a leader
job; smoke results are admission evidence, not qualified production yield.

APPROXIMATION: smoke demand comprises two overlapping fixture-vessel calls,
24 dry20/light boxes, eight exports/four local imports each; actual yard layout,
equipment limits and A8 crossing remain untouched. This is not terminal data.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from stowage.bounds import components
from stowage.conventions import make_container_id
from stowage.model import Instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import Placement, ShipState
from stowage.model.yard import YardStack, YardState
from stowage.ports import effective_yard_tiers, list_ports, load_port, load_simulator_params
from stowage.ports.profiles import SimulatorParameters
from stowage.rules.catalogue import rule_freeze_v1, rule_freeze_v1_enabled
from stowage.yard.simulator import ShipCall, SimulationResult, simulate_port

QUALIFICATION = "synthetic admission probe; not qualified production yield"


def _production_layout(instance: Instance) -> bool:
    """Compare actual layout, including equipment limits; never resize it here."""
    original = load_port(instance.port.archetype)
    return (instance.port.yard == original.yard
            and instance.port.equipment == original.equipment
            and instance.port.yard_max_tiers == original.yard_max_tiers)


def measure_snapshot(instance: Instance) -> dict[str, Any]:
    """Measure foreign burial, presence and heights without certifying feasibility.

    A foreign box means ANY non-load box for this call, including its own
    imports. Count blocked exports, not foreign blockers: a single foreign box
    can bury several exports, whereas the shared lower bound counts it once.
    The main share divides by all declared exports (absent/direct-delivery
    included); a second share divides by yard-present exports. Both denominators
    and direct-delivery counts are explicit, avoiding selection on admission.
    Mean and histogram use nonempty physical stacks (not TEU positions).
    """
    required = set(instance.load_list)
    present = {cid for stack in instance.yard.stacks for cid in stack.boxes}
    blocked: set[str] = set()
    for stack in instance.yard.stacks:
        foreign_above = False
        for cid in reversed(stack.boxes):
            if cid not in required:
                foreign_above = True
            elif foreign_above:
                blocked.add(cid)
    heights = [len(stack.boxes) for stack in instance.yard.stacks]
    direct = {box.id for box in instance.containers if box.direct_delivery} & required
    yard_present = len(required & present)
    return {
        "instance_id": instance.id,
        "port_archetype": instance.port.archetype,
        "yard_id": instance.yard.yard_id,
        "production_yard_layout": _production_layout(instance),
        "yard_geometry": [{"block_id": block.id, "bays": block.n_bays,
                           "rows": block.n_rows,
                           "effective_limit": effective_yard_tiers(instance.port, block)}
                          for block in instance.port.yard.blocks],
        "requested_exports": len(required),
        "direct_delivery_exports": len(direct),
        "exports_present": yard_present,
        "exports_absent": len(required - present),
        "foreign_boxes_present": len(present - required),
        "foreign_blocked_exports": len(blocked),
        "foreign_blocked_export_share": len(blocked) / len(required) if required else 0.0,
        "foreign_blocked_present_export_share": len(blocked) / yard_present if yard_present else 0.0,
        "yard_rehandle_bound": components.yard_rehandle_bound(instance),
        "stack_count_nonempty": len(heights),
        "stack_height_mean_nonempty": sum(heights) / len(heights) if heights else 0.0,
        "stack_height_max": max(heights, default=0),
        "stack_height_histogram": {str(height): count for height, count in sorted(Counter(heights).items())},
        "yard_boxes": len(present),
    }


def _admission_counts(calls: list[ShipCall], result: SimulationResult) -> dict[str, int]:
    exports = {cid for call in calls for cid in call.instance.load_list}
    imports = {cid for call in calls for cid in call.instance.discharge_list}
    gate_accepted = {record.move.container_id for record in result.moves
                     if record.move.kind == "gate_in"}
    import_accepted = {record.move.container_id for record in result.moves
                       if record.move.kind == "discharge"}
    return {
        "requested_exports": len(exports),
        "requested_imports": len(imports),
        "gate_admitted_exports": len(exports & gate_accepted),
        "discharged_imports": len(imports & import_accepted),
        "queued_gate_events": result.queued_gate_events,
        "external_queue": len(result.external_queue),
        "missed_cutoffs": len(result.missed_cutoffs),
        "unserved_loads": len(result.unserved_loads),
        "unserved_discharges": len(result.unserved_discharges),
        "retained_import_exits": len(result.retained_import_exits),
        "calls_started": len(result.snapshots),
        "calls_not_started": len(calls) - len(result.snapshots),
    }


def _json_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def measure_calls(calls: list[ShipCall], parameters: SimulatorParameters, *, seed: int,
                  noise: float, congestion_mix_share: float = .25,
                  initial_yard: YardState | None = None, until_s: float | None = None,
                  require_production_layout: bool = True) -> dict[str, Any]:
    """Paired D72-only comparison of fixed demand and clustering/retrieval noise.

    The public caller supplies production calls or cached call bundles; this
    function performs no whole-instance generation or qualification. ``until_s``
    is forwarded unchanged to both simulator runs. Metrics/counts describe that
    horizon; future calls are reported separately, never labelled unserved.
    """
    with rule_freeze_v1():
        if require_production_layout and any(not _production_layout(call.instance) for call in calls):
            raise ValueError("yield measurement requires unmodified production yard layouts")
        input_json = json.dumps({"calls": [asdict(call) for call in calls],
                                 "initial_yard": initial_yard},
                                default=_json_model, sort_keys=True, separators=(",", ":"))
        report: dict[str, Any] = {
            "qualification": QUALIFICATION,
            "call_input_sha256": hashlib.sha256(input_json.encode()).hexdigest(),
            "seed": seed, "noise": noise, "until_s": until_s,
            "control_definition": "D72 off = mix share zero; rule freeze/A8/D25 stay enabled in both arms",
            "share_definition": "foreign_blocked_export_share divides by all declared call exports; present-only share also reported",
            "arms": {},
        }
        for name, share in (("d72_off", 0.0), ("d72_on", congestion_mix_share)):
            result = simulate_port(calls, parameters, seed=seed, noise=noise,
                                   congestion_mix_share=share, initial_yard=initial_yard, until_s=until_s)
            call_by_id = {call.id: call for call in calls}
            registry = [box for call in calls for box in call.instance.containers]
            snapshots = []
            for snapshot in result.snapshots:
                template = call_by_id[snapshot.call_id].instance
                instance = template.model_copy(update={"yard": snapshot.yard, "containers": registry})
                snapshots.append({"call_id": snapshot.call_id, "time_s": snapshot.time_s,
                                  **measure_snapshot(instance)})
            report["arms"][name] = {
                "rule_freeze_v1": rule_freeze_v1_enabled(),
                "noise": noise,
                "requested_congestion_mix_share": share,
                "simulator_version": result.simulator_version,
                "congestion_mixing": result.provenance.congestion_mixing,
                "admission": _admission_counts(calls, result),
                "snapshots": snapshots,
            }
        return report


def production_yard_smoke_calls(port_name: str = "laem_chabang_like") -> list[ShipCall]:
    """Small synthetic demand, with actual production archetype geometry intact.

    Rule activation is the caller's responsibility, as for load_port itself.
    This is a smoke fixture, never a booking/generation acceptance screen.
    The valid Instance template stores singleton export positions, as required
    by its schema; ShipCall simulation generates yard placement afresh and does
    not use this template inventory as initial_yard.
    """
    from stowage.model.fixtures import build_instance

    port = load_port(port_name)
    calls = []
    for number in range(2):
        instance = build_instance()
        instance.port = port.model_copy(deep=True)
        next_port = "SGSIN" if port.locode != "SGSIN" else "THLCH"
        instance.rotation = Rotation(service="synthetic-d72-admission", calls=[
            PortCall(index=0, locode=port.locode, archetype=port_name),
            PortCall(index=1, locode=next_port),
            PortCall(index=2, locode=port.locode, archetype=port_name)])
        prototype = instance.containers[2]
        boxes = [prototype.model_copy(update={
            "id": make_container_id("QZXU", 980000 + number * 100 + i),
            "pol": port.locode if i < 8 else next_port,
            "pod": next_port if i < 8 else port.locode,
            "category": None, "special_stowage": [], "oog": None,
            "weight_class": "light", "direct_delivery": False,
        }) for i in range(12)]
        instance.id = f"synthetic-d72-{port_name}-{number}"
        instance.containers = boxes
        instance.load_list = [box.id for box in boxes[:8]]
        instance.discharge_list = [box.id for box in boxes[8:]]
        instance.stage = "B"
        instance.onboard = ShipState(vessel_id=instance.vessel.id, placements=[
            Placement(slot=slot, container_id=box.id) for slot, box in zip(
                ("0010182", "0010282", "0030182", "0030282"), boxes[8:])])
        positions = ((block.id, bay, row) for block in port.yard.blocks
                     if 20 in block.allowed_lengths_ft
                     for bay in range(1, block.n_bays + 1)
                     for row in range(1, block.n_rows + 1))
        instance.yard = YardState(yard_id=port.yard.id, stacks=[
            YardStack(key=f"{block}-{bay:03d}-{row:02d}", length_ft=20, boxes=[box.id])
            for box, (block, bay, row) in zip(boxes[:8], positions)])
        instance = Instance.model_validate(instance.model_dump())
        start = 1000 + 1000 * number
        calls.append(ShipCall(instance.id, instance, start, start - 100,
                              {cid: 100 * number + i for i, cid in enumerate(instance.load_list)}))
    return calls


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--snapshot", action="append", type=Path,
                        help="saved Instance JSON; repeat to consume yield-matrix snapshots without simulation")
    inputs.add_argument("--calls", type=Path,
                        help="fixed production-yard ShipCall JSON bundle; no cargo generation")
    parser.add_argument("--ports", nargs="+", default=["laem_chabang_like"],
                        help="smoke layout archetypes, or 'all' for every operational profile")
    parser.add_argument("--seeds", nargs="+", type=int, default=[3, 7])
    parser.add_argument("--noise", type=float, default=.5)
    parser.add_argument("--mix-share", type=float, default=.25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"schema_version": 1, "qualification": QUALIFICATION, "cases": []}
    with rule_freeze_v1():
        if args.snapshot:
            report["input_mode"] = "existing snapshots; no simulation or generation"
            report["snapshots"] = [measure_snapshot(Instance.model_validate_json(path.read_text()))
                                    for path in args.snapshot]
        elif args.calls:
            report["input_mode"] = "fixed supplied calls; no generation"
            bundle = json.loads(args.calls.read_text())
            calls = [ShipCall(**{**value, "instance": Instance.model_validate(value["instance"]),
                                 "load_order": tuple(value.get("load_order", ()))})
                     for value in bundle["calls"]]
            initial_yard = YardState.model_validate(bundle["initial_yard"]) if bundle.get("initial_yard") else None
            for seed in args.seeds:
                report["cases"].append(measure_calls(calls, load_simulator_params(calls[0].instance.port.archetype),
                    seed=seed, noise=args.noise, congestion_mix_share=args.mix_share,
                    initial_yard=initial_yard, until_s=bundle.get("until_s")))
        else:
            report["input_mode"] = "synthetic small demand on unmodified production yard layouts"
            ports = args.ports
            if ports == ["all"]:
                ports = []
                for port_name in list_ports():
                    if load_port(port_name).yard.blocks:
                        ports.append(port_name)
            for port_name in ports:
                calls = production_yard_smoke_calls(port_name)
                for seed in args.seeds:
                    report["cases"].append(measure_calls(calls, load_simulator_params(port_name),
                        seed=seed, noise=args.noise, congestion_mix_share=args.mix_share,
                        until_s=min(call.arrival_s for call in calls)))
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end="")
    return report


if __name__ == "__main__":
    main()
