"""Frozen C6 mixed-equipment multiport TRAIN demand; native generator owns physics.

Adapted from c3/c3-multiport/cohort.py, with an explicit geometric demand budget
and no equipment conditioning. No planner or experimental runtime is imported.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
              "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS"):
    os.environ[_name] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from stowage.generate import random_yard as generator
from stowage.generate.random_export import expected_cargo_mix
from stowage.model import Instance
from stowage.rules.catalogue import rule_freeze_v1_enabled

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[5]
FIRST_SEED = 263360000
DESTINATIONS = ("SGSIN", "MYPKG", "VNSGN", "HKHKG", "THBKK", "USLAX")
COHORT = "c6-multiport-mixed-v1"
FILL_TOLERANCE = .02
DEVELOPMENT_CELLS = frozenset((
    ("feeder_500", 3, 60, 0), ("feeder_500", 4, 70, 0),
    ("feeder_500", 5, 80, 0), ("feeder_800", 6, 60, 0),
    ("feeder_800", 3, 70, 0), ("feeder_800", 4, 80, 0)))


def write_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def specifications():
    cells = [(ship, ports, fill, 0) for ship in ("feeder_500", "feeder_800")
             for ports in (3, 4, 5, 6) for fill in (60, 70, 80)]
    cells += [(ship, ports, 80, 1) for ship in ("feeder_500", "feeder_800")
              for ports in (3, 4, 5, 6)]
    rows = []
    for index, (ship, ports, fill_pct, repeat) in enumerate(cells):
        rows.append(dict(size="medium", vessel=ship, ports=ports, fill_pct=fill_pct,
            requested_fill=fill_pct / 100, repeat=repeat, seed=FIRST_SEED + index,
            development=(ship, ports, fill_pct, repeat) in DEVELOPMENT_CELLS))
    return rows


def source_hashes():
    paths = [Path(__file__).resolve(), *[REPO / name for name in (
        "src/stowage/generate/random_yard.py", "src/stowage/generate/cargo.py",
        "src/stowage/generate/random_export.py", "src/stowage/generate/background_acceptance.py",
        "src/stowage/vessel/fleet_slices.py", "params/cargo/feeder_export.toml")]]
    return {str(path.relative_to(REPO)): digest(path) for path in paths}


def generate_one(spec):
    if rule_freeze_v1_enabled():
        raise RuntimeError("C6 recipe assumes current legacy activation; no activation override is allowed")
    ports, fraction = spec["ports"], spec["requested_fill"]
    destinations = DESTINATIONS[:ports]
    saved = {name: getattr(generator, name) for name in (
        "load_param_file", "capacity_load_size", "generate_load_list", "completion_failures")}
    coverage = dict(cargo_draws=0, pod_rejections=0, fill_rejections=0,
                    singleton_redraws=0)
    budget = {}
    native_equipment = []

    def parameters(*args, **kwargs):
        params = deepcopy(saved["load_param_file"](*args, **kwargs))
        if args[:2] != ("cargo", "feeder_export"):
            return params
        nonlocal native_equipment
        native_equipment = deepcopy(params["equipment"])
        route = deepcopy(params["rotations"][0])
        route.update(id=f"train-c6-multiport-{ports}", port_calls=["THLCH", *destinations],
                     assumption="Synthetic open TRAIN rotation, uniform distinct discharge ports")
        route["pods"] = [dict(port=port,
            share=dict(value=1 / ports, unit="fraction_of_boxes_on_rotation",
                       assumption="Uniform synthetic multiport demand"),
            payload_weight_multiplier=dict(value=1., unit="dimensionless",
                       assumption="Synthetic port-independent payload masses")) for port in destinations]
        params["rotations"] = [route]
        # Keep every equipment record, share, empty rate and DG/reefer/OOG rule.
        assert params["equipment"] == native_equipment
        return params

    def capacity(vessel, rung, params, **kwargs):
        # Native regional booking targets 90% and may be mass limited. This
        # experiment instead fixes geometric TEU demand, without weakening the
        # subsequent native mass, geometry, C1 or background acceptance gates.
        mean_teu, _ = expected_cargo_mix(params)
        teu_budget = math.floor(len(vessel.cells) * fraction)
        length_slots = generator._length_slots(vessel)
        limits = {length: math.floor(n * fraction) for length, n in length_slots.items()}
        count = math.floor(teu_budget / mean_teu)
        budget.update(capacity_teu=len(vessel.cells), teu_budget=teu_budget,
            minimum_teu=(fraction - FILL_TOLERANCE) * len(vessel.cells),
            expected_teu_per_box=mean_teu, requested_boxes=count,
            length_slot_budgets=limits)
        return count, teu_budget, limits

    def cargo(*args, **kwargs):
        if args[2] < ports:
            coverage["singleton_redraws"] += 1
            return saved["generate_load_list"](*args, **kwargs)
        original_seed = kwargs["seed"]
        for attempt in range(1000):
            options = dict(kwargs)
            if attempt:
                value = f"{COHORT}:demand:{original_seed}:{attempt}".encode()
                options["seed"] = int.from_bytes(hashlib.sha256(value).digest(), "big")
            boxes = saved["generate_load_list"](*args, **options)
            coverage["cargo_draws"] += 1
            present = {box.pod for box in boxes}
            teu = sum(box.length_ft / 20 for box in boxes)
            pod_ok = set(destinations) <= present
            fill_ok = budget["minimum_teu"] - 1e-9 <= teu <= budget["teu_budget"] + 1e-9
            coverage["pod_rejections"] += int(not pod_ok)
            coverage["fill_rejections"] += int(not fill_ok)
            if pod_ok and fill_ok:
                return boxes
        raise RuntimeError("1000 source cargo draws without frozen POD/fill demand conditions")

    def completion(instance):
        failures = saved["completion_failures"](instance)
        if len(instance.load_list) >= ports:
            selected = set(instance.load_list)
            loads = [box for box in instance.containers if box.id in selected]
            if not set(destinations) <= {box.pod for box in loads}:
                failures += ("train.POD_coverage",)
            teu = sum(box.length_ft / 20 for box in loads)
            if not budget["minimum_teu"] - 1e-9 <= teu <= budget["teu_budget"] + 1e-9:
                failures += ("train.requested_fill",)
        return failures

    start = perf_counter()
    report = generator.RandomGenerationReport()
    try:
        generator.load_param_file = parameters
        generator.capacity_load_size = capacity
        generator.generate_load_list = cargo
        generator.completion_failures = completion
        instance = generator.generate_random_instance("medium", seed=spec["seed"],
            blocking_ratio=.2, vessel_name=spec["vessel"], port_name="laem_chabang_like",
            report=report, max_attempts=1000, c2_screen=False)
    except Exception as error:
        write_json(HERE / "cohort" / f"failed.s{spec['seed']}.generation.json",
            dict(spec=spec, error=f"{type(error).__name__}: {error}",
                 generation=asdict(report), demand=budget, coverage=coverage,
                 generation_s=perf_counter() - start))
        raise
    finally:
        for name, value in saved.items():
            setattr(generator, name, value)
    payload = instance.model_dump(mode="json")
    payload["family"].update(split="TRAIN", cohort=COHORT, discharge_ports=str(ports),
        requested_geometric_fill=repr(fraction), fill_tolerance=repr(FILL_TOLERANCE),
        equipment_profile="original_feeder_export_full_mix", development=str(spec["development"]).lower())
    payload["provenance"]["parents"].append(
        "c6/c3-multiport/cohort.py: full-mix synthetic multiport TRAIN demand; POD/fill conditioned")
    instance = Instance.model_validate(payload)
    audit = dict(spec=spec, generation=asdict(report), demand=budget, coverage=coverage,
                 equipment_ids=[item["id"] for item in native_equipment],
                 generation_s=perf_counter() - start)
    return instance, audit


def inventory(instance):
    boxes = {box.id: box for box in instance.containers}
    loads = [boxes[cid] for cid in instance.load_list]
    teu = sum(box.length_ft / 20 for box in loads)
    return dict(n_boxes=len(loads), capacity_teu=len(instance.vessel.cells), realised_teu=teu,
        fill=teu / len(instance.vessel.cells), by_pod=dict(sorted(Counter(b.pod for b in loads).items())),
        cargo=dict(dg=sum(b.dg is not None for b in loads), reefer=sum(b.reefer for b in loads),
            high_cube=sum(b.height_m > 2.7 for b in loads), oog=sum(b.oog is not None for b in loads),
            empty=sum(b.empty for b in loads), special_stowage=sum(bool(b.special_stowage) for b in loads),
            by_length_ft=dict(sorted(Counter(b.length_ft for b in loads).items())),
            by_iso_type=dict(sorted(Counter(b.iso_type for b in loads).items()))))


def main():
    HERE.joinpath("cohort").mkdir(exist_ok=True)
    specs = specifications()
    spec = dict(split="TRAIN", cohort=COHORT, first_seed=FIRST_SEED, maps=specs,
        development_seeds=[row["seed"] for row in specs if row["development"]],
        fill_condition="requested_fill - .02 <= actual TEU / decision cells <= requested_fill",
        selection="Fixed full grid and second 80% replicate before any planner result")
    spec_path = HERE / "cohort-spec.json"
    if spec_path.exists() and read_json(spec_path) != spec:
        raise RuntimeError("Frozen specification differs; never overwrite or replace its seeds")
    write_json(spec_path, spec)
    initial_hashes = source_hashes()
    rows = []
    for item in specs:
        filename = f"medium.{item['vessel']}.p{item['ports']}.f{item['fill_pct']}.s{item['seed']}.json"
        path = HERE / "cohort" / filename
        audit_path = path.with_suffix(".generation.json")
        if path.exists() and audit_path.exists():
            instance = Instance.model_validate_json(path.read_text())
            audit = read_json(audit_path)
            assert audit["spec"] == item
        else:
            instance, audit = generate_one(item)
            path.write_text(instance.model_dump_json(indent=2) + "\n")
            write_json(audit_path, audit)
        metrics = inventory(instance)
        assert len(metrics["by_pod"]) == item["ports"]
        assert item["requested_fill"] - FILL_TOLERANCE - 1e-9 <= metrics["fill"] <= item["requested_fill"] + 1e-9
        assert instance.seed == item["seed"] and not instance.provenance.tainted
        row = dict(item, path=str(path.relative_to(REPO)), instance_id=instance.id,
            sha256=digest(path), generation_path=str(audit_path.relative_to(REPO)),
            generation_sha256=digest(audit_path), generation_s=audit["generation_s"], **metrics)
        rows.append(row)
        write_json(HERE / "cohort-manifest.json", dict(split="TRAIN", cohort=COHORT,
            source_sha256=initial_hashes, maps=rows, complete=len(rows) == 32,
            verification=dict(instance_roundtrip=True, seed_grid=True, POD_coverage=True,
                              requested_fill_band=True, no_provenance_taint=True)))
        (HERE / "cohort.txt").write_text("".join(row["path"] + "\n" for row in rows))
        (HERE / "development.txt").write_text("".join(row["path"] + "\n" for row in rows if row["development"]))
        print(f"{filename}: {metrics['n_boxes']} boxes, {metrics['fill']:.2%} fill; cargo={metrics['cargo']}", flush=True)
    assert initial_hashes == source_hashes(), "Generator or wrapper changed during generation"
    assert len(rows) == 32 and len({row["seed"] for row in rows}) == 32
    assert len([row for row in rows if row["development"]]) == 6
    for row in rows:
        assert digest(REPO / row["path"]) == row["sha256"]
        assert digest(REPO / row["generation_path"]) == row["generation_sha256"]
    print(json.dumps(dict(verified_maps=32, development_maps=6,
        generation_s=sum(row["generation_s"] for row in rows)), indent=2), flush=True)


def read_json(path):
    return json.loads(path.read_text())


if __name__ == "__main__":
    main()
