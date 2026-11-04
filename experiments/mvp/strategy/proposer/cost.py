"""D185 offline Spark collection accounting; never initiates collection or calls.

The entire immutable response pool is charged, including unconsumed responses
and malformed layout texts. Checked-evaluation budgets do not bound API spend.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

INPUT_USD_PER_MILLION = Decimal("1.25")
OUTPUT_USD_PER_MILLION = Decimal("4.25")
SPARK_LIMIT_USD = Decimal("2")


def _amount(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return amount


def _tokens(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def validate_spark_source_cost(source: bytes, *, expected_sha256: str | None = None,
                               limit_usd: float = 2.0) -> dict:
    """Validate complete source spend, failing closed on missing cost evidence.

    A Spark row needs ``usage`` with either (a) ``input_tokens`` and
    ``output_tokens`` plus ``output_tokens_includes_reasoning: true``, or
    (b) ``billed_cost_usd`` plus ``billed_cost_includes_all_tokens: true``.
    The latter is actual billed cost and takes precedence over rate estimates.
    Any supplied numeric cost/token fields are validated even when not chosen.
    Non-Spark rows do not incur Spark spend. Empty or Spark-free pools fail.

    This function validates one complete collection pool, not a filtered run or
    resumed evaluation log. Callers must bind all Spark arms to that same frozen
    pool and include this metadata in comparisons; separate pools need aggregate
    accounting before they can satisfy the D185 *total* budget.
    """
    if not isinstance(source, bytes):
        raise TypeError("Spark cost validation requires the exact source bytes")
    source_sha256 = hashlib.sha256(source).hexdigest()
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError("Spark source sha256 does not match frozen collection")
    limit = _amount(limit_usd, "limit_usd")
    if limit <= 0 or limit > SPARK_LIMIT_USD:
        raise ValueError("Spark limit must be positive and at most D185's $2 total cap")
    total, rows, spark_rows, methods = Decimal(0), 0, 0, set()
    try:
        lines = source.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Spark source must be UTF-8 JSONL") from exc
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, parse_float=Decimal, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid collection JSON at line {lineno}: {exc}") from exc
        if not isinstance(row, dict) or row.get("arm") not in {"spark", "glimmer"}:
            raise ValueError(f"unknown collection arm at line {lineno}")
        rows += 1
        if row["arm"] != "spark":
            continue
        spark_rows += 1
        usage = row.get("usage")
        if not isinstance(usage, dict):
            raise ValueError(f"Spark usage required at line {lineno}")
        for key in ("input_tokens", "output_tokens"):
            if key in usage:
                _tokens(usage[key], f"usage.{key} at line {lineno}")
        if "billed_cost_usd" in usage:
            cost = _amount(usage["billed_cost_usd"], f"usage.billed_cost_usd at line {lineno}")
            if usage.get("billed_cost_includes_all_tokens") is not True:
                raise ValueError("actual Spark billed cost must include all tokens, including reasoning")
            method = "actual-billed-cost"
        else:
            input_tokens = _tokens(usage.get("input_tokens"), "usage.input_tokens")
            output_tokens = _tokens(usage.get("output_tokens"), "usage.output_tokens")
            if usage.get("output_tokens_includes_reasoning") is not True:
                raise ValueError("Spark output token usage must explicitly include reasoning")
            cost = (input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
            method = "recorded-token-rates"
        total += cost
        methods.add(method)
    if not spark_rows:
        raise ValueError("Spark collection pool contains no Spark responses")
    if total >= limit:
        raise ValueError(f"Spark total ${total} must be strictly under ${limit}; includes all source responses")
    return {"format": "D185-spark-total-cost-v1", "source_sha256": source_sha256,
            "total_cost_usd": float(total), "total_cost_usd_decimal": str(total),
            "total_rows": rows, "spark_rows": spark_rows,
            "method": next(iter(methods)) if len(methods) == 1 else "mixed-actual-and-recorded-rates",
            "strict_limit_usd": float(limit), "budget_rule": "Spark total strictly under $2",
            "accounting_scope": "entire frozen source including unused, invalid and wasted proposals",
            "input_usd_per_million": float(INPUT_USD_PER_MILLION),
            "output_usd_per_million": float(OUTPUT_USD_PER_MILLION),
            "calls_made": False}


def preparation_budget_plan(token_counts: dict, *, max_proposals: int = 256,
                            output_token_cap: int = 4096) -> dict:
    """Estimate future collection cost; a proxy estimate never authorizes spend.

    Each map is charged max_proposals prompts and output_token_cap output tokens
    per response, unlike a checked-evaluation target. Full-ship synthetic sizing
    rows are excluded. A future collector must enforce its actual aggregate cost
    before collecting each next response and must include hidden reasoning in its
    configured total output-token cap. No collector is implemented here.
    """
    if type(max_proposals) is not int or max_proposals < 1:
        raise ValueError("max_proposals must be a positive integer")
    if type(output_token_cap) is not int or output_token_cap < 1:
        raise ValueError("output_token_cap must be a positive integer")
    maps = []
    for row in token_counts.get("measurements", []):
        if row.get("size") not in {"small", "medium"}:
            continue
        tokens = _tokens(row["prompt"]["glimmer_raw_tokens"], "prompt.glimmer_raw_tokens")
        one = (tokens * INPUT_USD_PER_MILLION + output_token_cap * OUTPUT_USD_PER_MILLION) / 1_000_000
        maps.append({"map_id": row["map_id"], "size": row["size"], "proxy_input_tokens": tokens,
                     "usd_per_proposal_with_output_cap": float(one),
                     "usd_at_max_proposals": float(one * max_proposals)})
    if not maps:
        raise ValueError("budget sizing requires small/medium prompt measurements")
    if len({row["map_id"] for row in maps}) != len(maps):
        raise ValueError("duplicate map in budget sizing")
    per_round = sum((Decimal(str(row["usd_per_proposal_with_output_cap"])) for row in maps), Decimal(0))
    total = per_round * max_proposals
    return {"format": "D185-spark-preparation-budget-v1", "max_proposals_per_map": max_proposals,
            "output_token_cap_including_reasoning": output_token_cap,
            "strict_total_limit_usd": 2.0, "estimated_usd_at_max_proposals": float(total),
            "estimated_under_cap": total < SPARK_LIMIT_USD,
            "estimated_usd_by_proposal_count": {str(k): float(per_round * k) for k in (1, 4, 16, max_proposals)},
            "maps": maps, "calls_made": False,
            "input_usd_per_million": float(INPUT_USD_PER_MILLION),
            "output_usd_per_million": float(OUTPUT_USD_PER_MILLION),
            "estimate_caveat": "Glimmer raw prompt-token proxy; Spark tokenizer/chat wrappers unmeasured. Output cap must include reasoning. No guarantee of attaining k checked evaluations within $2; invalid proposals still cost.",
            "required_future_collection_guard": "Before each response, reserve its maximum all-token cost against the shared collection's remaining strict-under-$2 budget; stop rather than exceed the cap. Then retain usage or all-token billed cost for every response in one immutable pool."}


def _record_cost(record: dict) -> tuple[str, Decimal]:
    if not isinstance(record, dict) or record.get("format") != "D185-spark-total-cost-v1":
        raise ValueError("registered pool requires validated D185 Spark cost metadata")
    sha = record.get("source_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("registered Spark pool requires full source SHA256")
    decimal_value = record.get("total_cost_usd_decimal")
    if not isinstance(decimal_value, str):
        raise ValueError("registered Spark pool requires exact decimal cost")
    try:
        amount = Decimal(decimal_value)
    except InvalidOperation as exc:
        raise ValueError("invalid registered Spark pool cost") from exc
    if not amount.is_finite() or amount < 0 or amount >= SPARK_LIMIT_USD:
        raise ValueError("registered Spark pool must cost strictly under $2")
    if _amount(record.get("total_cost_usd"), "total_cost_usd") != Decimal(str(float(amount))):
        raise ValueError("registered Spark pool cost fields disagree")
    return sha, amount


def _registry_total(registry: dict) -> Decimal:
    if (not isinstance(registry, dict)
            or registry.get("format") != "D185-spark-campaign-budget-v1"
            or not isinstance(registry.get("pools"), dict)):
        raise ValueError("invalid D185 Spark campaign registry")
    total = Decimal(0)
    for sha, record in registry["pools"].items():
        actual_sha, amount = _record_cost(record)
        if actual_sha != sha:
            raise ValueError("Spark registry pool SHA key does not match source")
        total += amount
    if total >= SPARK_LIMIT_USD:
        raise ValueError("Spark campaign total must be strictly under $2 across all unique pools")
    return total


def assert_registry_covers(cost_record: dict, registry_record: dict) -> dict:
    """Recompute campaign spend and verify a source-bound registry snapshot.

    This attests imported pools registered here. It makes no claim about spend
    outside these recorded collections and does not perform any model calls.
    """
    sha, amount = _record_cost(cost_record)
    total = _registry_total(registry_record)
    if registry_record["pools"].get(sha) != cost_record:
        raise ValueError("Spark campaign registry does not cover this exact immutable pool cost")
    if (registry_record.get("campaign_total_cost_usd_decimal") != str(total)
            or registry_record.get("campaign_total_cost_usd") != float(total)):
        raise ValueError("Spark campaign registry total does not match complete pool accounting")
    return {"source_sha256": sha, "source_cost_usd": float(amount),
            "campaign_total_cost_usd": float(total), "campaign_total_cost_usd_decimal": str(total),
            "unique_pools": len(registry_record["pools"]),
            "budget_rule": "Spark total strictly under $2 across all registered unique pools",
            "accounting_scope": "all imported responses in registered immutable pools; external unregistered spend is unknown"}


def register_spark_pool(cost_record: dict, registry_path) -> dict:
    """Atomically register a whole pool once, enforcing aggregate D185 spend.

    Only actual offline execution should register; preparation/plan-only checks
    use validate_spark_source_cost without mutating this campaign registry.
    An owned exclusive-create lock prevents concurrent oversubscription. A
    pre-existing lock fails closed immediately; it is never removed by us.
    """
    from copy import deepcopy
    import os
    from pathlib import Path
    import tempfile

    sha, _ = _record_cost(cost_record)
    path = Path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError("Spark campaign registry is locked; retry after its owner finishes") from exc
    temporary = None
    try:
        os.write(fd, json.dumps({"pid": os.getpid(), "registry": str(path)}).encode())
        if path.exists():
            try:
                registry = json.loads(path.read_text(), object_pairs_hook=_unique_object)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValueError("invalid Spark campaign registry file") from exc
            _registry_total(registry)
        else:
            registry = {"format": "D185-spark-campaign-budget-v1", "pools": {}}
        existing = registry["pools"].get(sha)
        if existing is not None and existing != cost_record:
            raise ValueError("Spark cost metadata changed for the same immutable source SHA256")
        registry["pools"][sha] = deepcopy(cost_record)
        total = _registry_total(registry)
        registry.update(campaign_total_cost_usd=float(total), campaign_total_cost_usd_decimal=str(total),
                        budget_rule="Spark total strictly under $2 across all registered unique pools",
                        accounting_scope="entire imported response pools; external unregistered spend unknown",
                        registry_path=str(path.resolve()), calls_made=False)
        assert_registry_covers(cost_record, registry)
        temporary_fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(temporary_fd, "w") as stream:
            json.dump(registry, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return deepcopy(registry)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        os.close(fd)
        lock.unlink()
