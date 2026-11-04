"""Collect the D185 response pools: independent zero-shot calls, one immutable JSONL per model arm.

    uv run --no-sync python experiments/mvp/strategy/proposer/collect/collect.py spark
    uv run --no-sync python experiments/mvp/strategy/proposer/collect/collect.py glimmer

Rules are in ``responses/d185-collection-plan.json``, written before the first cohort call. Rounds go over the maps,
medium first; a map leaves once it holds its target of format-valid responses or hits its attempt cap. Only
``parse_layout`` is consulted, never an evaluator outcome, and no model sees a previous answer. Resumes from
``<pool>.partial.jsonl``; the final pool appears atomically at the end, once.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CALL_BOUND_USD, Glimmer, PLAN, PROPOSER, RESPONSES, ROOT, STOP_USD, Spark, spent


def channel_text(raw: str) -> str | None:
    """The final message channel of Glimmer's output, or None if it never left its reasoning channel."""
    pieces = re.split(r"<\|start\|>assistant", raw)
    last = pieces[-1]
    if "to=self" in last.split("<|message|>", 1)[0] or "<|message|>" not in last:
        return None if ("<|message|>" in raw or "to=self" in raw) else raw.strip()
    return re.sub(r"<\|(eot|eom|end)\|>\s*$", "", last.split("<|message|>", 1)[1])


def jobs():
    from experiments.mvp.strategy.proposer.prompt import build_prompt
    from stowage.io import load_instance
    from stowage.rules.catalogue import rule_freeze_v1

    rows = json.loads((PROPOSER / "maps_d185.json").read_text())["maps"]
    out = []
    with rule_freeze_v1(enabled=False):
        for row in sorted(rows, key=lambda r: r["size"] != "medium"):
            instance = load_instance(ROOT / row["input"])
            out.append(dict(map_id=row["map_id"], size=row["size"], instance=instance, prompt=build_prompt(instance),
                            input_sha256=row.get("input_sha256") or hashlib.sha256((ROOT / row["input"]).read_bytes()).hexdigest()))
    return out


def valid(text: str, instance) -> bool:
    from experiments.mvp.strategy.proposer.format import parse_layout

    try:
        parse_layout(text, instance)
        return True
    except ValueError:
        return False


def spark_row(call, job, attempt, effort):
    from llm.spark.client import SparkError, SparkIncompleteError

    meta = {"reasoning_effort": effort}
    try:
        answer = call(job["prompt"], purpose="d185-proposer-spark", max_output_tokens=2048, effort=effort)
        text, usage, meta["status"] = answer.text, answer.usage, "completed"
    except SparkIncompleteError as error:
        text, usage, meta["status"] = "", error.usage, f"incomplete:{error.reason}"
    except SparkError as error:
        meta.update(status="provider_failure", error=type(error).__name__)
        return "", {"input_tokens": 2500, "output_tokens": 2048, "output_tokens_includes_reasoning": True,
                    "estimated_upper_bound": True}, meta
    return text, {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                  "output_tokens_includes_reasoning": True}, meta


def main() -> int:
    from experiments.mvp.strategy.proposer.registration import collection_requirements

    arm = sys.argv[1]
    plan = json.loads(PLAN.read_text())[arm]
    target = plan["format_valid_target"]
    caps = plan["attempt_cap"]
    final = RESPONSES / f"d185-{arm}.jsonl"
    if final.exists():
        print("pool already final", flush=True)
        return 0
    partial, sidecar = RESPONSES / f"d185-{arm}.partial.jsonl", RESPONSES / f"d185-{arm}.meta.jsonl"
    done = [json.loads(line) for line in partial.read_text().splitlines()] if partial.exists() else []
    tasks = jobs()
    by_map = {job["map_id"]: job for job in tasks}
    attempts = {job["map_id"]: 0 for job in tasks}
    good = {job["map_id"]: 0 for job in tasks}
    for row in done:
        attempts[row["map_id"]] = max(attempts[row["map_id"]], row["attempt"])
        good[row["map_id"]] += valid(row["text"], by_map[row["map_id"]]["instance"])
    if arm == "spark":
        call, effort = Spark(plan["sent_fields"]), plan["reasoning_effort"]
    else:
        model, strength = Glimmer(), plan["reasoning_strength"]
    stop = None
    medium_open = True
    for round_number in range(1, max(caps.values()) + 1):
        for job in tasks:
            map_id, size = job["map_id"], job["size"]
            if attempts[map_id] >= round_number or attempts[map_id] >= caps[size] or good[map_id] >= target[size]:
                continue
            if size == "medium" and not medium_open:
                continue
            attempt = attempts[map_id] + 1
            if arm == "spark":
                if spent() + CALL_BOUND_USD > STOP_USD:
                    stop = "spend stop"
                    break
                text, usage, meta = spark_row(call, job, attempt, effort)
            else:
                result = model(job["prompt"], seed=collection_requirements(arm, attempt)["seed"], strength=strength)
                text = channel_text(result["raw"]) or ""
                usage, meta = None, result
            provenance = collection_requirements(arm, attempt) | {
                "prompt_sha256": hashlib.sha256(job["prompt"].encode("utf-8")).hexdigest(),
                "input_sha256": job["input_sha256"]}
            row = dict(map_id=map_id, arm=arm, attempt=attempt, text=text, collection_provenance=provenance)
            if usage is not None:
                row["usage"] = usage
            with partial.open("a") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            with sidecar.open("a") as stream:
                stream.write(json.dumps(dict(map_id=map_id, attempt=attempt, **meta)) + "\n")
            attempts[map_id] = attempt
            is_valid = valid(text, job["instance"])
            good[map_id] += is_valid
            print(json.dumps(dict(map=map_id, attempt=attempt, valid=is_valid, status=meta.get("status") or
                                  meta.get("finish_reason"), spent=str(spent()))), flush=True)
        if stop:
            break
        rule = plan.get("medium_futility_after_rounds")
        medium = [job["map_id"] for job in tasks if job["size"] == "medium"]
        if rule and medium_open and round_number >= rule and not sum(good[m] for m in medium):
            medium_open = False
            print(f"medium futility stop after round {round_number}", flush=True)
    temporary = final.with_suffix(".tmp")
    rows = sorted(partial.read_text().splitlines())
    temporary.write_text("\n".join(rows) + "\n")
    temporary.replace(final)
    (RESPONSES / f"d185-{arm}.collected.json").write_text(json.dumps(dict(
        stop=stop or "plan complete", responses=len(rows), format_valid=good, attempts=attempts,
        spent_usd=str(spent()), source_sha256=hashlib.sha256(final.read_bytes()).hexdigest()), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
