"""Decimal pricing and durable local accounting for Muse Spark.

The JSONL file is authoritative for harness calls. Every append automatically
updates the managed ``spark-jsonl`` row in compute.md. That row binds a compute
budget to one JSONL path and a persistent ceiling; clients requesting another
empty path adopt the authoritative path. Other Meta Model API rows are additional
spend. Keep the JSONL file alongside its summary.
Reservations count against the cap until a settlement replaces them. An unknown
billing outcome must settle at its full reservation; this deliberately preserves
budget rather than pretending a timed-out request was free.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Iterator, Mapping, Any
from urllib.parse import quote, unquote


class SpendError(ValueError):
    """Accounting is invalid; refuse further paid calls."""


class SpendCapExceeded(SpendError):
    """A reservation would exceed the configured spend cap."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens, self.cached_tokens):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise SpendError("Token counts must be nonnegative integers")
        if self.cached_tokens > self.input_tokens:
            raise SpendError("Cached input exceeds total input tokens")


# USD per million tokens; cached input is a subset of total input.
PRICES = {"muse-spark-1.3": (Decimal("1.25"), Decimal("4.25"), Decimal("0.15"))}
_ROOT = Path(__file__).resolve().parents[3]


def calculate_cost(usage: Usage, model: str = "muse-spark-1.3") -> Decimal:
    try:
        input_price, output_price, cached_price = PRICES[model]
    except KeyError as exc:
        raise SpendError("Model has no verified price") from exc
    return (
        (usage.input_tokens - usage.cached_tokens) * input_price
        + usage.output_tokens * output_price
        + usage.cached_tokens * cached_price
    ) / Decimal(1_000_000)


def _amount(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise SpendError("Invalid USD amount in spend accounting") from exc
    if not result.is_finite() or result < 0:
        raise SpendError("USD amounts must be finite and nonnegative")
    return result


class SpendLedger:
    """Cross-process serialized spend ledger. cap_usd is the total API budget.

    Hold ``locked()`` across check, reservation, HTTP request and settlement.
    The compute budget lock is shared even when callers request different JSONL
    paths; the JSONL lock is also retained for existing cooperating tools.
    Individual operations also lock, allowing safe use outside that transaction.
    Missing compute.md fails closed; an explicitly supplied empty file is valid.
    """

    def __init__(self, path: str | Path | None = None,
                 compute_path: str | Path | None = None,
                 cap_usd: Decimal | None = None) -> None:
        self.path = (Path(path) if path is not None else _ROOT / "experiments/spark-spend/calls.jsonl").resolve()
        self.compute_path = (Path(compute_path) if compute_path is not None else _ROOT / "docs/compute.md").resolve()
        self.cap_usd = _amount(Decimal("150") if cap_usd is None else cap_usd)
        self._mutex = threading.RLock()
        self._depth = 0

    @contextmanager
    def locked(self) -> Iterator[SpendLedger]:
        with self._mutex:
            if self._depth:
                self._depth += 1
                try:
                    yield self
                finally:
                    self._depth -= 1
                return
            # Lock an inode separate from compute.md, which is atomically replaced.
            budget_lock = self.compute_path.with_suffix(self.compute_path.suffix + ".spark.lock")
            with budget_lock.open("a") as budget_handle:
                fcntl.flock(budget_handle.fileno(), fcntl.LOCK_EX)
                try:
                    self._resolve_budget()
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.with_suffix(self.path.suffix + ".lock").open("a") as handle:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                        self._depth = 1
                        try:
                            yield self
                        finally:
                            self._depth = 0
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    fcntl.flock(budget_handle.fileno(), fcntl.LOCK_UN)

    def _compute_text(self) -> str:
        try:
            return self.compute_path.read_text()
        except (OSError, UnicodeError) as exc:
            raise SpendError("Cannot read compute spend ledger") from exc

    @staticmethod
    def _spend_section(text: str) -> tuple[int, int]:
        match = re.search(r"^## Spend ledger\s*$", text, re.MULTILINE)
        if match is None:
            if text.strip():
                raise SpendError("compute.md lacks a Spend ledger section")
            return len(text), len(text)
        following = re.search(r"^## ", text[match.end():], re.MULTILINE)
        return match.end(), match.end() + following.start() if following else len(text)

    def _resolve_budget(self) -> None:
        text = self._compute_text()
        start, end = self._spend_section(text)
        bindings = re.findall(r"spark-jsonl; ledger=([^;\s)]+); cap-usd=([^;\s)]+)", text[start:end])
        paths = {(self.compute_path.parent / unquote(path)).resolve() for path, _ in bindings}
        if len(paths) > 1:
            raise SpendError("Compute budget names multiple authoritative Spark ledgers")
        if paths:
            authoritative = paths.pop()
            if self.path != authoritative and self.path.exists() and self.path.stat().st_size:
                raise SpendError("Alternate Spark ledger contains records; reconcile before calling")
            if not authoritative.exists():
                raise SpendError("Authoritative Spark ledger is missing")
            self.path = authoritative
            self.cap_usd = min(self.cap_usd, *(_amount(cap) for _, cap in bindings))

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        try:
            for line in self.path.read_text().splitlines():
                if not line.strip():
                    raise SpendError("Blank line in spend ledger")
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise SpendError("Spend record must be an object")
                records.append(record)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SpendError("Cannot read valid spend ledger") from exc
        return records

    @staticmethod
    def _totals(records: list[dict[str, Any]]) -> tuple[Decimal, Decimal]:
        actual = Decimal(0)
        conservative = Decimal(0)
        held: dict[str, Decimal] = {}
        settled: set[str] = set()
        for record in records:
            if "cost_usd" not in record:
                raise SpendError("Spend record lacks cost_usd")
            cost = _amount(record["cost_usd"])
            phase = record.get("phase")
            if phase is None:
                actual += cost
                continue
            reservation_id = record.get("reservation_id")
            if not isinstance(reservation_id, str) or not reservation_id:
                raise SpendError("Missing reservation identifier")
            if phase == "reservation":
                if reservation_id in held or reservation_id in settled:
                    raise SpendError("Duplicate spend reservation")
                held[reservation_id] = cost
            elif phase == "settlement":
                if reservation_id not in held:
                    raise SpendError("Settlement has no pending reservation")
                held.pop(reservation_id)
                settled.add(reservation_id)
                if record.get("cost_kind") == "conservative_hold":
                    conservative += cost
                else:
                    actual += cost
            else:
                raise SpendError("Unknown spend record phase")
        return actual, conservative + sum(held.values(), Decimal(0))

    def _manual_spend(self) -> Decimal:
        text = self._compute_text()
        # Only the Spend ledger section; the Budgets table is not expenditure.
        start, end = self._spend_section(text)
        total = Decimal(0)
        for line in text[start:end].splitlines():
            if not line.lstrip().startswith("|"):
                continue
            columns = [value.strip() for value in line.strip().strip("|").split("|")]
            if len(columns) < 3 or columns[1].casefold() != "meta model api":
                continue
            if "spark-jsonl" in line:
                continue
            amount = columns[2].replace("$", "").replace(",", "").strip()
            total += _amount(amount)
        return total

    def total_usd(self) -> Decimal:
        with self.locked():
            actual, held = self._totals(self._records())
            return self._manual_spend() + actual + held

    def remaining_usd(self) -> Decimal:
        with self.locked():
            return max(Decimal(0), self.cap_usd - self.total_usd())

    def check_capacity(self, reservation_usd: Decimal) -> None:
        with self.locked():
            reservation = _amount(reservation_usd)
            if self.total_usd() + reservation > self.cap_usd:
                raise SpendCapExceeded("Muse Spark spend cap would be exceeded")

    def append(self, record: Mapping[str, Any]) -> None:
        with self.locked():
            entry = dict(record)
            records = self._records()
            actual, held = self._totals([*records, entry])  # Validate before changing durable state.
            if entry.get("phase") != "settlement" and self._manual_spend() + actual + held > self.cap_usd:
                raise SpendCapExceeded("Muse Spark spend cap would be exceeded")
            try:
                payload = json.dumps(entry, separators=(",", ":"), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise SpendError("Spend record must be JSON serializable") from exc
            # Bind the shared budget before records can be written. If compute
            # persistence fails, no HTTP attempt may be sent. An empty durable
            # JSONL file lets future clients distinguish initial binding from loss.
            with self.path.open("a") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._sync_compute()
            with self.path.open("a") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sync_compute()

    def sync_compute(self) -> None:
        """Repair the managed compute summary from authoritative durable records."""
        with self.locked():
            if not self.path.exists():
                raise SpendError("Cannot summarize a missing authoritative Spark ledger")
            self._sync_compute()

    def _sync_compute(self) -> None:
        text = self._compute_text()
        start, end = self._spend_section(text)
        section = text[start:end]
        lines = section.splitlines(keepends=True)
        row = self.summary_row() + "\n"
        managed = [index for index, line in enumerate(lines)
                   if line.lstrip().startswith("|") and "Muse Spark harness (spark-jsonl" in line]
        if managed:
            lines[managed[0]] = row
            for index in reversed(managed[1:]):
                del lines[index]
        else:
            # Insert after the last table row, preserving every unrelated byte.
            position = max((index + 1 for index, line in enumerate(lines)
                            if line.lstrip().startswith("|")), default=0)
            if position and not lines[position - 1].endswith("\n"):
                lines[position - 1] += "\n"
            lines.insert(position, row)
        prefix = text[:start]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        replacement = prefix + "".join(lines) + text[end:]
        if not text.strip():
            replacement = "## Spend ledger\n" + replacement
        if replacement == text:
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=self.compute_path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, self.compute_path.stat().st_mode & 0o777)
                handle.write(replacement)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.compute_path)
            directory = os.open(self.compute_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def summary_row(self) -> str:
        with self.locked():
            actual, held = self._totals(self._records())
            remaining = max(Decimal(0), self.cap_usd - self._manual_spend() - actual - held)
            date = datetime.now(timezone.utc).date().isoformat()
            relative = quote(os.path.relpath(self.path, self.compute_path.parent), safe="/.")
            return (f"| {date} | Meta Model API | ${actual} | "
                    f"Muse Spark harness (spark-jsonl; ledger={relative}; cap-usd={self.cap_usd}; "
                    f"conservative pending/unknown hold ${held}) | "
                    f"${remaining} |")
