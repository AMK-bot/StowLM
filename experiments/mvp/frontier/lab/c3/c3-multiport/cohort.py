"""Fixed TRAIN multiport cohort; scoped generator recipe changes, no solver selection.

Run from repository root with PYTHONPATH=src and one numerical thread.
The original random-yard generator owns cargo, yard, vessel and screening.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from time import perf_counter

from stowage.generate import random_yard as generator
from stowage.model import Instance
from stowage.rules.catalogue import rule_freeze_v1_enabled

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[5]
FIRST_SEED = 263310000
DESTINATIONS = ("SGSIN", "MYPKG", "VNSGN", "HKHKG", "THBKK")


def specifications():
    """Freeze every cell and seed before opening any planner or result."""
    rows = []
    for size in ("tiny", "small"):
        for ports in (3, 4, 5):
            for repeat in range(6):
                rows.append(dict(size=size, ports=ports, repeat=repeat,
                                 seed=FIRST_SEED + len(rows)))
    for repeat in range(3):
        rows.append(dict(size="medium", ports=4, repeat=repeat,
                         seed=FIRST_SEED + len(rows)))
    return rows


def generate_one(spec):
    if rule_freeze_v1_enabled():
        raise RuntimeError("This recipe is for the current legacy generator; activation changed")
    size, ports, seed = spec["size"], spec["ports"], spec["seed"]
    destinations = DESTINATIONS[:ports]
    saved = {name: getattr(generator, name) for name in
             ("SIZE_LADDER", "load_param_file", "capacity_load_size", "generate_load_list",
              "completion_failures")}
    coverage = dict(draws=0, rejected=0)

    def parameters(*args, **kwargs):
        params = deepcopy(saved["load_param_file"](*args, **kwargs))
        if args[:2] != ("cargo", "feeder_export"):
            return params
        route = deepcopy(params["rotations"][0])
        route.update(id=f"train-multiport-{ports}",
                     port_calls=["THLCH", *destinations],
                     assumption="Synthetic TRAIN open rotation; uniform distinct discharge ports")
        route["pods"] = [dict(port=port,
            share=dict(value=1 / ports, unit="fraction_of_boxes_on_rotation",
                       assumption="Synthetic uniform TRAIN multiport coverage"),
            payload_weight_multiplier=dict(value=1.0, unit="dimensionless",
                       assumption="Synthetic port-independent payload mixture"))
            for port in destinations]
        params["rotations"] = [route]
        if size == "medium":
            params["equipment"] = [e for e in params["equipment"] if e["id"] == "dry_20"]
        return params

    def capacity(vessel, rung, params, **kwargs):
        # Supplying this explicit hook deliberately avoids D36's 90%-fill
        # whole-ship regional booking policy. The original geometric budgets
        # and all subsequent screens stay in place.
        fraction = {"tiny": 1.0, "small": .5, "medium": .6}[size]
        rung = replace(rung, fill_fraction=fraction,
                       boxes=6 if size == "tiny" else 10000)
        return saved["capacity_load_size"](vessel, rung, params)

    def cargo(*args, **kwargs):
        # Condition demand on each advertised POD actually being represented.
        # Hash-addressed streams are fixed independently of any solver result.
        original_seed = kwargs["seed"]
        if args[2] < ports:
            return saved["generate_load_list"](*args, **kwargs)
        for attempt in range(1000):
            options = dict(kwargs)
            if attempt:
                value = f"multiport-coverage-v1:{original_seed}:{attempt}".encode()
                options["seed"] = int.from_bytes(hashlib.sha256(value).digest(), "big")
            boxes = saved["generate_load_list"](*args, **options)
            coverage["draws"] += 1
            if set(destinations) <= {box.pod for box in boxes}:
                return boxes
            coverage["rejected"] += 1
        raise RuntimeError("1000 cargo draws without requested POD coverage")

    def completion(instance):
        failures = saved["completion_failures"](instance)
        if len(instance.load_list) >= ports:
            selected = set(instance.load_list)
            present = {box.pod for box in instance.containers if box.id in selected}
            if not set(destinations) <= present:
                failures += ("train.POD_coverage",)
        return failures

    report = generator.RandomGenerationReport()
    start = perf_counter()
    try:
        generator.load_param_file = parameters
        generator.capacity_load_size = capacity
        generator.generate_load_list = cargo
        generator.completion_failures = completion
        instance = generator.generate_random_instance(size, seed=seed,
            blocking_ratio=.2, vessel_name="feeder_500", port_name="laem_chabang_like",
            report=report, max_attempts=1000, c2_screen=False)
    finally:
        for name, value in saved.items():
            setattr(generator, name, value)
    payload = instance.model_dump(mode="json")
    payload["family"].update(split="TRAIN", cohort="c3-multiport-v1",
        discharge_ports=str(ports), coverage_condition="every_declared_POD_present",
        equipment_profile="dry20" if size == "medium" else "original",
        requested_geometric_fill=repr({"tiny": 1.0, "small": .5, "medium": .6}[size]))
    payload["provenance"]["parents"].append(
        "c3-multiport/cohort.py: synthetic uniform POD recipe; all ports represented; TRAIN")
    instance = Instance.model_validate(payload)
    boxes = {box.id: box for box in instance.containers}
    selected = [boxes[cid] for cid in instance.load_list]
    assert set(destinations) == {box.pod for box in selected}
    assert len(instance.load_list) >= ports
    return instance, dict(generation=asdict(report), coverage=coverage,
                         generation_s=perf_counter() - start)


def main():
    specs = specifications()
    ROOT.joinpath("cohort").mkdir(exist_ok=True)
    # This specification is written before the first generation; fixed seeds
    # are never replaced after a generation or planner failure.
    ROOT.joinpath("cohort-spec.json").write_text(json.dumps(dict(
        split="TRAIN", first_seed=FIRST_SEED, maps=specs), indent=2) + "\n")
    rows = []
    for spec in specs:
        filename = f"{spec['size']}.p{spec['ports']}.s{spec['seed']}.json"
        path = ROOT / "cohort" / filename
        audit_path = path.with_suffix(".generation.json")
        if path.exists() and audit_path.exists():
            instance = Instance.model_validate_json(path.read_text())
            audit = json.loads(audit_path.read_text())
        else:
            instance, audit = generate_one(spec)
            path.write_text(instance.model_dump_json(indent=2) + "\n")
            audit_path.write_text(json.dumps(audit, indent=2) + "\n")
        boxes = {box.id: box for box in instance.containers}
        loads = [boxes[cid] for cid in instance.load_list]
        row = dict(spec, path=str(path.relative_to(REPO)), instance_id=instance.id,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            n_boxes=len(loads), capacity_teu=len(instance.vessel.cells),
            realised_teu=sum(box.length_ft / 20 for box in loads),
            fill=sum(box.length_ft / 20 for box in loads) / len(instance.vessel.cells),
            by_pod=dict(sorted(Counter(box.pod for box in loads).items())),
            generation_s=audit["generation_s"])
        assert len(row["by_pod"]) == spec["ports"]
        rows.append(row)
        ROOT.joinpath("cohort-manifest.json").write_text(json.dumps(dict(
            split="TRAIN", selection="Fixed grid and contiguous fresh seeds before results",
            maps=rows), indent=2) + "\n")
        ROOT.joinpath("cohort.txt").write_text("".join(row["path"] + "\n" for row in rows))
        print(f"{filename}: {len(loads)} boxes, {row['fill']:.1%} fill, {row['by_pod']}", flush=True)


if __name__ == "__main__":
    main()
