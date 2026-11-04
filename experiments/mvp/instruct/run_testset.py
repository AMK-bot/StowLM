"""Measure the Spark instruction parser on the 20-sentence test set (plan v4 task 5.10).

    set -a; . /path/outside/repo/.env; set +a; export SPARK_CREDIT_READY=1
    uv run python experiments/mvp/instruct/run_testset.py <tag>

Sequential calls, retries off, guarded by the task's $3 allocation. Writes responses/<tag>.json (raw answers, costs)
and prints the scores. ``--replay responses/<tag>.json`` rescores recorded answers offline.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

from llm.instructions.parser import JobGuard, ParseResult, parse_detailed
from llm.instructions.schema import Refusal, validate_answer

HERE = Path(__file__).parent
JOB_START = "2026-10-29T16:00:00"
ALLOCATION = Decimal("3")


def score(cases, outcomes):
    rows = []
    for case, (outcome, raw, cost, error) in zip(cases, outcomes):
        exp = case["expected"]
        got = ({"refusal": outcome.code} if isinstance(outcome, Refusal) else
               {"constraint": outcome.model_dump(mode="json")})
        if "refusal" in exp:
            correct = "refusal" in got and error is None
            code_match = correct and got["refusal"] == exp["refusal"]
        else:
            correct = got == exp
            code_match = None
        rows.append(dict(id=case["id"], kind=case["kind"], sentence=case["sentence"], expected=exp, got=got,
                         correct=correct, refusal_code_match=code_match, error=error, cost_usd=str(cost), raw=raw))
    pos = [r for r in rows if "constraint" in r["expected"]]
    neg = [r for r in rows if "refusal" in r["expected"]]
    summary = dict(
        intent_accuracy=f"{sum(r['correct'] for r in pos)}/{len(pos)}",
        refusal_correct=f"{sum(r['correct'] for r in neg)}/{len(neg)}",
        refusal_code_match=f"{sum(bool(r['refusal_code_match']) for r in neg)}/{len(neg)}",
        false_constraints_on_refuse_cases=sum("constraint" in r["got"] for r in neg),
        false_refusals=sum("refusal" in r["got"] for r in pos),
        thai_english=f"{sum(r['correct'] for r in rows if 'thai-english' in r['kind'])}/"
                     f"{sum('thai-english' in r['kind'] for r in rows)}",
        overall=f"{sum(r['correct'] for r in rows)}/{len(rows)}",
        parser_errors=sum(r["error"] is not None for r in rows),
        cost_usd=str(sum(Decimal(r["cost_usd"]) for r in rows)),
    )
    return summary, rows


def main():
    name = sys.argv[sys.argv.index("--set") + 1] if "--set" in sys.argv else "testset"
    cases = json.loads((HERE / f"{name}.json").read_text())
    if "--replay" in sys.argv:
        recorded = json.loads(Path(sys.argv[sys.argv.index("--replay") + 1]).read_text())["rows"]
        outcomes = []
        for r in recorded:
            try:
                outcomes.append((validate_answer(r["raw"].strip()), r["raw"], Decimal(r["cost_usd"]), None))
            except (ValueError, AttributeError):
                outcomes.append((Refusal(code="ambiguous", reason="invalid"), r["raw"], Decimal(r["cost_usd"]),
                                 "invalid answer"))
        print(json.dumps(score(cases, outcomes)[0], indent=1))
        return
    from llm.spark.client import SparkClient

    tag = sys.argv[1]
    client = SparkClient(max_retries=0)
    guard = JobGuard(client.ledger.path, since=JOB_START, allocation_usd=ALLOCATION)
    outcomes = []
    for case in cases:
        res: ParseResult = parse_detailed(case["sentence"], client=client, guard=guard)
        outcomes.append((res.outcome, res.raw, res.cost_usd, res.error))
        print(case["id"], json.dumps(res.outcome.model_dump(mode="json"), ensure_ascii=False), res.cost_usd,
              res.error or "", flush=True)
    summary, rows = score(cases, outcomes)
    (HERE / "responses").mkdir(exist_ok=True)
    from llm.instructions.parser import PROMPT_VERSION
    (HERE / "responses" / f"{tag}.json").write_text(json.dumps(dict(set=name, prompt_version=PROMPT_VERSION,
        summary=summary, rows=rows,
        job_spend_usd=str(guard.spent())), indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1), "job spend", guard.spent())


if __name__ == "__main__":
    main()
