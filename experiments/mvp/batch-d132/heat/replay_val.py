"""Replay only approved VAL committed prefixes in one isolated runtime arm."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import resource
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
MANIFEST = HERE / "val-prefixes.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("PYTHONHASHSEED") == "0"
    tree = args.tree.resolve()
    sys.path.insert(0, str(tree / "src"))
    import numpy as np
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.feasibility import _engine
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from llm.securing.mask_filter import SecuringEngine
    from llm.lookahead.mask_filter import LookaheadEngine
    assert Path(_engine.__file__).resolve().is_relative_to(tree / "src")
    assert Path(sys.modules["llm.securing.mask_filter"].__file__).resolve().is_relative_to(tree / "src")
    assert not rule_freeze_v1_enabled()
    spec = importlib.util.spec_from_file_location("d132_source_guard", HERE.parent / "masks/guard.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    before = helper.source_epoch(tree)
    records = json.loads(MANIFEST.read_text())["prefixes"]
    space = get_action_space()
    results = []
    started, cpu_start = time.perf_counter(), time.process_time()
    for record in records:
        if os.environ.get("D132_MASK_STOP") and Path(os.environ["D132_MASK_STOP"]).exists():
            raise SystemExit(75)
        result = {"id": record["id"], "arm": record["arm"], "input_sha256": record["input_sha256"],
                  "structural_heat_rejection_at_prefix": record["structural_heat_rejection_at_prefix"]}
        begin = time.perf_counter()
        try:
            path = (ROOT / record["map"]).resolve()
            assert path.is_relative_to((ROOT / "experiments/mvp/corpus/val").resolve())
            assert sha256(path.read_bytes()).hexdigest() == record["input_sha256"]
            instance = load_instance(path)
            native = make_feasibility_engine(instance, space,
                config=FeasibilityConfig(extra={"global_screen": 0}))
            securing = SecuringEngine(native)
            assert securing.securing_metrics()["securing_active"]
            engine = LookaheadEngine(securing) if record["lookahead"] else securing
            for index, encoded in enumerate(record["actions"]):
                action = space.decode(encoded)
                if not engine.is_legal(action):
                    raise ValueError(f"Historical committed action {index} no longer legal: {encoded}")
                engine.push(action)
            assert [space.encode(a) for a in engine.actions()] == record["actions"]
            legal = sorted((space.encode(a) for a in engine.legal_actions()),
                           key=lambda a: json.dumps(a, sort_keys=True))
            raw_heat = {}
            for box_id in record["remaining_heat"]:
                bi = native.T.box_ids.index(box_id)
                slots = native._open_slots(native._cur)
                slots = slots[native.T.static[bi, slots]]
                bs = np.full(len(slots), bi, dtype=np.int64)
                passes = native._heat_many(native._cur, bs, slots)
                raw_heat[box_id] = {"candidate_open_static_slots": len(slots),
                                   "e8_only_allowed": int(np.sum(passes)),
                                   "inventory_missing": bool(native._heat_data[1]),
                                   "allowed_slots": [native.T.slot_ids[int(s)] for s in slots[passes]]}
            result.update(replayed=True, legal=legal, legal_count=len(legal),
                complete=engine.is_complete(), raw_heat=raw_heat, securing=securing.securing_metrics())
        except Exception as error:
            result.update(replayed=False, error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        result["wall_s"] = time.perf_counter() - begin
        results.append(result)
        print(json.dumps({k: result.get(k) for k in ("id", "replayed", "legal_count", "error", "wall_s")}), flush=True)
        helper.write(HERE / f"{args.arm}-prefix-replay.json", {
            "runtime": str(tree), "pid": os.getpid(), "results": results,
            "source_before": before, "manifest_sha256": sha256(MANIFEST.read_bytes()).hexdigest(),
            "wall_s": time.perf_counter() - started, "cpu_s": time.process_time() - cpu_start,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "c2": 0, "rule_freeze": False, "test_bodies_read": False,
            "source_stable": before == helper.source_epoch(tree),
            "no_completion_search_or_full_checker": True})


if __name__ == "__main__":
    main()
