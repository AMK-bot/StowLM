"""Preflight pricing for Spark baseline runs, without contacting the provider.

A token budget multiplies local Glimmer token counts by the Standard rates from
``docs/compute.md``. Glimmer is a proxy tokenizer: this is an estimate, while
SparkClient's context-sized reservations remain the actual spending safeguard.
Source: experiments/format/README.md; llm.spark.spend.PRICES.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Iterator

from llm.spark.spend import SpendCapExceeded, SpendError, SpendLedger, Usage, _amount, calculate_cost


@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class _JobSpend:
    """Spend owned by one job (one SpendLedger instance), from ledger line positions.

    ``start`` is the record count when the job first entered an allocation;
    ``owned`` holds the line positions of records this job appended through the
    guarded ``append``; ``reservations`` the reservation ids it created.
    """

    def __init__(self, start: int, prior_usd: Decimal) -> None:
        self.start = start
        self.prior_usd = prior_usd
        self.owned: set[int] = set()
        self.reservations: set[str] = set()

    def used(self, records: list[dict[str, Any]]) -> Decimal:
        if len(records) < self.start or any(index >= len(records) for index in self.owned):
            raise SpendError("Spend ledger shrank during a job allocation; reconcile before calling")
        own: list[dict[str, Any]] = []
        foreign: list[dict[str, Any]] = []
        foreign_reservations: set[str] = set()
        for index, record in enumerate(records):
            phase = record.get("phase")
            reservation_id = record.get("reservation_id")
            if index in self.owned:
                if phase != "settlement" or reservation_id in self.reservations:
                    own.append(record)
                continue  # Settling a hold this job did not make never adds headroom.
            if index < self.start:
                continue
            # Foreign calls made after the start count like the job's own: a hold
            # while pending, its measured cost once settled. Settlements of holds
            # reserved before the start are ignored (they never add headroom).
            if phase == "reservation":
                foreign_reservations.add(reservation_id)
            if phase != "settlement" or reservation_id in foreign_reservations:
                foreign.append(record)
        actual, held = SpendLedger._totals(own)
        foreign_actual, foreign_held = SpendLedger._totals(foreign)
        return self.prior_usd + actual + held + foreign_actual + foreign_held


def _job(ledger: SpendLedger) -> _JobSpend:
    if "_spark_allocation_start_usd" in ledger.__dict__:
        raise SpendError("_spark_allocation_start_usd is no longer supported: job usage is now "
                         "job-owned records; pass allocation_budget(..., prior_job_usd=...) to resume")
    return ledger._spark_job


@contextmanager
def allocation_budget(ledger: SpendLedger, *,
                      allocation_usd: Decimal = Decimal("60"),
                      prior_job_usd: Decimal = Decimal("0")) -> Iterator[SpendLedger]:
    """Enforce an incremental job allocation without changing the project cap.

    Job identity is the ledger instance: reuse it for every request in a job.
    Its first entry records the ledger's record count (the job start); later
    and nested contexts keep that earliest start and take the tighter
    allocation. Job usage, which only grows except through the job's own
    settlements, is

    * ``prior_job_usd``: spend of this job in earlier processes (resume seed);
    * the canonical totals (measured settlements + unsettled holds, as in
      ``SpendLedger._totals``) over records this job appended through the
      guarded ``append``: its own direct records, its reservations, and
      settlements of its own reservations. An own reservation that never
      settles stays charged in full; an own settlement replaces it;
    * foreign records appended after the start, by the same canonical totals:
      direct records at cost, a foreign post-start reservation at its hold
      while pending and at its measured settlement afterwards, so concurrent
      jobs see each other's real spend. (Counting every settled foreign call at
      its ~$1.5 context-sized hold, as 1.0 of this fix did, exhausted a $15
      allocation after seven cheap calls by another executive, 29 Oct.) Usage
      never drops below the job's own spend plus the foreign spend actually
      incurred after the start.

    Settlements or releases of holds this job did not create (including holds
    that predate the job) are ignored, so they can never add headroom. The
    durable project ceiling, enforced by the original ``SpendLedger`` checks,
    still covers all other project spend. Resume: a new process cannot see the
    old instance's ownership, so pass the old job's measured + held spend as
    ``prior_job_usd`` (the legacy ``_spark_allocation_start_usd`` attribute
    fails closed). Guards run under the unchanged cross-process project lock,
    cover capacity checks and direct reservation appends (retries, unknown-
    billing holds) and are restored on exit. Settlements always remain
    writable. No ledger records or durable caps are changed by this context.
    """
    allocation = Decimal(str(allocation_usd))
    prior = Decimal(str(prior_job_usd))
    for value in (allocation, prior):
        if not value.is_finite() or value < 0:
            raise ValueError("allocation and prior job spend must be finite and nonnegative")
    with ledger.locked():
        if "_spark_job" not in ledger.__dict__ and "_spark_allocation_start_usd" not in ledger.__dict__:
            ledger._spark_job = _JobSpend(len(ledger._records()), prior)
        job = _job(ledger)
        job.prior_usd = max(job.prior_usd, prior)  # A reseed can never add headroom.
        previous_allocation = getattr(ledger, "_spark_active_allocation_usd", None)
        ledger._spark_active_allocation_usd = (
            allocation if previous_allocation is None else min(allocation, previous_allocation))
        allocation_limit = ledger._spark_active_allocation_usd
        original_check = ledger.check_capacity
        original_append = ledger.append
        # Preserve instance attributes exactly, including nested scoped guards.
        previous_check = ledger.__dict__.get("check_capacity")
        previous_append = ledger.__dict__.get("append")

        def check_capacity(reservation_usd):
            with ledger.locked():
                original_check(reservation_usd)
                if job.used(ledger._records()) + Decimal(str(reservation_usd)) > allocation_limit:
                    raise SpendCapExceeded("Muse Spark job allocation would be exceeded")

        def append(record):
            with ledger.locked():
                if record.get("phase") != "settlement":
                    check_capacity(record["cost_usd"])
                before = len(ledger._records())
                original_append(record)
                if before not in job.owned:  # Outer guards record the same line once.
                    job.owned.add(before)
                    if record.get("phase") == "reservation":
                        job.reservations.add(record["reservation_id"])

        ledger.check_capacity = check_capacity
        ledger.append = append
        try:
            yield ledger
        finally:
            for name, previous in (("check_capacity", previous_check), ("append", previous_append)):
                if previous is None:
                    delattr(ledger, name)
                else:
                    setattr(ledger, name, previous)
            if previous_allocation is None:
                del ledger._spark_active_allocation_usd
            else:
                ledger._spark_active_allocation_usd = previous_allocation


def estimate_instance(instance, tokenizer: Any = None, *, max_output_tokens: int | None = None,
                      appendix: bool = True) -> TokenEstimate:
    """Price one actual API prompt and its entire requested output allowance."""
    from llm.spark.client import MAX_OUTPUT_TOKENS
    from llm.spark.planner import build_prompt, output_cap
    from llm.stowlm.format import load_tokenizer

    max_output_tokens = output_cap(instance, max_output_tokens)

    if type(max_output_tokens) is not int or not 16 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError("invalid Spark output allowance")
    tokenizer = load_tokenizer() if tokenizer is None else tokenizer
    encoded = tokenizer.encode(build_prompt(instance, appendix=appendix), add_special_tokens=False)
    count = len(encoded.ids if hasattr(encoded, "ids") else encoded)
    # APPROXIMATION: Glimmer's tokenizer proxies Spark's unavailable tokenizer.
    # Full output allowance includes reasoning and formatting, not just actions.
    return TokenEstimate(count, max_output_tokens, calculate_cost(Usage(count, max_output_tokens)))


def guard_run(estimates: Iterable[TokenEstimate], ledger: SpendLedger, *,
              allocation_usd: Decimal = Decimal("60")) -> Decimal:
    """Refuse before the first HTTP request if the complete run cannot fit.

    Outside allocation_budget, conservatively charge all prior project spend
    and unknown holds to the baseline allocation. Within it, price against the
    job allocation minus the job's own usage (see allocation_budget). This estimate
    is not a reservation. The scoped guard enforces conservative reservations
    and retries atomically, while the durable project ceiling stays unchanged.
    """
    allocation = Decimal(str(allocation_usd))
    if not allocation.is_finite() or allocation < 0:
        raise ValueError("allocation must be finite and nonnegative")
    estimates = list(estimates)
    if any(not e.cost_usd.is_finite() or e.cost_usd < 0 for e in estimates):
        raise ValueError("estimate costs must be finite and nonnegative")
    cost = sum((e.cost_usd for e in estimates), Decimal(0))
    with ledger.locked():
        active_allocation = getattr(ledger, "_spark_active_allocation_usd", None)
        if active_allocation is None:
            used = ledger.total_usd()
        else:
            allocation = min(allocation, active_allocation)
            used = _job(ledger).used(ledger._records())
        remaining = max(Decimal(0), min(allocation - used, ledger.remaining_usd()))
        if cost > remaining:
            raise SpendCapExceeded(f"Spark baseline estimate ${cost} exceeds remaining allocation ${remaining}")
    return remaining
