"""Bounded, checkpointed C2-on/off comparison on identical current tiny inputs.

No model, repair, retries or sample selection. Run with one BLAS/OMP thread.
Every completed sample uses the normal 30-second ballast budget and full checker.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

from pass_or_dead_end import sample_and_check
from llm.stowlm.format import load_tokenizer
from stowage.generate.instances import generate_simulated_instance
from stowage.generate.random_yard import generate_random_instance

ROOT = Path(__file__).resolve().parents[2]


def pins():
    result = {}
    for directory in ("src/stowage", "src/llm/stowlm", "params"):
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".toml"}:
                # SFT/training/planner edits cannot affect this no-model experiment.
                # Keep all physics/generator/parameter sources, plus the format and mask.
                if directory == "src/llm/stowlm" and not (
                    path.name.startswith(("format", "mask")) or path.name == "__init__.py"
                ):
                    continue
                result[str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    for path in (Path(__file__), Path(__file__).with_name("pass_or_dead_end.py")):
        result[str(path.relative_to(ROOT))] = sha256(path.read_bytes()).hexdigest()
    tokenizer = ROOT / "models/glimmer-30b-4bit/tokenizer.json"
    result[str(tokenizer.relative_to(ROOT))] = sha256(tokenizer.read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("c2-on-followup"))
    parser.add_argument("--sample-seeds", nargs="+", type=int, default=[17, 18])
    args = parser.parse_args()
    OUT = args.out
    expected = 4 * len(args.sample_seeds)
    if expected > 20:
        raise ValueError("maximum twenty samples per invocation")
    OUT.mkdir(exist_ok=True)
    if (OUT / "outcomes.jsonl").exists():
        raise FileExistsError("retain completed evidence; choose a new output directory for another campaign")
    started = time.perf_counter()
    initial = pins()
    initial_source = sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest()
    (OUT / "pins-start.json").write_text(json.dumps(initial, indent=2) + "\n")
    token = load_tokenizer()
    counts = {"on": Counter(), "off": Counter()}
    cases = []
    with (OUT / "outcomes.jsonl").open("x") as output:
        for source, generate in (("random", generate_random_instance), ("simulated", generate_simulated_instance)):
            instance = generate("tiny", seed=1)
            # Persist the exact object used by both modes, including all policy/evidence fields.
            (OUT / f"{source}-tiny-1.instance.json").write_text(instance.model_dump_json(indent=2) + "\n")
            for seed in args.sample_seeds:
                for c2 in (True, False):
                    if pins() != initial:
                        raise RuntimeError("source changed before sample; retain completed shards and stop")
                    case_start = time.perf_counter()
                    row = sample_and_check(instance, token, seed=seed, c2=c2, ballast_seconds=30)
                    row.update(source=source, rung="tiny", instance_seed=1,
                               source_sha256=initial_source, source_unchanged=pins() == initial,
                               sample_finished_utc=datetime.now(timezone.utc).isoformat(),
                               ballast_budget_s=30)
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    counts["on" if c2 else "off"][row["outcome"]] += 1
                    cases.append(row)
                    print(source, seed, "C2on" if c2 else "C2off", row["outcome"],
                          f"{time.perf_counter()-case_start:.3f}s", flush=True)
                    if not row["source_unchanged"]:
                        raise RuntimeError("source changed during sample; retain row but do not qualify it")
                    if c2:
                        assert row["engine_diagnostics"]["global_screen"] == "on"
                        assert "unavailable" not in row.get("dead_end", {}).get("reason", "").lower()
        assert len(cases) == expected
        for source in ("random", "simulated"):
            for seed in args.sample_seeds:
                pair = [row for row in cases if row["source"] == source and row["sample_seed"] == seed]
                assert len(pair) == 2 and pair[0]["input_sha256"] == pair[1]["input_sha256"]
    (OUT / "pins-end.json").write_text(json.dumps(pins(), indent=2) + "\n")
    summary = dict(outcomes=counts, samples=len(cases), source_unchanged=pins() == initial,
                   wall_s=time.perf_counter()-started, ballast_budget_s=30,
                   bank_sha256=initial["src/stowage/linear/margin_policy_data.json"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
