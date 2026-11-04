"""9.2 contract test, CPU only and without decoding any showcase input: a pilot-2 val15 run folder (D128 recipe, row c)
is copied into a scratch ``runs/stowlm-showcase-test`` layout; then verify92's fresh-check gate, mvp-ui's loader
(app.inputs.load_inputs -> model_run verification) and its FINAL_RULE / swap registration (app.stage_stowlm) are
exercised. The scratch RUNS root keeps the real experiments/mvp/runs/stowlm-showcase* glob untouched."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:  # the demo app package lives at the repo root
    sys.path.insert(0, str(ROOT))
SRC = ROOT / "experiments/mvp/sft/pilot2/val15/c"
SHIPPED, DEAD = "sim.small.40100013", "random.small.40100031.0.5"
pytestmark = pytest.mark.skipif(not (SRC / "manifest.json").exists(), reason="pilot-2 val15 row c missing")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    dst = runs / "stowlm-showcase-test"
    dst.mkdir()
    for name in ("manifest.json", "config.json", "summary.json"):
        shutil.copy(SRC / name, dst / name)
    for iid in (SHIPPED, DEAD):
        shutil.copytree(SRC / "instances" / iid, dst / "instances" / iid)
    return runs, dst


def test_verify_gate_rechecks_shipped_plans(run):
    _, dst = run
    proc = subprocess.run(["uv", "run", "python", str(ROOT / "experiments/mvp/sft/eval92/verify92.py"), str(dst)],
                          cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rows = json.loads((dst / "verify.json").read_text())["rows"]
    assert rows[SHIPPED]["fresh_check_passed"] is True and rows[SHIPPED]["weighted_lifts"] == 41
    assert rows[DEAD]["status"] == "dead_end" and "fresh_check_passed" not in rows[DEAD]


def test_loader_verifies_the_run_and_final_rule_swaps_it_in(run, monkeypatch):
    import app.stage_stowlm as stage
    from app.inputs import load_inputs

    runs, dst = run
    loaded = load_inputs(run_folder=dst, entry=f"instances/{SHIPPED}/result.json")
    model = loaded.metadata["model_run"]
    assert model["verified"] and model["planner_kind"] == "stowlm_adapter" and model.get("adapter_clean") is True
    monkeypatch.setattr(stage, "RUNS", runs)
    # registration: only shipped entries; the scratch folder is found by the stowlm-showcase* glob
    assert stage.showcase_run_folders(runs) == [dst]
    assert stage.shipped_entries(dst) == [f"instances/{SHIPPED}/result.json"]
    assert stage.shipped_entries(dst, include_unshipped=True) == sorted(
        [f"instances/{SHIPPED}/result.json", f"instances/{DEAD}/result.json"])
    # FINAL_RULE: a val map is a PREVIEW; the same folder on a showcase call id is the final run
    final, why = stage.final_showcase(loaded.metadata, SHIPPED)
    assert not final and "showcase" in why and stage.preview_label(loaded.metadata, SHIPPED)
    monkeypatch.setattr(stage, "SHOWCASE_INSTANCES", (*stage.SHOWCASE_INSTANCES, SHIPPED))
    assert stage.final_showcase(loaded.metadata, SHIPPED) == (True, "final StowLM showcase run")
    assert stage.preview_label(loaded.metadata, SHIPPED) is None


def test_dead_end_is_recorded_honestly(run):
    from app.inputs import load_inputs

    _, dst = run
    loaded = load_inputs(run_folder=dst, entry=f"instances/{DEAD}/result.json")
    model = loaded.metadata["model_run"]
    assert model["verified"] and loaded.plan is None  # dead-end card, never a shipped plan
    assert model["outcome"]["label"] == "dead end"
    assert json.loads((dst / "instances" / DEAD / "outcome.json").read_text())["outcome"] == "dead end"
