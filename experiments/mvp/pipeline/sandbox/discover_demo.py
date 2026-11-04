"""Find deterministic demo drags without searching for or changing a plan.

Default discovery runs only the static/rough screen. ``--record`` explicitly
runs the full checker for shortlisted cases and stores verdicts/explanations.
Default discovery reports missing rule families without changing cargo facts.
Explicit synthetic-source helpers annotate generated declarations and challenge
limits, and require a fresh full baseline gate before recording.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter

from .benchmark import HERE, admission, candidates, read_fixture


def _category(rule):
    if "segregation" in rule:
        return "segregation"
    if "stack" in rule and ("weight" in rule or "mass" in rule):
        return "stack_weight"
    if rule.startswith("iscode."):
        return "stability"
    return None


def discover(source, output, *, count=160):
    from stowage.checker.full import _ballast
    from stowage.conventions import cells_of_slot
    from stowage.model import Instance, MovePlan
    from stowage.moves import apply_plan
    from .physics import fast_check
    from .moves import fast_screen

    fixture = read_fixture(source)
    instance = Instance.model_validate(fixture["instance"])
    plan = MovePlan.model_validate(fixture["plan"])
    load = admission()
    replay = apply_plan(instance, plan)
    if not replay.legal or replay.executed != len(plan.moves):
        raise ValueError("source plan does not completely replay")
    base, _ = _ballast(instance, plan, replay.final_state.ship)
    placements = {p.container_id: p for p in base.placements}
    cell_owner = {cell: p.container_id for p in base.placements for cell in cells_of_slot(p.slot)}
    found, checked = {}, 0
    started = perf_counter()
    for box, target in candidates(instance, plan, count):
        if checked % 20 == 0:
            load = admission()
        origin = placements[box].slot
        owners = {cell_owner[cell] for cell in cells_of_slot(target) if cell in cell_owner and cell_owner[cell] != box}
        if len(owners) > 1:
            continue
        slots = {box: target}
        if owners:
            slots[next(iter(owners))] = origin
        changed = [p.model_copy(update={"slot": slots[p.container_id]}) if p.container_id in slots else p
                   for p in base.placements]
        ship = base.model_copy(update={"placements": changed})
        broken, gauges = fast_check(instance, ship)
        kinds = {_category(row["rule_id"]) for row in broken}
        if not broken and "legal" not in found:
            screen = fast_screen(instance, plan, box, target)
            if screen.legal:
                found["legal"] = {"box_id": box, "target_slot": target,
                                  "fast": screen.model_dump(mode="json")}
        for kind in ("stability", "segregation", "stack_weight"):
            if kind in kinds and kind not in found:
                screen = fast_screen(instance, plan, box, target)
                found[kind] = {"box_id": box, "target_slot": target,
                               "fast": screen.model_dump(mode="json")}
        checked += 1
        if checked % 20 == 0:
            print(json.dumps({"screened": checked, "found": sorted(found), "wall_s": perf_counter()-started}), flush=True)
        if len(found) == 4 or checked >= count:
            break
    data = {"synthetic": True, "mode": "recorded", "label": "Recorded fast-screen candidate discovery; no full verdict",
            "created_utc": datetime.now(timezone.utc).isoformat(), "source_fixture": str(Path(source)),
            "source_result": fixture["source_result"], "n_boxes": len(instance.load_list),
            "screened": checked, "wall_s": perf_counter()-started, "admission": load,
            "cases": found, "missing": sorted({"legal", "stability", "segregation", "stack_weight"}-set(found)),
            "note": "Same unmodified source plan and cargo facts for every drag; no plan search; approximate stability screens are not checker facts."}
    Path(output).write_text(json.dumps(data, indent=2, allow_nan=False)+"\n")
    return data


def record(source, discovery, output):
    import hashlib
    from experiments.mvp.pipeline.contract.schema import Mode
    from stowage.checker import check
    from stowage.model import Instance, MovePlan
    from .moves import try_move
    from .explain import explain_verdict
    fixture = read_fixture(source)
    instance, plan = Instance.model_validate(fixture["instance"]), MovePlan.model_validate(fixture["plan"])
    data = read_fixture(discovery)
    source_sha256 = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if data.get("source_sha256", source_sha256) != source_sha256:
        raise ValueError("candidate discovery does not bind to this source fixture")
    baseline_admission = admission()
    baseline = check(instance, plan)
    if not baseline.passed:
        raise ValueError("derived demo baseline failed fresh full checker: " +
                         str([v.rule_id for v in baseline.violations]))
    cases = []
    for kind, candidate in data["cases"].items():
        load = admission()
        verdict = try_move(instance, plan, candidate["box_id"], candidate["target_slot"])
        families = {_category(row["rule_id"]) for row in verdict.broken}
        confirmed = verdict.legal if kind == "legal" else kind in families
        if kind == "stability":
            confirmed = confirmed and any(_category(row["rule_id"]) == kind and
                row.get("measured") is not None and row.get("limit") is not None for row in verdict.broken)
        cases.append({"kind": kind, "confirmed": confirmed, "box_id": candidate["box_id"],
                      "target_slot": candidate["target_slot"], "admission": load,
                      "verdict": verdict.model_dump(mode="json"),
                      "explanation": explain_verdict(verdict).model_copy(update={"mode": Mode.recorded}).model_dump(mode="json")})
        Path(output).write_text(json.dumps({"synthetic": True, "mode": "recorded",
            "label": "Recorded development demo", "source_fixture": str(Path(source)),
            "source_sha256": source_sha256,
            "baseline_passed": True, "baseline_report": baseline.model_dump(mode="json"),
            "baseline_admission": baseline_admission,
            "cases": cases, "complete": len(cases) == 4 and all(c["confirmed"] for c in cases),
            "missing": data["missing"]}, indent=2, allow_nan=False)+"\n")
        print(json.dumps({"kind": kind, "confirmed": confirmed, "wall_s": verdict.wall_s}), flush=True)
    return cases


def synthetic_segregation_source(source, output):
    """Make one explicit synthetic cargo declaration using the curated DGL.

    A fresh full baseline gate is still required. Neither the existing plan nor
    a stored passing flag is promoted into evidence for this derived instance.
    """
    import hashlib
    from stowage.rules.imdg.dgl import lookup_dgl
    from stowage.model import Instance
    fixture = read_fixture(source)
    boxes = fixture["instance"]["containers"]
    box = next(row for row in boxes if row["id"] == "QZXU0000077")
    before = dict(box["dg"])
    entry = lookup_dgl(1689, "I")
    # Same physical synthetic box/mass, a newly generated declaration.
    box["dg"].update(un_number="1689", imdg_class="6.1", packing_group="I",
                     stowage_category="B", subsidiary_risks=[],
                     stowage_codes=list(entry.stowage_codes),
                     segregation_codes=list(entry.segregation_codes),
                     shipment_provenance={"created_by": "sandbox.synthetic_demo",
                         "parents": ["imdg-42-24:UN1689-I"]})
    fixture["instance"]["id"] += ".sandbox-synthetic-dg"
    fixture["plan"]["instance_id"] = fixture["instance"]["id"]
    fixture["plan"]["id"] += ".sandbox-synthetic-dg"
    fixture["synthetic"] = True
    fixture["label"] = "Synthetic demo cargo variant; baseline full checker gate required"
    fixture["derived_from"] = str(Path(source))
    fixture["derived_from_sha256"] = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    fixture["modifications"] = [{"field": "instance.containers[QZXU0000077].dg",
        "before": before, "after": dict(box["dg"]),
        "source": "src/stowage/rules/imdg/data/dgl.json, UN1689-I; synthetic shipment declaration"}]
    fixture["baseline_passed"] = None
    Instance.model_validate(fixture["instance"])
    Path(output).write_text(json.dumps(fixture, indent=2, allow_nan=False)+"\n")
    return fixture


def finalize_synthetic_design(*args, **kwargs):
    """Historical limit-fitting generator retired after logic review S1/S5."""
    raise ValueError("Fitted trim and stack demo limits are retired. Use demo_refresh on the checked hero with unchanged limits.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=HERE/"fixtures/envelope190.json")
    parser.add_argument("--output", type=Path, default=HERE/"fixtures/demo_candidates190.json")
    parser.add_argument("--count", type=int, default=160)
    parser.add_argument("--record", type=Path, help="Full-check discovered cases into this fixture")
    parser.add_argument("--derive-segregation", type=Path, help="Write an explicit synthetic DG source; no checker call")
    args = parser.parse_args()
    if args.derive_segregation:
        synthetic_segregation_source(args.source, args.derive_segregation)
    elif args.record:
        record(args.source, args.output, args.record)
    else:
        data = discover(args.source, args.output, count=args.count)
        print(json.dumps({"found": list(data["cases"]), "missing": data["missing"]}), flush=True)


if __name__ == "__main__":
    main()
