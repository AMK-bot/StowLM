"""Pre-collection checks on no cohort map: which sampling fields Spark accepts, and whether answers fit the 2,048 cap."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROPOSER, RESPONSES, ROOT, spent

PILOT_SMALL = "experiments/mvp/corpus/train/small/feeder_1700/random_yard/seed-031100000.json"


def pilot_inputs():
    from stowage.io import load_instance

    cohort = {row["input"] for row in json.loads((PROPOSER / "maps_d185.json").read_text())["maps"]}
    old = json.loads((PROPOSER / "maps.json").read_text())
    medium = next(row["input"] for row in (old["maps"] if isinstance(old, dict) else old)
                  if row["size"] == "medium" and row["input"] not in cohort)
    assert PILOT_SMALL not in cohort
    return [("small", load_instance(ROOT / PILOT_SMALL)), ("medium", load_instance(ROOT / medium))]


def build(instance):
    from experiments.mvp.strategy.proposer.format import parse_layout
    from experiments.mvp.strategy.proposer.prompt import build_prompt

    return build_prompt(instance), parse_layout


def fields():
    from common import Spark
    from llm.spark.client import SparkHTTPError

    out = {}
    for name, extra in (("baseline", {}), ("temperature", {"temperature": 0.7}), ("top_p", {"top_p": 1.0}),
                        ("seed", {"seed": 179}), ("temperature_top_p", {"temperature": 0.7, "top_p": 1.0}),
                        ("all", {"temperature": 0.7, "top_p": 1.0, "seed": 179})):
        try:
            answer = Spark(extra)("Reply with the word OK.", purpose="d185-proposer-probe", max_output_tokens=256, effort="low")
            out[name] = {"accepted": True, "output_tokens": answer.usage.output_tokens}
        except SparkHTTPError as error:
            out[name] = {"accepted": False, "http_status": error.status_code}
    return out


def spark_pilot(extra):
    from common import Spark
    from llm.spark.client import SparkIncompleteError

    rows = []
    for size, instance in pilot_inputs():
        prompt, parse = build(instance)
        for effort in (None, "low", "minimal"):
            row = {"size": size, "effort": effort or "provider-default"}
            try:
                answer = Spark(extra)(prompt, purpose="d185-proposer-pilot", max_output_tokens=2048, effort=effort)
                row.update(status="completed", input_tokens=answer.usage.input_tokens, output_tokens=answer.usage.output_tokens,
                           cost=str(answer.cost_usd))
                try:
                    parse(answer.text, instance)
                    row["format_valid"] = True
                except ValueError as error:
                    row.update(format_valid=False, why=str(error)[:120])
            except SparkIncompleteError as error:
                row.update(status=f"incomplete:{error.reason}", output_tokens=error.usage.output_tokens, cost=str(error.cost_usd))
            rows.append(row)
            print(row, flush=True)
    return rows


def main():
    out = {"fields": fields()}
    print(out, flush=True)
    accepted = {"temperature": 0.7, "top_p": 1.0, "seed": 179}
    accepted = {name: value for name, value in accepted.items() if out["fields"][name]["accepted"]}
    if not out["fields"]["temperature_top_p"]["accepted"]:
        accepted.pop("top_p", None)  # the provider default is already 1.0
    out["accepted"] = accepted
    out["pilot"] = spark_pilot(accepted)
    out["spent_usd"] = str(spent())
    RESPONSES.mkdir(exist_ok=True)
    (RESPONSES / "d185-spark-precheck.json").write_text(json.dumps(out, indent=1))
    print("spent", out["spent_usd"])


if __name__ == "__main__":
    main()
