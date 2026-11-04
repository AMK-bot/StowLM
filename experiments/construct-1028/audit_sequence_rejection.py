"""Replay one concrete rejected-prefix witness and audit native mask stages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import time

from cache_runtime import redirect_cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnostic-c2-off", action="store_true")
    parser.add_argument("--diagnostic-completion-off", action="store_true")
    parser.add_argument("--box")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = args.output.resolve()
    if not output.is_relative_to(here):
        parser.error("output must be under construct-1028")
    redirect_cache(here / "runtime")
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.io import load_instance
    from stowage.solver.construct_sequence import audit_candidate_stages, _rejected_child
    record = json.loads(args.result.read_text())
    details = record.get("failure", record["sequence"])
    prefix, rejected = [], None
    for event in details["trace"]:
        depth = event["depth"]
        if event["event"] == "accepted":
            prefix = prefix[:depth]+[event["action"]]
        elif event["event"] == "mask_rejected" and (not args.box or event["action"]["box"] == args.box):
            prefix, rejected = prefix[:depth], event["action"]
            break
    if rejected is None:
        raise ValueError("no rejected action in saved trace")
    def deadline(signum, frame):
        raise TimeoutError("single candidate audit exceeded 30 seconds")
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 30.)
    started = time.monotonic()
    instance = load_instance(record["input"])
    space = get_action_space("boxslot")
    extra = {}
    if args.diagnostic_c2_off:
        extra["global_screen"] = 0
    if args.diagnostic_completion_off:
        extra["completion_screens"] = 0
    engine = make_feasibility_engine(instance, space, config=FeasibilityConfig(extra=extra))
    for index, encoded in enumerate(prefix):
        decoded = space.decode(encoded)
        if not engine.is_legal(decoded):
            result = dict(input=record["input"], diagnostic=True, acceptance_evidence=False,
                config_extra=extra, prefix_actions=prefix[:index], rejected_action=encoded,
                replay_disagreement_index=index, stages=audit_candidate_stages(engine, decoded),
                child_completion=_rejected_child(engine, decoded), wall_s=time.monotonic()-started)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2)+"\n")
            print(json.dumps(result))
            return
        engine.push(decoded)
    action = space.decode(rejected)
    result = dict(input=record["input"], diagnostic=True, acceptance_evidence=False, config_extra=extra,
                  prefix_actions=prefix, rejected_action=rejected,
                  public_is_legal=engine.is_legal(action), stages=audit_candidate_stages(engine, action),
                  child_completion=_rejected_child(engine, action), wall_s=time.monotonic()-started)
    signal.setitimer(signal.ITIMER_REAL, 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
