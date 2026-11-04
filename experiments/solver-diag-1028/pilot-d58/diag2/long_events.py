"""Observation-only event hooks; returned plans/verdicts are unchanged."""
import json
import time
from pathlib import Path


def install(directory, solver):
    started = [None]
    emitted = set()
    path = Path(directory) / "first-events.jsonl"

    def emit(kind, **values):
        if kind in emitted:
            return
        emitted.add(kind)
        with path.open("a") as output:
            output.write(json.dumps(dict(kind=kind, planner_elapsed_s=None if started[0] is None else
                time.monotonic()-started[0], **values)) + "\n")

    import stowage.checker as checker
    original_check = checker.check

    def check(*args, **kwargs):
        result = original_check(*args, **kwargs)
        if result.passed:
            emit("full_checker_pass", checker_passed=True)
        return result

    checker.check = check
    if solver == "scalable":
        import stowage.solver.scalable as module
        original = module._construct

        def construct(*args, **kwargs):
            result = original(*args, **kwargs)
            if result[0]:
                emit("complete_construction_returned", candidates=len(result[0]))
            return result

        module._construct = construct
    elif solver == "twostep":
        import stowage.solver.twostep as module
        original = module._preplan

        def preplan(*args, **kwargs):
            result = original(*args, **kwargs)
            if result.status == "ok":
                emit("cargo_only_prestow_returned", slots=len(result.slots))
            return result

        module._preplan = preplan
    return lambda: started.__setitem__(0, time.monotonic())
