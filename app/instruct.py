"""Instruction box for the stage page (task 9.3 prep): a planner's sentence -> constraint -> replan -> checker.

    GET  /api/instruct/jobs/<id>                                    -> a job, for rehearsal views
    GET  /api/instruct/plan?plan_id=<id>                            -> instruction record of a replanned plan, or null
    GET  /api/instruct/examples                                     -> example chips (recorded sentences only)
    GET  /api/instruct/recorded[?plan_id=<id>]                      -> recorded solver replan (D144, app/stage_instruction.py)

**No HTTP route starts a replan here.** ``POST /api/instruct`` is gone: it skipped the frozen reader and the planner's
confirmation. A sentence on the page goes through the brief panel (``app/brief.py``), and the box hands it there.
``InstructService.start`` stays for scripts and tests.

Pipeline (all from ``src/llm/instructions``, used read-only): ``parse`` -> ``apply`` (tighten-only forbidden
placements) -> ``replan`` (scalable solver, D79 C2 off, ``allow_repair=False``, full checker inside
``run_planner``) -> a fresh engine replay + full checker (``build_replay``, the same gate every shown plan passes)
-> the instruction check. A replan that is honoured, passes the full checker and is shippable (``shown``) is registered
in the ``DemoService`` registry so the stage can switch to it with ``/api/stage?plan_id=<new>``; any other gets no
registry id and stays a labelled diagnostic in its job.

Spark costs money, so parsing is OFFLINE by default: answers come from Muse Spark responses recorded by the
instructions executive (``experiments/mvp/instruct/responses/*-v2*.json`` and ``demo/*.json``) plus this app's own
cache of live answers (``experiments/mvp/ui/instruct-cache.json``), keyed by the normalised sentence. The parser
module offers no rule-based fallback, so a sentence with no recorded answer is refused ("no recorded response
(offline mode)"). Live Spark is used only when the app was started with ``--instruct-live`` AND the Spark credit
gate (``SPARK_CREDIT_READY=1`` + the ``SpendLedger`` cap inside ``SparkClient``) allows the call.

StowLM hook (see ``experiments/mvp/instruct/STOWLM-HOOK.md``): the replan planner is the ``planner`` argument of
:class:`InstructService` (``"solver.scalable"`` now). Switching to StowLM is one line: register a factory under
``"stowlm"`` in ``PLANNER_FACTORIES`` (the call is spelled out there) and start the service with
``planner="stowlm"``.

All work (the replan, the replay, the checker) runs on the stage service's single background worker, so it never
overlaps a live baseline run (``replan`` scopes a module-level solver hook to this instance id while it runs) and
uses one core.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import unicodedata
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
RECORDED_DIR = ROOT / "experiments/mvp/instruct"
LIVE_CACHE = ROOT / "experiments/mvp/ui/instruct-cache.json"
REPLAN_SIZES = {"tiny"}           # live replans only on tiny instances (one core, ~30-50 s measured)
DEFAULT_TIME_LIMIT_S = 120.0      # bounded; the 5.10 demo replans took 32-48 s on tiny
MAX_TIME_LIMIT_S = 180.0
MAX_JOBS = 32
MAX_SENTENCE = 500


# ---------------------------------------------------------------- planners (the StowLM hook)
def _scalable_factory(constrained):
    from llm.instructions.replan import scalable_planner  # D79: C2 global screen off, same as app.flow
    return scalable_planner


def _stowlm_factory(constrained):  # pragma: no cover - no adapter exists yet (task 9.3)
    # Per STOWLM-HOOK.md: the production mask over an instruction-filtered engine.
    from stowage.feasibility import FeasibilityConfig
    from llm.instructions.mask_filter import instruction_decoder_factory
    from llm.stowlm.planner import StowLMPlanner
    adapter = os.environ.get("STOWLM_ADAPTER") or None
    return lambda _tl: StowLMPlanner(adapter_path=adapter, engine_config=FeasibilityConfig(extra={"global_screen": 0}),
                                     decoder_factory=instruction_decoder_factory(constrained))


# planner id -> (factory(constrained) -> planner_factory(time_limit_s), display label)
PLANNER_FACTORIES: dict[str, tuple[Callable, str]] = {
    "solver.scalable": (_scalable_factory, "Scalable solver"),
    "stowlm": (_stowlm_factory, "StowLM · Muse Glimmer 30B (LoRA)"),
}


# ---------------------------------------------------------------- recorded parser answers
def normalise(sentence: str) -> str:
    """Cache key: Unicode NFC, case-folded, whitespace collapsed, trailing punctuation dropped."""
    text = unicodedata.normalize("NFC", sentence).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"[\s.!?。]+$", "", text)


def _recorded_rows(base: Path) -> list[tuple[str, str, str]]:
    """(sentence, raw Spark answer, source label) from the instructions executive's recorded runs (prompt v2)."""
    rows: list[tuple[str, str, str]] = []
    for path in sorted((base / "responses").glob("*-v2*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if str(data.get("prompt_version")) != "2":
            continue
        for row in data.get("rows", []):
            if isinstance(row.get("sentence"), str) and isinstance(row.get("raw"), str):
                rows.append((row["sentence"], row["raw"], f"recorded Muse Spark answer ({path.relative_to(ROOT)})"))
    for path in sorted((base / "demo").glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        raw = (data.get("parse") or {}).get("raw")
        if isinstance(data.get("sentence"), str) and isinstance(raw, str):
            rows.append((data["sentence"], raw, f"recorded Muse Spark answer ({path.relative_to(ROOT)})"))
    return rows


class ParserCache:
    """Recorded answers by normalised sentence. Every raw answer is re-validated with ``validate_answer``."""

    def __init__(self, recorded_dir: Path = RECORDED_DIR, live_cache: Path = LIVE_CACHE):
        from llm.instructions.schema import validate_answer
        self.live_cache = Path(live_cache)
        self._lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        live_rows = []
        if self.live_cache.is_file():
            try:
                live_rows = [(r["sentence"], r["raw"], r.get("source") or "cached live Muse Spark answer")
                             for r in json.loads(self.live_cache.read_text()).get("entries", [])]
            except (OSError, ValueError, KeyError, TypeError):
                live_rows = []
        for sentence, raw, source in _recorded_rows(Path(recorded_dir)) + live_rows:
            try:
                validate_answer(raw.strip())
            except ValueError:
                continue  # an answer that fails the schema is never served
            self.entries.setdefault(normalise(sentence), {"sentence": sentence, "raw": raw, "source": source})

    def get(self, sentence: str) -> dict | None:
        return self.entries.get(normalise(sentence))

    def add_live(self, sentence: str, raw: str, cost_usd: str) -> None:
        from datetime import datetime, timezone
        entry = {"sentence": sentence, "raw": raw, "cost_usd": cost_usd,
                 "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "source": "cached live Muse Spark answer (app --instruct-live)"}
        with self._lock:
            self.entries[normalise(sentence)] = entry
            data = {"entries": []}
            if self.live_cache.is_file():
                try:
                    data = json.loads(self.live_cache.read_text())
                except (OSError, ValueError):
                    data = {"entries": []}
            data.setdefault("entries", []).append(entry)
            self.live_cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.live_cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
            tmp.replace(self.live_cache)


EXAMPLES = [  # chips: shown only if a recorded answer exists (never a sentence that would cost a live call)
    ("Crane 1 is down, nothing goes into bay 01 this call.", "close a bay"),
    ("Only one tier in the hold of bay 1, the tank top is under repair.", "tier limit"),
    ("Bay 03 is off-limits this call.", "close a bay"),
    ("The port says we can stack 4 high on deck in bay 3 today, go ahead.", "loosening: refused"),
    ("Keep reefers on deck.", "no recorded answer"),
]


# ---------------------------------------------------------------- verification helpers
def _final_placements(replay: dict) -> list[tuple[str, str]]:
    frames = replay.get("frames") or []
    if not frames:
        return []
    return [(p["container_id"], p["slot"]) for p in frames[-1]["state"]["ship"]["placements"]]


def honoured_check(constrained, plan, replay: dict) -> dict:
    """Two checks that the instruction holds, both computed on the finished plan (not trusted from the planner):

    1. ``ConstrainedInstance.violations(plan)``: every placing move (load, restow_on, restow_shift) of the plan,
       judged by the instructions module's ``forbids`` predicate.
    2. App-side, on the engine-replayed final ship state: every box aboard at departure is tested again; for a
       ``close_bay`` constraint the slot's physical 20-ft bays are compared with the closed bays directly (no use of
       ``forbids``), for other kinds through ``forbids``.
    """
    from stowage.conventions import odd_bays_of, parse_slot
    from llm.instructions.schema import CloseBay

    move_violations = constrained.violations(plan)
    closed_bays = {b for c in constrained.constraints if isinstance(c, CloseBay) for b in odd_bays_of(c.bay)}
    state_violations = []
    for box, slot in _final_placements(replay):
        direct = bool(closed_bays and set(odd_bays_of(parse_slot(slot).bay)) & closed_bays)
        reason = constrained.forbids(box, slot)
        if direct or reason:
            state_violations.append(f"{box} at {slot}: " + ("in a closed bay" if direct else reason))
    return {"honoured": not move_violations and not state_violations,
            "move_violations": move_violations, "state_violations": state_violations,
            "method": ("instructions module ConstrainedInstance.violations over every placing move + app-side "
                       "re-check of the replayed final ship state" + (" (closed bays compared directly)"
                                                                       if closed_bays else ""))}


def boxes_without_a_slot(constrained) -> dict[str, str]:
    """Load-list boxes the per-box constraints leave no ship slot of their length (box -> reason).

    ``capacity_shortfall`` counts cells, so it misses a 40-ft box whose every slot spans a closed bay (D144: the
    class 3 instruction on the two-bay feeder_1700 slice). This is the slot-level necessary check.
    """
    from stowage.conventions import cells_of_slot, format_slot, parse_slot

    instance = constrained.instance
    cells = {c.id for c in instance.vessel.cells}
    slots = {length: set() for length in (20, 40)}
    for cell in cells:
        address = parse_slot(cell)
        slots[20].add(cell)
        for bay in (address.bay - 1, address.bay + 1):
            if bay > 0:
                slot = format_slot(bay, address.row, address.tier)
                if set(cells_of_slot(slot)) <= cells:
                    slots[40].add(slot)
    lengths = {b.id: b.length_ft for b in instance.containers}
    out = {}
    for box in constrained._per_box:
        if box in instance.load_list:
            mine = slots[20 if lengths[box] == 20 else 40]
            if not any(constrained.forbids(box, slot) is None for slot in mine):
                out[box] = f"{box} ({lengths[box]}-ft) has no slot on this ship that keeps it away as instructed"
    return out


def _not_shown_reason(passed: bool, shippable: bool, check: dict, replay: dict) -> dict:
    """Diagnostics of a refused replan (why, counts); the plan itself gets no registry id."""
    why = []
    if not check["honoured"]:
        why.append("the instruction is not honoured")
    if not passed:
        why.append("the fresh full checker did not pass it")
    if not shippable:
        why.append("it has no clean shipping approval")
    metadata = replay.get("metadata") or {}
    return {"why": why, "blockers": list(metadata.get("shipping_blockers") or []),
            "move_violations": len(check["move_violations"]), "state_violations": len(check["state_violations"]),
            "checker_violations": len(((replay.get("checker") or {}).get("report") or {}).get("violations") or [])}


def _counts(replay: dict) -> dict | None:
    from app.stage import stage_counts
    frames = replay.get("frames") or []
    return stage_counts(frames[-1]["counts"]) if frames else None


def _total(counts: dict | None) -> int | None:
    return None if not counts else counts.get("total_lifts")


def _later(counts: dict | None) -> int | None:
    if not counts or counts.get("projected_later_restows") is None:
        return None
    return counts["projected_later_restows"] + (counts.get("projected_later_hatch_lifts") or 0)


# ---------------------------------------------------------------- the service
class InstructService:
    def __init__(self, demo, stage=None, *, live: bool = False, planner: str = "solver.scalable",
                 time_limit_s: float = DEFAULT_TIME_LIMIT_S, cache: ParserCache | None = None,
                 client_factory: Callable[[], Any] | None = None, submit: Callable | None = None):
        if planner not in PLANNER_FACTORIES:
            raise ValueError(f"unknown replan planner {planner!r}")
        if not (math.isfinite(time_limit_s) and 0 < time_limit_s <= MAX_TIME_LIMIT_S):
            raise ValueError("time_limit_s must be in (0, 180]")
        self.demo, self.stage, self.live, self.planner = demo, stage, bool(live), planner
        self.time_limit_s = float(time_limit_s)
        self.cache = cache if cache is not None else ParserCache()
        self.client_factory = client_factory
        self._submit_fn = submit
        self._lock = threading.RLock()
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._records: dict[str, dict] = {}   # replanned plan id -> instruction record
        self._active: str | None = None

    # ------------------------------------------------ parse (offline first)
    def live_status(self) -> dict:
        if not self.live:
            return {"live": False, "reason": "offline mode: recorded Muse Spark answers only (start the app with "
                                             "--instruct-live to ask Spark)"}
        if os.environ.get("SPARK_CREDIT_READY") != "1":
            return {"live": False, "reason": "--instruct-live set, but the Spark credit gate is closed "
                                             "(SPARK_CREDIT_READY != 1); recorded answers only"}
        return {"live": True, "reason": None}

    def parse(self, sentence: str) -> dict:
        """-> {"outcome": Constraint | Refusal | None, "refusal": {...} | None, "source", "raw", "cost_usd"}."""
        from llm.instructions.schema import Refusal, validate_answer
        hit = self.cache.get(sentence)
        if hit is not None:
            outcome = validate_answer(hit["raw"].strip())
            return {"outcome": outcome, "source": hit["source"], "raw": hit["raw"], "cost_usd": "0",
                    "recorded": True}
        status = self.live_status()
        if not status["live"]:
            return {"outcome": None, "source": "none", "raw": None, "cost_usd": "0", "recorded": False,
                    "refusal": {"code": "no_recorded_response",
                                "reason": "No recorded Muse Spark response for this sentence (offline mode); "
                                          "nothing was applied. The parser has no rule-based fallback. "
                                          f"[{status['reason']}]"}}
        from llm.instructions.parser import parse_detailed
        client = self.client_factory() if self.client_factory else None
        if client is None:
            from llm.spark.client import SparkClient
            client = SparkClient(max_retries=0)
        result = parse_detailed(sentence, client=client)
        if result.error is None and result.raw is not None:
            self.cache.add_live(sentence, result.raw, str(result.cost_usd))
        return {"outcome": result.outcome, "source": "live Muse Spark call" + (f" [{result.error}]" if result.error
                                                                               else ""),
                "raw": result.raw, "cost_usd": str(result.cost_usd), "recorded": False}

    # ------------------------------------------------ jobs
    def examples(self) -> dict:
        chips = []
        for sentence, kind in EXAMPLES:
            hit = self.cache.get(sentence)
            if hit is None:
                chips.append({"sentence": sentence, "kind": kind, "recorded": False})
                continue
            from llm.instructions.schema import Refusal, validate_answer
            outcome = validate_answer(hit["raw"].strip())
            chips.append({"sentence": sentence, "kind": kind, "recorded": True,
                          "refused": isinstance(outcome, Refusal)})
        return {"examples": chips, **self.live_status(), "planner": self.planner,
                "planner_label": PLANNER_FACTORIES[self.planner][1], "time_limit_s": self.time_limit_s,
                "replan_sizes": sorted(REPLAN_SIZES)}

    def job(self, identity: str) -> dict:
        with self._lock:
            job = self._jobs[identity]
            out = deepcopy({k: v for k, v in job.items() if k not in {"started", "finished"}})
            out["elapsed_s"] = round(job.get("finished", monotonic()) - job["started"], 1)
            return out

    def record(self, plan_id: str | None) -> dict:
        with self._lock:
            return {"plan_id": plan_id, "instruction": deepcopy(self._records.get(plan_id or ""))}

    def _new_job(self, sentence: str, plan_id: str) -> str:
        identity = uuid4().hex
        with self._lock:
            self._jobs[identity] = {"id": identity, "status": "queued", "stage": "Queued", "sentence": sentence,
                                    "plan_id": plan_id, "started": monotonic(), "steps": [],
                                    "constraint": None, "refusal": None, "parse": None, "result": None,
                                    "error": None}
            while len(self._jobs) > MAX_JOBS:
                old, value = next(iter(self._jobs.items()))
                if value["status"] in {"queued", "running"}:
                    break
                self._jobs.pop(old)
        return identity

    def _update(self, identity: str, **values) -> None:
        with self._lock:
            self._jobs[identity].update(values)

    def _step(self, identity: str, name: str, ok: bool | None, detail: str) -> None:
        with self._lock:
            self._jobs[identity]["steps"].append({"step": name, "ok": ok, "detail": detail})

    def _finish(self, identity: str, status: str, stage: str, **values) -> dict:
        with self._lock:
            self._jobs[identity].update(status=status, stage=stage, finished=monotonic(), **values)
            if self._active == identity:
                self._active = None
        return self.job(identity)

    def start(self, sentence: Any, plan_id: Any) -> dict:
        if not isinstance(sentence, str) or not sentence.strip():
            raise ValueError("sentence must be non-empty text")
        if len(sentence) > MAX_SENTENCE:
            raise ValueError(f"sentence must be at most {MAX_SENTENCE} characters")
        if plan_id is None:
            plan_id = self.demo.active_plan_id
        if not isinstance(plan_id, str):
            raise ValueError("plan_id must be a registry id")
        sentence = sentence.strip()
        replay = self.demo.replay(plan_id)  # KeyError -> 404 for unknown ids (registry ids only, never paths)
        identity = self._new_job(sentence, plan_id)
        if replay.get("plan") is None:
            return self._finish(identity, "refused", "No plan to replan",
                                refusal={"code": "no_plan", "reason": "This registry entry has no plan."})
        size = (replay.get("instance") or {}).get("size_class")
        if size not in REPLAN_SIZES:
            return self._finish(identity, "refused", "Not replanned",
                                refusal={"code": "too_large",
                                         "reason": f"Live replans are limited to tiny instances on this laptop demo "
                                                   f"(one core, ~30-50 s); this plan is {size}. Nothing was applied."})
        with self._lock:
            if self._active is not None:
                self._jobs.pop(identity, None)
                from app.flow import ConflictError
                raise ConflictError("An instruction replan is already running")
            self._active = identity
        try:
            parsed = self.parse(sentence)
        except Exception as error:  # the parser never raises by design; this guards the cache/IO path
            return self._finish(identity, "error", "Parser failed", error=f"{type(error).__name__}: {error}")
        return self._after_parse(identity, sentence, plan_id, replay, parsed)

    def _after_parse(self, identity, sentence, plan_id, replay, parsed) -> dict:
        from llm.instructions.apply import NotApplicable, apply, describe
        from llm.instructions.schema import DgKeepAway, Refusal
        from stowage.model import Instance, MovePlan

        outcome = parsed.get("outcome")
        parse_view = {"source": parsed["source"], "raw": parsed["raw"], "cost_usd": parsed["cost_usd"],
                      "recorded": parsed["recorded"]}
        self._update(identity, parse=parse_view)
        if outcome is None:
            self._step(identity, "parse", False, parsed["refusal"]["reason"])
            return self._finish(identity, "refused", "Not parsed", refusal=parsed["refusal"])
        if isinstance(outcome, Refusal):
            self._step(identity, "parse", False, f"Muse Spark refused ({outcome.code})")
            return self._finish(identity, "refused", "Refused by the parser",
                                refusal={"code": outcome.code, "reason": outcome.reason})
        constraint = outcome.model_dump(mode="json")
        self._update(identity, constraint=constraint)
        self._step(identity, "parse", True, json.dumps(constraint))
        instance = Instance.model_validate(replay["instance"])
        plan = MovePlan.model_validate(replay["plan"])
        try:
            constrained = apply(instance, outcome)
        except NotApplicable as error:
            self._step(identity, "apply", False, str(error))
            return self._finish(identity, "refused", "Not applicable to this ship",
                                refusal={"code": "not_applicable", "reason": f"{error}. Nothing was applied."})
        if isinstance(outcome, DgKeepAway) and not constrained._per_box:
            # D144: "honoured" would be vacuous here, so say plainly that the sentence had nothing to act on.
            reason = f"no class {outcome.imdg_class} boxes on this call: nothing to act on"
            self._step(identity, "apply", False, reason)
            return self._finish(identity, "refused", "Nothing to act on",
                                refusal={"code": "no_matching_boxes", "reason": reason})
        description = describe(constrained)
        broken = constrained.violations(plan)
        self._step(identity, "apply", True, description)
        original = {"plan_id": plan_id, "counts": _counts(replay), "checker_passed": bool(replay["checker"]["passed"]),
                    "label": (replay.get("metadata") or {}).get("label") or (replay.get("metadata") or {}).get("planner"),
                    "breaks_instruction": broken}
        self._update(identity, original=original, applied=description)
        if not broken:
            check = honoured_check(constrained, plan, replay)
            if check["honoured"]:
                self._step(identity, "replan", None, "not needed: the current plan already honours the instruction")
                return self._finish(identity, "done", "Already honoured · no replan needed",
                                    result={"replanned": False, "plan_id": plan_id, "honoured": True,
                                            "honoured_check": check, "checker_passed": original["checker_passed"],
                                            "counts": original["counts"], "delta_lifts": 0})
        shortfall = constrained.capacity_shortfall() or "; ".join(boxes_without_a_slot(constrained).values())
        if shortfall:
            self._step(identity, "replan", False, shortfall)
            return self._finish(identity, "refused", "Cannot honour",
                                refusal={"code": "infeasible", "reason": f"Cannot honour on this ship: {shortfall}. "
                                                                         "The current plan stands."})
        self._update(identity, stage=f"Queued for the planner ({PLANNER_FACTORIES[self.planner][1]})")
        self._submit(identity, lambda: self._replan(identity, sentence, plan_id, instance, constrained, original))
        return self.job(identity)

    def _submit(self, identity: str, fn: Callable[[], None]) -> None:
        if self._submit_fn is not None:
            self._submit_fn("instruct:" + identity, fn)
            return
        if self.stage is not None:
            from app.stage_service import MAX_ENTRIES
            with self.stage._lock:  # the stage queue drops silently when full: say so instead of hanging
                full = len(self.stage._queue) >= MAX_ENTRIES
            if full:
                self._finish(identity, "error", "Stopped", error="The background worker queue is full; try again "
                                                                 "in a minute")
                return
            self.stage._submit("instruct:" + identity, fn)  # the stage service's single worker (one core)
            return
        threading.Thread(target=fn, name=f"stowlm-instruct-{identity[:8]}", daemon=True).start()

    def _replan(self, identity, sentence, plan_id, instance, constrained, original) -> None:
        try:
            from app.backend import build_replay
            from app.flow import SOURCE_KEYS
            from app.identity import instance_content_digest
            from app.inputs import LoadedInputs
            from app.stage_instruction import LIVE_SOURCE_KIND, shown_gate
            from llm.instructions.replan import replan

            factory, label = PLANNER_FACTORIES[self.planner]
            self._update(identity, status="running",
                         stage=f"Replanning: {label}, D79 config, limit {self.time_limit_s:g} s")
            digest = instance_content_digest(instance)
            run = replan(constrained, time_limit_s=self.time_limit_s, planner_factory=factory(constrained))
            if instance_content_digest(instance) != digest:
                raise ValueError("planner changed the instance contents")
            if run.precheck:
                self._step(identity, "replan", False, run.precheck)
                self._finish(identity, "refused", "Cannot honour",
                             refusal={"code": "infeasible", "reason": f"Cannot honour: {run.precheck}"})
                return
            result = run.result
            if result is None or result.plan is None:
                msg = f"{label} found no plan under the instruction ({result.status if result else 'no result'}: " \
                      f"{result.message if result else ''})"
                self._step(identity, "replan", False, msg)
                self._finish(identity, "refused", "No plan under the instruction",
                             refusal={"code": "no_plan_found", "reason": msg + ". The current plan stands."})
                return
            self._step(identity, "replan", True, f"{result.status} in {run.wall_s:.1f} s")
            self._update(identity, stage="Fresh engine replay + full checker")
            replay = build_replay(instance, result.plan, planner_result=result)
            check = honoured_check(constrained, result.plan, replay)
            passed = bool(replay["checker"]["passed"])
            shippable = bool(replay["metadata"].get("shippable"))
            self._step(identity, "checker", passed, "full checker PASS" if passed else "full checker FAIL")
            self._step(identity, "honoured", check["honoured"], check["method"])
            counts = _counts(replay)
            shown = shown_gate(passed, shippable, check["honoured"])
            new_id = None
            if shown:
                meta = {"planner": self.planner,
                        "planner_version": result.planner_version, "planner_status": result.status,
                        "label": f"Replanned: {sentence}", "source_kind": LIVE_SOURCE_KIND,
                        "planner_result": result.model_dump(mode="json")}
                replay["metadata"].update({k: v for k, v in meta.items() if k in SOURCE_KEYS})
                replay["metadata"]["instruction_gate"] = {"honoured": check["honoured"], "shown": True}
                replay["metadata"]["planner_label"] = f"Replanned · {label}"
                replay["metadata"]["notice"] = f"Replanned under the instruction \u201c{sentence.rstrip('.')}\u201d. "
                new_id = self.demo.register(LoadedInputs(instance, result.plan, meta), replay)
            delta = (_total(counts) - _total(original["counts"])
                     if _total(counts) is not None and _total(original["counts"]) is not None else None)
            later_delta = (_later(counts) - _later(original["counts"])
                           if _later(counts) is not None and _later(original["counts"]) is not None else None)
            job = self.job(identity)
            record = {"sentence": sentence, "constraint": job["constraint"], "parse": job["parse"],
                      "applied": job.get("applied"), "original": original,
                      "replanned": {"plan_id": new_id, "counts": counts, "checker_passed": passed,
                                    "shippable": shippable, "planner": self.planner, "planner_label": label,
                                    "wall_s": round(run.wall_s, 1), "status": result.status},
                      "delta_lifts": delta, "delta_later": later_delta,
                      "honoured": check["honoured"], "honoured_check": check, "shown": shown,
                      "refusal": None if shown else _not_shown_reason(passed, shippable, check, replay)}
            if shown:
                with self._lock:
                    self._records[new_id] = record
            self._finish(identity, "done", "Replanned · checker " + ("PASS" if passed else "FAIL") +
                         (" · instruction honoured" if check["honoured"] else " · NOT honoured") +
                         ("" if shown else " · not shown as a plan"),
                         result={"replanned": True, **record})
        except Exception as error:
            self._finish(identity, "error", "Stopped", error=f"{type(error).__name__}: {error}")


# ---------------------------------------------------------------- HTTP glue (called from app/server.py)
def recorded_view(service: InstructService, plan_id: str | None) -> dict:
    """The recorded instruction replan (D144): its registry id and bundle block, recomputed from the replay."""
    from app.stage_instruction import find_recorded_plan, instruction_block

    plan_id = plan_id or find_recorded_plan(service.demo)
    if plan_id is None:
        return {"plan_id": None, "instruction": None}
    return {"plan_id": plan_id, "instruction": instruction_block(service.demo.replay(plan_id))}


_SERVICE_LOCK = threading.Lock()


def service_for(handler) -> InstructService | None:
    """Lazily attach one InstructService per handler class (like ``stage_service``)."""
    if handler.demo_service is None:
        return None
    cls = type(handler)
    with _SERVICE_LOCK:
        current = getattr(cls, "_instruct", None)
        if current is None or current.demo is not handler.demo_service:
            stage = handler.stage_service()
            cls._instruct = InstructService(handler.demo_service, stage, live=bool(getattr(cls, "instruct_live", False)))
        return cls._instruct


def handle_get(handler, route: str, query: dict) -> bool:
    """Serve ``/api/instruct/*`` GETs; return False for other routes."""
    if not route.startswith("/api/instruct"):
        return False
    service = service_for(handler)
    if service is None:
        handler.json_response(404, {"error": "not found"})
    elif route == "/api/instruct/examples":
        handler.json_response(200, service.examples())
    elif route == "/api/instruct/recorded":
        handler.json_response(200, recorded_view(service, query.get("plan_id", [None])[0]))
    elif route == "/api/instruct/plan":
        handler.json_response(200, service.record(query.get("plan_id", [None])[0]))
    elif route.startswith("/api/instruct/jobs/"):
        try:
            handler.json_response(200, service.job(route.removeprefix("/api/instruct/jobs/")))
        except KeyError:
            handler.json_response(404, {"error": "Unknown instruction job"})
    else:
        handler.json_response(404, {"error": "not found"})
    return True


