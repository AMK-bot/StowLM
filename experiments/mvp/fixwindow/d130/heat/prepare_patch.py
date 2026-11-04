"""Prepare a pinned patch only; never edit the production feasibility engine."""
from hashlib import sha256
import difflib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
SOURCE = ROOT / "src/stowage/feasibility/_engine.py"
old = SOURCE.read_text()
before = "            heat_ids = {h.id for h in self.instance.vessel.heat_sources}\n"
after = """            # Tank IDs and source IDs are distinct; match the scalar checker.
            heat_ids = {h.tank_id if h.tank_id is not None else h.id
                        for h in self.instance.vessel.heat_sources
                        if h.kind == "heated_tank"}
"""
assert old.count(before) == 1, "Live source drift: inspect before regenerating"
new = old.replace(before, after)
(HERE / "engine-before.py").write_text(old)
(HERE / "engine-candidate.py").write_text(new)
patch = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
    fromfile="a/src/stowage/feasibility/_engine.py", tofile="b/src/stowage/feasibility/_engine.py"))
(HERE / "feasibility-heat-tank-links.patch").write_text(patch)
(HERE / "source-pins.json").write_text(json.dumps({
    "source": str(SOURCE), "before_sha256": sha256(old.encode()).hexdigest(),
    "candidate_sha256": sha256(new.encode()).hexdigest(),
    "patch_sha256": sha256(patch.encode()).hexdigest(),
    "checker_sha256": sha256((ROOT / "src/stowage/checker/rules_special.py").read_bytes()).hexdigest(),
    "test_sha256": sha256((HERE / "test_heat_tank_links.py").read_bytes()).hexdigest(),
}, indent=2) + "\n")
