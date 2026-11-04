"""Write the TRAIN/fixture-only replay cohort and local input digests."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from llm.maskspeed.replay import HERE, ROOT, saved_traces, small_maps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "out/input-manifest.json")
    parser.add_argument("--guard", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    traces = list(saved_traces())
    maps = small_maps()
    train_root = (ROOT / "experiments/mvp/corpus/train/small").resolve()
    assert all(path.resolve().is_relative_to(train_root) for path in maps)
    assert {str(path.relative_to(ROOT)) for path in maps} == {t["map"] for t in traces}
    paths = {ROOT / t["source"] for t in traces} | set(maps)
    paths |= {ROOT / "models/glimmer-30b-4bit/tokenizer.json",
              ROOT / "src/stowage/actions/samples.py", ROOT / "src/stowage/model/fixtures.py",
              Path(__file__).resolve()}
    result = dict(schema_version=1, cohort="small TRAIN plus seeded tiny fixtures only",
        test_inputs_used=False, required_pythonhashseed="0",
        input_sha256={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in sorted(paths)},
        benchmark_maps=[str(path.relative_to(ROOT)) for path in maps],
        saved=dict(unique_trajectories=len(traces), statuses=dict(Counter(t["expected_status"] for t in traces)),
                   trace_ids=[t["id"] for t in traces],
                   sources=sorted({t["source"] for t in traces})),
        tiny=dict(planned_seed_range_inclusive=[0,31], n_load=8, n_other=3, p40=.2, max_tiers=3,
                  layover="seed modulo 2 equals 1", immediate_successor_lookahead="8 if seed modulo 4 equals 0, else 0",
                  backtrack="one pop/rebranch after depth3 if reached", prefetch="every11th queried prefix",
                  replacement_seed_rule="32 + shard_index + rejection_index * shard_count"),
        benchmark=dict(row_rng_seeds=list(range(17,25)), batches=[1,8],
                       full_trajectory_action_cap=1000))
    if args.guard:
        guards = [json.loads(path.read_text()) for path in args.guard]
        completed = [trace for guard in guards for trace in guard["traces"]]
        assert len({trace["id"] for trace in completed}) == len(completed)
        result["actual_guard"] = dict(artifacts=[str(path.relative_to(ROOT)) for path in args.guard],
            traces=len(completed), kinds=dict(Counter(t["kind"] for t in completed)),
            statuses=dict(Counter(t["status"] for t in completed)),
            tiny_seeds=sorted(t["seed"] for t in completed if t["kind"] == "tiny"),
            baseline_unreplayable=[item for guard in guards for item in guard.get("unreplayable",[])])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(dict(output=str(args.output), saved=len(traces), training_maps=len(maps))))


if __name__ == "__main__":
    main()
