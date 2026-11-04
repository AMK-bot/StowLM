"""Replay a saved final assignment through the deliberate production sequencer."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import time

from cache_runtime import redirect_cache


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-plan", type=Path)
    source.add_argument("--from-rejection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=float, default=60.)
    parser.add_argument("--cap", type=float, default=120.)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = args.output.resolve()
    if not output.is_relative_to(here):
        parser.error("output must be under construct-1028")
    output.mkdir(parents=True, exist_ok=True)
    redirect_cache(here / "runtime")
    from stowage.actions import get_action_space
    from stowage.checker import check
    from stowage.io import load_instance, load_plan, save_plan
    from stowage.moves import apply_plan
    from stowage.solver.construct_sequence import SequenceFailure, sequence

    started = time.monotonic()
    diagnostic = {}
    result = {"diagnostic": True, "shippable_evidence": False, "sequence": diagnostic,
              "sequence_status": "started"}
    files = [Path("src/stowage/solver/construct_sequence.py"),
             Path("src/stowage/feasibility/_engine.py"), Path("src/stowage/feasibility/global_bounds.py")]
    def hashes():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    result["source_start"] = hashes()
    def save():
        result["wall_s"] = time.monotonic()-started
        (output / "result.json").write_text(json.dumps(result, indent=2)+"\n")
    original = None
    if args.from_rejection:
        prior = json.loads(args.from_rejection.read_text())
        args.input = args.input or Path(prior["input"])
        metrics = prior.get("pipeline_metrics", prior.get("metrics", {}))
        failures = [r for r in metrics["rejections"] if r["phase"] == "mask_sequence"]
        failure = failures[-1]["details"]
        assignment = {action["box"]: action["slot"] for action in failure["prefix_actions"]
                      if action["op"] == "P"}
        assignment.update(failure["pending"])
    else:
        if args.input is None:
            parser.error("--input is required with --from-plan")
        original = load_plan(args.from_plan)
        assignment = None
    instance = load_instance(args.input)
    if original is not None:
        replay = apply_plan(instance, original)
        assert replay.legal, replay.violations
        assignment = {p.container_id: p.slot for p in replay.final_state.ship.placements
                      if p.container_id in instance.load_list}
    assignment = {cid: slot for cid, slot in assignment.items() if cid in instance.load_list}
    result.update(input=str(args.input), assignment=assignment, n_assignment=len(assignment))
    save()
    def deadline(signum, frame):
        raise TimeoutError("whole fixed-assignment diagnostic cap expired")
    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.cap)
    try:
        actions, plan = sequence(instance, assignment, time.monotonic()+args.budget,
                                 diagnostics=diagnostic)
        result["sequence_status"] = "ok"
        result["actions"] = [get_action_space("boxslot").encode(action) for action in actions]
        if original is not None:
            plan = plan.model_copy(update={"ballast": original.ballast})
        save_plan(plan, output / "plan.json")
        save()
        if original is not None:
            report = check(instance, plan)
            result["checker_passed"] = report.passed
            result["hard_rules"] = sorted({v.rule_id for v in report.violations if v.severity == "hard"})
            save()
            from worker import replay_mask
            result["token_mask_replay"] = replay_mask(instance, plan)
    except SequenceFailure as error:
        result.update(sequence_status="failed", error=str(error), failure=error.details)
    except Exception as error:
        result.update(sequence_status="error", error=f"{type(error).__name__}: {error}")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        result["source_end"] = hashes()
        result["source_drift"] = result["source_start"] != result["source_end"]
        save()
    print(json.dumps({key: value for key, value in result.items()
                      if key not in {"sequence", "actions", "assignment", "failure"}}))


if __name__ == "__main__":
    main()
