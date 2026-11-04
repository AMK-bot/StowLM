"""One pre-registered StowLM instruction replan whose sentence actually binds (D168, P1-5).

    uv run python experiments/mvp/instruct/binding_run.py prereg "<sentence>"   # Spark parse + binding proof; no decode
    uv run python experiments/mvp/instruct/binding_run.py decode                # once only, needs a binding PREREG.json
    uv run python experiments/mvp/instruct/binding_run.py verify                # fresh checker + honoured, writes RESULT.md

``prereg`` records every attempt in ``PREREG.json`` (at most 3 sentences). The constraint the decode uses is read back
from that file, so what runs is what Spark returned, not something retyped here. MODEL_API_KEY is read from the parent
Projects folder's .env into this process only.

Spend: ``JobGuard`` is the parser's normal gate, at D161's $5 (its worst-case hold is $1.31, so it can't express this
order's $0.50). The order's cap is enforced beside it on measured spend: each parse is at most about $0.04 (8,192 output
tokens, ~1.5k input), and a parse is refused when the job's measured spend plus $0.05 would pass $0.50.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT / "src", ROOT / "experiments/eval-c1", ROOT / "experiments/mvp/ui"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

RUN = ROOT / "experiments/mvp/runs/stowlm-instruction-binding"
PREREG = RUN / "PREREG.json"
INPUT = ROOT / "experiments/mvp/corpus/showcase/feeder_1700-small.json"
INSTANCE_ID = "random.small.29000201.0.7"
HERO = ROOT / f"experiments/mvp/runs/stowlm-showcase/instances/{INSTANCE_ID}/result.json"
ADAPTER = "experiments/mvp/sft/stowlm-v2/adapter"
LEDGER = ROOT / "experiments/spark-spend/calls.jsonl"
JOB_START = "2026-10-30T18:40:00"
MAX_ATTEMPTS = 3
SPEND_CAP = Decimal("0.50")
CALL_BOUND = Decimal("0.05")
D161_CAP = Decimal("5")
# Ruled out with no Spark call: the slice has bays 01 and 03 only, so every 40-ft slot spans both.
OFFLINE_RULED_OUT = [{"constraint": {"type": "close_bay", "bay": bay}, "why": "capacity_shortfall(): 40-ft QZXU0000009 "
                      "has no slot left, and every 40-ft slot on this two-bay slice spans bays 01 and 03"}
                     for bay in (1, 3)]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save(path: Path, value) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str))
    tmp.replace(path)


def instance_and_hero():
    from stowage.io import load_instance
    from stowage.model import PlannerResult

    return load_instance(INPUT), PlannerResult.model_validate(json.loads(HERO.read_text())).plan


def load_key() -> None:
    for line in (ROOT.parent / ".env").read_text().splitlines():
        name, _, value = line.strip().partition("=")
        if name.removeprefix("export ").strip() == "MODEL_API_KEY":
            os.environ["MODEL_API_KEY"] = value.strip().strip("'\"")
    os.environ["SPARK_CREDIT_READY"] = "1"


def prereg(sentence: str) -> int:
    from llm.instructions.apply import NotApplicable, apply
    from llm.instructions.parser import JobGuard, parse_detailed
    from llm.instructions.schema import Refusal

    record = json.loads(PREREG.read_text()) if PREREG.exists() else {
        "order": "instr-run (D168 P1-5)", "instance": INSTANCE_ID,
        "input": {"path": str(INPUT.relative_to(ROOT)), "sha256": sha256(INPUT)},
        "hero_plan": {"path": str(HERO.relative_to(ROOT)), "sha256": sha256(HERO), "weighted": 67.0},
        "recipe": "9.1: greedy, securing + look-ahead, <= 32 rewinds, V4 finisher 120 s, no repair; StowLM 8.2",
        "adapter": ADAPTER, "offline_ruled_out": OFFLINE_RULED_OUT, "attempts": [], "binding": None,
        "decode_started": None}
    if record["binding"] is not None or len(record["attempts"]) >= MAX_ATTEMPTS:
        raise SystemExit("PREREG closed: already binding, or 3 sentences used")
    guard = JobGuard(LEDGER, since=JOB_START, allocation_usd=D161_CAP)
    if guard.spent() + CALL_BOUND > SPEND_CAP:
        raise SystemExit(f"order cap: measured {guard.spent()} + {CALL_BOUND} would pass {SPEND_CAP}")
    load_key()
    parsed = parse_detailed(sentence, guard=guard)
    attempt = {"time": now(), "sentence": sentence, "model": "muse-spark-1.3", "raw_answer": parsed.raw,
               "error": parsed.error, "cost_usd": str(parsed.cost_usd), "binds": False}
    if isinstance(parsed.outcome, Refusal) or parsed.error:
        attempt["refusal"] = parsed.outcome.model_dump(mode="json")
    else:
        attempt["constraint"] = parsed.outcome.model_dump(mode="json")
        instance, hero = instance_and_hero()
        try:
            constrained = apply(instance, parsed.outcome)
        except NotApplicable as exc:
            attempt["not_applicable"] = str(exc)
        else:
            violations = constrained.violations(hero)
            attempt.update(hero_honoured=constrained.honoured(hero), hero_violations=violations,
                           capacity_shortfall=constrained.capacity_shortfall())
            attempt["binds"] = bool(violations) and not constrained.honoured(hero) \
                and attempt["capacity_shortfall"] is None
    record["attempts"].append(attempt)
    record["spark_spend_usd"] = str(sum(Decimal(a["cost_usd"]) for a in record["attempts"]))
    record["job_ledger_spend_usd"] = str(guard.spent())
    if attempt["binds"]:
        record["binding"] = {k: attempt[k] for k in ("sentence", "constraint", "hero_honoured", "hero_violations")}
        record["registered_at"] = now()
    save(PREREG, record)
    print(json.dumps({k: v for k, v in attempt.items() if k != "raw_answer"}, indent=1, ensure_ascii=False))
    print("spark spend", record["spark_spend_usd"], "ledger", record["job_ledger_spend_usd"])
    return 0 if attempt["binds"] else 1


def constrained_from_prereg():
    from llm.instructions.apply import apply
    from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers

    record = json.loads(PREREG.read_text())
    if not record.get("binding"):
        raise SystemExit("no binding sentence in PREREG.json")
    types = {"close_bay": CloseBay, "max_tiers": MaxTiers, "dg_keep_away": DgKeepAway}
    constraint = record["binding"]["constraint"]
    instance, hero = instance_and_hero()
    constrained = apply(instance, types[constraint["type"]].model_validate(constraint))
    if constrained.honoured(hero):
        raise SystemExit("the hero plan honours this constraint: it doesn't bind")
    return record, constrained


def decode() -> int:
    from types import SimpleNamespace

    import run_stowlm_instruction as wrapper

    record, constrained = constrained_from_prereg()
    if record.get("decode_started") or (RUN / "config.json").exists():
        raise SystemExit("decode is once only and has already started")
    record["decode_started"] = now()
    save(PREREG, record)
    import run_c1

    run_c1.make_planner = wrapper.planner_maker(run_c1, constrained)
    os.environ.setdefault("STOWLM_MLX_CACHE_LIMIT_GB", "2")
    sys.argv = wrapper.c1_argv(SimpleNamespace(run_dir=str(RUN.relative_to(ROOT)), adapter=ADAPTER, time_limit=600.0,
                                               finalize_s=120.0))
    run_c1.main()
    return 0


def verify() -> int:
    from stowage.checker import check
    from stowage.model import PlannerResult
    from stowage.moves import apply_plan

    record, constrained = constrained_from_prereg()
    binding = record["binding"]
    instance, hero = constrained.instance, instance_and_hero()[1]
    result = PlannerResult.model_validate(json.loads((RUN / "instances" / INSTANCE_ID / "result.json").read_text()))
    hero_count = apply_plan(instance, hero).count
    out = {"sentence": binding["sentence"], "constraint": binding["constraint"], "parser": "muse-spark-1.3",
           "input": str(INPUT.relative_to(ROOT)), "input_sha256": sha256(INPUT), "status": result.status,
           "hero_violations": binding["hero_violations"], "hero_weighted": hero_count.weighted_nonproductive,
           "hero_total_lifts": hero_count.total_lifts_now, "message": result.message, "shown": False}
    if result.status == "ok" and result.plan is not None:
        report = check(instance, result.plan)
        result._record_full_check(instance, report)  # a reloaded result carries no evidence: bind the fresh check
        count = apply_plan(instance, result.plan).count
        out.update(fresh_checker_passed=report.passed, instruction_violations=constrained.violations(result.plan),
                   honoured=constrained.honoured(result.plan), weighted=count.weighted_nonproductive,
                   total_lifts=count.total_lifts_now, repaired=result.repaired, shippable=result.shippable,
                   identical_to_hero=result.plan.moves == hero.moves)
        out["final_slots_changed"] = final_slot_changes(instance, hero, result.plan)
        config, hero_config = (json.loads((path / "config.json").read_text()) for path in (RUN, HERO.parents[2]))
        keys = ("adapter_content_sha256", "model_content_sha256")
        out["same_model_as_hero"] = all(config["planner_parameters"][k] == hero_config["planner_parameters"][k]
                                        for k in keys)
        out["active_stubs"] = config["active_stubs"]
        metrics = result.metrics
        out.update(actions=metrics["n_actions"], rewinds=metrics["gen_backtracks"][0], decode_s=metrics["gen_wall_s"],
                   pipeline_s=metrics["pool_plan_wall_s"])
        out["shown"] = bool(report.passed and out["honoured"] and result.shippable and not result.repaired
                            and out["same_model_as_hero"] and not out["active_stubs"])
        write_card_sidecar(record, out, hero)
    save(RUN / "instruction-check.json", out)
    write_result(record, out)
    print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return 0 if out["shown"] else 1


def final_slot_changes(instance, hero, plan) -> dict:
    def final(moves) -> dict:
        slots = {}
        for move in moves:
            if move.kind in ("load", "restow_on", "restow_shift") and hasattr(move, "container_id"):
                slots[move.container_id] = move.dst
        return slots

    before, after = final(hero.moves), final(plan.moves)
    return {box: [before.get(box), after.get(box)] for box in sorted(before.keys() | after.keys())
            if before.get(box) != after.get(box)}


def write_card_sidecar(record: dict, out: dict, hero) -> None:
    """For mvp-ui: the ``instruction-replan-v1`` fields ``app/stage_instruction.py`` reads. ``config.json`` is run_c1's,
    so this sits beside it, and the before-plan file has the shape ``_before_plan`` expects."""
    save(RUN / "before-plan.json", {"input_sha256": out["input_sha256"], "plan": hero.model_dump(mode="json")})
    save(RUN / "instruction-card.json", {
        "format": "instruction-replan-v1", "planner": "stowlm", "label": "StowLM replan under the instruction",
        "input": {"path": out["input"], "sha256": out["input_sha256"]},
        "gold_plan": str((RUN / "before-plan.json").relative_to(ROOT)),
        "instruction": {"sentence": out["sentence"], "constraint": out["constraint"],
                        "parse": {"model": out["parser"], "raw_answer": record["attempts"][-1]["raw_answer"],
                                  "cost_usd": record["attempts"][-1]["cost_usd"]}}})


def write_result(record: dict, out: dict) -> None:
    head = ("**Verdict: a real StowLM instruction replan.** Fresh full checker passed and the instruction is honoured."
            if out["shown"] else "**Verdict: not a StowLM instruction replan.** The video keeps the labelled solver "
            "replan.")
    rows = [("Sentence", out["sentence"]), ("Parsed by", f"{out['parser']} (spend ${record['spark_spend_usd']})"),
            ("Constraint", f"`{json.dumps(out['constraint'])}`"), ("Call", INSTANCE_ID),
            ("Hero plan under it", f"breaks it, {len(out['hero_violations'])} placements (violation proof below)"),
            ("Status", out["status"]), ("Fresh full checker", out.get("fresh_checker_passed", "no plan")),
            ("Honoured", out.get("honoured", "no plan")), ("Repaired", out.get("repaired", "no plan")),
            ("Weighted moves", f"{out.get('weighted', 'no plan')} vs hero {out['hero_weighted']}"),
            ("Total lifts", f"{out.get('total_lifts', 'no plan')} vs hero {out['hero_total_lifts']}"),
            ("Boxes with a different final slot", len(out.get("final_slots_changed", {}))),
            ("Decode", f"{out.get('actions', 'no')} actions, {out.get('rewinds', 'no')} rewinds, "
                       f"{out.get('decode_s', 'no')} s decode, {out.get('pipeline_s', 'no')} s with finisher"),
            ("Same adapter and base as the hero run", out.get("same_model_as_hero", "no plan")),
            ("Shippable (fresh check bound to the result)", out.get("shippable", "no plan")),
            ("Shown", out["shown"])]
    lines = ["# StowLM instruction replan that binds (D168 P1-5)", "", head, "", "| Field | Value |", "|---|---|",
             *(f"| {k} | {v} |" for k, v in rows), "", "Hero plan's violations, recorded before any decode:", "",
             *(f"- `{v}`" for v in out["hero_violations"])]
    if out.get("instruction_violations"):
        lines += ["", "This plan's violations:", "", *(f"- `{v}`" for v in out["instruction_violations"])]
    if out.get("final_slots_changed"):
        lines += ["", "Final slots, hero to this plan:", "",
                  *(f"- {box}: {a} -> {b}" for box, (a, b) in out["final_slots_changed"].items())]
    if out.get("message"):
        lines += ["", f"Planner message: {out['message']}"]
    lines += ["", f"Sentences tried: {len(record['attempts'])}, all in `PREREG.json`. Machine-readable result: "
              "`instruction-check.json`.", ""]
    (RUN / "RESULT.md").write_text("\n".join(lines))


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "prereg" and len(sys.argv) == 3:
        raise SystemExit(prereg(sys.argv[2]))
    if action in ("decode", "verify") and len(sys.argv) == 2:
        raise SystemExit({"decode": decode, "verify": verify}[action]())
    raise SystemExit(__doc__)
