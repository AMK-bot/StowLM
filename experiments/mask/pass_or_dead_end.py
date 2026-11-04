"""CPU-only task 5.1 integration: uniform masked tokens, ballast, full checker.

Completed but rejected plans remain ``failed``; only a decoder-reported dead
end is ``dead_end``. There is no solver repair or successful-sample selection.
C2-off is a diagnostic run, not production qualification. Every completed plan
is checked in the D50 departure and arrival conditions, even if ballast fails.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import random
import time

from llm.stowlm.format import ActionCodec, load_tokenizer
from llm.stowlm.mask import MASK_VERSION, random_masked_plan
from stowage.actions import get_action_space
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import PlannerResult
from stowage.planner import finalize
from stowage.versions import component_versions


def source_digest():
    """Detect concurrent input/source changes without modifying shared files."""
    root = Path(__file__).resolve().parents[2]
    digest = sha256()
    for directory in (root / "src/stowage", root / "src/llm/stowlm", root / "params"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".toml"}:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def sample_and_check(instance, tokenizer, *, seed=17, c2=False, ballast_seconds=2.0):
    started = time.perf_counter()
    hull = instance.vessel.hull
    assert hull is not None
    # These are real generator inputs, not hull-free action fixtures.
    for field in ("min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg"):
        assert getattr(hull, field) is not None
        assert getattr(hull, field + "_provenance") is not None
    cargo = [load for load in instance.vessel.fixed_loads if load.kind == "cargo" and load.mass_kg]
    assert cargo and all(load.cargo_boxes for load in cargo)
    assert instance.voyage is not None
    record = dict(instance=instance.id, sample_seed=seed, c2=c2,
                  mask_version=MASK_VERSION, versions=component_versions(),
                  input_sha256=sha256(instance.model_dump_json().encode()).hexdigest(),
                  background_boxes=sum(len(load.cargo_boxes) for load in cargo))
    config = FeasibilityConfig() if c2 else FeasibilityConfig(extra={"global_screen": 0})
    engine = make_feasibility_engine(instance, get_action_space("boxslot"), config=config)
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    decoder = random_masked_plan(engine, codec, random.Random(seed))
    record.update(decoder_status=decoder.status, tokens=len(decoder.tokens), depth=decoder.engine.depth,
                  engine_diagnostics=decoder.engine.diagnostics)
    if decoder.status == "dead_end":
        assert decoder.dead_end is not None
        record.update(outcome="dead_end", dead_end=dict(depth=decoder.dead_end.depth,
                      reason=decoder.dead_end.reason, rule_ids=list(decoder.dead_end.rule_ids)))
    elif decoder.status == "complete":
        assert codec.decode_plan(decoder.tokens) == decoder.actions
        result = finalize(instance, PlannerResult(status="ok", plan=decoder.engine.to_plan(),
                          planner="stowlm.mask-uniform", planner_version=MASK_VERSION, seed=seed),
                          time_limit_s=ballast_seconds)
        report = check(instance, result.plan)
        conditions = {}
        for identity in (instance.voyage.departure, instance.voyage.arrival):
            conditions[identity.phase] = {}
            for part in ("stability", "strength"):
                key = f"condition:{identity.id}:{part}"
                item = report.parts[key]
                conditions[identity.phase][part] = dict(ran=item.ran,
                    passed=item.ran and not any(v.severity == "hard" for v in item.violations),
                    hard_rules=sorted({v.rule_id for v in item.violations if v.severity == "hard"}))
        record.update(outcome="passed" if result.status == "ok" and report.passed else "failed",
                      finalize_status=result.status, ballast_status=result.metrics.get("ballast_status"),
                      checker_passed=report.passed, voyage_conditions=conditions,
                      hard_violations=[dict(part=name, rule_id=v.rule_id, message=v.message)
                          for name, part in report.parts.items() for v in part.violations
                          if v.severity == "hard"],
                      checker_binding=list(report.provenance.parents))
    else:
        raise AssertionError(f"mask did not terminate: {decoder.status}")
    record["wall_s"] = time.perf_counter() - started
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", nargs="+", choices=("random", "simulated"), default=["random", "simulated"])
    parser.add_argument("--instance-seeds", nargs="+", type=int, default=[1])
    parser.add_argument("--sample-seeds", nargs="+", type=int, default=[17, 18])
    parser.add_argument("--ballast-seconds", type=float, default=2.0)
    parser.add_argument("--c2", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    from stowage.generate.random_yard import generate_random_instance
    from stowage.generate.instances import generate_simulated_instance
    token = load_tokenizer()
    before = source_digest()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    outcomes = Counter()
    with args.out.open("w") as output:
        for source in args.sources:
            generate = generate_random_instance if source == "random" else generate_simulated_instance
            for seed in args.instance_seeds:
                instance = generate("tiny", seed=seed)
                for sample_seed in args.sample_seeds:
                    row = sample_and_check(instance, token, seed=sample_seed, c2=args.c2,
                                           ballast_seconds=args.ballast_seconds)
                    row.update(source=source, rung="tiny", instance_seed=seed, source_sha256=before,
                               source_unchanged=before == source_digest())
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    outcomes[row["outcome"]] += 1
                    print(source, seed, sample_seed, row["outcome"], flush=True)
    print(json.dumps(dict(outcomes=outcomes, c2=args.c2, source_unchanged=before == source_digest())))


if __name__ == "__main__":
    main()
