"""Fresh-process one-draw reproduction; observe original public checker reports.

The observer forwards the original arguments/result unchanged and consumes no
RNG. It records the initial witness check before a failed finisher search can
hide its diagnostics. This is supplemental evidence, not a replacement raw draw.
"""
from argparse import ArgumentParser
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
import json
from pathlib import Path
import time

from measure import sources


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--generator", choices=("random", "simulated"), required=True)
    parser.add_argument("--vessel", required=True)
    parser.add_argument("--rung", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    import stowage.checker as checker
    from stowage.generate import random_yard, instances

    args.out.mkdir(parents=True, exist_ok=True)
    original = checker.check
    reports = []

    @wraps(original)
    def observed(instance, plan, *a, **kw):
        result = original(instance, plan, *a, **kw)
        index = len(reports)
        prefix = args.out/f"check-{index}"
        prefix.with_suffix(".instance.json").write_text(instance.model_dump_json(indent=2)+"\n")
        prefix.with_suffix(".plan.json").write_text(plan.model_dump_json(indent=2)+"\n")
        prefix.with_suffix(".report.json").write_text(result.model_dump_json(indent=2)+"\n")
        reports.append(dict(index=index, passed=result.passed,
            violations=[violation.model_dump(mode="json") for violation in result.violations],
            parts={name: dict(ran=part.ran, error=part.error) for name, part in result.parts.items()}))
        return result

    checker.check = observed
    record = dict(generator=args.generator, vessel=args.vessel, rung=args.rung, seed=args.seed,
        started_utc=datetime.now(timezone.utc).isoformat(), source_start=sources()[0],
        observer="Original public checker arguments and results are unchanged; reports captured read-only.")
    started = time.perf_counter()
    module = random_yard if args.generator == "random" else instances
    report = random_yard.RandomGenerationReport() if args.generator == "random" else instances.GenerationReport()
    try:
        function = module.generate_random_instance if args.generator == "random" else module.generate_simulated_instance
        instance = function(args.rung, seed=args.seed, vessel_name=args.vessel, max_attempts=1, report=report)
        record["accepted"] = True
        (args.out/"emitted.instance.json").write_text(instance.model_dump_json(indent=2)+"\n")
    except Exception as error:
        record.update(accepted=False, error=f"{type(error).__name__}: {error}")
    finally:
        if checker.check is observed:
            checker.check = original
        record.update(elapsed_s=time.perf_counter()-started, source_end=sources()[0],
            generation_report=asdict(report), public_checks=reports)
        (args.out/"result.json").write_text(json.dumps(record, indent=2, sort_keys=True)+"\n")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
