"""Live brief flow on the stage page (plan v5 task 2.2): say it, confirm it, read it, plan it, check it.

    GET  /api/brief/state                      -> modes, calls, prepared sentences, refusal cards, hero slot, gate state
    POST /api/brief/draft   {text, call}       -> editable draft (typed); never calls a reader
    POST /api/brief/clip    {clip}             -> editable draft from a recorded voice clip's stored transcript
    POST /api/brief/audio?call=<id>  (WAV)     -> editable draft from Muse Voice Transcribe (needs --brief-live)
    POST /api/brief/confirm {draft, text}      -> the reading: rule, JSON, reader taken from evidence, or a refusal
    POST /api/brief/plan    {reading}          -> stored plan (through the gates) or the plain no-stored-plan message
    POST /api/brief/replan  {reading}          -> real replan job in its own process (needs --brief-live-replan)
    POST /api/brief/cancel  {job}              -> stop that job
    GET  /api/brief/jobs/<id>                  -> real elapsed seconds and result; never a progress percentage

**The order is the safety.** Text reaches a reader only through ``VoiceFlow.confirm`` (experiments/mvp/voice/flow.py),
after the planner has seen and could edit it. A plan is shown only after a second explicit step ("Plan this").

**Which reader did it comes from evidence.** ``reader_label`` reads the ``provider_evidence`` the parser returned and
nothing else. The active reader is injected in exactly one place, ``make_reader``. "On this laptop" appears only for
the on-device parser with an adapter hash; anything unrecognised gets a neutral label.

**A misreading isn't caught by the checker.** The checker tests the plan against the rule as read, so a passed plan
carries ``claim_scope = parsed_constraint_execution`` and ``sentence_compliance_verified = False``.

**Spend.** Recorded mode (default) makes no paid call: readings come from recordings bound to a sentence, and to a ship
where one was recorded. ``--brief-live`` (plus the existing SPARK_CREDIT_READY gate) parses and transcribes through the
unchanged ``SparkParser`` / ``MuseTranscriber`` ledgers and the existing voice purpose caps.

**Plans.** ``app/brief_prepared.json`` maps instance + exact constraint to a stored before and after plan. Every stored
plan passes the same gate as a live one on each launch: fresh ``build_replay`` (full checker, exact memo allowed),
``shippable``, and the instruction recomputed with ``intrinsic_violations``. A plan that fails is "not shown as a plan".
Matching needs the identical instance digest and an equal canonical constraint; similar text never matches.

**Refusals.** Task 1.4's evidence in ``experiments/mvp/reader/runs/refusals`` is released only when ``REFUSALS.md``
exists, the case binds to the shown instance file, and ``refusal_card.read_recorded_card`` accepts it. Otherwise the
card reads as pending. Arithmetic comes from the proof, alternatives only when certified.

The registry is rebuilt with ``python -m app.brief --build-registry``. Plans here come from Frontier search software, so
the labels say "search software"; they're never called StowLM's plans.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import wave
from collections import OrderedDict
from pathlib import Path
from time import monotonic, perf_counter
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name("brief_prepared.json")
BINDING = ROOT / "experiments/mvp/reader/runs/binding"
REFUSAL_RUN = ROOT / "experiments/mvp/reader/runs/refusals"
REFUSALS_MD = ROOT / "experiments/mvp/reader/REFUSALS.md"
INSTRUCT_DIR = ROOT / "experiments/mvp/instruct"
VOICE_RUN = ROOT / "experiments/mvp/voice/runs/v5-1.3"
LEDGER = ROOT / "experiments/spark-spend/calls.jsonl"

MAX_TEXT = 500
MAX_AUDIO_BYTES = 2 * 1024 * 1024
MAX_AUDIO_S = 30.0
MAX_JOBS = 16
REPLAN_SEARCH_S = 78.0        # the stored binding runs' allocation on these small calls
REPLAN_FINALIZE_S = 300.0
REPLAN_HARD_CAP_S = 1500.0    # a stuck worker is stopped; a slow one still reports its real time

NO_STORED_PLAN = ("No stored plan for this sentence. A real replan takes about 5 minutes on one CPU worker: "
                  "reader → search → finisher → full checker.")
RULE_AS_READ = ("The checker tests the plan against the rule as read. It can't tell whether the reader understood you, "
                "so check the reading before you plan.")
PENDING_REFUSALS = "refusal evidence pending (task 1.4)"
SPEECH_OFF = ("Recorded mode: speech isn't sent anywhere. Type the sentence, or restart with --brief-live for "
              "Muse Voice Transcribe.")
NO_RECORDING = "No recorded reading for this sentence on this ship, and recorded mode makes no model calls."

PLAN_LABEL = "search software (Frontier engine)"
BINDING_CASES = ("one_port_per_hatch-05", "dg_keep_away-04", "max_tiers-05", "one_port_per_hatch-01", "close_bay-01")
REFUSAL_INPUT = "experiments/mvp/corpus/val/small/feeder_500/random_yard/seed-040100016.json"
VOICE_CLIP = "en-reader-0d33e36e565f04e5"
VOICE_CALL = "sim.small.40100002"
HERO_SENTENCE = "Keep the corrosives one bay clear of bay one."
HERO_CALL = "v6:hero"  # app/v6.py's hero ship, served only in a --v6 launch


# ---------------------------------------------------------------- labels: read from evidence, never configuration
def reader_label(evidence: dict | None) -> dict:
    """Who read the sentence, judged only from provider evidence. Unknown evidence gets a neutral label."""
    evidence = evidence if isinstance(evidence, dict) else {}
    parser, model, adapter = evidence.get("parser"), evidence.get("model"), evidence.get("adapter_sha256")
    tuned = (parser == "GlimmerParser" and isinstance(adapter, str) and re.fullmatch(r"[0-9a-f]{64}", adapter) is not None
             and isinstance(model, str) and "glimmer" in model.lower())
    if tuned:
        return {"kind": "stowlm_reader", "on_device": True, "short": "StowLM Reader",
                "label": "StowLM Reader · fine-tuned Muse Glimmer 30B · on this laptop"}
    if (parser in {"SparkParser", "llm.instructions.parser"} and not adapter and isinstance(model, str)
            and model.startswith("muse-spark")):
        return {"kind": "muse_spark", "on_device": False, "short": "Muse Spark",
                "label": "Muse Spark · Meta Model API · cloud"}
    return {"kind": "unknown", "on_device": False, "short": "The reader", "label": "Reader · provenance not recorded"}


def plan_label(evidence: dict | None) -> str:
    """Who planned, from the stored plan's evidence. Frontier is search software; it's never called StowLM."""
    method = (evidence or {}).get("method")
    return PLAN_LABEL if isinstance(method, str) and method.startswith("frontier") else "planner · provenance not recorded"


def credits_line(reader: dict) -> str:
    return f"{reader['short']} read it; search software planned it; the checker passed it"


def rule_text(envelope: dict | None) -> str:
    """One strict rule in plain words, from the validated structure alone."""
    constraint = (envelope or {}).get("constraint")
    if not constraint:
        return ""
    bay = lambda number: f"bay {int(number):02d}"
    kind = constraint.get("type")
    if kind == "close_bay":
        return f"Nothing loads into {bay(constraint['bay'])}."
    if kind == "max_tiers":
        place = "on deck" if constraint["zone"] == "deck" else "in the hold"
        scope = "in every bay" if constraint.get("bay") is None else f"in {bay(constraint['bay'])}"
        tiers = int(constraint["max_tiers"])
        if tiers == 0:
            return f"No containers {place} {scope}."
        return f"At most {tiers} tier{'' if tiers == 1 else 's'} per stack {place} {scope}."
    if kind == "dg_keep_away":
        away = constraint["away_from"]
        anchor = bay(away["bay"]) if away.get("kind") == "bay" else "the accommodation"
        distance = int(constraint["min_bays"])
        return f"IMDG class {constraint['imdg_class']} stays at least {distance} bay{'' if distance == 1 else 's'} from {anchor}."
    if kind == "one_port_per_hatch":
        return "Each hatch cover serves one discharge port only, across hold and deck."
    return str(kind)


_EVIDENCE_KEYS = ("parser", "model", "adapter_sha256", "request_id", "purpose", "recorded", "source")


def safe_evidence(evidence: dict | None) -> dict:
    """What the browser may see: provenance, never raw model text, provider bodies or keys."""
    return {key: (evidence or {}).get(key) for key in _EVIDENCE_KEYS}


def canonical_key(envelope: dict) -> str:
    from experiments.mvp.reader.schema import canonicalize
    return json.dumps(canonicalize(envelope), sort_keys=True, separators=(",", ":"))


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- recorded readings (recorded mode, no network)
class NoRecording(LookupError):
    """Recorded mode has nothing for this sentence on this ship. Never turned into a guessed reading."""


class RecordedReadings:
    """Recordings by normalised sentence. Rows from a known ship are served only on that ship's exact context."""

    def __init__(self, registry: dict | None = None):
        from app.instruct import normalise
        self.normalise = normalise
        self.rows: dict[str, list[dict]] = {}
        registry = registry or {}
        spark = registry.get("binding_reader") or {}
        for path in sorted((BINDING / "parser").glob("*.json")):
            data = self._json(path)
            if data and data.get("parsed") and spark:
                evidence = {**(data.get("parser") or {}), "parser": spark.get("parser"), "model": spark.get("model")}
                self._add(data["sentence"], data["parsed"], evidence, data.get("parse_wall_s"), path,
                          data.get("instance_id"), data.get("context"))
        by_file = {call["input_sha256"]: key for key, call in (registry.get("calls") or {}).items()}
        cases = (self._json(REFUSAL_RUN / "manifest.json") or {}).get("cases", [])
        ships = {case["case_id"]: by_file.get(case["input_sha256"]) for case in cases}
        for path in sorted((REFUSAL_RUN / "parses").glob("*.json")):
            data = self._json(path)
            if data and data.get("parsed") and data.get("parser") and ships.get(data.get("case_id")):
                self._add(data["sentence"], data["parsed"], data["parser"], None, path, ships[data["case_id"]], None)
        for path in sorted((VOICE_RUN / "checkpoints").glob("en-*.json")):
            data = self._json(path)
            if data and data.get("status") == "complete" and data.get("answer"):
                said = data.get("transcript") or data.get("script")
                if isinstance(said, str):
                    self._add(said, data["answer"], data.get("parser_evidence"), data.get("parser_latency_s"), path)
        from llm.instructions.schema import Refusal, validate_answer as old_validate
        from experiments.mvp.reader.schema import answer_envelope
        files = sorted((INSTRUCT_DIR / "responses").glob("*-v2*.json")) + sorted((INSTRUCT_DIR / "demo").glob("*.json"))
        for path in files:
            data = self._json(path)
            rows = (data or {}).get("rows") or ([{"sentence": data.get("sentence"), "raw": (data.get("parse") or {}).get("raw")}]
                                                if data and data.get("parse") else [])
            for row in rows:
                if not (isinstance(row.get("sentence"), str) and isinstance(row.get("raw"), str)):
                    continue
                try:
                    answer = old_validate(row["raw"].strip())
                except ValueError:
                    continue
                refusal = isinstance(answer, Refusal)
                if refusal and answer.code not in {"ambiguous", "unsupported", "loosening", "not_an_instruction"}:
                    continue
                evidence = {"parser": "llm.instructions.parser", "model": "muse-spark-1.3", "adapter_sha256": None}
                self._add(row["sentence"], answer_envelope(answer), evidence, None, path)

    @staticmethod
    def _json(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _add(self, sentence, envelope, evidence, seconds, path, ship=None, context=None):
        self.rows.setdefault(self.normalise(sentence), []).append(
            {"sentence": sentence, "envelope": envelope, "evidence": dict(evidence or {}), "seconds": seconds,
             "ship": ship, "context": context, "source": str(Path(path).relative_to(ROOT))})

    def find(self, sentence: str, context: dict) -> tuple[Any, dict, float | None]:
        """(validated answer, evidence, seconds when recorded); ship-bound rows first, then re-validated others."""
        from experiments.mvp.reader.schema import validate_answer
        rows = self.rows.get(self.normalise(sentence), [])
        bound = [r for r in rows if r["ship"] == context.get("id") and (r["context"] is None or r["context"] == context)]
        unbound = [r for r in rows if r["ship"] is None]
        for row in bound + unbound:
            try:
                answer = validate_answer(json.dumps(row["envelope"]), context)
            except ValueError:
                continue   # a bay this ship doesn't have: this recording says nothing about this ship
            evidence = {**row["evidence"], "recorded": True, "source": row["source"],
                        "context_bound": row["ship"] is not None}
            return answer, evidence, row["seconds"]
        raise NoRecording(sentence)


class RecordedParser:
    """The default reader: ``parse(sentence, context)`` over recordings. It makes no call of any kind."""

    def __init__(self, readings: RecordedReadings):
        self.readings = readings
        self.seconds: float | None = None

    def parse(self, sentence: str, context: dict):
        answer, evidence, self.seconds = self.readings.find(sentence, context)
        return answer, evidence


class _Capture:
    """Keeps the provider evidence VoiceFlow deliberately drops from UI state; the flow's gates stay unchanged."""

    def __init__(self, parser: Callable[[], Any]):
        self.parser = parser
        self.evidence: dict | None = None
        self.no_recording = False
        self.recorded_seconds: float | None = None

    def parse(self, sentence: str, context: dict):
        self.evidence, self.no_recording, self.recorded_seconds = None, False, None
        inner = self.parser()
        try:
            answer, evidence = inner.parse(sentence, context)
        except NoRecording:
            self.no_recording = True
            raise
        self.evidence = dict(evidence)
        self.recorded_seconds = getattr(inner, "seconds", None)
        return answer, evidence


class _FixedAnswer:
    """Returns one already validated reading, only for that sentence and that ship (voice/DEMO.md step 4)."""

    def __init__(self, sentence, envelope, evidence, context_id):
        self.sentence, self.envelope, self.evidence, self.context_id = sentence, envelope, evidence, context_id

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        if sentence != self.sentence or context.get("id") != self.context_id:
            raise ValueError("this reading belongs to another sentence or ship")
        return validate_answer(json.dumps(self.envelope), context), dict(self.evidence)


class _StoredAnswer:
    """The recording itself, so ``read_recorded_card`` can run its own gates with no model call."""

    def __init__(self, envelope, evidence):
        self.envelope, self.evidence = envelope, evidence

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        return validate_answer(json.dumps(self.envelope), context), dict(self.evidence)


# ---------------------------------------------------------------- the one place the reader is chosen
def make_reader(options: dict) -> Callable[[], Any] | None:
    """-> factory of the active live reader, or None for recorded mode. Change the reader here and nowhere else.

    ``reader = "glimmer"`` builds the on-device parser (free, so it needs no spend flag). ``live = True`` builds the
    existing SparkParser under the voice purpose and its unchanged allocation.
    """
    if options.get("parser") is not None:     # tests and integrations inject an object
        fixed = options["parser"]
        return lambda: fixed
    if options.get("reader") == "glimmer":
        return glimmer_reader(options.get("reader_adapter"))
    if options.get("live") and os.environ.get("SPARK_CREDIT_READY") == "1":
        from experiments.mvp.voice.flow import _default_parser
        made: list = []
        return lambda: made[0] if made else (made.append(_default_parser()) or made[0])
    return None


def glimmer_reader(adapter_dir: str | Path | None, *, model_dir: Path = ROOT / "models/glimmer-30b-4bit"):
    """Build ``GlimmerParser`` from an adapter folder: the model loads on first use, in this process, on MLX.

    ``masked_answer`` is the decode ``reader/v3/evaluate.py`` measured. ``GlimmerParser`` raises if the adapter isn't valid,
    and there's no fallback to Spark or rules.
    """
    if not adapter_dir:
        raise ValueError("--reader glimmer needs --reader-adapter <dir>")
    weights = Path(adapter_dir) / "adapters.safetensors"
    if not weights.is_file():
        raise ValueError(f"no adapters.safetensors in {adapter_dir}")
    from experiments.mvp.reader.pipeline import GlimmerParser
    loaded: dict = {}

    def generate(sentence: str, context: dict) -> str:
        from mlx_lm import load
        if not loaded:
            loaded["model"], loaded["tokenizer"] = load(str(model_dir), adapter_path=str(adapter_dir))
        return masked_answer(loaded["model"], loaded["tokenizer"], sentence, context)
    parser = GlimmerParser(generate, adapter_sha256=_sha(weights), model="muse-glimmer-30b")
    return lambda: parser


def masked_answer(model, tokenizer, sentence: str, context: dict) -> str:
    """One greedy reading through the v3 feasibility mask: the logits processor sets every token the ship's grammar forbids
    to minus infinity before sampling. Same call as ``reader/v3/evaluate.py``, so sealed accuracy describes this path."""
    from experiments.mvp.reader.evaluate import render_prompt
    from experiments.mvp.reader.schema import messages
    from experiments.mvp.reader.v3.decoder import make_logits_processor
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    stops = set(tokenizer.eos_token_ids)
    if not stops:
        raise ValueError("tokenizer has no EOS token for schema completion")
    prompt = render_prompt(tokenizer, messages(sentence, context))
    tokens = tokenizer.encode(prompt, add_special_tokens=False)  # this exact list: no implicit BOS
    processor = make_logits_processor(tokenizer, context, len(tokens), min(stops))
    answer = generate(model, tokenizer, prompt=tokens, max_tokens=256, sampler=make_sampler(temp=0),
                      logits_processors=[processor], verbose=False)
    return processor.validate_output(answer)  # a truncated answer fails here, never repaired


# ---------------------------------------------------------------- refusal slot (task 1.4)
def refusal_cards(calls: dict, readings: RecordedReadings) -> dict:
    """Cards keyed by normalised sentence, released only under the task's three conditions; else pending."""
    if not REFUSALS_MD.is_file():
        return {"status": "pending", "message": PENDING_REFUSALS, "cards": []}
    try:
        from experiments.mvp.reader.refusal_card import read_recorded_card
        from experiments.mvp.reader.refusal_demo import verify
        manifest = verify()
    except Exception as error:
        return {"status": "pending", "message": f"{PENDING_REFUSALS}: {type(error).__name__}", "cards": []}
    cards = []
    by_sha = {call["input_sha256"]: key for key, call in calls.items()}
    for case in manifest["cases"]:
        call = by_sha.get(case["input_sha256"])
        path = REFUSAL_RUN / "examples" / f"{case['case_id']}.json"
        if call is None or not path.is_file():
            continue
        record = json.loads(path.read_text())
        parsed = (record.get("parsed") or {})
        try:
            card = read_recorded_card(case["case_id"], parser=_StoredAnswer(parsed.get("parsed"), parsed.get("parser") or {}))
        except Exception as error:
            cards.append({"case_id": case["case_id"], "call": call, "sentence": case["sentence"],
                          "status": "unavailable", "reason": type(error).__name__})
            continue
        if card.get("cached_explanation_released") is True:
            cards.append(card_view(card, case, call))
    return {"status": "available", "message": None, "cards": cards,
            "witness_audited": (REFUSAL_RUN / "witness-audit.json").is_file()}


def card_view(card: dict, case: dict, call: str) -> dict:
    proof = card.get("proof") or {}
    parsed = proof.get("parsed") or {}
    refusal = parsed.get("refusal") or {}
    return {"case_id": case["case_id"], "call": call, "sentence": case["sentence"], "status": card["status"],
            "code": refusal.get("code"), "reason": refusal.get("reason"), "constraint": parsed if "constraint" in parsed else None,
            "rule": rule_text(parsed), "existing_rule": proof.get("rule_details"),
            "explanation": card.get("explanation"), "explanation_label": card.get("label"),
            "explainer_model": (card.get("explainer") or {}).get("model"),
            "clarifications": [c.get("text") for c in proof.get("clarifications") or []],
            "arithmetic": arithmetic(proof.get("proof")), "alternatives": certified(proof),
            "reader": reader_label(card.get("parser")), "evidence": safe_evidence(card.get("parser")),
            "no_planner_called": True, "checker_report": None}


def arithmetic(proof: dict | None) -> dict | None:
    """Space needed and space left, only from the proof."""
    if not proof or not proof.get("proven_impossible"):
        return None
    return {"needed_teu": proof.get("demand_teu"), "left_cells": proof.get("open_cells"),
            "boxes_with_no_slot": len(proof.get("nofit_ids") or []), "load_boxes": proof.get("load_box_count"),
            "reasons": proof.get("proof_reasons"), "scope": proof.get("scope"),
            "rows": [{k: row.get(k) for k in ("container_id", "length_ft", "demand_cells", "size_compatible_footprints",
                                              "allowed_footprints")}
                     for row in proof.get("load_boxes") or [] if not row.get("allowed_footprints")]}


def certified(proof: dict) -> list[dict]:
    """Alternatives with a fresh full check of a stored plan. A screen-only candidate isn't offered as working."""
    return [{"sentence": a.get("sentence"), "text": a.get("text"), "relaxation": a.get("relaxation")}
            for a in proof.get("alternatives") or [] if a.get("fresh_audit", {}).get("passed") is True]


# ---------------------------------------------------------------- gates
def lift_view(replay: dict) -> dict:
    from app.instruct import _counts, _later
    counts = _counts(replay) or {}
    return {"weighted": counts.get("weighted_nonproductive"), "total_lifts": counts.get("total_lifts"),
            "later": _later(counts), "yard_rehandles": counts.get("yard_rehandles"), "restows": counts.get("restows"),
            "hatch_lifts": counts.get("hatch_lifts"), "passed": bool((replay.get("checker") or {}).get("passed")),
            "shippable": bool((replay.get("metadata") or {}).get("shippable"))}


def honoured(instance, answer, plan) -> list[str]:
    """Instruction recomputed on the finished plan; never read from the stored record."""
    from experiments.mvp.reader.pipeline import intrinsic_violations
    return list(intrinsic_violations(instance, answer, plan))


def gate_plans(instance, answer, before_plan, after_plan) -> dict:
    """The one gate for stored and live plans. -> dict with replays, verdict and the plain reasons it failed."""
    from app.backend import build_replay
    from app.stage_instruction import boxes_moved, shown_gate
    from experiments.mvp.reader.pipeline import plan_signature
    after = build_replay(instance, after_plan)
    after_view = lift_view(after)
    after_violations = honoured(instance, answer, after_plan)
    is_honoured = not after_violations
    before, before_view, before_violations = None, None, None
    if before_plan is not None:
        before = build_replay(instance, before_plan)
        before_view = lift_view(before)
        before_violations = honoured(instance, answer, before_plan)
    shown = shown_gate(after_view["passed"], after_view["shippable"], is_honoured)
    why = []
    if not after_view["passed"]:
        why.append("the full checker did not pass it")
    if not after_view["shippable"]:
        why.append("it has no clean shipping approval")
    if not is_honoured:
        why.append("it breaks the rule as read")
    binding = None
    if before_plan is not None:
        changed = plan_signature(before_plan) != plan_signature(after_plan)
        binding = bool(before_violations) and changed
        if not (before_view["passed"] and before_view["shippable"]):
            shown = False
            why.append("the plan before the rule did not pass, so there is nothing fair to compare")
        if not binding:
            shown = False
            why.append("the plan before the rule already obeyed it, so nothing was replanned")
    moved = boxes_moved(instance, before_plan, after_plan) if shown and before_plan is not None else None
    return {"shown": bool(shown), "why": why, "before": before_view, "after": after_view, "binding": binding,
            "honoured": is_honoured, "before_violations": before_violations, "after_violations": after_violations,
            "boxes_moved": moved, "replays": (before, after)}


# ---------------------------------------------------------------- registry builder
def _clip_peaks(path: Path, bins: int = 180) -> dict:
    with wave.open(str(path), "rb") as clip:
        rate, frames, raw = clip.getframerate(), clip.getnframes(), clip.readframes(clip.getnframes())
    samples = [int.from_bytes(raw[i:i + 2], "little", signed=True) for i in range(0, len(raw) - 1, 2)]
    size = max(1, len(samples) // bins)
    peaks = [max(map(abs, samples[i:i + size])) for i in range(0, len(samples), size)][:bins]
    top = max(peaks) or 1
    return {"seconds": round(frames / rate, 3), "peaks": [round(p / top, 3) for p in peaks]}


def build_registry(path: Path = REGISTRY) -> dict:
    """Seed the registry from audited binding runs. Refuses any case whose audit isn't a binding pass."""
    from stowage.io import load_instance
    from app.backend import vessel_display_label
    from app.identity import instance_content_digest
    from experiments.mvp.reader.schema import canonicalize, context_from_instance

    relative = lambda p: str(Path(p).relative_to(ROOT))
    ledger = {}
    for line in LEDGER.read_text().splitlines():
        row = json.loads(line)
        if row.get("request_id"):
            ledger[row["request_id"]] = row
    readers = set()
    for parser_file in sorted((BINDING / "parser").glob("*.json")):
        request = json.loads(parser_file.read_text())["parser"]["request_id"]
        readers.add(ledger[request]["model"])
    if readers != {"muse-spark-1.3"}:
        raise ValueError(f"binding readings are not one Muse Spark model: {readers}")
    calls: dict[str, dict] = {}

    def add_call(input_path: str, baseline: dict | None = None) -> str:
        instance = load_instance(ROOT / input_path)
        call = calls.setdefault(instance.id, {
            "id": instance.id, "input": input_path, "input_sha256": _sha(ROOT / input_path),
            "instance_digest": instance_content_digest(instance), "vessel": vessel_display_label(instance),
            "size_class": instance.size_class, "load_boxes": len(instance.load_list), "baseline": None,
            "context": context_from_instance(instance)})
        if baseline and call["baseline"] is None:
            call["baseline"] = baseline
        return instance.id

    cases = []
    for case_id in BINDING_CASES:
        plan_path, audit_path = BINDING / "plans" / f"{case_id}.json", BINDING / "audits" / f"{case_id}.json"
        stored, audit = json.loads(plan_path.read_text()), json.loads(audit_path.read_text())
        if not (audit.get("passed") is True and audit.get("binding") is True and audit.get("fresh_process") is True):
            raise ValueError(f"{case_id}: only freshly audited binding replans enter the registry")
        if audit.get("plan_record_sha256") != _sha(plan_path):
            raise ValueError(f"{case_id}: audit is bound to a different plan record")
        before_path = ROOT / stored["baseline_file"]
        before = json.loads(before_path.read_text())
        baseline = {"file": relative(before_path), "sha256": _sha(before_path), "method": before.get("method"),
                    "version": before.get("version")}
        call = add_call(stored["input"], baseline)
        cases.append({
            "id": case_id, "call": call, "sentence": stored["sentence"], "constraint": canonicalize(stored["parsed"]),
            "sealed_id": stored.get("sealed_id"), "before": baseline,
            "after": {"file": relative(plan_path), "sha256": _sha(plan_path), "method": before.get("method"),
                      "version": before.get("version"), "wall_s": round(stored["wall_s"], 2),
                      "search_s": stored.get("search_s"), "finalize_s": stored.get("finalize_s"),
                      "recorded_wall_note": "planning worker, including its fresh check of the stored before plan"},
            "audit": {"file": relative(audit_path), "sha256": _sha(audit_path), "fresh_process": True,
                      "changed_final_placements": audit.get("changed_final_placements")}})
    refusal_call = add_call(REFUSAL_INPUT)
    clip = json.loads((VOICE_RUN / "checkpoints" / f"{VOICE_CLIP}-clean.json").read_text())
    wav = VOICE_RUN / "audio" / f"{VOICE_CLIP}-clean.wav"
    value = {
        "schema_version": "stowlm.brief-prepared/1",
        "note": "Generated by `python -m app.brief --build-registry`. Plans are gated again on every launch.",
        "binding_reader": {"parser": "SparkParser", "model": "muse-spark-1.3",
                           "evidence": "request ids in runs/binding/parser matched in experiments/spark-spend/calls.jsonl"},
        "calls": calls, "default_call": cases[0]["call"], "refusal_call": refusal_call, "cases": cases,
        "hero": {"id": "hero", "sentence": HERO_SENTENCE, "status": "pending",
                 "note": "Stored plan pending (task 3.2): needs the final reader and pipeline on the chosen ship."},
        "voice_clips": [{
            "id": VOICE_CLIP, "call": VOICE_CALL, "said": clip["script"], "transcript": clip["transcript"],
            "audio": relative(wav), "audio_sha256": _sha(wav), "asr_latency_s": round(clip["asr_latency_s"], 3),
            "voice": "stock public text-to-speech clip, synthetic; no real person",
            "asr_model": clip["asr_evidence"]["model"], "asr_session": clip["asr_evidence"].get("session_id"),
            **_clip_peaks(wav)}],
    }
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n")
    return value


# ---------------------------------------------------------------- the service
class BriefService:
    """One draft at a time, one reader injected once, one replan process at a time."""

    def __init__(self, demo, stage=None, *, options: dict | None = None, registry: Path = REGISTRY,
                 transcriber: Any = None, submit: Callable | None = None, replan_command: Callable | None = None):
        self.demo, self.stage = demo, stage
        self.options = dict(options or {})
        self.registry = json.loads(Path(registry).read_text())
        self.transcriber = transcriber
        self._submit_fn = submit
        self.replan_command = replan_command or _replan_command
        self.readings = RecordedReadings(self.registry)
        self.reader = make_reader(self.options)
        self._lock = threading.RLock()
        self._ships: dict[str, dict] = {}
        self._draft: dict | None = None
        self._readings: OrderedDict[str, dict] = OrderedDict()
        self._gates: dict[str, dict] = {c["id"]: {"status": "pending"} for c in self.registry["cases"]}
        self._gates_started = False
        self._cards: dict | None = None
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._active: str | None = None

    # ------------------------------------------------ modes
    def modes(self) -> dict:
        live = bool(self.options.get("live"))
        credit = os.environ.get("SPARK_CREDIT_READY") == "1"
        injected = self.options.get("reader") == "glimmer" or self.options.get("parser") is not None
        reading = "live" if injected or (live and credit) else "recorded"
        speech = "live" if live and (credit or self.transcriber is not None) else "off"
        reason = None
        if reading == "recorded":
            reason = ("--brief-live set, but the Spark credit gate is closed (SPARK_CREDIT_READY != 1)" if live else
                      "Recorded mode: no model calls")
        return {"reading": reading, "speech": speech, "live_replan": bool(self.options.get("live_replan")), "reason": reason}

    # ------------------------------------------------ ships
    def ship(self, key: str) -> dict:
        """Load a registry call once; its file bytes and content digest must match the registry."""
        from stowage.io import load_instance
        from app.identity import instance_content_digest
        from experiments.mvp.reader.schema import context_from_instance
        if key == HERO_CALL:  # a v6 launch's hero, on the context its candidate wordings were read on; no other launch has it
            if not self.options.get("v6"):
                raise KeyError(key)
            from app.v6 import hero_ship
            return hero_ship()
        with self._lock:
            found = self._ships.get(key)
        if found is not None:
            return found
        call = self.registry["calls"].get(key)
        if call is None:
            raise KeyError(key)
        path = ROOT / call["input"]
        if _sha(path) != call["input_sha256"]:
            raise ValueError("This ship's file changed since the registry was built")
        instance = load_instance(path)
        if instance_content_digest(instance) != call["instance_digest"]:
            raise ValueError("This ship's contents changed since the registry was built")
        context = context_from_instance(instance)
        if context != call["context"]:
            raise ValueError("This ship's reader context changed since the registry was built")
        found = {"key": key, "call": call, "instance": instance, "context": context}
        with self._lock:
            self._ships[key] = found
        return found

    # ------------------------------------------------ registry views
    def _recorded_reader(self, sentence: str, call: str) -> dict | None:
        try:
            _, evidence, seconds = self.readings.find(sentence, self.registry["calls"][call]["context"])
        except NoRecording:
            return None
        return {"reader": reader_label(evidence), "evidence": safe_evidence(evidence), "seconds": seconds}

    def _case_view(self, case: dict) -> dict:
        with self._lock:
            gate = {k: v for k, v in self._gates.get(case["id"], {}).items() if k != "replays"}
        return {"id": case["id"], "call": case["call"], "sentence": case["sentence"], "constraint": case["constraint"],
                "rule": rule_text(case["constraint"]), "reading": self._recorded_reader(case["sentence"], case["call"]),
                "plan_label": plan_label(case["after"]), "recorded_wall_s": case["after"]["wall_s"], "gate": gate,
                "recorded_label": "recorded plan, not a live solve"}

    def cards(self) -> dict:
        """Loaded once, on the worker at launch; a reading that needs them before that loads them itself."""
        with self._lock:
            if self._cards is not None:
                return self._cards
        value = refusal_cards(self.registry["calls"], self.readings)    # seconds of work: outside the lock
        with self._lock:
            self._cards = value
            return value

    def _cards_view(self) -> dict:
        with self._lock:
            return self._cards if self._cards is not None else {"status": "checking", "message": None, "cards": []}

    def state(self) -> dict:
        self.start_gates()
        return {"modes": self.modes(), "default_call": self.registry["default_call"],
                "calls": [{k: c[k] for k in ("id", "vessel", "size_class", "load_boxes")}
                          for c in self.registry["calls"].values()],
                "prepared": [self._case_view(c) for c in self.registry["cases"]],
                "hero": self.registry["hero"], "voice_clips": self.registry.get("voice_clips", []),
                "refusals": self._cards_view(), "messages": {"no_stored_plan": NO_STORED_PLAN, "rule_as_read": RULE_AS_READ}}

    # ------------------------------------------------ gates on load
    def start_gates(self) -> None:
        if self.options.get("v6"):  # the v6 flow has its own gate; these cases' plans aren't registered in that launch
            return
        with self._lock:
            if self._gates_started:
                return
        self._submit("brief:gates", self.run_gates)

    def _submit(self, key: str, fn: Callable[[], None]) -> None:
        if self._submit_fn is not None:
            self._submit_fn(key, fn)
        elif self.stage is not None:
            self.stage._submit(key, fn)     # the stage's single worker: never overlaps other checks
        else:
            threading.Thread(target=fn, name=key, daemon=True).start()

    def run_gates(self) -> None:
        with self._lock:
            if self._gates_started:
                return
            self._gates_started = True
        self.cards()
        for case in self.registry["cases"]:
            with self._lock:
                self._gates[case["id"]] = {"status": "checking"}
            try:
                value = self.gate_case(case)
            except Exception as error:
                value = {"status": "not_shown", "why": [f"the stored plan could not be checked ({type(error).__name__})"]}
            with self._lock:
                self._gates[case["id"]] = value
        if self.stage is not None:     # gauges ready before anyone switches: one worker, reused from the disk cache
            for gate in self.gate_status().values():
                plan_id = (gate.get("after") or {}).get("plan_id")
                if plan_id:
                    self.stage.physics_status(plan_id)

    def _stored_plan(self, entry: dict, key: str = "plan"):
        from stowage.model import MovePlan
        path = ROOT / entry["file"]
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("stored plan file changed since the registry was built")
        return MovePlan.model_validate(json.loads(data)[key])

    def gate_case(self, case: dict) -> dict:
        from experiments.mvp.reader.schema import validate_answer
        ship = self.ship(case["call"])
        answer = validate_answer(json.dumps(case["constraint"]), ship["context"])
        return self._finish_gate(ship, answer, self._stored_plan(case["before"]), self._stored_plan(case["after"]),
                                 case["after"], recorded=True, label=case["sentence"])

    def _finish_gate(self, ship, answer, before_plan, after_plan, evidence, *, recorded: bool, label: str) -> dict:
        gate = gate_plans(ship["instance"], answer, before_plan, after_plan)
        before_replay, after_replay = gate.pop("replays")
        view = dict(gate, status="shown" if gate["shown"] else "not_shown", plan_label=plan_label(evidence),
                    recorded=recorded, wall_s=evidence.get("wall_s"), label=label)
        if gate["shown"]:
            view["before"] = dict(view["before"] or {}, plan_id=self._register(ship, before_plan, before_replay, evidence,
                                                                               "before the rule", label, recorded) if before_plan is not None else None)
            view["after"] = dict(view["after"], plan_id=self._register(ship, after_plan, after_replay, evidence,
                                                                       "under the rule", label, recorded))
            with_delta = (view["after"].get("weighted"), view["before"].get("weighted"))
            view["delta_weighted"] = (with_delta[0] - with_delta[1]) if None not in with_delta else None
        else:   # counts of a plan that isn't shown stay on the server
            view["before"], view["after"] = None, None
        return view

    def _register(self, ship, plan, replay, evidence, role, sentence, recorded) -> str:
        from app.inputs import LoadedInputs
        tag = "recorded plan, not a live solve" if recorded else "live replan"
        meta = {"planner": str(evidence.get("method") or "supplied-plan"), "planner_version": str(evidence.get("version") or ""),
                "label": f"Brief · {role}: {sentence}"[:120], "source_kind": f"Brief plan ({tag})"}
        replay["metadata"].update(meta)
        replay["metadata"]["planner_label"] = f"Search software (Frontier engine) · {tag}"
        return self.demo.register(LoadedInputs(ship["instance"], plan, meta), replay)

    def match(self, ship: dict, envelope: dict) -> dict | None:
        """Equal canonical constraint on the identical instance digest; text similarity plays no part."""
        wanted = canonical_key(envelope)
        for case in self.registry["cases"]:
            call = self.registry["calls"][case["call"]]
            if call["instance_digest"] == ship["call"]["instance_digest"] and canonical_key(case["constraint"]) == wanted:
                return case
        return None

    # ------------------------------------------------ brief -> draft -> confirm
    def _flow(self, ship: dict):
        from experiments.mvp.voice.flow import VoiceFlow
        capture = _Capture(self.reader if self.reader is not None else (lambda: RecordedParser(self.readings)))
        return VoiceFlow(ship["context"], parser=capture, transcriber=self.transcriber), capture

    def _new_draft(self, flow, capture, ship, result, **extra) -> dict:
        identity = uuid4().hex
        with self._lock:
            self._draft = {"id": identity, "flow": flow, "capture": capture, "ship": ship}
        return {"status": result.status, "draft": identity, "transcript": result.transcript, "source": result.source,
                "message": result.message, "call": ship["key"], "timings": dict(result.timings), **extra}

    def draft(self, text: Any, call: Any) -> dict:
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        if len(text) > MAX_TEXT:
            raise ValueError(f"text must be at most {MAX_TEXT} characters")
        ship = self.ship(str(call or self.registry["default_call"]))
        flow, capture = self._flow(ship)
        result = flow.prepare_typed(text)
        if result.status != "awaiting_confirmation":
            return result.to_dict()
        return self._new_draft(flow, capture, ship, result, heard_by=None)

    def clip_draft(self, clip_id: Any) -> dict:
        """A recorded voice clip's stored transcript enters the identical gate. No audio leaves the machine."""
        clip = next((c for c in self.registry.get("voice_clips", []) if c["id"] == clip_id), None)
        if clip is None:
            raise KeyError(clip_id)
        ship = self.ship(clip["call"])
        flow, capture = self._flow(ship)
        result = flow.prepare_typed(clip["transcript"])
        return dict(self._new_draft(flow, capture, ship, result, heard_by="Muse Voice Transcribe · cloud call",
                                    recorded=True), source="audio",
                    message="Recorded transcript of a synthetic clip. Review or edit it, then confirm it.")

    def audio_draft(self, payload: bytes, call: Any) -> dict:
        info = wav_info(payload)
        if self.modes()["speech"] != "live":
            return {"status": "typed_required", "message": SPEECH_OFF, "source": "audio", "audio_seconds": info["seconds"]}
        ship = self.ship(str(call or self.registry["default_call"]))
        flow, capture = self._flow(ship)
        handle, name = tempfile.mkstemp(prefix="stowlm-brief-", suffix=".wav")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
            result = flow.prepare_audio(name)
        finally:
            Path(name).unlink(missing_ok=True)    # never kept beyond this call
        if result.status != "awaiting_confirmation":
            return result.to_dict()
        return self._new_draft(flow, capture, ship, result, heard_by="Muse Voice Transcribe · cloud call",
                               audio_seconds=info["seconds"])

    def cancel_draft(self) -> dict:
        with self._lock:
            self._draft = None
        return {"status": "cancelled"}

    def confirm(self, draft_id: Any, text: Any) -> dict:
        with self._lock:
            draft, self._draft = self._draft, None
        if draft is None or draft["id"] != draft_id:
            raise ValueError("That draft is gone; confirm the sentence on screen again")
        flow, capture, ship = draft["flow"], draft["capture"], draft["ship"]
        result = flow.confirm(text if isinstance(text, str) else None)
        if capture.no_recording:
            return {"status": "no_recording", "sentence": str(text).strip(), "call": ship["key"], "message": NO_RECORDING,
                    "hero": self._hero(str(text))}
        if result.status not in {"constraint_ready", "refused"}:
            return result.to_dict()
        evidence = capture.evidence or {}
        recorded = evidence.get("recorded") is True
        seconds = capture.recorded_seconds if recorded else result.timings.get("parse_s")
        view = {"id": uuid4().hex, "status": result.status, "sentence": result.transcript, "source": result.source,
                "call": ship["key"], "answer": result.answer, "rule": rule_text(result.answer),
                "reader": reader_label(evidence), "evidence": safe_evidence(evidence), "recorded": recorded,
                "context_bound": evidence.get("context_bound"), "seconds": seconds,
                "recorded_label": "recorded reading" if recorded else "live reading", "as_read_note": RULE_AS_READ,
                "timings": {k: round(v, 3) for k, v in result.timings.items()}}
        if result.status == "refused":
            view["card"] = self._refusal_card(ship, view)
        else:
            impossible = self._impossible(ship, view)
            if impossible is not None:
                view.update(status="impossible", card=impossible)
        with self._lock:
            self._readings[view["id"]] = {**view, "ship": ship}
            while len(self._readings) > MAX_JOBS:
                self._readings.popitem(last=False)
        return view

    def _hero(self, sentence: str) -> dict | None:
        hero = self.registry["hero"]
        return hero if self.readings.normalise(sentence) == self.readings.normalise(hero["sentence"]) else None

    def _matching_card(self, ship: dict, sentence: str) -> dict | None:
        cards = self.cards()
        for card in cards.get("cards", []):
            if (card.get("call") == ship["key"] and card.get("status") != "unavailable"
                    and self.readings.normalise(card["sentence"]) == self.readings.normalise(sentence)):
                return card
        return None

    def _refusal_card(self, ship: dict, view: dict) -> dict:
        refusal = (view["answer"] or {}).get("refusal") or {}
        base = {"code": refusal.get("code"), "reason": refusal.get("reason"), "sentence": view["sentence"], "bound": False,
                "reader": view["reader"]}
        card = self._matching_card(ship, view["sentence"])
        return {**base, **card, "bound": True} if card and card.get("code") == refusal.get("code") else base

    def _impossible(self, ship: dict, view: dict) -> dict | None:
        """Software's geometry screen on the parsed rule. It makes no model call; a pass is never called feasible."""
        from experiments.mvp.reader.refusal_evidence import build_static_proof
        from experiments.mvp.reader.schema import OnePortPerHatch, validate_answer
        answer = validate_answer(json.dumps(view["answer"]), ship["context"])
        if isinstance(answer, OnePortPerHatch):
            return None
        proof = build_static_proof(ship["instance"], answer)
        if not proof["proven_impossible"]:
            return None
        card = self._matching_card(ship, view["sentence"])
        if card and card.get("constraint") and canonical_key(card["constraint"]) == canonical_key(view["answer"]):
            return {**card, "bound": True}
        return {"status": "static_impossible", "sentence": view["sentence"], "rule": view["rule"], "bound": False,
                "arithmetic": arithmetic(proof), "alternatives": [], "explanation": None, "reader": view["reader"],
                "no_planner_called": True, "checker_report": None}

    # ------------------------------------------------ plan this
    def _reading(self, identity: Any) -> dict:
        with self._lock:
            reading = self._readings.get(identity)
        if reading is None:
            raise KeyError(identity)
        return reading

    def plan(self, reading_id: Any) -> dict:
        from app import check_cache
        reading = self._reading(reading_id)
        if reading["status"] != "constraint_ready":
            raise ValueError("Only a confirmed reading with a rule can be planned")
        self.start_gates()
        ship = reading["ship"]
        scope = {"claim_scope": "parsed_constraint_execution", "sentence_compliance_verified": False,
                 "checked_against": "checked against the rule as read", "rule": reading["rule"], "reader": reading["reader"],
                 "call": ship["key"], "sentence": reading["sentence"]}
        drift = check_cache.epoch_drift()
        case = self.match(ship, reading["answer"])
        if case is None:
            with self._lock:
                live = next((v for v in self._gates.values() if v.get("live_key") == (ship["key"], canonical_key(reading["answer"]))), None)
            if live is not None and drift:
                return {**scope, "kind": "stored", "case": None, "gate": {"status": "not_shown", "why": [drift], "before": None, "after": None}}
            if live is not None:
                return {**scope, "kind": "stored", "case": None, "gate": _gate_json(live), "credits": credits_line(reading["reader"])}
            return {**scope, "kind": "none", "message": NO_STORED_PLAN, "replan_available": self.modes()["live_replan"],
                    "hero": self._hero(reading["sentence"])}
        with self._lock:
            gate = _gate_json(self._gates.get(case["id"], {"status": "pending"}))
        if drift:
            gate = {"status": "not_shown", "why": [drift], "before": None, "after": None}
        same_words = self.readings.normalise(case["sentence"]) == self.readings.normalise(reading["sentence"])
        return {**scope, "kind": "stored", "case": case["id"], "gate": gate, "plan_label": plan_label(case["after"]),
                "recorded_label": "recorded plan, not a live solve", "recorded_wall_s": case["after"]["wall_s"],
                "credits": credits_line(reading["reader"]) if gate.get("status") == "shown" else None,
                "same_words": same_words, "stored_sentence": case["sentence"]}

    def gate_status(self) -> dict:
        with self._lock:
            return {key: _gate_json(value) for key, value in self._gates.items()}

    # ------------------------------------------------ the real replan (one process, flag only)
    def replan(self, reading_id: Any) -> dict:
        if not self.options.get("live_replan"):
            raise PermissionError("Real replans are off; start the app with --brief-live-replan")
        reading = self._reading(reading_id)
        if reading["status"] != "constraint_ready":
            raise ValueError("Only a confirmed reading with a rule can be replanned")
        from app.flow import ConflictError
        with self._lock:
            if self._active is not None:
                raise ConflictError("A replan is already running")
            identity = uuid4().hex
            self._active = identity
            folder = Path(tempfile.mkdtemp(prefix="stowlm-brief-replan-"))
            job = {"id": identity, "status": "running", "stage": "Search and finisher running in their own process",
                   "started": monotonic(), "reading": reading_id, "sentence": reading["sentence"], "rule": reading["rule"],
                   "result": None, "error": None, "cancel": False, "folder": folder, "process": None}
            self._jobs[identity] = job
            while len(self._jobs) > MAX_JOBS:
                old = next(iter(self._jobs))
                if self._jobs[old]["status"] == "running":
                    break
                self._jobs.pop(old)
        ship = reading["ship"]
        request = {"input": ship["call"]["input"], "sentence": reading["sentence"], "envelope": reading["answer"],
                   "evidence": safe_evidence(reading["evidence"]), "search_s": self.options.get("replan_search_s", REPLAN_SEARCH_S),
                   "finalize_s": self.options.get("replan_finalize_s", REPLAN_FINALIZE_S), "output": str(folder / "result.json")}
        (folder / "request.json").write_text(json.dumps(request))
        environment = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1"}
        job["process"] = subprocess.Popen(self.replan_command(folder / "request.json"), cwd=ROOT, env=environment,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=self._watch, args=(job, reading), name="stowlm-brief-replan", daemon=True).start()
        return self.job(identity)

    def _watch(self, job: dict, reading: dict) -> None:
        process = job["process"]
        try:
            try:
                code = process.wait(timeout=REPLAN_HARD_CAP_S)
            except subprocess.TimeoutExpired:
                _stop(process)
                self._end(job, "failed", error=f"Stopped after {REPLAN_HARD_CAP_S:.0f} s. Nothing was shown.")
                return
            if job["cancel"]:
                self._end(job, "cancelled")
                return
            output = job["folder"] / "result.json"
            if code != 0 or not output.is_file():
                self._end(job, "failed", error="The planner process stopped without a result. Nothing was shown.")
                return
            self._after_replan(job, reading, json.loads(output.read_text()))
        except Exception as error:
            self._end(job, "failed", error=f"Replan failed ({type(error).__name__}). Nothing was shown.")
        finally:
            for name in ("request.json", "result.json"):
                (job["folder"] / name).unlink(missing_ok=True)
            try:
                job["folder"].rmdir()
            except OSError:
                pass

    def _end(self, job: dict, status: str, **values) -> None:
        with self._lock:
            job.update(status=status, finished=monotonic(), stage=status, **values)
            if self._active == job["id"]:
                self._active = None

    def _after_replan(self, job: dict, reading: dict, output: dict) -> None:
        from stowage.model import MovePlan
        from experiments.mvp.reader.schema import validate_answer
        ship = reading["ship"]
        evidence = {"claim_scope": output.get("claim_scope"), "sentence_compliance_verified": output.get("sentence_compliance_verified"),
                    "planner_status": output.get("status"), "wall_s": output.get("wall_s")}
        if output.get("plan") is None:
            messages = {"constraint_unavailable": "No checked plan found: this rule can't be compiled on this ship.",
                        "no_checked_plan": "No checked plan found. The search ran out; that doesn't prove the rule is impossible."}
            self._end(job, "done", result={"kind": "no_plan", "message": messages.get(output.get("status"), "No checked plan found."), **evidence})
            return
        plan = MovePlan.model_validate(output["plan"])
        answer = validate_answer(json.dumps(reading["answer"]), ship["context"])
        baseline = ship["call"].get("baseline")
        before = self._stored_plan(baseline) if baseline else None
        job["stage"] = "Fresh engine replay and full checker"
        done = threading.Event()

        def task():
            try:
                plan_evidence = {"method": output.get("method") or "frontier.alns", "wall_s": output.get("wall_s")}
                view = self._finish_gate(ship, answer, before, plan, plan_evidence, recorded=False, label=reading["sentence"])
                key = f"live:{job['id']}"
                with self._lock:
                    self._gates[key] = dict(view, live_key=(ship["key"], canonical_key(reading["answer"])))
                self._end(job, "done", result={"kind": "gated", "gate": _gate_json(view), **evidence,
                                               "credits": credits_line(reading["reader"]) if view["status"] == "shown" else None})
            except Exception as error:
                self._end(job, "failed", error=f"The gates could not run ({type(error).__name__}). Nothing was shown.")
            finally:
                done.set()
        self._submit(f"brief:replan:{job['id']}", task)

    def cancel(self, identity: Any) -> dict:
        with self._lock:
            job = self._jobs.get(identity)
        if job is None:
            raise KeyError(identity)
        if job["status"] == "running" and job.get("process") is not None:
            job["cancel"] = True
            _stop(job["process"])
        return self.job(identity)

    def job(self, identity: str) -> dict:
        with self._lock:
            job = self._jobs[identity]
            return {"id": job["id"], "status": job["status"], "stage": job["stage"], "sentence": job["sentence"],
                    "rule": job["rule"], "result": job["result"], "error": job["error"],
                    "elapsed_s": round(job.get("finished", monotonic()) - job["started"], 1)}

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job["status"] == "running" and job.get("process") is not None:
                job["cancel"] = True
                _stop(job["process"])


def _gate_json(gate: dict) -> dict:
    return {k: v for k, v in gate.items() if k not in {"replays", "live_key"}}


def _stop(process) -> None:
    """Terminate the replan's whole process group; only ever the group this service started."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _replan_command(request: Path) -> list[str]:
    return ["nice", "-n", "5", sys.executable, "-m", "app.brief", "--replan-worker", str(request)]


def wav_info(payload: bytes) -> dict:
    """Mono PCM16 at 16 or 24 kHz, at most 30 s, not silent. Read in memory: nothing touches the disk here."""
    try:
        with wave.open(io.BytesIO(payload), "rb") as clip:
            channels, width, rate, frames = clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getnframes()
            samples = clip.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise ValueError("Audio must be a WAV file") from error
    if channels != 1 or width != 2 or rate not in (16000, 24000):
        raise ValueError("Audio must be mono 16-bit PCM at 16 or 24 kHz")
    seconds = frames / rate
    if not 0 < seconds <= MAX_AUDIO_S:
        raise ValueError(f"Audio must be between 0 and {MAX_AUDIO_S:g} seconds")
    if not any(samples):
        raise ValueError("Audio is silent")
    return {"seconds": round(seconds, 2)}


# ---------------------------------------------------------------- separate replan process
def replan_worker(request_path: str) -> None:
    """Runs ``plan_sentence`` once with the already confirmed reading, then writes one JSON file."""
    from stowage.io import load_instance
    from experiments.mvp.reader.instruction_planner import plan_sentence
    from experiments.mvp.reader.schema import context_from_instance
    request = json.loads(Path(request_path).read_text())
    instance = load_instance(ROOT / request["input"])
    context = context_from_instance(instance)
    parser = _FixedAnswer(request["sentence"], request["envelope"], request["evidence"], context.get("id"))
    started = perf_counter()
    result, evidence = plan_sentence(instance, request["sentence"], parser=parser, search_s=request["search_s"],
                                     finalize_s=request["finalize_s"], seed=0, workers=1)
    output = {"status": evidence.get("status"), "passed": evidence.get("passed"), "claim_scope": evidence.get("claim_scope"),
              "sentence_compliance_verified": evidence.get("sentence_compliance_verified"),
              "wall_s": round(perf_counter() - started, 1), "method": "frontier.alns",
              "plan": result.plan.model_dump(mode="json") if result is not None else None}
    target = Path(request["output"])
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(output))
    temporary.replace(target)


# ---------------------------------------------------------------- HTTP glue (called from app/server.py)
_SERVICE_LOCK = threading.Lock()


def service_for(handler) -> BriefService | None:
    if handler.demo_service is None:
        return None
    cls = type(handler)
    with _SERVICE_LOCK:
        current = getattr(cls, "_brief", None)
        if current is None or current.demo is not handler.demo_service:
            cls._brief = BriefService(handler.demo_service, handler.stage_service(), options=getattr(cls, "brief_options", None))
        return cls._brief


def handle_get(handler, route: str, query: dict) -> None:
    service = service_for(handler)
    if service is None:
        handler.json_response(404, {"error": "not found"})
    elif route == "/api/brief/state":
        handler.json_response(200, service.state())
    elif route == "/api/brief/gates":
        handler.json_response(200, service.gate_status())
    elif route.startswith("/api/brief/jobs/"):
        try:
            handler.json_response(200, service.job(route.removeprefix("/api/brief/jobs/")))
        except KeyError:
            handler.json_response(404, {"error": "Unknown replan job"})
    else:
        handler.json_response(404, {"error": "not found"})


def _same_origin(handler) -> bool:
    if handler.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
        return False
    origin = handler.headers.get("Origin")
    return not origin or origin == f"http://{handler.headers.get('Host')}"


def _body(handler, *, limit: int, kind: str) -> bytes:
    length = int(handler.headers.get("Content-Length", "0"))
    if not 0 < length <= limit:
        raise ValueError(f"Request body must be between 1 and {limit} bytes")
    if handler.headers.get_content_type() != kind:
        raise ValueError(f"Content-Type must be {kind}")
    return handler.rfile.read(length)


def _fields(handler, allowed: set[str], required: set[str]) -> dict:
    request = json.loads(_body(handler, limit=4096, kind="application/json"))
    if not isinstance(request, dict) or set(request) - allowed or required - set(request):
        raise ValueError(f"Expected {', '.join(sorted(allowed))}")
    return request


def handle_post(handler) -> None:
    parts = urlsplit(handler.path)
    route = parts.path
    if not _same_origin(handler):
        handler.json_response(403, {"error": "Cross-origin requests are not accepted"})
        return
    try:
        service = service_for(handler)
    except (ValueError, OSError) as error:
        handler.json_response(400, {"error": str(error)})
        return
    if service is None:
        handler.json_response(404, {"error": "not found"})
        return
    from app.flow import ConflictError
    from urllib.parse import parse_qs
    try:
        if route == "/api/brief/draft":
            request = _fields(handler, {"text", "call"}, {"text"})
            handler.json_response(200, service.draft(request["text"], request.get("call")))
        elif route == "/api/brief/clip":
            handler.json_response(200, service.clip_draft(_fields(handler, {"clip"}, {"clip"})["clip"]))
        elif route == "/api/brief/audio":
            payload = _body(handler, limit=MAX_AUDIO_BYTES, kind="audio/wav")
            handler.json_response(200, service.audio_draft(payload, parse_qs(parts.query).get("call", [None])[0]))
        elif route == "/api/brief/cancel-draft":
            handler.json_response(200, service.cancel_draft())
        elif route == "/api/brief/confirm":
            request = _fields(handler, {"draft", "text"}, {"draft", "text"})
            handler.json_response(200, service.confirm(request["draft"], request["text"]))
        elif route == "/api/brief/plan":
            handler.json_response(200, service.plan(_fields(handler, {"reading"}, {"reading"})["reading"]))
        elif route == "/api/brief/replan":
            handler.json_response(202, service.replan(_fields(handler, {"reading"}, {"reading"})["reading"]))
        elif route == "/api/brief/cancel":
            handler.json_response(200, service.cancel(_fields(handler, {"job"}, {"job"})["job"]))
        else:
            handler.json_response(404, {"error": "not found"})
    except PermissionError as error:
        handler.json_response(403, {"error": str(error)})
    except ConflictError as error:
        handler.json_response(409, {"error": str(error)})
    except KeyError:
        handler.json_response(404, {"error": "Unknown ship, clip, reading or job"})
    except (ValueError, TypeError, UnicodeError) as error:
        handler.json_response(400, {"error": str(error)})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cli.add_argument("--build-registry", action="store_true")
    cli.add_argument("--replan-worker", metavar="REQUEST")
    args = cli.parse_args()
    if args.build_registry:
        built = build_registry()
        print(json.dumps({"cases": [c["id"] for c in built["cases"]], "calls": list(built["calls"])}))
    elif args.replan_worker:
        replan_worker(args.replan_worker)
    else:
        cli.error("nothing to do")
