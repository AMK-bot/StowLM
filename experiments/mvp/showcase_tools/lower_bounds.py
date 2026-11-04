"""Measured counting lower bounds for immutable showcases; no solver search.

A lower bound counts unavoidable work and can be below achievable moves; it
is not a plan. Source: stowage.bounds and its component validity arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"


def main():
    from stowage.bounds import lower_bound
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    assert not rule_freeze_v1_enabled(), "MVP requires the rule-freeze switch OFF"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=ROOT / "experiments/mvp/corpus/showcase")
    parser.add_argument("--output", type=Path, default=ROOT / "experiments/mvp/runs/showcase-solver/lower-bounds.json")
    args = parser.parse_args()
    manifest = json.loads((args.corpus / "showcase-manifest.json").read_text())
    rows = []
    for filename, expected in sorted(manifest["hashes"].items()):
        if not filename.endswith(".json") or filename.endswith((".audit.json", ".selection.json")):
            continue
        path = args.corpus / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected, f"Immutable showcase hash changed: {path}"
        instance = load_instance(path)
        started = time.monotonic()
        bound = lower_bound(instance)
        rows.append(dict(showcase=path.stem, instance_id=instance.id, input_sha256=digest,
                         targets=len(instance.load_list), wall_time_s=time.monotonic()-started,
                         bound=bound.model_dump(mode="json")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(rule_freeze_v1_enabled=False, rows=rows), indent=2)+"\n")
    for row in rows:
        print(f"{row['showcase']}: bound={row['bound']['weighted']} "
              f"rehandles={row['bound']['relocations']} restows={row['bound']['restow_lifts']} "
              f"hatch={row['bound']['hatch_cover_lifts']} ({row['wall_time_s']:.4f}s)")


if __name__ == "__main__":
    main()
