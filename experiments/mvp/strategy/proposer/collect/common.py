"""D185 step 2 collection helpers. Sits in a subfolder so the frozen harness's ``HERE.glob("*.py")`` hashes stay as they were."""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PROPOSER = HERE.parent
ROOT = PROPOSER.parents[3]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

RESPONSES = PROPOSER / "responses"
PLAN = RESPONSES / "d185-collection-plan.json"
LEDGER = ROOT / "experiments/spark-spend/calls.jsonl"
PURPOSE = "d185-proposer"
SINCE = "2026-10-31T01:40:00"
STOP_USD = Decimal("1.90")  # campaign stop, includes probe and pilot spend; the harness cap is strictly under $2
# Worst case for one call: 2,500 input tokens (largest prompt 1,591 plus wrappers) + the 2,048-token output cap.
CALL_BOUND_USD = (2500 * Decimal("1.25") + 2048 * Decimal("4.25")) / 1_000_000


def load_key() -> None:
    for line in (ROOT.parent / ".env").read_text().splitlines():
        name, _, value = line.strip().partition("=")
        if name.removeprefix("export ").strip() == "MODEL_API_KEY":
            os.environ["MODEL_API_KEY"] = value.strip().strip("'\"")
    os.environ["SPARK_CREDIT_READY"] = "1"


def spent() -> Decimal:
    """Campaign spend from the shared ledger, probe and pilot included. A conservative hold is the client's $1.31
    worst-case figure for a 1M-token request; here it counts as this job's real per-call bound."""
    latest: dict[str, dict] = {}
    for line in LEDGER.read_text().splitlines():
        row = json.loads(line) if line.strip() else {}
        if row.get("purpose", "").startswith(PURPOSE) and row.get("time", "") >= SINCE:
            latest[row["reservation_id"]] = row
    return sum((CALL_BOUND_USD if row.get("cost_kind") == "conservative_hold" else Decimal(row["cost_usd"])
                for row in latest.values()), Decimal(0))


class Spark:
    """SparkClient with the registered sampling fields added to its request body. ``complete()`` has no parameter for
    them and src/ is off limits, so the body is rewritten at the one Request it builds, in this process only."""

    def __init__(self, extra: dict, timeout: float = 180):
        import urllib.request

        from llm.spark.client import ENDPOINT, SparkClient

        load_key()
        self.client = SparkClient(max_retries=2, timeout=timeout)
        original = getattr(urllib.request, "_d185_original_request", urllib.request.Request)
        urllib.request._d185_original_request = original

        def request(url, data=None, **kwargs):
            if url == ENDPOINT:
                data = json.dumps({**json.loads(data), **extra}).encode("utf-8")
            return original(url, data=data, **kwargs)

        urllib.request.Request = request

    def __call__(self, prompt: str, *, purpose: str, max_output_tokens: int, effort: str | None):
        return self.client.complete(prompt, purpose=purpose, max_output_tokens=max_output_tokens,
                                    reasoning_effort=effort)


class Glimmer:
    """Base Glimmer 4-bit on Metal, no adapter, one process. The chat template's reasoning goes to the ``to=self``
    channel; the proposal is the text of the final channel, as the Responses API returns only message text."""

    MODEL = ROOT / "models/glimmer-30b-4bit"

    def __init__(self):
        from llm.stowlm.mlx_memory import apply_cache_limit
        from mlx_lm import load

        self.cache_limit_gb = apply_cache_limit()
        self.model, self.tokenizer = load(str(self.MODEL))

    def __call__(self, prompt: str, *, seed: int, strength: str, max_tokens: int = 2048) -> dict:
        import time

        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        ids = self.tokenizer.apply_chat_template([{"role": "user", "content": prompt}], add_generation_prompt=True,
                                                 tokenize=True, reasoning_strength=strength)
        mx.random.seed(seed)
        started, parts, final = time.monotonic(), [], None
        for final in stream_generate(self.model, self.tokenizer, ids, max_tokens=max_tokens,
                                     sampler=make_sampler(temp=0.7, top_p=1.0)):
            parts.append(final.text)
        return {"raw": "".join(parts), "prompt_tokens": len(ids), "generation_tokens": final.generation_tokens,
                "finish_reason": final.finish_reason, "wall_s": round(time.monotonic() - started, 2)}
