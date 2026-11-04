"""Acquire family-level Muse Spark paraphrases through the shared paid-call ledger.

Run once before generate.py; no sealed inputs are accepted. Credentials are loaded
only into this process environment from the authorised parent Projects .env.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "data" / "spark_paraphrases.json"
PURPOSE = "reader-data-paraphrases-v1"
MAX_OUTPUT = 8192
GROUPS = {
    "close": "Close bay {bay}.",
    "deck": "Limit deck stacks in bay {bay} to at most {tiers} tiers.",
    "hold": "Limit hold stacks in bay {bay} to at most {tiers} tiers.",
    "dg_accommodation": "Keep class {cls} at least {distance} bays from the accommodation.",
    "dg_bay": "Keep class {cls} at least {distance} bays from bay {bay}.",
    "hatch": "Use only one discharge port per hatch cover, across hold and deck.",
    "loosening": "Reopen bay {bay}; loading there is allowed again.",
    "hedged": "Maybe close bay {bay}, but I have not decided.",
}


def load_credentials(path: Path) -> None:
    """Load only recognised credential variables; never return or display them."""
    allowed = {"MODEL_API_KEY", "META_API_KEY", "SPARK_API_KEY", "META_MODEL_API_KEY"}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep and key.strip() in allowed:
            os.environ[key.strip()] = value.strip().strip("\"'")
    if not os.environ.get("MODEL_API_KEY"):
        for alias in sorted(allowed - {"MODEL_API_KEY"}):
            if os.environ.get(alias):
                os.environ["MODEL_API_KEY"] = os.environ[alias]
                break
    if not os.environ.get("MODEL_API_KEY"):
        raise RuntimeError("No recognised Spark credential in authorised environment file")
    os.environ["SPARK_CREDIT_READY"] = "1"


def validate_templates(groups: dict) -> None:
    if set(groups) != set(GROUPS):
        raise ValueError("Paraphrase group names differ from the requested contract")
    for key, values in groups.items():
        if not isinstance(values, list) or not 4 <= len(values) <= 10:
            raise ValueError("Each group needs 4 to 10 paraphrases")
        expected = sorted(re.findall(r"\{[a-z]+\}", GROUPS[key]))
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > 300:
                raise ValueError("Invalid paraphrase text")
            if sorted(re.findall(r"\{[a-z]+\}", value)) != expected:
                raise ValueError("Paraphrase changed required placeholders")
            if key == "hedged" and not any(w in value.lower() for w in ("maybe", "perhaps", "might", "consider", "tentative", "undecided", "not decided", "not sure", "possibly")):
                raise ValueError("Hedge was lost")


def acquire(output: Path = OUT) -> dict:
    from llm.spark.client import SparkClient, CONTEXT_TOKENS
    from llm.spark.spend import calculate_cost, Usage, SpendCapExceeded

    if output.exists():
        result = json.loads(output.read_text())
        validate_templates(result["templates"])
        return result
    load_credentials(ROOT.parent / ".env")
    client = SparkClient(max_retries=0, timeout=180)
    # Existing client writes every reservation/settlement and compute.md itself.
    # Count this purpose across restarts, including any conservative unknown hold.
    with client.ledger.locked():
        latest = {}
        if client.ledger.path.exists():
            for line in client.ledger.path.read_text().splitlines():
                row = json.loads(line)
                if row.get("purpose", "").startswith(PURPOSE):
                    latest[row["reservation_id"]] = Decimal(row["cost_usd"])
        reserve = calculate_cost(Usage(CONTEXT_TOKENS - MAX_OUTPUT, MAX_OUTPUT))
        if sum(latest.values(), Decimal(0)) + reserve > Decimal("5"):
            raise SpendCapExceeded("Reader data allocation would exceed $5")
        prompt = (
            "Write training paraphrase families for a synthetic ship-planner instruction reader. "
            "Return only a JSON object mapping the eight keys below to arrays of exactly 6 distinct sentences. "
            "Preserve intent and every brace placeholder exactly once. Keep sentences short, varied and natural, "
            "including radio, terminal and planner phrasing. Do not add extra constraints. "
            "Close/deck/hold/DG/hatch groups must be firm restrictions. Loosening must grant permission to reopen, "
            "never a closure. Hedged must remain explicitly undecided and never issue a firm instruction. "
            "For hatch say one destination/discharge port per physical cover across both hold and deck, "
            "not separate ports above and below. Do not emit actual numbers instead of placeholders.\n"
            + json.dumps(GROUPS, separators=(",", ":"))
        )
        response = client.complete(prompt, purpose=PURPOSE, max_output_tokens=MAX_OUTPUT,
                                   reasoning_effort="low")
    groups = json.loads(response.text)
    validate_templates(groups)
    result = {"source": "Muse Spark", "model": "muse-spark-1.3",
              "created_utc": datetime.now(timezone.utc).isoformat(),
              "request_id": response.request_id, "cost_usd": str(response.cost_usd),
              "usage": vars(response.usage), "purpose": PURPOSE,
              "templates": groups, "prompt": prompt,
              "review": "Placeholder and hedge checks automated; semantic review required before generation."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    result = acquire(args.output)
    print(json.dumps({"file": str(args.output.relative_to(ROOT)),
                      "families": sum(map(len, result["templates"].values())),
                      "cost_usd": result["cost_usd"]}))
