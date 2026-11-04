"""Read-only loading of the six frozen ballast-review stows for attribution.

No generator, ballast search or input migration is run. Decision placements may
be removed in a copied ShipState while fixed D42 cargo and actual liquids stay.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "experiments/ballast/review_fix/inputs"
MANIFEST = ROOT / "experiments/ballast/review_fix/manifest.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cases():
    return [row["case"] for row in json.loads(MANIFEST.read_text())]


def load_case(case):
    from stowage.checker.full import departure_condition
    from stowage.io import load_instance, load_plan
    from stowage.moves import apply_plan

    row = next(row for row in json.loads(MANIFEST.read_text()) if row["case"] == case)
    folder = INPUTS / case
    assert digest(folder / "instance.json") == row["instance_sha256"]
    assert digest(folder / "input-plan.json") == row["plan_sha256"]
    instance = load_instance(folder / "instance.json")
    plan = load_plan(folder / "input-plan.json")
    replay = apply_plan(instance, plan)
    assert replay.legal, replay.violations
    ship = departure_condition(instance, plan)
    assert not instance.onboard.placements
    assert {p.container_id for p in ship.placements} == set(instance.load_list)
    assert not ship.covers_off
    # The frozen input has no proposed ballast replacement. Keep its actual
    # arrival-at-current-port liquids, exactly as the archived initial report.
    assert plan.ballast is None
    assert ship.tank_masses_kg == instance.onboard.tank_masses_kg
    return instance, plan, ship


def background_only(ship):
    return ship.model_copy(deep=True, update={"placements": []})


def source_identity():
    from stowage.versions import component_versions

    files = sorted({
        *(ROOT / "src/stowage/checker").glob("*.py"),
        *(ROOT / "src/stowage/model").glob("*.py"),
        *(ROOT / "src/stowage/vessel").glob("*.py"),
        *(ROOT / "src/stowage/moves").glob("*.py"),
        *(ROOT / "params/fleet").glob("*.toml"),
    })
    return dict(versions=component_versions(),
        files={str(path.relative_to(ROOT)): digest(path) for path in files},
        manifest_sha256=digest(MANIFEST))
