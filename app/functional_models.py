"""Live model stages for fresh synthetic calls, with explicit availability gates.

Importing this module performs no inference, credential loading or HTTP requests.
Test callbacks attach at the existing intake, transcription and reader seams.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import threading
from time import perf_counter
import wave
import urllib.error
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
_ADOPTION_RECORD = ROOT / "experiments/mvp/reader/v3/results-final/final-adoption-d266.json"
_DEFAULT_MODEL_DIR = ROOT / "models/glimmer-30b-4bit"
_READER_STATES: dict = {}
_READER_LOCK = threading.RLock()
_DECODE = "schema mask (v3 decoder)"
_MERGED_INTAKE_SCHEMA = "stowlm.functional-intake-merge/1"
_GENERATOR_INTAKE_SCHEMA = "stowlm.functional-generator-merge/1"
_GENERATOR_CHOICE = "planner chose the generator's record (not read by a model)"
_DOCUMENT_ROLES = frozenset({
    "booking", "shipping_instructions", "vgm", "dg_declaration", "gate_slip",
    "reefer_list", "oog_list", "amendment", "correspondence", "unspecified",
})


class Unavailable(RuntimeError):
    """A model stage cannot run; the reason is safe for the page to display."""

    def __init__(self, stage: str, reason: str):
        self.stage, self.reason = stage, reason
        super().__init__(reason)


class ModelError(RuntimeError):
    """A live model attempt failed; only safe facts belong in its reason."""

    def __init__(self, stage: str, reason: str):
        self.stage, self.reason = stage, reason
        super().__init__(reason)


class AnswerConversionError(ModelError):
    """Muse Spark answered one booking, but turning that answer into load-list rows raised.

    Only that booking's reading is lost: the worker marks it failed with this reason and keeps the other bookings, so the
    planner can retry it. The original exception stays in the worker log; only its type name is public.
    """

    retry_booking = True


def _in_answer_conversion(error) -> bool:
    """True when the exception was raised while intake turned a received answer into rows (the frozen intake's
    finish step), not while asking the model. Decided from the traceback's code objects, never from message text."""
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_name in ("finish", "_finish") and "/pipeline/intake/" in code.co_filename.replace("\\", "/"):
            return True
        tb = tb.tb_next
    return False


from llm.spark.spend import SpendLedger as _SharedSpendLedger
from dataclasses import dataclass
from decimal import Decimal
from contextlib import contextmanager

_FUNCTIONAL_SPEND_ROOT = ROOT / "experiments/mvp/ui/functional/spend"


def _spend_launch_id(launch_id=None) -> str:
    import re
    from uuid import uuid4
    from llm.spark.spend import SpendError

    if launch_id is None:
        launch_id = os.environ.setdefault("STOWLM_FUNCTIONAL_LAUNCH_ID", uuid4().hex)
    if not isinstance(launch_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", launch_id):
        raise SpendError("The Functional app spend launch id is invalid")
    return launch_id


def _launch_directory(launch_id=None):
    from llm.spark.spend import SpendError

    launch_id = _spend_launch_id(launch_id)
    root = Path(_FUNCTIONAL_SPEND_ROOT).resolve()
    directory = (root / launch_id).resolve()
    if not directory.is_relative_to(root):
        raise SpendError("The Functional app spend directory is invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return launch_id, directory


@contextmanager
def _grant_lock(directory):
    """Held only for grant/configuration updates, never during a model call."""
    import fcntl

    with (directory / "grants.lock").open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _save_budget(directory, budget):
    import tempfile
    from llm.spark.spend import SpendError

    temporary = None
    try:
        payload = json.dumps(budget, separators=(",", ":"), sort_keys=True, allow_nan=False)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / "budget.json")
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except (OSError, TypeError, ValueError):
        raise SpendError("Cannot save the Functional app spend budget") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_budget(directory, launch_id):
    from llm.spark.spend import SpendError, _amount

    try:
        budget = json.loads((directory / "budget.json").read_text())
        if (budget.get("schema") != "stowlm.functional-budget/1"
                or budget.get("launch_id") != launch_id or budget.get("ledger") != "calls.jsonl"
                or type(budget.get("generation", 0)) is not int or budget.get("generation", 0) < 0
                or not isinstance(budget.get("grants", {}), dict)):
            raise ValueError
        _amount(budget["cap_usd"])
        if not (directory / "calls.jsonl").is_file():
            raise ValueError
        return budget
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        raise SpendError("The Functional app spend budget is missing or invalid") from None


def _ensure_budget(directory, launch_id, cap_usd=None):
    """Initialize under the short grant lock; existing authorization wins."""
    from llm.spark.spend import SpendError, _amount

    if (directory / "budget.json").exists():
        return _read_budget(directory, launch_id)
    legacy = directory / "calls.jsonl"
    if legacy.exists() and legacy.stat().st_size:
        raise SpendError("The Functional app spend budget is missing for existing records")
    if any((directory / "bookings").glob("*.jsonl")):
        raise SpendError("The Functional app spend budget is missing for existing booking records")
    cap = _amount(os.environ.get("STOWLM_FUNCTIONAL_CAP_USD", "10") if cap_usd is None else cap_usd)
    with legacy.open("a") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    budget = {"schema": "stowlm.functional-budget/1", "launch_id": launch_id,
              "ledger": "calls.jsonl", "cap_usd": str(cap), "generation": 0, "grants": {}}
    _save_budget(directory, budget)
    return budget


def _snapshot_records(path, *, partial_tail=False):
    from llm.spark.spend import SpendError

    try:
        payload = path.read_bytes()
        if payload and not payload.endswith(b"\n"):
            if not partial_tail:
                raise ValueError
            payload = payload.rsplit(b"\n", 1)[0] + b"\n" if b"\n" in payload else b""
        records = [json.loads(line) for line in payload.splitlines()]
        if any(not isinstance(record, dict) for record in records):
            raise ValueError
        return records
    except (OSError, UnicodeError, ValueError, TypeError):
        raise SpendError("The Functional app spend records are missing or invalid") from None


@dataclass(frozen=True)
class Grant(dict):
    id: str
    launch_id: str
    ledger_path: Path
    cap_usd: Decimal

    def __post_init__(self):
        dict.__init__(self, id=self.id, launch_id=self.launch_id,
                      ledger_path=str(self.ledger_path), cap_usd=str(self.cap_usd))


def _same_grant(left, right):
    # dict supplies its own __ne__; compare the immutable attributes explicitly
    # as well as the mutable compatibility mapping before trusting either view.
    return (type(left) is Grant and type(right) is Grant
            and (left.id, left.launch_id, left.ledger_path, left.cap_usd)
            == (right.id, right.launch_id, right.ledger_path, right.cap_usd)
            and dict(left) == dict(right))


def _grant_from_entry(directory, launch_id, grant_id, entry):
    import re
    from llm.spark.spend import SpendError, _amount

    try:
        if not isinstance(grant_id, str) or not re.fullmatch(r"[0-9a-f]{32}", grant_id):
            raise ValueError
        if entry.get("state") not in ("active", "released"):
            raise ValueError
        relative = Path(entry["ledger"])
        if relative.is_absolute() or len(relative.parts) != 2 or relative.parts[0] != "bookings":
            raise ValueError
        path = (directory / relative).resolve()
        if not path.is_relative_to((directory / "bookings").resolve()) or path.suffix != ".jsonl":
            raise ValueError
        amount = _amount(entry["cap_usd"])
        if amount <= 0 or not path.is_file():
            raise ValueError
        return Grant(grant_id, launch_id, path, amount)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise SpendError("The Functional app booking grant is invalid") from None


def _snapshot_totals(directory, launch_id, budget):
    """Active allocations cover in-flight/partial writes without double counting."""
    from llm.spark.spend import SpendError

    records = _snapshot_records(directory / "calls.jsonl")
    spent, held = _SharedSpendLedger._totals(records)
    outstanding = Decimal(0)
    for grant_id, entry in budget.get("grants", {}).items():
        grant = _grant_from_entry(directory, launch_id, grant_id, entry)
        booking = _snapshot_records(grant.ledger_path, partial_tail=entry["state"] == "active")
        actual, pending = _SharedSpendLedger._totals(booking)
        spent += actual
        held += pending
        records.extend(booking)
        if entry["state"] == "active":
            outstanding += max(Decimal(0), grant.cap_usd - actual - pending)
    registered = {entry.get("ledger") for entry in budget.get("grants", {}).values()}
    # A grant creates and fsyncs its empty ledger before publishing the registry.
    # No client can use it yet. Ignore that harmless creation window, but never
    # ignore unregistered files containing accounting records.
    if any(str(path.relative_to(directory)) not in registered and path.stat().st_size
           for path in (directory / "bookings").glob("*.jsonl")):
        raise SpendError("The Functional app has unregistered booking spend records")
    return spent, held, outstanding, records


class FunctionalSpendLedger(_SharedSpendLedger):
    """Legacy ledger access; paid calls use an independent granted ledger."""

    def __init__(self, *, launch_id=None, cap_usd=None):
        from llm.spark.spend import _amount

        self.launch_id, directory = _launch_directory(launch_id)
        with _grant_lock(directory):
            budget = _ensure_budget(directory, self.launch_id, cap_usd)
        super().__init__(path=directory / "calls.jsonl", compute_path=directory / "budget.json",
                         cap_usd=_amount(budget["cap_usd"]))

    def _resolve_budget(self):
        from llm.spark.spend import _amount

        self.cap_usd = _amount(_read_budget(self.path.parent, self.launch_id)["cap_usd"])

    def _records(self):
        budget = _read_budget(self.path.parent, self.launch_id)
        return _snapshot_totals(self.path.parent, self.launch_id, budget)[3]

    def append(self, record):
        from llm.spark.spend import SpendCapExceeded, _amount

        with self.locked():
            with _grant_lock(self.path.parent):
                budget = _read_budget(self.path.parent, self.launch_id)
                spent, held, grants, _ = _snapshot_totals(self.path.parent, self.launch_id, budget)
                local = _snapshot_records(self.path)
                before = sum(self._totals(local), Decimal(0))
                after = sum(self._totals([*local, dict(record)]), Decimal(0))
                if record.get("phase") != "settlement" and spent + held + grants + after - before > _amount(budget["cap_usd"]):
                    raise SpendCapExceeded("The Functional app launch spend cap would be exceeded")
                super().append(record)

    def _manual_spend(self):
        return Decimal(0)

    def _sync_compute(self):
        return None


class _GrantedSpendLedger(_SharedSpendLedger):
    """The frozen client may hold only this request's locks during HTTP."""

    def __init__(self, grant):
        self.grant = grant
        self.launch_id = grant.launch_id
        super().__init__(path=grant.ledger_path,
                         compute_path=grant.ledger_path.with_suffix(".budget.json"), cap_usd=grant.cap_usd)

    def _resolve_budget(self):
        self.cap_usd = self.grant.cap_usd

    def _manual_spend(self):
        return Decimal(0)

    def _sync_compute(self):
        return None

    def append(self, record):
        from llm.spark.spend import SpendError

        with self.locked():
            directory = self.path.parent.parent
            budget = _read_budget(directory, self.launch_id)
            entry = budget.get("grants", {}).get(self.grant.id)
            if not isinstance(entry, dict) or entry.get("state") != "active":
                raise SpendError("The Functional app booking grant has been released")
            registered = _grant_from_entry(directory, self.launch_id, self.grant.id, entry)
            if not _same_grant(registered, self.grant):
                raise SpendError("The Functional app booking grant does not match its ledger")
            super().append(record)


def get_grant(grant_id, *, launch_id=None) -> Grant:
    from llm.spark.spend import SpendError

    launch_id, directory = _launch_directory(launch_id)
    budget = _read_budget(directory, launch_id)
    entry = budget.get("grants", {}).get(grant_id)
    if not isinstance(entry, dict) or entry.get("state") != "active":
        raise SpendError("The Functional app booking grant is not active")
    return _grant_from_entry(directory, launch_id, grant_id, entry)


def functional_ledger(*, grant=None, launch_id=None, cap_usd=None):
    from llm.spark.spend import SpendError

    if grant is not None:
        if not isinstance(grant, Grant):
            raise SpendError("The Functional app booking grant is invalid")
        registered = get_grant(grant.id, launch_id=grant.launch_id)
        if not _same_grant(registered, grant):
            raise SpendError("The Functional app booking grant is invalid")
        return _GrantedSpendLedger(grant)
    return FunctionalSpendLedger(launch_id=launch_id, cap_usd=cap_usd)


def get_grant_for_path(ledger_path, *, cap_usd=None) -> Grant:
    from llm.spark.spend import SpendError, _amount

    try:
        path = Path(ledger_path).resolve()
        root = Path(_FUNCTIONAL_SPEND_ROOT).resolve()
        if (not path.is_relative_to(root) or path.parent.name != "bookings"
                or path.parent.parent.parent != root):
            raise ValueError
        directory = path.parent.parent
        launch_id = _spend_launch_id(directory.name)
        budget = _read_budget(directory, launch_id)
        for grant_id, entry in budget.get("grants", {}).items():
            if isinstance(entry, dict) and entry.get("state") == "active":
                grant = _grant_from_entry(directory, launch_id, grant_id, entry)
                if grant.ledger_path == path:
                    if cap_usd is not None and _amount(cap_usd) != grant.cap_usd:
                        raise ValueError
                    return grant
    except (OSError, TypeError, ValueError):
        raise SpendError("The Functional app booking ledger path or cap is invalid") from None
    raise SpendError("The Functional app booking ledger is not active")


def grant_call(cap_usd=None, *, launch_id=None, call_id=None, booking_ref=None, hold_usd=None, cap=None,
               refusal=None):
    from uuid import uuid4
    from llm.spark.spend import _amount

    launch_id, directory = _launch_directory(launch_id)
    if refusal is not None:
        if not isinstance(refusal, dict):
            raise ValueError("The booking refusal details must be a dictionary")
        refusal.clear()
    if cap is not None:
        if hold_usd is not None and _amount(hold_usd) != _amount(cap):
            raise ValueError("Supply one Functional app booking allocation")
        hold_usd = cap
    requested = _intake_hold_usd() if hold_usd is None else _amount(hold_usd)
    if requested <= 0:
        raise ValueError("The Functional app booking allocation must be positive")
    with _grant_lock(directory):
        budget = _ensure_budget(directory, launch_id, cap_usd)
        spent, held, outstanding, _ = _snapshot_totals(directory, launch_id, budget)
        ceiling = _amount(budget["cap_usd"])
        if cap_usd is not None:
            ceiling = min(ceiling, _amount(cap_usd))
        remaining = max(Decimal(0), ceiling - spent - held - outstanding)
        if requested > remaining:
            if refusal is not None:
                refusal.update(spent_usd=str(spent), holds_usd=str(held), grants_usd=str(outstanding),
                               hold_usd=str(requested), cap_usd=str(ceiling))
            return None
        # Use only spare fractional room: an exact N-hold cap still admits N.
        margin = min(Decimal("0.000001"), remaining % requested) if hold_usd is None else Decimal(0)
        allocation = requested + margin
        grant_id = uuid4().hex
        reference = str(booking_ref or call_id or "request")
        label = hashlib.sha256(reference.encode()).hexdigest()[:12]
        relative = Path("bookings") / f"{label}-{grant_id}.jsonl"
        path = directory / relative
        path.parent.mkdir(mode=0o700, exist_ok=True)
        with path.open("x") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        entry = {"ledger": relative.as_posix(), "cap_usd": str(allocation), "state": "active"}
        budget.setdefault("grants", {})[grant_id] = entry
        budget["generation"] = budget.get("generation", 0) + 1
        _save_budget(directory, budget)
        return Grant(grant_id, launch_id, path.resolve(), allocation)


def release(grant):
    from llm.spark.spend import SpendError

    if not isinstance(grant, Grant):
        raise SpendError("The Functional app booking grant is invalid")
    launch_id, directory = _launch_directory(grant.launch_id)
    # Lock only this request first. A release never holds the launch lock while
    # waiting for its HTTP call, and no append can race the released marker.
    ledger = _GrantedSpendLedger(grant)
    with ledger.locked():
        with _grant_lock(directory):
            budget = _read_budget(directory, launch_id)
            entry = budget.get("grants", {}).get(grant.id)
            if not isinstance(entry, dict):
                raise SpendError("The Functional app booking grant is invalid")
            registered = _grant_from_entry(directory, launch_id, grant.id, entry)
            if not _same_grant(registered, grant):
                raise SpendError("The Functional app booking grant is invalid")
            if entry["state"] == "released":
                return
            _SharedSpendLedger._totals(_snapshot_records(grant.ledger_path))
            entry["state"] = "released"
            budget["generation"] = budget.get("generation", 0) + 1
            _save_budget(directory, budget)


def _intake_hold_usd():
    """Read the frozen intake's token bound without executing a model call."""
    import ast
    import inspect
    import textwrap
    from experiments.mvp.pipeline.intake import interpretation, spark_reader
    from llm.spark.spend import SpendError

    # The frozen intake exposes its bound only as a keyword literal. Read that
    # source rather than duplicating either its bound or the client's prices.
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(interpretation.read)))
        limits = [ast.literal_eval(keyword.value)
                  for call in ast.walk(tree) if isinstance(call, ast.Call)
                  and isinstance(call.func, ast.Attribute) and call.func.attr == "complete_documents"
                  for keyword in call.keywords if keyword.arg == "max_output_tokens"]
        if (not limits or any(type(limit) is not int or not 16 <= limit <= min(
                spark_reader.MAX_OUTPUT_TOKENS, spark_reader.CONTEXT_TOKENS) for limit in limits)
                or len(set(limits)) != 1):
            raise ValueError
        limit = limits[0]
        hold = spark_reader.calculate_cost(spark_reader.Usage(spark_reader.CONTEXT_TOKENS - limit, limit))
        if not hold.is_finite() or hold <= 0:
            raise ValueError
        return hold
    except (OSError, TypeError, ValueError, SyntaxError):
        raise SpendError("The frozen Spark intake reservation could not be determined") from None


def _snapshot_status(directory, launch_id):
    from llm.spark.spend import SpendError, _amount

    # Registry replacement is atomic. Re-read its generation after scanning so
    # a concurrent grant/release cannot produce a mixed accounting snapshot.
    for _ in range(3):
        budget = _read_budget(directory, launch_id)
        try:
            spent, held, grants, _ = _snapshot_totals(directory, launch_id, budget)
        except SpendError:
            # A new empty booking may precede its atomic registry replacement.
            # Retry a bounded number of times; persistent corruption fails shut.
            continue
        if budget != _read_budget(directory, launch_id):
            continue
        cap = _amount(budget["cap_usd"])
        committed = spent + held + grants
        hold_per_call = _intake_hold_usd()
        remaining = max(Decimal(0), cap - committed)
        return {"available": True, "reason": None, "launch_id": launch_id,
                "cap_usd": float(cap), "spent_usd": float(spent), "held_usd": float(held),
                "holds_usd": float(held), "grants_usd": float(grants), "hold_usd_per_call": float(hold_per_call),
                "room_for_calls": int(remaining // hold_per_call),
                "committed_usd": float(committed), "remaining_usd": float(remaining)}
    raise SpendError("The Functional app spend snapshot changed; retry status")


def _spend_view(ledger: FunctionalSpendLedger) -> dict:
    _, directory = _launch_directory(ledger.launch_id)
    return _snapshot_status(directory, ledger.launch_id)


def spend_status(*, launch_id=None, cap_usd=None) -> dict:
    from llm.spark.spend import SpendError

    try:
        launch_id, directory = _launch_directory(launch_id)
        if not (directory / "budget.json").exists():
            with _grant_lock(directory):
                _ensure_budget(directory, launch_id, cap_usd)
        return _snapshot_status(directory, launch_id)
    except (SpendError, OSError):
        return {"available": False, "reason": "The Functional app spend ledger cannot be read; paid calls are stopped",
                "launch_id": None, "cap_usd": None, "spent_usd": None, "held_usd": None,
                "holds_usd": None, "grants_usd": None, "hold_usd_per_call": None, "room_for_calls": None,
                "committed_usd": None, "remaining_usd": None}


def configure_spend(*, launch_id=None, cap_usd=10.0) -> dict:
    """Apply an explicitly authorized cap under the shared launch budget lock."""
    from llm.spark.spend import _amount

    cap = _amount(cap_usd)
    launch_id, directory = _launch_directory(launch_id)
    with _grant_lock(directory):
        budget = _ensure_budget(directory, launch_id, cap)
        budget["cap_usd"] = str(cap)
        budget["generation"] = budget.get("generation", 0) + 1
        _save_budget(directory, budget)
    return _snapshot_status(directory, launch_id)


def _usage_cost_note(value, *, scope="this response") -> str:
    try:
        cost = float(value)
    except (TypeError, ValueError, OverflowError):
        return " Usage cost is unknown; see spent and held in the API key panel."
    if not math.isfinite(cost) or cost < 0:
        return " Usage cost is unknown; see spent and held in the API key panel."
    return f" Reported usage cost for {scope}: ${cost:.8f}; see spent and held in the API key panel."


def intake_cap_error(booking_ref, *, spent_usd, hold_usd, cap_usd, other_bookings_read=None,
                     holds_usd=0, grants_usd=0) -> ModelError:
    """Explain a booking refused before sending, using the scheduler's facts.

    grant_call(refusal=details) supplies an atomic budget snapshot. Only the
    worker knows how many successful booking reads it persisted for this call.
    """
    from llm.spark.spend import _amount

    spent, hold, cap = (_amount(value) for value in (spent_usd, hold_usd, cap_usd))
    held, grants = (_amount(value) for value in (holds_usd, grants_usd))
    if spent + held + grants + hold <= cap:
        raise ValueError("The supplied booking costs do not exceed the cap")
    if other_bookings_read is not None and (type(other_bookings_read) is not int or other_bookings_read < 0):
        raise ValueError("The count of retained booking reads must be a nonnegative integer")
    label = str(booking_ref) if booking_ref is not None else "(unassigned)"
    amount = f"${spent:.2f} spent"
    if held:
        amount += f" plus ${held:f} held"
    if grants:
        amount += f" plus ${grants:f} reserved for other calls"
    reason = (f"Booking {label} wasn't sent: {amount} plus this call's worst-case hold of ${hold:f} "
              f"would pass your ${cap:.2f} cap. Nothing was charged for it. ")
    if other_bookings_read is not None:
        reason += (f"{other_bookings_read} other bookings were read and are kept for a retry; "
                   "nothing has been planned from a partial reading. ")
    reason += "Raise the cap in the API key panel to read the rest."
    _key_safe_view({"reason": reason}, "spark")
    return ModelError("spark", reason)


def _schema_error(metadata) -> ModelError:
    metadata = metadata if isinstance(metadata, dict) else {}
    attempts = metadata.get("attempts")
    count = len(attempts) if isinstance(attempts, list) else 0
    detail = f" after {count} attempt{'s' if count != 1 else ''}" if count else ""
    return ModelError(
        "spark", "Muse Spark's answer failed intake schema validation" + detail
        + "; nothing from it was used." + _usage_cost_note(metadata.get("cost_usd"), scope="these attempts"),
    )


def _has_timeout(error) -> bool:
    """Inspect exception types only: transport messages can contain credentials."""
    seen = set()
    for _ in range(8):
        if error is None or id(error) in seen:
            return False
        seen.add(id(error))
        if isinstance(error, TimeoutError):
            return True
        if isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
            return True
        error = error.__cause__ or error.__context__
    return False


def _spark_error(error) -> ModelError:
    from llm.spark.client import SparkHTTPError, SparkIncompleteError, SparkProtocolError
    from llm.spark.spend import SpendCapExceeded, SpendError

    if isinstance(error, SparkIncompleteError):
        if error.reason == "max_output_tokens":
            tokens = getattr(error.usage, "output_tokens", None)
            limit = f" ({tokens} tokens)" if type(tokens) is int and tokens >= 0 else " (token count unknown)"
            reason = "Muse Spark's answer was cut off at its output limit" + limit
        elif error.reason == "content_filter":
            reason = "Muse Spark's answer was stopped by its content filter"
        else:
            reason = "Muse Spark returned an incomplete answer; its stopping reason is unknown"
        return ModelError("spark", reason + "; nothing from it was used." + _usage_cost_note(error.cost_usd))
    if isinstance(error, (SparkHTTPError, urllib.error.HTTPError)):
        code = error.status_code if isinstance(error, SparkHTTPError) else error.code
        status = f" HTTP {code}" if type(code) is int and 100 <= code <= 599 else " HTTP error"
        return ModelError("spark", "Muse Spark returned" + status + "; nothing from it was used; see spent and held in the API key panel.")
    if isinstance(error, SpendCapExceeded):
        return ModelError("spark", "Muse Spark's next request wasn't sent because its worst-case hold would exceed the available spending cap. Earlier requests may already have been charged; see spent and held in the API key panel.")
    if isinstance(error, SpendError):
        return ModelError("spark", "Muse Spark's spend ledger prevented the call from completing; see spent and held in the API key panel.")
    if _has_timeout(error):
        return ModelError("spark", "Muse Spark's request timed out; nothing from it was used. Billing is unknown; see spent and held in the API key panel.")
    if isinstance(error, SparkProtocolError):
        return ModelError("spark", "Muse Spark returned a protocol error; nothing from it was used. Billing details are unknown here; see spent and held in the API key panel.")
    return ModelError("spark", "Muse Spark's transport or request failed; nothing from it was used. The failure details and billing status are unknown; see spent and held in the API key panel.")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _adoption() -> tuple[dict, str | None]:
    try:
        adopted = json.loads(Path(_ADOPTION_RECORD).read_text())["adopted"]
        if not isinstance(adopted["path"], str) or not isinstance(adopted["adapter_sha256"], str):
            raise ValueError
        if len(adopted["adapter_sha256"]) != 64:
            raise ValueError
        int(adopted["adapter_sha256"], 16)
        return adopted, None
    except (OSError, ValueError, KeyError, TypeError):
        return {}, "The adopted reader record is missing or invalid"


def _adapter_path(adapter_dir, adopted: dict) -> Path | None:
    value = adapter_dir if adapter_dir is not None else adopted.get("path")
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _state_key(adapter: Path, model: Path, sha: str) -> tuple:
    return str(model.resolve()), str(adapter.resolve()), sha


def _reader_setup_reason(reason: str) -> str:
    guide = Path("hf-reader-bundle/README.md")
    source_guide = Path("experiments/mvp/pipeline/repo/hf-reader-bundle/README.md")
    if not (ROOT / guide).is_file() and (ROOT / source_guide).is_file():
        guide = source_guide
    if reason == "mlx_lm isn't installed":
        return (f"{reason}. On Apple silicon, run uv sync --python 3.12 --locked; "
                f"follow {guide} for the local reader setup.")
    return (f"{reason}. Follow {guide} for Glimmer base conversion and adopted adapter setup, "
            "then start with --reader-adapter models/stowlm-reader. "
            "Separately, run uv run python -m stowage.data_setup for the full checker's hull data; "
            "that command does not install model weights.")


def _reader_availability(adapter_dir=None, model_dir=None) -> dict:
    adopted, adoption_error = _adoption()
    adapter = _adapter_path(adapter_dir, adopted)
    model = Path(model_dir) if model_dir is not None else _DEFAULT_MODEL_DIR
    result = {"available": False, "reason": None, "who": "StowLM Reader",
              "where": "fine-tuned Muse Glimmer 30B (LoRA), on this laptop (MLX)",
              "model": "muse-glimmer-30b", "adapter_path": str(adapter) if adapter else None,
              "adapter_sha256": None, "adopted": False, "decode": _DECODE, "loaded": False}
    reason = adoption_error if adapter_dir is None else None
    if reason is None:
        try:
            if adapter is None or not (adapter / "adapters.safetensors").is_file():
                reason = f"no adapters.safetensors in {adapter}"
            else:
                sha = result["adapter_sha256"] = _sha(adapter / "adapters.safetensors")
                result["adopted"] = sha == adopted.get("adapter_sha256")
                expected_dir = _adapter_path(None, adopted)
                if expected_dir is not None and adapter.resolve() == expected_dir.resolve() and not result["adopted"]:
                    reason = "Reader adapter SHA256 does not match the adopted reader record"
                elif not (adapter / "adapter_config.json").is_file():
                    reason = f"no adapter_config.json in {adapter}"
        except OSError:
            reason = "The reader adapter files cannot be read"
    if reason is None:
        if not model.is_dir() or not (model / "config.json").is_file():
            reason = f"Reader base weights or config.json are missing in {model}"
        elif not any(model.glob("*.safetensors")):
            reason = f"Reader base weights are missing in {model}"
        elif not (model / "tokenizer_config.json").is_file() or not any(
                (model / name).is_file() for name in ("tokenizer.json", "tokenizer.model")):
            reason = f"Reader tokenizer files are missing in {model}"
        else:
            index = model / "model.safetensors.index.json"
            if index.is_file():
                try:
                    shards = set(json.loads(index.read_text())["weight_map"].values())
                    if not shards or any(not (model / shard).is_file() for shard in shards):
                        reason = f"Reader base weight shards are missing in {model}"
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    reason = f"Reader base weight index is invalid in {model}"
    if reason is None:
        try:
            installed = importlib.util.find_spec("mlx_lm") is not None
        except (ImportError, ValueError):
            installed = False
        if not installed:
            reason = "mlx_lm isn't installed"
    if adapter is not None and result["adapter_sha256"]:
        with _READER_LOCK:
            state = _READER_STATES.get(_state_key(adapter, model, result["adapter_sha256"]))
            result["loaded"] = state is not None and "model" in state
    result.update(available=reason is None,
                  reason=_reader_setup_reason(reason) if reason is not None else None)
    return result


def _paid_consent() -> bool:
    return (os.environ.get("STOWLM_ACCEPT_PAID_CALLS") == "1"
            or os.environ.get("SPARK_CREDIT_READY") == "1")


def _cloud_reason(key_provider=None) -> str | None:
    """Use only presence and consent; a provider never exposes its key here."""
    if key_provider is None:
        present = bool(os.environ.get("MODEL_API_KEY"))
        accepted = _paid_consent()
    else:
        try:
            summary = key_provider if isinstance(key_provider, dict) else key_provider.summary()
        except Exception:
            raise ValueError("The key provider status cannot be read") from None
        if not isinstance(summary, dict):
            raise ValueError("The key provider must return a safe status summary")
        present = summary.get("present") is True
        accepted = summary.get("paid_calls_accepted") is True
    if not present:
        return ("No Meta Model API key: enter yours in the app, or put MODEL_API_KEY in .env "
                "(see .env.example).")
    if not accepted:
        return ("Paid calls aren't accepted yet: accept them in the app, "
                "or set STOWLM_ACCEPT_PAID_CALLS=1.")
    return None


def availability(*, key_provider=None, adapter_dir=None, model_dir=None) -> dict:
    """Inspect safe credential status, files and MLX installation without loading it."""
    reason = _cloud_reason(key_provider)
    return {
        "spark": {"available": reason is None, "reason": reason, "who": "Muse Spark",
                  "where": "Meta Model API, cloud", "model": "muse-spark-1.3"},
        "voice": {"available": reason is None, "reason": reason, "who": "Muse Voice Transcribe",
                  "where": "Meta Model API, cloud", "model": "muse-voice-transcribe-1.0",
                  "formats": ["wav"], "audio_requirements": "Mono PCM16 WAV at 16 or 24 kHz, 0–60 seconds"},
        "reader": _reader_availability(adapter_dir, model_dir),
    }


def _require(stage: str) -> None:
    status = availability()[stage]
    if not status["available"]:
        raise Unavailable(stage, status["reason"])


def _key_safe_view(value: dict, stage: str, *, extra_key=None) -> dict:
    """Refuse credential echoes rather than persisting a changed model receipt."""
    keys = {key for key in (os.environ.get("MODEL_API_KEY"), extra_key) if isinstance(key, str) and key}
    if not keys:
        return value
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, str):
            if any(key in item for key in keys):
                raise ModelError(stage, "The model result contained credential text; nothing from it was used")
            # Receipt notes and provider output are JSON strings inside JSON.
            # Inspect their decoded values too, including escaped credentials.
            if item.startswith(("{", "[", '"')):
                try:
                    decoded = json.loads(item)
                except (ValueError, TypeError):
                    continue
                if decoded != item:
                    pending.append(decoded)
    return value


def _intake_view(result, wall_s: float) -> dict:
    from experiments.mvp.pipeline.contract.schema import sha256_json

    raw = result.model_dump(mode="json")
    records = raw["records"]
    if not records or any(record["mode"] != "live" for record in records):
        raise ModelError("spark", "Muse Spark intake did not return a live reading")
    if raw["status"] == "rejected":
        metadata = None
        for record in reversed(records):
            try:
                note = json.loads(record["notes"])
            except (ValueError, TypeError):
                continue
            if isinstance(note, dict) and isinstance(note.get("schema_rejection"), dict):
                metadata = note["schema_rejection"]
                break
        raise _schema_error(metadata)
    rows = raw["rows"]
    questions = []
    for question in raw["questions"]:
        index = question["row"]
        box = rows[index].get("id") if isinstance(index, int) and 0 <= index < len(rows) else None
        item = {"id": question["id"], "text": question["prompt"], "field": question["field"], "box": box}
        if question["candidates"]:
            item["choices"] = question["candidates"]
        questions.append(item)
    findings = []
    for record in reversed(records):
        try:
            note = json.loads(record["notes"])
        except (ValueError, TypeError):
            continue
        if isinstance(note, dict) and isinstance(note.get("findings"), list):
            findings = note["findings"]
            break
    return _key_safe_view({"mode": "live", "rows": rows, "questions": questions,
            "conflicts": [item for item in findings if isinstance(item, dict)
                          and ("conflict" in str(item.get("code", "")) or "contradict" in str(item.get("code", "")))],
            "complete": raw["status"] == "complete", "boxes": len(rows), "model": "muse-spark-1.3",
            "wall_s": wall_s, "cost_usd": sum(float(record["cost_usd"]) for record in records),
            "receipt_sha256": sha256_json(raw), "raw": raw}, "spark")


def _booking_ref(raw: dict) -> str | None:
    """Find host booking metadata in the receipt, never in model proposals."""
    if raw.get("schema") == _GENERATOR_INTAKE_SCHEMA:
        return _booking_ref(raw["source_intake"]["raw"])
    for record in reversed(raw["records"]):
        try:
            note = json.loads(record["notes"])
        except (TypeError, ValueError):
            continue
        context = note.get("intake_context") if isinstance(note, dict) else None
        documents = context.get("documents") if isinstance(context, dict) else None
        if not isinstance(documents, list):
            continue
        refs = {item["booking_ref"] for item in documents if isinstance(item, dict)
                and item.get("role") == "booking"
                and isinstance(item.get("booking_ref"), str) and item["booking_ref"]}
        if len(refs) > 1:
            raise ValueError("Each merged intake view must contain papers for only one booking")
        return next(iter(refs), None)
    return None


def _merge_intake_bookings(bookings: list[dict]) -> dict:
    from experiments.mvp.pipeline.contract.schema import sha256_json

    if not isinstance(bookings, list) or not bookings:
        raise ValueError("Supply at least one booking intake view")
    children, summaries, questions, conflicts, routes = [], [], [], [], {}
    occurrences = {}
    booking_ids = set()
    all_complete = True
    for booking in bookings:
        if not isinstance(booking, dict) or not isinstance(booking.get("view"), dict):
            raise ValueError("Each booking needs its intake view")
        identity = booking.get("id")
        if not isinstance(identity, str) or not identity or identity in booking_ids:
            raise ValueError("Booking ids must be nonempty and unique")
        booking_ids.add(identity)
        view = booking["view"]
        raw = view.get("raw")
        if not isinstance(raw, dict) or raw.get("schema") == _MERGED_INTAKE_SCHEMA:
            raise ValueError("Merge individual booking intake views, not another merged view")
        if (raw.get("schema") == _GENERATOR_INTAKE_SCHEMA
                and raw.get("source_intake", {}).get("raw", {}).get("schema") == _MERGED_INTAKE_SCHEMA):
            raise ValueError("Merge individual booking intake views, not another merged view")
        wall = view.get("wall_s")
        if isinstance(wall, bool) or not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall < 0:
            raise ValueError("Each booking needs a finite, nonnegative processing time")
        # Rebuild projections and accounting from the full pipeline receipt.
        child = _restore_intake_view(view)
        ref = _booking_ref(child["raw"])
        children.append({"id": identity, "booking_ref": ref, "view": child})
        summaries.append({"id": identity, "booking_ref": ref,
                          **{key: child[key] for key in ("boxes", "complete", "wall_s", "cost_usd", "receipt_sha256")}})
        all_complete = all_complete and child["complete"] and not child["questions"]
        local_ids = set()
        prefix = f"{quote(ref or identity, safe='')}::{identity}::"
        for question in child["questions"]:
            local_id = question["id"]
            if local_id in local_ids:
                raise ValueError("Question ids must be unique within each booking")
            local_ids.add(local_id)
            public_id = prefix + quote(local_id, safe="")
            questions.append({**deepcopy(question), "id": public_id})
            routes[public_id] = {"booking": identity, "question": local_id}
        conflicts.extend({**deepcopy(conflict), "booking": identity, "booking_ref": ref}
                         for conflict in child["conflicts"])
        for row in child["rows"]:
            box = row.get("id")
            if not isinstance(box, str) or not box.strip():
                raise ValueError("Every intake row needs a nonempty container id")
            occurrences.setdefault(box, []).append((identity, row))
    rows = []
    for box, entries in occurrences.items():
        if len(entries) == 1:
            rows.append(deepcopy(entries[0][1]))
            continue
        ids = list(dict.fromkeys(identity for identity, _ in entries))
        message = (f"Container {box} appears more than once in the booking readings. "
                   "Correct the booking documents and read them again; no row was selected.")
        questions.append({"id": "merge::duplicate-box::" + quote(box, safe=""),
                          "text": message, "field": "_duplicate", "box": box})
        conflicts.append({"code": "duplicate_box_id", "box": box,
                          "bookings": ids, "message": message})
        all_complete = False
    raw = {"schema": _MERGED_INTAKE_SCHEMA, "bookings": children, "question_routes": routes}
    choices = [deepcopy(choice) for child in children
               for choice in child["view"].get("generator_choices", [])]
    if choices:
        rows = [{**row, "origin": row.get("origin", "spark")} for row in rows]
    view = {"mode": "live", "rows": rows, "questions": questions, "conflicts": conflicts,
            "complete": bool(all_complete and not questions), "boxes": len(rows), "model": "muse-spark-1.3",
            "wall_s": sum(item["wall_s"] for item in summaries),
            "cost_usd": sum(item["cost_usd"] for item in summaries),
            "receipt_sha256": sha256_json(raw), "bookings": summaries, "raw": raw}
    if choices:
        view["generator_choices"] = choices
    return view


def merge_intake_views(views: list[dict]) -> dict:
    """Combine booking receipts, withholding every duplicate container row.

    wall_s is the sum of booking processing durations, including later answers.
    It is not elapsed time for concurrent work; the worker measures that outside
    this function. Every child receipt and its records survive JSON persistence.
    """
    if not isinstance(views, list) or not views:
        raise ValueError("Supply at least one booking intake view")
    return _merge_intake_bookings([{"id": f"b{index:04d}", "view": view}
                                   for index, view in enumerate(views, 1)])


def _restore_intake_view(view: dict) -> dict:
    """Rebuild app projections without altering their source model receipts."""
    from experiments.mvp.pipeline.contract.schema import IntakeResult

    if not isinstance(view, dict) or not isinstance(view.get("raw"), dict):
        raise ValueError("The intake validation receipt is missing")
    _key_safe_view(view, "spark")
    raw = view["raw"]
    if raw.get("schema") == _GENERATOR_INTAKE_SCHEMA:
        projection = _generator_intake_view(raw)
    elif raw.get("schema") == _MERGED_INTAKE_SCHEMA:
        projection = _merge_intake_bookings(raw.get("bookings"))
    else:
        wall = view.get("wall_s")
        if isinstance(wall, bool) or not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall < 0:
            raise ValueError("The intake needs a finite, nonnegative processing time")
        projection = _intake_view(IntakeResult.model_validate(deepcopy(raw)), float(wall))
    restored = deepcopy(view)
    restored.update(projection)
    return restored


def _raw_open_questions(view: dict) -> dict:
    raw = view["raw"]
    if raw.get("schema") == _GENERATOR_INTAKE_SCHEMA:
        questions = _raw_open_questions(raw["source_intake"])
        return {q["id"]: questions[q["id"]] for q in view["questions"]}
    if raw.get("schema") == _MERGED_INTAKE_SCHEMA:
        children = {b["id"]: _raw_open_questions(b["view"]) for b in raw["bookings"]}
        return {public: children[route["booking"]][route["question"]]
                for public, route in raw["question_routes"].items()}
    questions = {q["id"]: q for q in raw["questions"]}
    if len(questions) != len(raw["questions"]):
        raise ValueError("Question ids must be unique within each booking")
    return questions


def _generator_intake_view(raw: dict) -> dict:
    """Apply explicit generator choices; the frozen intake receipt stays intact."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    from stowage.model.cargo import Container, OutOfGauge

    source = _restore_intake_view(raw.get("source_intake"))
    replacements = raw.get("generator_rows")
    if not isinstance(replacements, list) or not replacements:
        raise ValueError("Choose at least one box with an equipment or OOG review question")
    chosen = {}
    for row in replacements:
        validated = Container.model_validate(row).model_dump(mode="json")
        if validated["id"] in chosen:
            raise ValueError("Choose each generator box only once")
        chosen[validated["id"]] = deepcopy(row)
    source_rows = {}
    for row in source["rows"]:
        source_rows.setdefault(row["id"], []).append(row)
    raw_questions = _raw_open_questions(source)
    closures = deepcopy(source.get("generator_choices", []))
    for box in chosen:
        if len(source_rows.get(box, [])) != 1:
            raise ValueError("A chosen box must occur exactly once in the intake rows")
        questions = [q for q in source["questions"] if q.get("box") == box]
        if not questions:
            raise ValueError("A chosen box must have an open equipment or OOG review question")
        for question in questions:
            original = raw_questions.get(question["id"], {})
            equipment = (original.get("reason") == "unsupported_equipment"
                         and original.get("field") == "iso_type"
                         and original.get("prompt") == f"{box}: {source_rows[box][0].get('iso_type')} needs equipment review; this intake supports general purpose and reefer groups.")
            oog = (original.get("reason") == "missing_oog_declaration"
                   and original.get("field") == "oog"
                   and original.get("prompt") == f"{box}: listed OOG cargo requires verified dimensions; an absent model reading cannot declare it in gauge.")
            if not (equipment or oog):
                raise ValueError("A chosen box has another kind of open question; answer it or correct the papers first")
            if oog:
                dimensions = next(row.get("oog") for row in replacements if row.get("id") == box)
                expected = set(OutOfGauge.model_fields)
                if (not isinstance(dimensions, dict) or set(dimensions) != expected
                        or not all(isinstance(n, (int, float)) and not isinstance(n, bool)
                                   and math.isfinite(n) and n >= 0 for n in dimensions.values())
                        or not any(n > 0 for n in dimensions.values())):
                    raise ValueError("The generator record must contain verified OOG dimensions")
        closures.append({"box": box, "origin": "generator", "reason": _GENERATOR_CHOICE,
                         "closed_questions": deepcopy(questions)})
    view = deepcopy(source)
    view["rows"] = [{**deepcopy(chosen.get(row["id"], row)),
                     "origin": "generator" if row["id"] in chosen else row.get("origin", "spark")}
                    for row in source["rows"]]
    view["questions"] = [deepcopy(q) for q in source["questions"] if q.get("box") not in chosen]
    view["complete"] = bool(not view["questions"] and source.get("raw", {}).get("status") != "rejected")
    view["generator_choices"] = closures
    if source["raw"].get("schema") == _MERGED_INTAKE_SCHEMA:
        summaries = []
        for booking, summary in zip(source["raw"]["bookings"], source["bookings"]):
            child = _restore_intake_view(booking["view"])
            child_ids = {row["id"] for row in child["rows"]}
            child_rows = [row for row in replacements if row["id"] in child_ids]
            if child_rows:
                child_raw = {**raw, "source_intake": child, "generator_rows": child_rows}
                child = _generator_intake_view(child_raw)
            summaries.append({**deepcopy(summary),
                              **{key: child[key] for key in ("complete", "receipt_sha256")}})
        view["bookings"] = summaries
    view["raw"] = deepcopy(raw)
    view["receipt_sha256"] = sha256_json(raw)
    return _key_safe_view(view, "spark")


def merge_generator_records(intake_view: dict, base_instance_path: Path, box_ids: list[str]) -> dict:
    """Explicit planner choice for frozen intake's two by-design limitations.

    Pure apart from reading the supplied base file: no model, ledger, or writes.
    cost_usd remains the original cumulative reading charge; this merge costs
    zero. Rows carry app provenance which the worker removes before validating
    strict Container objects for the voyage packet.
    """
    from experiments.mvp.pipeline.contract.schema import sha256_json
    from stowage.model.instance import Instance

    if (not isinstance(box_ids, list) or not box_ids
            or any(not isinstance(box, str) or not box.strip() for box in box_ids)
            or len(set(box_ids)) != len(box_ids)):
        raise ValueError("Choose a nonempty list of unique box ids")
    source = _restore_intake_view(intake_view)
    base_bytes = Path(base_instance_path).read_bytes()
    try:
        original_base = json.loads(base_bytes)
        base = Instance.model_validate(original_base).model_dump(mode="json")
    except ValueError:
        raise ValueError("The generator base instance is invalid") from None
    _key_safe_view(base, "spark")
    if not set(box_ids) <= set(base["load_list"]):
        raise ValueError("Every chosen generator box must be in this call's base load list")
    # Keep explicit generator declarations: model defaults are not evidence of
    # a missing OOG dimension in the file the planner chose.
    containers = {row["id"]: row for row in original_base["containers"]}
    if not set(box_ids) <= containers.keys():
        raise ValueError("A chosen box has no generator record in the base instance")
    selected = {box: containers[box] for box in box_ids}
    if source["raw"].get("schema") == _GENERATOR_INTAKE_SCHEMA:
        previous = source["raw"]
        if previous["base_instance_sha256"] != sha256_json(base):
            raise ValueError("Keep using the same base instance for generator choices")
        if set(box_ids) & {row["id"] for row in previous["generator_rows"]}:
            raise ValueError("A chosen box must still have an open equipment or OOG review question")
        selected = {**{row["id"]: row for row in previous["generator_rows"]}, **selected}
        source = previous["source_intake"]
    raw = {"schema": _GENERATOR_INTAKE_SCHEMA, "source_intake": deepcopy(source),
           "generator_rows": list(selected.values()), "base_instance_sha256": sha256_json(base),
           "base_file_sha256": hashlib.sha256(base_bytes).hexdigest(),
           "reason": _GENERATOR_CHOICE, "cost_usd": 0.0}
    return _generator_intake_view(raw)


def _answer_merged_papers(intake_view: dict, answers: dict) -> dict:
    # Reconstruct the routing map from child receipts rather than trusting a
    # persisted map to redirect an answer to another booking's question.
    merged = _merge_intake_bookings(intake_view["raw"].get("bookings"))
    if not isinstance(answers, dict):
        raise ValueError("Answers must map question ids to values")
    questions = {item["id"] for item in merged["questions"]}
    if set(answers) - questions:
        raise ValueError("An answer names an unknown question")
    routes = merged["raw"]["question_routes"]
    if any(question not in routes for question in answers):
        raise ValueError("Duplicate container ids require corrected booking documents and another reading")
    grouped = {}
    for question, value in answers.items():
        route = routes[question]
        grouped.setdefault(route["booking"], {})[route["question"]] = value
    bookings = deepcopy(merged["raw"]["bookings"])
    for booking in bookings:
        if booking["id"] in grouped:
            previous_wall = booking["view"]["wall_s"]
            booking["view"] = answer_papers(booking["view"], grouped[booking["id"]])
            booking["view"]["wall_s"] += previous_wall
    return _merge_intake_bookings(bookings)


def _spark_client(grant):
    """Attach a booking ledger at the frozen client's public instance seam."""
    from experiments.mvp.pipeline.intake.interpretation import InterpretationClient

    ledger = functional_ledger(grant=grant)
    client = InterpretationClient(
        api_key=os.environ.get("MODEL_API_KEY"), credit_ready=_paid_consent(),
        ledger_path=ledger.path, compute_path=ledger.compute_path,
        spend_cap_usd=ledger.cap_usd,
    )
    client.ledger = ledger
    client.task_cap_usd = ledger.cap_usd
    return client


def read_papers(documents: list[dict], base_instance_path: Path, *, client=None, grant=None,
                ledger_path=None, cap_usd=None) -> dict:
    """Read files and pass Extension A host authority metadata to intake.

    Explicit ids are retained; otherwise the file stem provides a stable id.
    Missing or unknown roles are unspecified, never inferred from file contents.
    """
    if client is None:
        _require("spark")
    if ledger_path is not None:
        if grant is not None:
            raise ValueError("Supply the booking grant or its ledger path, not both")
        grant = get_grant_for_path(ledger_path, cap_usd=cap_usd)
    elif cap_usd is not None:
        raise ValueError("A booking cap requires its granted ledger path")
    if not isinstance(documents, list) or not documents:
        raise ValueError("Supply at least one cargo document")
    from experiments.mvp.pipeline.intake.spark_reader import MAX_DOCUMENT_BYTES

    normalized = []
    ids = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Each cargo document must be an object")
        if any(not isinstance(document.get(key), str) or not document[key].strip()
               for key in ("name", "media_type")):
            raise ValueError("Each cargo document needs a name and media_type")
        if not isinstance(document.get("path"), (str, Path)) or not str(document["path"]).strip():
            raise ValueError("Each cargo document needs a path")
        document_id = document.get("id", Path(document["path"]).stem)
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Cargo document ids must be nonempty strings")
        if document_id in ids:
            raise ValueError("Cargo document ids must be unique; supply explicit ids for matching file stems")
        ids.add(document_id)
        role = document.get("role")
        role = role if isinstance(role, str) and role in _DOCUMENT_ROLES else "unspecified"
        item = {"id": document_id, "name": document["name"], "role": role,
                "mime_type": document["media_type"], "path": str(document["path"])}
        for key in ("booking_ref", "complete_booking_list", "synthetic"):
            if key in document:
                item[key] = document[key]
        media_type = document["media_type"]
        if media_type.startswith("text/") or media_type in ("application/json", "application/csv"):
            path = Path(document["path"])
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                raise ValueError("Document exceeds the 20 MB limit")
            # Authority checks need the same source text sent to Spark, including
            # uploads whose filename has no recognized text extension.
            item["text"] = path.read_text(encoding="utf-8-sig")
        normalized.append(item)
    from experiments.mvp.pipeline.intake import read_documents
    from experiments.mvp.pipeline.intake.interpretation import SchemaRejection
    from llm.spark.client import SparkCreditNotReady, SparkError
    from llm.spark.spend import SpendError

    started = perf_counter()
    owned_grant = False
    try:
        if client is None:
            if grant is None:
                ref = next((item.get("booking_ref") for item in normalized
                            if item.get("role") == "booking" and item.get("booking_ref")), None)
                refusal = {}
                grant = grant_call(call_id=Path(base_instance_path).parent.name, booking_ref=ref, refusal=refusal)
                if grant is None:
                    raise intake_cap_error(ref, **refusal)
                owned_grant = True
            client = _spark_client(grant)
        result = read_documents(normalized, base_instance=Path(base_instance_path), client=client)
    except SparkCreditNotReady:
        raise Unavailable("spark", "Muse Spark's paid-call credit gate is closed; no request was sent") from None
    except SchemaRejection as error:
        raise _schema_error(error.metadata) from None
    except (SparkError, SpendError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as error:
        if isinstance(error, ModelError):
            raise
        raise _spark_error(error) from None
    except Exception as error:
        if not _in_answer_conversion(error):
            raise
        # e.g. decimal.InvalidOperation from a null set point beside a quoted mass: one booking's answer, not the intake
        raise AnswerConversionError(
            "spark", f"The app couldn't turn Muse Spark's reading of this booking into load-list rows ({type(error).__name__}); "
                     "nothing from it was used. Retry this booking; the other bookings' readings are kept.") from error
    finally:
        if owned_grant:
            release(grant)
    return _intake_view(result, perf_counter() - started)


def answer_papers(intake_view: dict, answers: dict) -> dict:
    """Resume the JSON receipt in a fresh worker; answering makes no model call.

    cost_usd remains the cumulative intake charge. The answers stage spends zero.
    """
    from experiments.mvp.pipeline.contract.schema import IntakeResult
    from experiments.mvp.pipeline.intake import answer_questions

    if not isinstance(intake_view, dict) or not isinstance(intake_view.get("raw"), dict):
        raise ValueError("The intake validation receipt is missing")
    if intake_view["raw"].get("schema") == _GENERATOR_INTAKE_SCHEMA:
        view = _restore_intake_view(intake_view)
        if not isinstance(answers, dict) or set(answers) - {q["id"] for q in view["questions"]}:
            raise ValueError("Answers must name open question ids")
        raw = deepcopy(view["raw"])
        raw["source_intake"] = answer_papers(raw["source_intake"], answers)
        return _generator_intake_view(raw)
    if intake_view["raw"].get("schema") == _MERGED_INTAKE_SCHEMA:
        return _answer_merged_papers(intake_view, answers)
    result = IntakeResult.model_validate(intake_view["raw"])
    if not result.records or any(record.mode.value != "live" for record in result.records):
        raise ModelError("spark", "Muse Spark intake did not return a live reading")
    started = perf_counter()
    answered = answer_questions(result, answers)
    return _intake_view(answered, perf_counter() - started)


def _voice_worker() -> None:
    """JSON-only subprocess entry; credentials arrive through its environment."""
    import sys

    try:
        identity = os.environ.get("STOWLM_FUNCTIONAL_GRANT_ID")
        grant = get_grant(identity) if identity else None
        payload = transcribe(Path(sys.argv[1]), grant=grant)
    except Unavailable as error:
        payload = {"error": "unavailable", "reason": error.reason}
    except ModelError as error:
        payload = {"error": "model", "reason": error.reason}
    except ValueError:
        payload = {"error": "input", "reason": "Use non-silent mono PCM16 WAV at 16 or 24 kHz, up to 60 seconds"}
    except Exception:
        payload = {"error": "model", "reason": "The voice worker could not finish transcription"}
    try:
        payload = _key_safe_view(payload, "voice")
    except ModelError:
        payload = {"error": "model", "reason": "The voice worker returned credential text; nothing from it was used"}
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _provider_transcribe(audio_path: Path, key_provider, *, grant=None) -> dict:
    import subprocess
    import sys

    reason = _cloud_reason(key_provider)
    if reason is not None:
        raise Unavailable("voice", reason)
    try:
        settings = key_provider.worker_env()
        if not isinstance(settings, dict):
            raise ValueError
        key = settings.get("MODEL_API_KEY")
        if not isinstance(key, str) or not key:
            raise ValueError
        if not isinstance(settings.get("STOWLM_FUNCTIONAL_LAUNCH_ID"), str) or not settings["STOWLM_FUNCTIONAL_LAUNCH_ID"]:
            raise ValueError
        if not any(settings.get(name) == "1" for name in ("STOWLM_ACCEPT_PAID_CALLS", "SPARK_CREDIT_READY")):
            raise ValueError
    except Exception:
        raise Unavailable("voice", "The key provider could not authorize the voice worker; check its key and paid-call consent") from None
    environment = os.environ.copy()
    for name in ("MODEL_API_KEY", "STOWLM_ACCEPT_PAID_CALLS", "SPARK_CREDIT_READY",
                 "STOWLM_FUNCTIONAL_LAUNCH_ID", "STOWLM_FUNCTIONAL_CAP_USD", "STOWLM_FUNCTIONAL_GRANT_ID"):
        environment.pop(name, None)
    for name in ("MODEL_API_KEY", "STOWLM_ACCEPT_PAID_CALLS", "SPARK_CREDIT_READY",
                 "STOWLM_FUNCTIONAL_LAUNCH_ID", "STOWLM_FUNCTIONAL_CAP_USD"):
        if name in settings:
            environment[name] = settings[name]
    if grant is not None:
        if grant.launch_id != settings["STOWLM_FUNCTIONAL_LAUNCH_ID"]:
            raise ValueError("The voice grant belongs to another launch")
        environment["STOWLM_FUNCTIONAL_GRANT_ID"] = grant.id
    command = [sys.executable, "-c", "from app.functional_models import _voice_worker; _voice_worker()",
               str(Path(audio_path).resolve())]
    try:
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                   text=True, timeout=90)
    except subprocess.TimeoutExpired:
        raise ModelError("voice", "The voice worker timed out; billing is unknown; see spent and held in the API key panel.") from None
    except (OSError, TypeError, ValueError):
        raise ModelError("voice", "The voice worker could not start") from None
    # Nothing from the worker's diagnostic stream is logged or returned.
    _key_safe_view({"stdout": completed.stdout, "stderr": completed.stderr}, "voice", extra_key=key)
    try:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise ModelError("voice", "The voice worker returned an invalid response") from None
    error = payload.get("error")
    if error:
        reason = payload.get("reason")
        reason = reason if isinstance(reason, str) and reason else "The voice worker could not finish transcription"
        if error == "unavailable":
            raise Unavailable("voice", reason)
        if error == "input":
            raise ValueError(reason)
        raise ModelError("voice", reason)
    if completed.returncode != 0 or payload.get("mode") != "live" or not isinstance(payload.get("transcript"), str):
        raise ModelError("voice", "The voice worker could not finish transcription")
    return _key_safe_view(payload, "voice", extra_key=key)


def transcribe(audio_path: Path, *, transcriber=None, key_provider=None, grant=None) -> dict:
    """Transcribe an accepted WAV; the real transcriber owns spend accounting."""
    if key_provider is not None:
        if transcriber is not None:
            raise ValueError("Use a key provider or an injected transcriber for the voice call")
        return _provider_transcribe(audio_path, key_provider, grant=grant)
    from experiments.mvp.voice.asr import MuseTranscriber, TranscriptionError, audio_info, RATE
    from llm.spark.spend import SpendError

    if transcriber is None:
        _require("voice")
    started = perf_counter()
    try:
        info = audio_info(Path(audio_path))
    except (wave.Error, EOFError):
        raise ValueError("Use mono PCM16 WAV at 16 or 24 kHz") from None
    owned_grant = False
    try:
        if transcriber is None:
            if grant is None:
                from decimal import Decimal
                reserve = Decimal(math.ceil(info["audio_seconds"] + 1)) * RATE
                grant = grant_call(hold_usd=reserve, call_id=Path(audio_path).parent.name, booking_ref="voice")
                if grant is None:
                    raise ModelError("voice", "The voice call wasn't sent because its worst-case hold would pass the available launch cap. Nothing was charged for it; see spent and held in the API key panel.")
                owned_grant = True
            # Credentials stay in this worker's environment. Disable upstream's
            # optional credentials-file loader and give it its own client lock.
            transcriber = MuseTranscriber(ledger=functional_ledger(grant=grant), load_key=lambda: None)
        result = transcriber.transcribe(Path(audio_path))
    except TranscriptionError:
        raise Unavailable("voice", "Muse Voice Transcribe could not transcribe this audio; type the instruction") from None
    except (SpendError, OSError):
        raise ModelError("voice", "Muse Voice Transcribe spend cap or ledger prevents this call; see spent and held in the API key panel.") from None
    finally:
        if owned_grant:
            release(grant)
    evidence = result["evidence"]
    return _key_safe_view({"mode": "live", "transcript": result["transcript"], "model": evidence["model"],
                          **info, "wall_s": perf_counter() - started,
                          "cost_usd": float(evidence["cost_usd"])}, "voice")


class Reader:
    """The local reader, loaded once per weight/adapter pair and serialized.

    generate(sentence, context) -> untouched JSON text is the test seam. Real
    inference always calls the existing v3 masked_answer decoder.
    """

    def __init__(self, adapter_dir=None, model_dir=None, *, generate=None):
        if generate is not None and not callable(generate):
            raise TypeError("generate must be callable")
        self.adapter_dir = adapter_dir
        self.model_dir = Path(model_dir) if model_dir is not None else _DEFAULT_MODEL_DIR
        self.generate = generate
        self._injected_lock = threading.RLock()

    def read(self, sentence: str, context: dict) -> dict:
        from app.brief import masked_answer, rule_text, safe_evidence
        from experiments.mvp.reader.pipeline import GlimmerParser, ParserOutputError
        from experiments.mvp.reader.schema import answer_envelope

        info = _reader_availability(self.adapter_dir, self.model_dir)
        if self.generate is None and not info["available"]:
            raise Unavailable("reader", info["reason"])
        if info["adapter_path"] is None or info["adapter_sha256"] is None:
            raise Unavailable("reader", info["reason"] or "The reader adapter cannot be read")
        adapter = Path(info["adapter_path"])
        sha = info["adapter_sha256"]
        with _READER_LOCK:
            state = _READER_STATES.setdefault(_state_key(adapter, self.model_dir, sha),
                                              {"lock": threading.RLock()})
        lock = state["lock"] if self.generate is None else self._injected_lock
        with lock:
            cold_load_s = None
            generate = self.generate
            if generate is None:
                if "model" not in state:
                    started = perf_counter()
                    try:
                        from mlx_lm import load
                        model, tokenizer = load(str(self.model_dir), adapter_path=str(adapter))
                    except Exception:
                        raise Unavailable("reader", "StowLM Reader couldn't load its local weights and adapter") from None
                    state.update(model=model, tokenizer=tokenizer)
                    cold_load_s = perf_counter() - started

                def generate(text, vessel_context):
                    return masked_answer(state["model"], state["tokenizer"], text, vessel_context)

            parser = GlimmerParser(generate, adapter_sha256=sha)
            evidence = {"parser": "GlimmerParser", "model": "muse-glimmer-30b", "adapter_sha256": sha}
            started = perf_counter()
            try:
                answer, evidence = parser.parse(sentence, context)
                envelope = answer_envelope(answer)
                refusal = envelope.get("refusal")
                status = "refused" if refusal else "constraint_ready"
            except (ParserOutputError, ValueError, TypeError):
                envelope = refusal = None
                status = "invalid"
            except Exception:
                raise Unavailable("reader", "StowLM Reader couldn't finish local inference") from None
            wall_s = perf_counter() - started
        return _key_safe_view({"mode": "live", "status": status, "sentence": sentence, "answer": envelope,
                               "adopted": info["adopted"],
                               "rule_text": rule_text(envelope) or None, "refusal": refusal,
                               "evidence": safe_evidence(evidence), "model": "muse-glimmer-30b", "decode": _DECODE,
                               "wall_s": wall_s, "cold_load_s": cold_load_s}, "reader")
