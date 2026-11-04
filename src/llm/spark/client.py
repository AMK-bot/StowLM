"""Muse Spark Standard client: bounded retries and durable, locked spend logging.

After the user authorizes a paid call, confirms the credit is available, and
exports MODEL_API_KEY and SPARK_CREDIT_READY=1, run from repo root::

    uv run python -m llm.spark.client --test-call

This sends a synthetic prompt, logs token usage/cost to
experiments/spark-spend/calls.jsonl, updates its designated docs/compute.md ledger
row, and prints that row.
To render the ledger without any network access::

    uv run python -m llm.spark.client --summary

No key is read from a file. Neither prompts, responses, HTTP error bodies nor
credentials are logged. CLI output contains accounting only. Default cap is the
$150 project budget, including prior manual Meta Model API ledger entries and
local spend; --spend-cap-usd sets a lower total-project ceiling.

A request reserves its worst-case uncached input plus bounded output cost before
sending. The documented 1,048,576-token context window bounds input; this avoids
an approximate tokenizer or an extra token-count API call. A small request may
therefore be refused with roughly $1.31 remaining. HTTP 4xx releases its reserve;
5xx, interrupted calls, timeouts and malformed responses retain a conservative
hold because billing is unknown. Holds are NOT measured spend. Inspect/reconcile
them against the provider before editing the ledger. Retries may duplicate billed
work; every attempt gets its own reservation. One lock covers the complete call.
The canonical compute_path binds clients to one authoritative JSONL ledger and
shared cap lock, even when they request alternate empty ledger paths. Distinct
compute files represent independent budgets.

The credit gate is closed by default and checked on each call. Only the exact
environment value SPARK_CREDIT_READY=1 opens it; credit_ready=True/False provides
an explicit per-client override. Tests must opt in only with a fake transport.
Incomplete responses settle their measured usage then raise SparkIncompleteError;
their partial output is never returned as a completed answer.

Public contract verified 2026-10-27 (no authenticated call):
https://dev.meta.ai/docs/quickstart
https://dev.meta.ai/docs/protocols/responses
https://dev.meta.ai/docs/pricing-rate-limits
https://dev.meta.ai/docs/error-handling
Standard rate limits are per team: 3,000 RPM and 4,000,000 TPM. HTTP 429 and
5xx retry with exponential jitter, respecting Retry-After. Other 4xx (including
400 validation, 401 auth, 402 billing, 403 access, 404 model, 413 size) do not.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import random
import time
import urllib.error
import urllib.request
from uuid import uuid4

from .spend import SpendError, SpendLedger, Usage, calculate_cost

MODEL = "muse-spark-1.3"
ENDPOINT = "https://api.meta.ai/v1/responses"
CONTEXT_TOKENS = 1_048_576
MAX_OUTPUT_TOKENS = 131_072
# Responses API reasoning.effort values (dev.meta.ai/docs/protocols/responses,
# read 29 Oct 2026); "none" is documented but rejected by Muse Spark (HTTP 400).
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


class SparkError(RuntimeError):
    """Safe-to-display client error; no provider error body is included."""


class SparkHTTPError(SparkError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Muse Spark HTTP {status_code}")


class SparkProtocolError(SparkError):
    """Response could not be safely interpreted or billed."""


class SparkCreditNotReady(SparkError):
    """The explicit credit-readiness gate is closed; no request was sent."""


class SparkIncompleteError(SparkProtocolError):
    """Incomplete response whose measured usage has already been recorded.

    Only accounting metadata and a fixed reason tag are exposed. Provider
    details and partial output may contain sensitive text and are discarded.
    """

    def __init__(self, reason: object, usage: Usage, cost_usd: Decimal, request_id: str):
        self.reason = reason if reason in ("max_output_tokens", "content_filter") else "unspecified"
        self.usage = usage
        self.cost_usd = cost_usd
        self.request_id = request_id
        super().__init__(f"Muse Spark response incomplete ({self.reason}); measured usage logged")


@dataclass(frozen=True)
class SparkResponse:
    text: str
    usage: Usage
    cost_usd: Decimal
    request_id: str


def _retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        delay = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            delay = (date - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, delay) if math.isfinite(delay) else 0.0


class SparkClient:
    def __init__(
        self, api_key: str | None = None, *, ledger_path: str | Path | None = None,
        compute_path: str | Path | None = None, spend_cap_usd: Decimal | str | None = None,
        timeout: float = 60, max_retries: int = 3, sleep=None, jitter=None,
        credit_ready: bool | None = None,
    ):
        self._key = api_key if api_key is not None else os.environ.get("MODEL_API_KEY", "")
        if not self._key or not self._key.strip() or any(c in self._key for c in "\r\n"):
            raise SparkError("Set MODEL_API_KEY in the environment before calling Muse Spark")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be an integer from 0 to 10")
        if credit_ready is not None and type(credit_ready) is not bool:
            raise ValueError("credit_ready must be a boolean or None")
        self._credit_ready = credit_ready
        kwargs = {}
        if ledger_path is not None:
            kwargs["path"] = ledger_path
        if compute_path is not None:
            kwargs["compute_path"] = compute_path
        self.ledger = SpendLedger(cap_usd=spend_cap_usd, **kwargs)
        self.timeout, self.max_retries = timeout, max_retries
        self._sleep = sleep or time.sleep
        self._jitter = jitter or random.random

    def _safe(self, value: str) -> str:
        return value.replace(self._key, "[REDACTED]")

    def complete(self, prompt: str, *, purpose: str, max_output_tokens: int = 128,
                 reasoning_effort: str | None = None) -> SparkResponse:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be nonempty text")
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 200:
            raise ValueError("purpose must be a nonempty tag of at most 200 characters")
        if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError("max_output_tokens must be an integer from 16 to 131072")
        if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"reasoning_effort must be one of {', '.join(REASONING_EFFORTS)} or None")
        ready = (os.environ.get("SPARK_CREDIT_READY") == "1"
                 if self._credit_ready is None else self._credit_ready)
        if not ready:
            raise SparkCreditNotReady("Muse Spark credit is on hold; confirm readiness before enabling calls")
        reservation = calculate_cost(Usage(CONTEXT_TOKENS - max_output_tokens, max_output_tokens, 0))
        body = {"model": MODEL, "input": prompt, "max_output_tokens": max_output_tokens,
                "stream": False, "store": False}
        if reasoning_effort is not None:
            # Omitted means the provider's default reasoning; reasoning tokens
            # still count against max_output_tokens either way.
            body["reasoning"] = {"effort": reasoning_effort}
        payload = json.dumps(body).encode("utf-8")
        with self.ledger.locked():
            for attempt in range(self.max_retries + 1):
                self.ledger.check_capacity(reservation)
                reservation_id = uuid4().hex
                base = {"time": datetime.now(timezone.utc).isoformat(), "model": MODEL,
                        "input_tokens": None, "output_tokens": None, "cached_tokens": None,
                        "purpose": self._safe(purpose), "request_id": None,
                        "reservation_id": reservation_id, "attempt": attempt + 1,
                        "reasoning_effort": reasoning_effort or "provider-default"}
                self.ledger.append({**base, "phase": "reservation", "cost_usd": str(reservation),
                                    "status": "pending", "cost_kind": "conservative_hold"})
                request = urllib.request.Request(ENDPOINT, data=payload, method="POST", headers={
                    "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
                    "Accept": "application/json"})
                retry_delay = 0.0
                error: SparkError
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        raw = response.read()
                        header_id = response.headers.get("x-request-id")
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    retry_delay = _retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                    request_id = exc.headers.get("x-request-id") if exc.headers else None
                    exc.close()
                    certain = 400 <= status < 500
                    self.ledger.append({**base, "phase": "settlement", "status": "http_error",
                        "http_status": status, "request_id": self._safe(request_id) if request_id else None,
                        "cost_usd": "0" if certain else str(reservation),
                        "cost_kind": "reported_rejection" if certain else "conservative_hold"})
                    error = SparkHTTPError(status)
                    if status != 429 and not 500 <= status < 600:
                        raise error from None
                except (urllib.error.URLError, TimeoutError, OSError):
                    self.ledger.append({**base, "phase": "settlement", "status": "transport_error",
                                        "cost_usd": str(reservation), "cost_kind": "conservative_hold"})
                    error = SparkError("Muse Spark transport failure; billing unknown")
                else:
                    if isinstance(header_id, str):
                        base["request_id"] = self._safe(header_id)
                    try:
                        data = json.loads(raw)
                        reported_model = data.get("model", MODEL)
                        if reported_model != MODEL:
                            raise ValueError("unexpected model")
                        usage_data = data["usage"]
                        usage = Usage(usage_data["input_tokens"], usage_data["output_tokens"],
                                      usage_data.get("input_tokens_details", {}).get("cached_tokens", 0))
                        cost = calculate_cost(usage)
                        if usage.input_tokens + usage.output_tokens > CONTEXT_TOKENS or usage.output_tokens > max_output_tokens:
                            raise ValueError("usage exceeds request bounds")
                        request_id = header_id or data["id"]
                        if not isinstance(request_id, str) or not request_id:
                            raise ValueError("missing request id")
                        text = "".join(part["text"] for item in data["output"] if item.get("type") == "message"
                                       for part in item["content"] if part.get("type") == "output_text")
                        if not isinstance(text, str):
                            raise ValueError("invalid output text")
                    except (ValueError, KeyError, TypeError, AttributeError, SpendError):
                        self.ledger.append({**base, "phase": "settlement", "status": "protocol_error",
                                            "cost_usd": str(reservation), "cost_kind": "conservative_hold"})
                        raise SparkProtocolError("Invalid Muse Spark response; billing unknown") from None
                    safe_id = self._safe(request_id)
                    response_status = data.get("status", "completed")
                    safe_status = response_status if response_status in ("completed", "incomplete", "failed", "cancelled") else "response_error"
                    self.ledger.append({**base, "phase": "settlement", "status": safe_status,
                        "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                        "cached_tokens": usage.cached_tokens, "request_id": safe_id,
                        "cost_usd": str(cost), "cost_kind": "measured"})
                    if response_status == "incomplete":
                        details = data.get("incomplete_details")
                        reason = details.get("reason") if isinstance(details, dict) else None
                        raise SparkIncompleteError(reason, usage, cost, safe_id)
                    if response_status != "completed":
                        raise SparkProtocolError("Muse Spark returned an unsuccessful response; usage logged")
                    return SparkResponse(text, usage, cost, safe_id)
                if attempt == self.max_retries:
                    raise error from None
                self._sleep(max(retry_delay, 0.5 * (2 ** attempt) + self._jitter()))
        raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test-call", action="store_true", help="Send one synthetic paid request (retries disabled)")
    mode.add_argument("--summary", action="store_true", help="Render ledger row without network or credentials")
    parser.add_argument("--spend-cap-usd", type=Decimal, default=None)
    args = parser.parse_args(argv)
    try:
        if args.summary:
            ledger = SpendLedger(cap_usd=args.spend_cap_usd)
        else:
            client = SparkClient(spend_cap_usd=args.spend_cap_usd, max_retries=0)
            result = client.complete("Reply with the word OK. This is a synthetic connectivity test.",
                                     purpose="harness-test", max_output_tokens=128)
            print(f"Logged {MODEL}: input={result.usage.input_tokens}, output={result.usage.output_tokens}, "
                  f"cached={result.usage.cached_tokens}, cost=${result.cost_usd}")
            ledger = client.ledger
        with ledger.locked():
            print(ledger.summary_row())
    except (SparkError, SpendError, ValueError, OSError):
        # Do not echo arbitrary exception strings: filesystem paths and provider
        # responses could contain a credential. Programmatic errors remain typed.
        print("Spark command failed; check environment, cap and spend ledger (billing may be unknown).")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
