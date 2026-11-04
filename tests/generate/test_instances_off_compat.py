"""Pinned producer/input evidence for the OFF compatibility regression.

The historical digest binds inputs as well as code. Run current producer code
against the immutable dependency/input snapshot rather than replacing its pin
when external parameter metadata changes. All children use the same uv Python.
"""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


HISTORICAL_SOURCE_SHA = "b1407e18f4a7fb0e3fd47ebe0d7ec071f32d55f56bd2dfd072a1b5e068e4598a"
HISTORICAL_OUTPUT_SHA = "61967b1627ee6de05a4ea291373de0016bb0ac68f22d0efb7852a0f632c5dfea"


def snapshot_root(repo_root):
    return repo_root / "experiments/yard-sim/d72-production/source-snapshot"


def historical_producer(repo_root, monkeypatch):
    source = snapshot_root(repo_root) / "src/stowage/generate/instances.py"
    assert sha256(source.read_bytes()).hexdigest() == HISTORICAL_SOURCE_SHA
    name = "stowage.generate._pinned_legacy_instances"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


_FROZEN_PROBE = r"""
from pathlib import Path
from hashlib import sha256
import importlib.util
import json, sys
from stowage.generate import instances as installed
from stowage.vessel.fleet_slices import load_fleet_preset
from stowage.rules.catalogue import rule_freeze_v1
source, snapshot = map(Path, sys.argv[1:])
assert Path(installed.__file__).is_relative_to(snapshot)
spec = importlib.util.spec_from_file_location('stowage.generate._current_off_probe', source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
geometry = load_fleet_preset('feeder_500', 'full', with_hull=False)
module._full_vessel = lambda name: geometry
screen = module.screen_load_list
module.screen_load_list = lambda *a, **k: [r for r in screen(*a, **k) if r != 'checker.missing_vessel_data']
module.accept_background_candidate = lambda instance, audit: instance
with rule_freeze_v1(enabled=False):
    rows = [module.generate_simulated_instance(size, seed=seed, n_boxes=3,
                without_dg=True).model_dump_json()
            for size, seed in [('tiny', 3), ('small', 7), ('medium', 13)]]
print(json.dumps({'sha256': sha256('\n'.join(rows).encode()).hexdigest()}))
"""


def current_code_frozen_input_digest(repo_root):
    snapshot = snapshot_root(repo_root)
    environment = {**os.environ, "PYTHONPATH": str(snapshot / "src"),
        "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
    completed = subprocess.run([sys.executable, "-c", _FROZEN_PROBE,
        str(repo_root / "src/stowage/generate/instances.py"), str(snapshot)],
        cwd=repo_root, env=environment, text=True, capture_output=True, check=True,
        timeout=60)
    return json.loads(completed.stdout)["sha256"]
