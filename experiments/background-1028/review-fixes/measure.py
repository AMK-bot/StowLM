"""D58 review matrix: six raw draws per cell, with DG selection and mass realism.

The only changes to the verified prior runner are read-only observers. No
declaration, RNG stream, screen, vessel, checker input or result is modified.
"""
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
ROOT = PARENT.parents[1]
sys.path.insert(0, str(HERE))
from dg_audit import inventory_histogram, install_cargo_observer

spec = importlib.util.spec_from_file_location("prior_medium_observer", PARENT / "followup-medium/measure.py")
followup = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(followup)
legacy_sources = followup.sources
legacy_utilisation = followup.utilisation
events = []


def sources():
    _, original = legacy_sources()
    manifest = deepcopy(original)
    for path in (Path(__file__), HERE / "dg_audit.py"):
        manifest["sources"][str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    manifest["measurement_format"] = "D58-review-D1-D2-D5-v1"
    key = sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    path = HERE / "sources" / f"{key}.json"
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    return key, manifest


def utilisation(instance):
    result = legacy_utilisation(instance)
    condition = result["condition"]
    masses = [cell["mass_kg"] for cell in condition["background_cells"]]
    stacks = {stack["stack_id"]: stack for stack in condition["background_stacks"]}
    deck = [cell["mass_kg"] for cell in condition["background_cells"] if stacks[cell["stack_id"]]["on_deck"]]
    result["background_box_mass_min_kg"] = min(masses) if masses else None
    result["deck_box_mass_distribution_kg"] = dict(n=len(deck), min=min(deck), max=max(deck),
        mean=statistics.mean(deck), population_std=statistics.pstdev(deck)) if deck else None
    return result


def main():
    from stowage.generate import instances, random_yard
    if "--out" not in sys.argv:
        sys.argv.extend(["--out", str(HERE / "draws.jsonl")])
    for option in ("--tiny-small-draws", "--medium-draws"):
        if option not in sys.argv:
            sys.argv.extend([option, "6"])
    out = Path(sys.argv[sys.argv.index("--out")+1])
    out.parent.mkdir(parents=True, exist_ok=True)
    sidecar = out.with_suffix(".cargo.jsonl")
    restore_cargo = install_cargo_observer(events)
    installed = []
    for module, name, generator in ((random_yard, "generate_random_instance", "random"),
                                    (instances, "generate_simulated_instance", "simulated")):
        original = getattr(module, name)
        def wrap(original, generator):
            @wraps(original)
            def observed(*args, **kwargs):
                events.clear()
                record = dict(generator=generator, vessel=kwargs.get("vessel_name", "feeder_500"),
                    rung=args[0] if args else kwargs.get("size", "tiny"), seed=kwargs["seed"],
                    source_start=sources()[0], started_utc=datetime.now(timezone.utc).isoformat())
                try:
                    instance = original(*args, **kwargs)
                    targets = set(instance.load_list)
                    record.update(accepted=True, emitted=inventory_histogram(
                        box for box in instance.containers if box.id in targets))
                    return instance
                except Exception as error:
                    record.update(accepted=False, emitted=None, error=f"{type(error).__name__}: {error}")
                    raise
                finally:
                    record.update(cargo_events=list(events), source_end=sources()[0],
                        ended_utc=datetime.now(timezone.utc).isoformat())
                    with sidecar.open("a") as stream:
                        stream.write(json.dumps(record, sort_keys=True)+"\n")
            return observed
        observed = wrap(original, generator)
        setattr(module, name, observed)
        installed.append((module, name, original, observed))
    followup.sources = sources
    followup.utilisation = utilisation
    try:
        followup.main()
    finally:
        restore_cargo()
        for module, name, original, observed in installed:
            if getattr(module, name) is observed:
                setattr(module, name, original)


if __name__ == "__main__":
    main()
