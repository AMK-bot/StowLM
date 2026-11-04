"""Offline, authoritative Modal accounting built on task 1.8's SpendLedger.

This is admission against estimates, never a provider-enforced dollar ceiling.
Reserve the whole run cap, including explicit lifecycle/storage/egress headroom,
before any Modal lifecycle operation. Pending/failed/unknown invoices remain held
until an operator reconciles every charge component from final provider records.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, unquote
from uuid import uuid4
from typing import Mapping

from llm.spark.spend import SpendLedger, SpendError, SpendCapExceeded, _amount

_ROOT = Path(__file__).resolve().parents[3]
_COMPONENTS = frozenset({"gpu", "cpu", "memory", "lifecycle", "storage", "egress"})


class ModalCreditNotReady(RuntimeError):
    """Hackathon credit is not confirmed; no paid lifecycle may begin."""


def require_credit_ready() -> None:
    # Check afresh for each attempt, so revocation takes effect immediately.
    if os.environ.get("MODAL_CREDIT_READY") != "1":
        raise ModalCreditNotReady("Modal credit readiness is closed; confirm applied credit before MODAL_CREDIT_READY=1")


class ModalSpendLedger(SpendLedger):
    """Use 1.8's fsynced JSONL, cross-process locks and conservative holds.

    All Modal paths sharing compute.md adopt its authoritative binding. The same
    compute lock as Spark prevents one provider's row update losing the other's.
    All manual Modal charges conservatively count against the GRPO allocation.
    """

    def __init__(self, path: str | Path | None = None, compute_path: str | Path | None = None,
                 cap_usd: Decimal | None = None):
        super().__init__(path=path or _ROOT / "experiments/grpo/spend.jsonl", compute_path=compute_path,
                         cap_usd=Decimal("80") if cap_usd is None else cap_usd)

    def _resolve_budget(self) -> None:
        text = self._compute_text()
        # Allocation and project ceiling come from the same authoritative document.
        budgets = re.search(r"^## Budgets\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        row = re.search(r"^\|\s*Modal\s*\|\s*\$([\d.,]+)\s*\|[^\n]*?\|\s*~?\$([\d.,]+)\s+GRPO", budgets[1] if budgets else "", re.MULTILINE)
        if row is None:
            raise SpendError("compute.md must state Modal total and GRPO allocation")
        self.cap_usd = min(self.cap_usd, *(_amount(value.replace(",", "")) for value in row.groups()))
        start, end = self._spend_section(text)
        bindings = re.findall(r"modal-jsonl; ledger=([^;\s)]+); cap-usd=([^;\s)]+)", text[start:end])
        paths = {(self.compute_path.parent / unquote(path)).resolve() for path, _ in bindings}
        if len(paths) > 1:
            raise SpendError("Compute budget names multiple authoritative Modal ledgers")
        if paths:
            authoritative = paths.pop()
            if self.path != authoritative and self.path.exists() and self.path.stat().st_size:
                raise SpendError("Alternate Modal ledger contains records; reconcile before running")
            if not authoritative.exists():
                raise SpendError("Authoritative Modal ledger is missing")
            self.path = authoritative
            self.cap_usd = min(self.cap_usd, *(_amount(cap) for _, cap in bindings))

    def _manual_spend(self) -> Decimal:
        text = self._compute_text()
        start, end = self._spend_section(text)
        total = Decimal(0)
        for line in text[start:end].splitlines():
            columns = [value.strip() for value in line.strip().strip("|").split("|")]
            if len(columns) < 3 or columns[1].casefold() != "modal" or "modal-jsonl" in line:
                continue
            match = re.match(r"\$([\d.,]+)(?:\s|$)", columns[2])
            if match is None:
                raise SpendError("Unrecognized manual Modal amount; reconcile before running")
            # Metered cost stays charged even if historical free credits covered it.
            total += _amount(match[1].replace(",", ""))
        return total

    def summary_row(self) -> str:
        with self.locked():
            actual, held = self._totals(self._records())
            remaining = max(Decimal(0), self.cap_usd - self._manual_spend() - actual - held)
            relative = quote(os.path.relpath(self.path, self.compute_path.parent), safe="/.")
            date = datetime.now(timezone.utc).date().isoformat()
            return (f"| {date} | Modal | ${actual} | GRPO accounting (modal-jsonl; ledger={relative}; "
                    f"cap-usd={self.cap_usd}; conservative pending/unknown hold ${held}; "
                    f"estimated admission only, final itemized billing required) | ${remaining} |")

    def _sync_compute(self) -> None:
        text = self._compute_text()
        start, end = self._spend_section(text)
        lines = text[start:end].splitlines(keepends=True)
        managed = [i for i, line in enumerate(lines) if line.lstrip().startswith("|") and "GRPO accounting (modal-jsonl;" in line]
        row = self.summary_row() + "\n"
        if managed:
            lines[managed[0]] = row
            for index in reversed(managed[1:]):
                del lines[index]
        else:
            position = max((i + 1 for i, line in enumerate(lines) if line.lstrip().startswith("|")), default=0)
            if position and not lines[position - 1].endswith("\n"):
                lines[position - 1] += "\n"
            lines.insert(position, row)
        prefix = text[:start]
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        replacement = prefix + "".join(lines) + text[end:]
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

    def reserve(self, run_id: str, cap: Decimal, *, compute_estimate: Decimal,
                overhead_usd: Decimal) -> dict:
        require_credit_ready()
        cap, compute_estimate, overhead = _amount(cap), _amount(compute_estimate), _amount(overhead_usd)
        if not run_id or not all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in run_id):
            raise SpendError("run_id must be a lowercase slug")
        if overhead <= 0 or compute_estimate + overhead > cap:
            raise SpendError("run cap must cover compute estimate plus positive lifecycle/storage/egress headroom")
        with self.locked():
            self.check_capacity(cap)
            record = {"phase": "reservation", "reservation_id": uuid4().hex, "run_id": run_id,
                      "cost_usd": str(cap), "compute_estimate_usd": str(compute_estimate),
                      "overhead_usd": str(overhead), "status": "pending_final_provider_billing",
                      "date": datetime.now(timezone.utc).isoformat()}
            self.append(record)
            return record

    def reconcile(self, reservation_id: str, *, charges: Mapping[str, Decimal], billing_final: bool) -> None:
        """Offline operator action; zero is required for absent charge categories.

        Never infer free execution from success, failure, timeout or cancellation.
        Final measured amounts may exceed the estimate and must still be recorded.
        """
        if billing_final is not True or set(charges) != _COMPONENTS:
            raise SpendError("Final itemized gpu/cpu/memory/lifecycle/storage/egress billing is required")
        amounts = {key: _amount(value) for key, value in charges.items()}
        self.append({"phase": "settlement", "reservation_id": reservation_id,
                     "cost_usd": str(sum(amounts.values(), Decimal(0))), "cost_kind": "measured",
                     "charges_usd": {key: str(value) for key, value in amounts.items()},
                     "date": datetime.now(timezone.utc).isoformat()})


def main() -> None:
    """Reconcile a final itemized invoice offline; this never contacts Modal."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Offline final Modal invoice reconciliation")
    parser.add_argument("--reservation-id", required=True)
    parser.add_argument("--charges", type=Path, required=True,
                        help="JSON object with USD gpu/cpu/memory/lifecycle/storage/egress amounts")
    parser.add_argument("--billing-final", action="store_true",
                        help="operator confirms these are final provider charges, including storage retention")
    args = parser.parse_args()
    charges = json.loads(args.charges.read_text())
    if not isinstance(charges, dict):
        parser.error("--charges must contain an itemized JSON object")
    ledger = ModalSpendLedger()
    ledger.reconcile(args.reservation_id, charges=charges, billing_final=args.billing_final)
    print(ledger.summary_row())


if __name__ == "__main__":
    main()
