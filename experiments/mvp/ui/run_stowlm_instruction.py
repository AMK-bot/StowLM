"""StowLM decode of the feeder_1700 showcase call under the D144 instruction mask filter (task 9.2 tail, not run yet).

    uv run python experiments/mvp/ui/run_stowlm_instruction.py --adapter <frozen_adapter_dir>
    uv run python experiments/mvp/ui/run_stowlm_instruction.py --dry-run     # prints the run_c1 call; loads nothing

``run_c1.py`` has no instruction flag, so this wrapper leaves it unedited and swaps its ``make_planner``: the planner
keeps the 9.2 recipe's layers (production mask, instructions, look-ahead, D95 securing), with
``llm.instructions.mask_filter.InstructionEngine`` installed through the look-ahead factory's ``engine_wrapper`` seam,
and the same wrapper on the trace's exact-count replay. ``--no-repair`` stays, so a repaired plan can't appear as
StowLM's. Afterwards the plan is judged again: fresh full checker plus ``ConstrainedInstance.violations``, written to
``<run>/instruction-check.json``. A failure exits 1 and leaves the run folder marked as such.

Same recipe flags as ``experiments/mvp/sft/eval92/run_92.sh``. Details: ``experiments/mvp/ui/STOWLM-INSTRUCTION-RUN.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src", ROOT / "experiments/eval-c1", ROOT / "experiments/mvp/ui"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

INPUT = "experiments/mvp/corpus/showcase/feeder_1700-small.json"
RUN_DIR = "experiments/mvp/runs/stowlm-instruction-dg"


def c1_argv(args) -> list[str]:
    """The run_92.sh recipe: greedy, sequential, mask + securing + look-ahead, <= 32 rewinds, no repair."""
    return ["run_c1.py", "--run-dir", args.run_dir, "--adapter", args.adapter or "<frozen_adapter_dir>",
            "--loose", INPUT, "--n-samples", "1", "--temperature", "0", "--mask-workers", "0",
            "--time-limit", str(args.time_limit), "--d79", "--securing", "--lookahead", "--ballast-v4-finalize",
            "--backtrack", "32", "--candidate-finalize-s", str(args.finalize_s), "--no-last-resort", "--no-repair"]


def constrained_instance():
    from llm.instructions.apply import apply
    from llm.instructions.schema import DgKeepAway
    from make_dg_replan import CONSTRAINT
    from stowage.io import load_instance

    return apply(load_instance(ROOT / INPUT), DgKeepAway.model_validate(CONSTRAINT))


def planner_maker(run_c1, constrained):
    """``run_c1.make_planner`` with the instruction filter inside the 9.2 mask stack."""
    original = run_c1.make_planner

    def make_planner(a):
        from decode_trace import RecordingFactory
        from llm.instructions.mask_filter import wrap_engine as instruction_wrap
        from llm.lookahead import lookahead_decoder_factory
        from llm.lookahead.mask_filter import wrap_engine as lookahead_wrap
        from llm.securing.mask_filter import wrap_engine as securing_wrap

        planner = original(a)
        wrap = lambda engine: instruction_wrap(engine, constrained)  # noqa: E731
        factory = lookahead_decoder_factory(engine_wrapper=wrap, securing=True)
        planner.decoder_factory = planner._recorder = RecordingFactory(factory)
        planner.engine_wrapper = lambda engine: lookahead_wrap(securing_wrap(wrap(engine)))
        return planner

    return make_planner


def verdict(run_dir: Path, constrained) -> bool:
    from make_dg_replan import CONSTRAINT, SENTENCE
    from stowage.checker import check
    from stowage.model import PlannerResult

    result_path = run_dir / "instances" / constrained.instance.id / "result.json"
    out = {"sentence": SENTENCE, "constraint": CONSTRAINT, "input": INPUT,
           "input_sha256": hashlib.sha256((ROOT / INPUT).read_bytes()).hexdigest()}
    result = PlannerResult.model_validate(json.loads(result_path.read_text()))
    out["status"] = result.status
    if result.status == "ok" and result.plan is not None:
        report = check(constrained.instance, result.plan)
        out.update(fresh_checker_passed=report.passed, instruction_violations=constrained.violations(result.plan),
                   weighted=report.count.weighted_nonproductive if report.count is not None else None,
                   repaired=result.repaired)
        out["shown"] = bool(report.passed and not out["instruction_violations"] and not result.repaired)
    else:
        out.update(shown=False, message=result.message)
    (run_dir / "instruction-check.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    return out["shown"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--adapter", help="Frozen adapter directory (with its provenance sidecar)")
    parser.add_argument("--run-dir", default=RUN_DIR)
    parser.add_argument("--time-limit", type=float, default=float(os.environ.get("TIME_LIMIT_S", 600)))
    parser.add_argument("--finalize-s", type=float, default=float(os.environ.get("FINALIZE_S", 120)))
    parser.add_argument("--dry-run", action="store_true", help="Print the call and exit; imports no model code")
    args = parser.parse_args(argv)
    call = c1_argv(args)
    if args.dry_run:
        print("would run under the instruction filter:", shlex.join(["experiments/eval-c1/" + call[0], *call[1:]]))
        return 0
    if not args.adapter:
        parser.error("--adapter is required unless --dry-run")
    constrained = constrained_instance()
    import run_c1

    run_c1.make_planner = planner_maker(run_c1, constrained)
    os.environ.setdefault("STOWLM_MLX_CACHE_LIMIT_GB", "2")
    sys.argv = call
    run_c1.main()
    return 0 if verdict(ROOT / args.run_dir, constrained) else 1


if __name__ == "__main__":
    raise SystemExit(main())
