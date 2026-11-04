"""Bounded before/after probe of a fixed-background stack violation."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, path = args.source.resolve(), args.input.resolve()
    sys.path.insert(0, str(source/'src'))
    sys.dont_write_bytecode = True
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.solver.ballast import finish_ballast
    from stowage.solver.ballast_deadline import shutdown_worker
    from stowage.checker.rules import check_static_rules
    instance = load_instance(path)
    report = check_static_rules(instance, instance.onboard, call_index=instance.call_index)
    started = time.monotonic()
    try:
        outcome = finish_ballast(instance, MovePlan(id='empty-background-ballast-proof',
            instance_id=instance.id, call_index=instance.call_index), time_limit_s=3.)
        result = dict(input=str(path), input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            source=str(source), fixed_stow_only=True, budget_s=3.,
            status=outcome.status, proof=outcome.proof, iterations=outcome.iterations,
            wall_s=time.monotonic()-started,
            static_rules=sorted({v.rule_id for v in report.violations if v.severity=='hard'}),
            source_hashes={name: hashlib.sha256((source/'src/stowage/solver'/name).read_bytes()).hexdigest()
                           for name in ('ballast.py', 'ballast_arrival.py', 'ballast_proof.py')})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result))
    finally:
        shutdown_worker()


if __name__ == '__main__':
    main()
