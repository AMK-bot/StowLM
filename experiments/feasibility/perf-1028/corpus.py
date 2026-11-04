"""Pin existing synthetic inputs for the 28 October mask equivalence audit.

Inputs are copied byte-for-byte as JSON, never regenerated. Verification uses
the production load_instance parser. Originals and copies have SHA-256 pins.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict:
    paths = [(f"measure-{rung}", f"experiments/feasibility/fix-t4-measure/{rung}-current.json", "random_yard")
             for rung in ("tiny", "small", "medium", "full")]
    for ship in ("500", "1000", "1700"):
        for rung in ("tiny", "small"):
            paths.append((f"sim-{ship}-{rung}",
                          f"experiments/instances-sim/fix-wave/d48-d49/feeder_{ship}-{rung}-seed-1.json",
                          "simulated_yard"))
    for seed in (100000000, 100000001, 100000002, 100000005, 100000009):
        paths.append((f"random-tiny-{seed}",
                      "experiments/linear-model/fix-t3-linear/post-promotion-c2/"
                      f"tiny-{seed}-completion-00-diagnostic-instance.json", "random_yard"))
    paths.append(("random-small-100001008", "experiments/linear-model/fix-t3-linear/post-promotion-c2/"
                  "small-100001008-completion-00-diagnostic-instance.json", "random_yard"))
    for suffix in ("100100015-2a1913beea", "100100075-7c468d575e", "100100240-5719de3306",
                   "100100036-14a29a0f3c", "100100051-63b32eb7c3", "100100018-5df70044df"):
        paths.append((f"random-small-{suffix}", f"experiments/ballast/review_fix/inputs/small-{suffix}/instance.json",
                      "random_yard"))
    paths.extend([
        ("gold-random-tiny", "experiments/gold-plans/tiny/fix-smoke-retry/inputs/random_yard/"
         "tiny/seed-281474976710659.json", "random_yard"),
        ("gold-simulated-tiny", "experiments/gold-plans/tiny/fix-smoke-simulated-retry/inputs/simulated_yard/"
         "tiny/seed-281474976710656.json", "simulated_yard"),
    ])
    rows = []
    for name, relative, generator in paths:
        path = ROOT / relative
        raw = path.read_bytes()
        data = json.loads(raw)
        archive = HERE / "corpus-instances" / (name + ".json")
        archive.parent.mkdir(exist_ok=True)
        archive.write_bytes(raw)
        assert archive.read_bytes() == raw
        previous_gzip = archive.with_suffix(".json.gz")
        if previous_gzip.exists():
            previous_gzip.unlink()  # Only the exact copy created by this script.
        family = data.get("family", {})
        rows.append(dict(id=name, path=str(archive.relative_to(ROOT)), sha256=sha(archive),
                         original_path=relative, uncompressed_sha256=hashlib.sha256(raw).hexdigest(), generator=generator,
                         rung=family["size"], vessel=family["vessel"],
                         load_boxes=len(data["load_list"]), measurement=name.startswith("measure-")))
    return dict(schema=1, seeds=[1704, 1705, 1706, 1707, 1708], instances=rows,
                note="Five independent masked walks per C2 mode; each runs until complete or dead end.")


if __name__ == "__main__":
    destination = HERE / "corpus-manifest.json"
    destination.write_text(json.dumps(build_manifest(), indent=2) + "\n")
    print(destination)
