"""Demo v6 flow (task 2.2): mess in, voice, reading, ship replans, x-ray, break-it, copilot, package.

    GET  /api/v6/state              -> hero, modes and one block per feature ({available, reason, ...})
    GET  /api/v6/intake             -> documents, rows, sources, questions, answers, measured figures
    GET  /api/v6/voice              -> radio clip, peaks, transcript with its wrong words, the recorded fix
    GET  /api/v6/reading            -> the rule as read, who read it (from evidence), the refused radio text
    GET  /api/v6/replan             -> before and after plan ids, gate, moved boxes, gauge walk, rule counts; the plan under the rule
         ?reading=<id>&token=<t>       only for a token issued for that selected reading (no reading: the recorded one), or
         ?recorded=1                   for the recorded answer chosen by name, labelled and logged (code review 12, P2)
    POST /api/v6/confirm            -> the planner confirms the reading on screen: a token bound to sentence, rule and ship
    POST /api/v6/replan  {token}    -> live replan in its own process (--brief-live-replan only; needs that token)
    GET  /api/v6/replan/<job>       -> its real elapsed seconds and incumbents
    GET  /api/v6/xray               -> reader trace (the player checks provenance itself)
    GET  /api/v6/sandbox            -> prepared drags and their recorded verdicts
    GET  /api/v6/copilot            -> recorded negotiation: turns, tool ribbon, arithmetic, offer
    GET  /api/v6/package            -> package index, note to the master, receipt summary
    GET  /api/v6/asset/<name>       -> allow-listed file (tally sheet, radio wav, x-ray player, bay plan)

**A feature that isn't there is a clean gap.** ``feature()`` turns any missing fixture or failed load into
``{available: false, reason}``. The page shows nothing for that chapter's panel and skips it; it never shows a broken one.

**Labels come from the fixture, not from here.** Each block carries the mode its producer recorded. No path in this
module calls a model, opens a socket or needs a GPU. Live paths stay behind the launch flags in ``app/__main__.py``.

**Models propose, software decides.** Nothing here approves a plan: ``replan()`` reports the full checker's verdict and
the instruction recomputed on the plan, and a stored plan that fails is not shown.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import importlib.util
import json
import logging
import math
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import wave
from pathlib import Path
from time import monotonic, time
from uuid import uuid4

from app import v6_build
from app.flow import ConflictError
from app.v6_fixtures import Withheld, dicts, dig, number, optional, read_optional
from app.v6_package import PackageFile

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "experiments/mvp/pipeline"
HERO_FILE = Path(__file__).with_name("v6_hero.json")
INTAKE = PIPELINE / "intake/fixtures/dev"
HELDOUT = PIPELINE / "intake/fixtures/heldout"
INTAKE_DEMO = PIPELINE / "intake/fixtures/demo_v2"
INTAKE_REALISTIC = PIPELINE / "intake/fixtures/realistic_v7"
INTAKE_AUTHORITY = PIPELINE / "intake/fixtures/authority_v8"
# First file that exists wins. Every path named here exists (the film freeze pins it): the development replay of the spent 60 boxes.
# The sealed intake rows were scored in another setup (row-level, D273), which isn't this chapter's whole-document reading, so none is listed.
INTAKE_DEVELOPMENT = PIPELINE / "intake/final_development_checks.json"
INTAKE_RESULTS = (INTAKE_DEVELOPMENT,)
FILL = ROOT / "experiments/mvp/video/fill-v6.json"
XRAY = PIPELINE / "xray"
PACKAGE = PIPELINE / "package/fixtures/complete"
PROFILE = PIPELINE / "runner/fixtures/profile-selection.json"
SANDBOX = PIPELINE / "sandbox/fixtures"
COPILOT = PIPELINE / "copilot/fixtures"
READER_PARSER = ROOT / "experiments/mvp/reader/v3/parser/sealed-v3-report.json"
ADOPTIONS = tuple(ROOT / "experiments/mvp/reader/v3/results-final" / name for name in ("final-adoption-d266.json", "final-adoption.json"))  # the successor leads
FILM_FIGURES = ROOT / "experiments/mvp/ui/fixtures/film-figures.json"
VOICE = ROOT / "experiments/mvp/voice/runs/v5-1.3"
LATTICE_STORE = PIPELINE / "lattice/store"
HERO_CACHE = ROOT / "experiments/mvp/ui/cache/v6-hero"  # plan files cut from stored entries, the scoped store view, regate results
REGATE_CACHE = ROOT / "experiments/mvp/ui/cache/regate"  # exact memos of the launch regate; delete freely
LIVE_DIR = ROOT / "experiments/mvp/ui/hero-live"

SYNTHETIC = "Synthetic ships and cargo, simulated port"
RECORDED = "Recorded answer"
log = logging.getLogger(__name__)


class IntakeFile:
    """A file of whichever recorded case the chapter plays, read per request."""

    def __init__(self, name: str):
        self.name = name

    def read_bytes(self) -> bytes:
        return (_intake_case() / self.name).read_bytes()


ASSETS = {  # name -> (path, mime). Only these names are served; nothing here comes from the request.
    "tally_sheet.png": (IntakeFile("tally_sheet.png"), "image/png"),
    "xray-player.js": (XRAY / "player.js", "text/javascript; charset=utf-8"),
    "xray-player.css": (XRAY / "player.css", "text/css; charset=utf-8"),
    "bay-plan.html": (PackageFile("bay-plan.html", display=True), "text/html; charset=utf-8"),
    "package-crane-sequence.csv": (PackageFile("crane-sequence.csv", display=True), "text/plain; charset=utf-8"),
    "package-note-to-master.md": (PackageFile("note-to-master.md", display=True), "text/plain; charset=utf-8"),
    "package-stability.json": (PackageFile("stability.json", display=True), "application/json"),
    "package-not-included.html": (PackageFile("not-included.html", display=True), "text/html; charset=utf-8"),
    "package-ballast-plan.csv": (PackageFile("ballast-plan.csv", display=True), "text/plain; charset=utf-8"),
    "package-manifest.csv": (PackageFile("manifest.csv", display=True), "text/plain; charset=utf-8"),
    "package-cargo-work.json": (PackageFile("cargo-work.json", display=True), "application/json"),
    "package-receipt.json": (PackageFile("receipt.json"), "application/json"),
    "replay.html": (ROOT / "experiments/mvp/ui/replay/index.html", "text/html; charset=utf-8"),
}


def hero() -> dict:
    """app/v6_hero.json with the slot it names resolved. No hero fact is written anywhere else: they all come from the slot."""
    config = hero_config()
    return {**config, **_hero_slot(config)} if config.get("hero_source") else config


def hero_config() -> dict:
    """app/v6_hero.json as written."""
    config = json.loads(HERO_FILE.read_text())
    if not isinstance(config, dict):
        raise ValueError("app/v6_hero.json is not an object")
    return config


def _settings() -> dict:
    """For a chapter that needs only the file's own settings: a slot that can't be resolved isn't its gap."""
    config = optional(hero)
    return config if config is not None else hero_config()


def _json(path: Path):
    return json.loads(Path(path).read_text())


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stored_row(key: str) -> dict:
    """One lattice entry, only if its bytes match the catalogue. This reads; the launch regate is what clears it to show."""
    path = LATTICE_STORE / f"{key}.json"
    if _json(LATTICE_STORE / "catalogue.json").get(key) != _file_sha256(path):
        raise ValueError(f"lattice entry {key[:12]} does not match its catalogue")
    return _json(path)


def _plan_file(plan: dict) -> tuple[str, str]:
    """(path, canonical sha256) of a stored plan as a file the loaders and precompute_timeline.py can read."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    digest = sha256_json(plan)
    path = HERO_CACHE / "plans" / f"{digest[:16]}.plan.json"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(plan))
        temporary.replace(path)
    return str(path), digest


def _instruction(path: Path, instance_sha256: str) -> dict | None:
    """The slot's rule, or None while its fixture hasn't landed: the chapter then holds the plan before, a clean gap."""
    if not path.is_file():
        return None
    data = _json(path)
    rules, sentences = data.get("canonical_rules"), data.get("sentences")
    if data.get("instance_sha256") != instance_sha256 or not isinstance(rules, list) or len(rules) != 1 or not sentences:
        raise ValueError(f"{path.name} is not one rule for this hero")
    return {"sentence": sentences[0], "rule": rules[0], "canonical_rules": rules}


SELECTED = "hero_wording"  # instructions.<slot> set to this: the selected hero wording's own reading is the rule, no fixture file


def _selected_instruction(config: dict, context: dict) -> dict | None:
    """The hero wording (D268 as amended) and the rule the adopted reader read it to, typed: the ship replans under exactly what was read.
    No exact typed reading, or a reading that doesn't fit this ship: None, and the chapter holds the plan before, a clean gap."""
    if not (config.get("radio") or {}).get("readings"):
        return None
    pick = _build_wording(config["radio"], config.get("hero_slot"))
    typed = pick["typed"] or {}
    raw = dig(typed, "evidence", "raw")
    if typed.get("binds") is not True or not isinstance(raw, str) or is_refusal(raw):
        return None
    rule = json.loads(raw)
    if optional(lambda: _on_this_ship(rule, context) or True) is None:
        return None
    return {"sentence": pick["sentence"], "rule": rule, "canonical_rules": [rule], "source": SELECTED}


def _build_slot(config: dict) -> dict:
    from experiments.mvp.pipeline.lattice.store import entry_key
    name = config["hero_slot"]
    row = _json(ROOT / config["hero_source"])[name]
    if _file_sha256(ROOT / row["path"]) != row["file_sha256"]:
        raise ValueError(f"{row['path']} does not match the hero source's file_sha256")
    instance_sha256, numbers = row["instance_sha256"], row["envelope"]["numbers"]
    named = (config.get("instructions") or {}).get(name)
    if named == SELECTED:
        instruction = _selected_instruction(config, row["context"])
    else:
        instruction = _instruction(ROOT / named, instance_sha256) if named else None
    base_key = entry_key(instance_sha256, [])
    base, keys = _stored_row(base_key), {"before": base_key}
    before_plan, before_sha = _plan_file(base["plan"])
    slot = {"slot": name, "instance": row["path"], "instance_sha256": instance_sha256, "file_sha256": row["file_sha256"], "context": row["context"],
            "envelope": row["envelope"], "instruction": instruction, "before_plan": before_plan, "before_sha256": before_sha, "after_plan": None, "after_sha256": None,
            "before_note": f"Plan before: {base.get('producer_label') or 'Frontier Engine ' + str(base.get('engine_version'))}",
            "engines": {"before": base.get("engine_version")}, "producers": {"before": base.get("producer_label")}, "computed_utc": None, "keys": keys, "ship": _ship_words(numbers)}
    if instruction:
        keys["after"] = entry_key(instance_sha256, instruction["canonical_rules"])
        after = optional(lambda: _stored_row(keys["after"]))  # not stored yet: the chapter holds the plan before
        if after:
            slot["after_plan"], slot["after_sha256"] = _plan_file(after["plan"])
            slot["engines"]["after"], slot["computed_utc"] = after.get("engine_version"), after.get("computed_utc")
            slot["producers"]["after"] = after.get("producer_label")  # None: the engine's own plan, no post-processing
    return slot


def _hero_slot(config: dict) -> dict:
    """Cuts the plan files once per change of the sources it reads."""
    named = (config.get("instructions") or {}).get(config["hero_slot"])
    own = _radio_paths(config["radio"]) if named == SELECTED and (config.get("radio") or {}).get("readings") else [ROOT / named] if named else []
    paths = [ROOT / config["hero_source"], LATTICE_STORE / "catalogue.json"] + own
    return _memoised(f"hero_slot:{config['hero_source']}:{config['hero_slot']}:{named}", paths, lambda: _build_slot(config))


def _ship_kind(numbers: dict) -> tuple[str, int | None]:
    match = re.fullmatch(r"([a-z]+)_(\d+)", str(numbers.get("ship")))
    return (match[1], int(match[2])) if match else ("container", None)


def _ship_words(numbers: dict) -> str:
    """The ship line, from the slot's own envelope numbers."""
    kind, teu = _ship_kind(numbers)
    size = f"{teu} TEU · " if teu else ""
    return f"Synthetic {kind}, {size}{numbers['boxes_to_load']} boxes · {round(100 * numbers['physical_cell_fill'])} % fill"


def feature(load) -> dict:
    """One chapter's data, or a clean gap with the reason. Never raises into the page."""
    try:
        value = load()
    except Exception as error:  # noqa: BLE001 - a missing or half-written fixture is an expected state during the build
        log.info("v6 feature %s unavailable: %s", getattr(load, "__name__", "?"), error)
        return {"available": False, "reason": (str(error) if isinstance(error, Withheld) else f"{type(error).__name__}: {error}")[:180]}
    value.setdefault("available", True)
    return value


# ---------------------------------------------------------------- intake
_DOC_LABELS = {"booking_email": ("Forwarded booking email", "email"), "gate_dump": ("Gate spreadsheet dump", "sheet"),
               "tally_sheet": ("Photographed tally sheet", "photo")}
_ROLE_LABELS = {"booking": ("Booking confirmation", "email"), "shipping_instructions": ("Shipping instructions sheet", "sheet"),
                "tally": _DOC_LABELS["tally_sheet"]}
_DOC_SHORT = {"email": "booking email", "sheet": "gate sheet", "photo": "tally photo"}
_COLUMNS = ("id", "iso", "pod", "mass", "dg", "reefer")
_FIELD_COLUMN = {"id": "id", "pod": "pod", "gross_mass_kg": "mass", "dg": "dg", "reefer": "reefer", "reefer_setpoint_c": "reefer"}
_FIELD_WORDS = {"pod": "port of discharge", "gross_mass_kg": "weight", "dg": "dangerous-goods declaration", "reefer": "reefer details",
                "reefer_setpoint_c": "reefer set point", "iso_type": "container type", "oog": "out-of-gauge status",
                "limited_quantity": "limited quantity", "net_mass_kg": "net weight", "tare_kg": "tare weight"}
_PAPER_WORDS = {"eir_gate_slips": "gate slip", "vgm_declaration": "VGM declaration", "dg_declaration": "dangerous-goods declaration",
                "tally_sheet": "tally photo", "booking_confirmation": "booking confirmation", "shipping_instructions": "shipping instructions",
                "reefer_list": "reefer list", "amendment_1": "amendment"}
_DEVELOPMENT_SCOPE = "development, realistic synthetic documents"
_CLOSED_WORLD_LISTS = {"dg": "dangerous-goods", "reefer": "reefer", "oog": "out-of-gauge"}
_CHECKS = (("dg", "Dangerous-goods tables"), ("ship", "Ship limits and rotation"), ("agree", "Cross-document agreement"),
           ("declared", "Declarations complete"))
_REASON_CHECK = {"unknown_un": "dg", "container_validation": "ship", "ship_capability": "ship", "pod_not_on_rotation": "ship",
                 "cross_document_conflict": "agree", "duplicate_id": "agree", "invalid_field": "declared",
                 "unsupported_source": "declared", "missing_reefer_setpoint": "declared", "amendment_review": "agree"}
_NO_CARD_REASONS = {"unverified_transcription", "missing_field"}  # bookkeeping: answered with the others, nobody watches those
# The conflict goes first: both documents light up at once, which is the film's held question.
_ASK_ORDER = ("cross_document_conflict", "amendment_review", "unmapped_sheet_headers", "model_sheet_value", "container_validation", "unknown_un",
              "missing_reefer_setpoint", "pod_not_on_rotation", "invalid_field", "duplicate_id")
_memo: dict = {}
_memo_lock = threading.Lock()


def _memoised(key: str, paths, build):
    """Fixtures change rarely and weigh 12 MB: parse once per change, not once per /api/v6/state."""
    stamp = tuple((str(p), Path(p).stat().st_mtime_ns if Path(p).is_file() else 0) for p in paths)
    with _memo_lock:
        hit = _memo.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    value = build()
    with _memo_lock:
        _memo[key] = (stamp, value)
    return value


def _intake_case() -> Path:
    """The newest recording that's complete, else the original."""
    for case in (INTAKE_AUTHORITY / "demo", INTAKE_REALISTIC / "demo"):
        if all((case / name).is_file() for name in ("reading.json", "final.json", "assembly.json")):
            return case
    return INTAKE_DEMO if dig(read_optional(INTAKE_DEMO / "metrics.json"), "status") == "complete" else INTAKE


def _cells(row: dict) -> dict:
    """One row as the load list shows it. A value the validators blocked is None here, so it reads as a question."""
    dg, mass, setpoint = row.get("dg"), number(row.get("gross_mass_kg")), number(row.get("reefer_setpoint_c"))
    reefer = "" if not row.get("reefer") else "?" if setpoint is None else f"{setpoint:g} °C".replace("-", "−")
    return {"id": row.get("id") or "?", "iso": row.get("iso_type") or "?", "pod": row.get("pod") or "?",
            "mass": f"{round(mass):,}" if mass is not None else "?", "reefer": reefer,
            "dg": f"UN {dg.get('un_number')}" if isinstance(dg, dict) else ""}


def _csv_cell(grid: list, row_id: str, quote: str) -> str | None:
    """Spreadsheet address of a quote in this box's own row. Some rejected sources carry no address of their own."""
    for number_, cells in enumerate(grid, 1):
        if cells and cells[0] == row_id and quote in cells:
            return f"{chr(65 + cells.index(quote))}{number_}"
    return None


def _spot(source: dict, photo_lines: list, grid: list) -> dict:
    """Where a source sits on its document: a sheet cell, a line of the photo, or quoted text."""
    document, quote = source.get("document"), source.get("quote", "")
    if document == "quay_photo" and quote in photo_lines:
        return {"document": document, "photo": photo_lines.index(quote), "quote": quote, "evidence": source.get("evidence")}
    if document == "gate_csv":
        return {"document": document, "cell": source.get("cell") or _csv_cell(grid, source.get("row_id"), quote)}
    return {"document": document, "quote": quote, "evidence": source.get("evidence")}


def _flights(rows: list, sources: list, pods: set, photo_lines: list, grid: list) -> list:
    """Every value that lifts off a document: where from (what it says there) and which load-list cell it lands in."""
    flights = []

    def take(index: int, column: str, source: dict, token: str) -> None:
        view = _cells(rows[index])
        flights.append({"row": index, "column": column, "from": token, "to": view[column], **_spot(source, photo_lines, grid)})

    def fly(index: int, row: dict) -> None:
        own = sorted((s for s in sources if s.get("row") == index), key=lambda s: order.get(s.get("document"), 3))
        first = lambda field: next((s for s in own if s.get("field") == field), None)
        if first("id"):
            take(index, "id", first("id"), row["id"])
        iso = first("iso_type")
        if iso:
            take(index, "iso", iso, iso["quote"])
        seen = set()
        for source in (s for s in own if s.get("field") == "pod"):
            value = next((p for p in pods if p in source.get("quote", "")), None)
            if value and source.get("document") not in seen:  # each paper that names a port flies once; two values land on one cell
                seen.add(source.get("document"))
                take(index, "pod", source, value)
        typed = next((s for s in own if s.get("field") == "gross_mass_kg" and s.get("column") == 5), None) \
            or next((s for s in own if s.get("field") == "gross_mass_kg" and s.get("document") == "gate_csv"), None)
        if typed:
            take(index, "mass", typed, typed.get("cell_value") or typed["quote"])
        un = next((s for s in own if s.get("field") in {"dg.un_number", "dg"} and s.get("document") == "booking_email"), None)
        if isinstance(row.get("dg"), dict) and un:
            match = re.search(r"UN\s?\d+", un.get("quote", ""))
            take(index, "dg", un, match.group() if match else f"UN {row['dg'].get('un_number')}")
        cold = next((s for s in own if s.get("field") == "reefer" and s.get("document") == "booking_email"), None)
        if row.get("reefer") and cold:
            match = re.search(r"RF=\w+", cold.get("quote", ""))
            take(index, "reefer", cold, match.group() if match else "reefer")

    order = {"booking_email": 0, "quay_photo": 1, "gate_csv": 2}
    for index, row in enumerate(rows):
        optional(lambda: fly(index, row))  # a row whose sources changed shape just has no flights
    return flights


def _said_port(source: dict) -> str | None:
    quote = source.get("quote", "")
    match = re.search(r"POD\s+([A-Z]{5})\b", quote) or re.search(r"\b([A-Z]{5})\b", quote)
    return match.group(1) if match else None


def _surface_conflicts(reading: dict) -> dict:
    """The stricter validator files a disagreement between documents as an unverified port. Where the quotes really name
    different ports, show it as what it is: the row's port is unknown and the question is which document is right."""
    rows, sources, questions = list(dicts(reading.get("rows"))), dicts(reading.get("field_sources")), []
    for question in dicts(reading.get("questions")):
        said = [_said_port(s) for s in sources if s.get("row") == question.get("row") and s.get("field") == "pod"]
        ports = list(dict.fromkeys(p for p in said if p))
        at = question.get("row")
        if question.get("reason") == "unverified_transcription" and question.get("field") == "pod" and len(ports) > 1 \
                and isinstance(at, int) and at < len(rows):
            question = {**question, "reason": "cross_document_conflict", "candidates": [json.dumps(p) for p in ports]}
            rows[at] = {**rows[at], "pod": None}
        questions.append(question)
    return {**reading, "questions": questions, "rows": rows, "field_sources": sources}


def _ask_plain(box: str, field: str) -> str:
    """Any question this page has no sentence for yet."""
    return f"Box {box}: the documents leave its {_FIELD_WORDS.get(field, field.replace('_', ' ') or 'details')} open. What's the right value?"


def _ask(reason: str, row: dict, doc_says: dict, field: str) -> str:
    """The validator's raw prompt, rewritten as one plain sentence from the fields of the row."""
    box = row.get("id") or "?"
    if reason == "cross_document_conflict" and doc_says:
        said = " ".join(f"The {' and the '.join(docs)} {'say' if len(docs) > 1 or docs[0].endswith('instructions') else 'says'} {value}." for value, docs in doc_says.items())
        root = field.split(".")[0]
        question = {"pod": f"Where does box {box} go?", "gross_mass_kg": f"What does box {box} really weigh?"}.get(root)
        word = _FIELD_WORDS.get(field.split(".")[-1], field.split(".")[-1].replace("_", " "))
        return f"{said} {question or f'Which {word} is right for box {box}?'}"
    if reason == "model_sheet_value":
        return f"The reefer list has box {box} at {_cells(row)['reefer'] or 'a set point'}, but the software couldn't bind that column to a verified header. Is that right?"
    if reason == "amendment_review":
        return f"An amendment names box {box}. Nothing changes until a person reviews it. Has it been reviewed?"
    if reason == "unknown_un":
        return f"Box {box} is declared UN {row['dg'].get('un_number')}. That number isn't in our local dangerous-goods table. What's the right declaration?"
    if reason == "container_validation":
        return f"Box {box} is listed at {round(row['gross_mass_kg']):,} kg. Its rating is {round(row['max_gross_mass_kg']):,} kg. What does it really weigh?"
    if reason == "pod_not_on_rotation":
        return f"Box {box} is booked to {row['pod']}, which isn't a later call on this rotation. Where is it going?"
    if reason == "missing_reefer_setpoint":
        return f"Box {box} is a reefer and nobody has sent its set point. What temperature, in °C?"
    if reason in {"invalid_field", "unsupported_source"} and field == "gross_mass_kg":
        return f"Box {box} has no verified weight, and the software won't assume one. What does it weigh?"
    if reason == "duplicate_id":
        return f"Box {box} appears twice in the gate sheet. Is it one physical box?"
    return _ask_plain(box, field.split(".")[-1])


def _ask_document(question: dict) -> str:
    """One document, one task: said once, however many boxes it touches."""
    paper = re.sub(r"^[a-z0-9]+-", "", str(question.get("field") or "").split(":")[-1])
    return f"The {_PAPER_WORDS.get(paper, paper.replace('_', ' '))} has a header row the software can't verify. One review covers the whole sheet, not each box. Has a person reviewed it?"


def _candidate(value):
    return optional(lambda: json.loads(value), value) if isinstance(value, str) and value.startswith('"') else value


def _shown_value(field: str, value):
    """A candidate as the load list would write it."""
    amount = number(value) if not isinstance(value, str) else optional(lambda: float(value))
    return f"{round(amount):,}" if field == "gross_mass_kg" and amount is not None else str(value)


def _said_by(candidates: list, sources: list, field: str, kinds: dict) -> dict:
    """{value: the documents saying it}. Newer candidates read 'document: value'; an amendment's is its sentence; older ones are the bare value."""
    said: dict = {}
    for raw in candidates:
        text = str(_candidate(raw))
        match = re.match(r"([a-z0-9]+(?:[-_][a-z0-9]+)+): (.+)$", text, re.S)
        if match:
            paper = re.sub(r"^[a-z0-9]+-", "", match[1])
            value, doc = _shown_value(field, _candidate(match[2])), _PAPER_WORDS.get(paper, paper.replace("_", " "))
        else:
            port = re.search(r"\b[A-Z]{5}\b", text) if field == "pod" and len(text) > 5 else None
            value = port[0] if port else _shown_value(field, _candidate(raw))
            doc = "amendment" if port else next((_DOC_SHORT.get(kinds.get(s.get("document")), s.get("document")) for s in sources if text in s.get("quote", "")), "a document")
        if doc not in said.setdefault(value, []):
            said[value].append(doc)
    return said


def _answer_words(answer) -> str:
    if isinstance(answer, dict) and answer.get("field") == "dg" and "value" in answer and answer["value"] is None:
        return "Not dangerous goods"
    value = answer if not isinstance(answer, dict) else answer["value"] if answer.get("value") is not None else str(answer.get("action") or "").replace("_", " ").capitalize() or None
    return "Answered" if value is None or isinstance(value, (dict, list)) else str(value).lower() if isinstance(value, bool) else str(value)


def _card(key: tuple, group: list, reading: dict, answers: dict, final_rows: list, kinds: dict, photo_lines: list, grid: list) -> dict:
    index, column = key
    document = index is None  # one card for the whole document, whatever it touches
    column = None if document else column
    fields = {str(q.get("field") or "").split(".")[0] for q in group}
    field = next(iter(sorted(fields)))
    leaf = str(group[0].get("field") or field).split(".")[-1]
    rows = reading["rows"]
    known = isinstance(index, int) and 0 <= index < len(rows)
    row = rows[index] if known else {}
    final_row = final_rows[index] if known and index < len(final_rows) else {}
    after = _cells(final_row)
    reason = next((r for r in _ASK_ORDER if any(q.get("reason") == r for q in group)), group[0].get("reason"))
    sources = [s for s in reading["field_sources"] if s.get("row") == index and str(s.get("field")).split(".")[0] in fields and not s.get("accepted")]
    spots, seen = [], set()
    for source in sources:
        spot = _spot(source, photo_lines, grid)
        place = spot.get("cell") or spot.get("quote")
        if place not in seen and len(spots) < 4:
            seen.add(place)
            spots.append(spot)
    candidates = [_candidate(c) for c in group[0].get("candidates") or []]
    doc_says = _said_by(group[0].get("candidates") or [], sources, field, kinds) if reason == "cross_document_conflict" else {}
    answer = answers.get(group[0].get("id"))
    landed = after.get(column) if column else None
    landed = _answer_words(answer) if landed in (None, "", "?") else landed
    recorded = "One box" if reason == "duplicate_id" else landed
    options = [{"label": recorded, "recorded": True}]
    choices = list(doc_says) or [str(c) for c in candidates if isinstance(c, (str, int, float))]
    options += [] if document else [{"label": c, "recorded": False} for c in choices if c not in (landed, "keep_one") and len(c) <= 24]
    merged = {**final_row, **{k: v for k, v in row.items() if v is not None}}
    box = merged.get("id") or "?"
    return {"id": group[0].get("id"), "ids": [q.get("id") for q in group], "row": index if known else None, "box": box,
            "column": column, "reason": reason, "check": _REASON_CHECK.get(reason, "declared"), "raw_prompt": group[0].get("prompt"),
            "scope": "document" if document else "box",
            "text": _ask_document(group[0]) if document else optional(lambda: _ask(reason, merged, doc_says, str(group[0].get("field") or field))) or _ask_plain(box, leaf),
            "options": sorted(options, key=lambda o: o["label"]), "after": landed, "spots": spots,
            "evidence": answer.get("quote") if isinstance(answer, dict) else None}


def _questions(reading: dict, answers: dict, final_rows: list, kinds: dict, photo_lines: list, grid: list) -> list:
    """One card per row and field, and one per document question. A question this page has no words for still gets a card, in plain generic wording."""
    groups: dict = {}
    for question in reading["questions"]:
        field = str(question.get("field") or "")
        root = field.split(".")[0]
        column = _FIELD_COLUMN.get(root, root)
        one_source = question.get("reason") == "needs_corroboration" and column not in _COLUMNS  # a second paper for a field nobody watches
        if question.get("reason") not in _NO_CARD_REASONS and "shipment_provenance" not in field and not one_source:
            key = (None, question.get("id")) if question.get("row") is None else (question.get("row"), column if column in _COLUMNS else None)
            groups.setdefault(key, []).append(question)
    cards = [optional(lambda key=key, group=group: _card(key, group, reading, answers, final_rows, kinds, photo_lines, grid)) for key, group in groups.items()]
    return sorted((c for c in cards if c), key=lambda c: _ASK_ORDER.index(c["reason"]) if c["reason"] in _ASK_ORDER else 99)


def _figure_rows(row: dict, floor: dict) -> list:
    """The closing table: [what, the model with software, the parser with the same gates]. A figure one side lacks drops the row."""
    def ratio(side: dict, top: str, bottom: str) -> str | None:
        return f"{side[top]:g} of {side[bottom]:g}" if number(side.get(top)) is not None and number(side.get(bottom)) else None

    def share(side: dict, top: str, bottom: str) -> str | None:
        return f"{round(100 * side[top] / side[bottom], 2):g} %" if number(side.get(top)) is not None and number(side.get(bottom)) else None

    rows = [("Fields", share, "fields_correct", "fields_total"), ("Rows exact", ratio, "rows_exact", "rows_total"),
            ("Traps stopped", ratio, "planted_problems_stopped", "planted_problems_total"), ("Boxes stopping", share, "boxes_stopped", "rows_total")]
    table = [[what, how(row, top, bottom), how(floor, top, bottom)] for what, how, top, bottom in rows]
    return [line for line in table if line[1] and line[2]]


def _map_figures(data: dict) -> dict | None:
    """One results file as {intake_pct, fields, baseline_name, baseline_pct, scope}. No baseline or no scope: no figures."""
    if "intake_field_accuracy_pct" in data:  # the story numbers (script-v6, shot 3)
        documents = data.get("intake_documents")
        out = {"name": "Muse Spark", "intake_pct": number(data["intake_field_accuracy_pct"]), "fields": None, "table": [],
               "baseline_name": data.get("intake_baseline_name"), "baseline_pct": number(data.get("intake_baseline_pct")),
               "scope": data.get("intake_scope") or (f"{documents} held-out synthetic documents" if documents else None)}
    else:  # the campaign's comparison (intake/campaign_v7.py): the model with software, and the parser with the same gates
        development = "development_methods" in data
        methods = data["development_methods"] if development else data["methods"]
        baseline_name = next(name for name in methods if "parser" in name.lower())
        name = next(name for name in methods if name != baseline_name)
        row, floor = methods[name], methods[baseline_name]
        correct, total = number(row["fields_correct"]), number(row["fields_total"])
        split = "Sealed, scored once" if "sealed" in str(data.get("evaluation_split")) else "Held-out" if "blind" in str(data.get("evaluation_split")) else None
        out = {"name": name, "intake_pct": round(100 * correct / total, 2), "fields": f"{correct:.0f} of {total:.0f}", "table": _figure_rows(row, floor),
               "baseline_name": baseline_name, "baseline_pct": round(100 * number(floor["fields_correct"]) / number(floor["fields_total"]), 2),
               "scope": _DEVELOPMENT_SCOPE if development else " · ".join(x for x in (split, data.get("caption")) if x) if data.get("caption") else None}
    return out if out["intake_pct"] is not None and out["baseline_pct"] is not None and out["baseline_name"] and out["scope"] else None


def intake_figures() -> dict | None:
    """The one seam for intake's closing figures: whichever new results exist, beside the parser. None: the page shows no figures."""
    for data in (_fill(), *(read_optional(path) for path in INTAKE_RESULTS)):
        figures = optional(lambda: _map_figures(data)) if isinstance(data, dict) else None
        if figures:
            return figures
    return None


def _intake_summary(figures: dict | None, rows: int, complete: bool, caption: str | None = None) -> dict:
    """Completion here follows synthetic planner answers: it's a demo interaction, never accuracy. The accuracy is in the figures."""
    lines = []
    if figures and not figures.get("table"):
        lines = [f"{figures['name']}: {figures['intake_pct']:g} % of fields", f"{figures['baseline_name']}: {figures['baseline_pct']:g} % · {figures['scope']}"]
    table = {"head": ["", figures["name"], figures["baseline_name"]], "rows": figures["table"], "scope": figures["scope"]} if figures and figures.get("table") else None
    scope = " ".join(x for x in (caption, "Planner answers here are synthetic, not accuracy.") if x)
    return {"headline": f"{rows}-box demo complete after synthetic answers" if complete else f"{rows} rows read", "lines": lines, "table": table, "figures": figures, "scope": scope}


def _derived(sources: list, rows: list) -> tuple:
    """Policy-derived facts, kept apart from read ones: ({row: columns reading "none"}, one plain line, hover text)."""
    closed = [s for s in sources if s.get("derived") and s.get("evidence") == "closed_world_declaration"]
    tares = [s for s in sources if s.get("derived") and s.get("evidence") == "derived_iso_tare"]
    marks: dict = {}
    for source in closed:
        at = source.get("row")
        column = {"dg": "dg", "reefer": "reefer"}.get(source.get("field"))
        if column and not source.get("value") and isinstance(at, int) and at < len(rows):
            marks.setdefault(at, set()).add(column)
    lists = [word for field, word in _CLOSED_WORLD_LISTS.items() if any(s.get("field") == field for s in closed)]
    parts = [f"“none” is derived from the complete {', '.join(lists)} lists, not read from a cell"] if lists else []
    if tares:
        parts.append(f"{len(tares)} tare derived from its ISO type, approximate" if len(tares) == 1 else f"{len(tares)} tares derived from ISO type, approximate")
    return marks, " · ".join(parts), " · ".join(str(s.get("quote")) for s in tares)


def _doc_view(doc: dict, kinds: dict) -> dict:
    stem = Path(doc.get("path") or doc["id"]).stem
    title, kind = _DOC_LABELS.get(doc["id"]) or _ROLE_LABELS.get(doc.get("role")) or _DOC_LABELS.get(stem, (doc["id"], "text"))
    is_image = str(doc.get("mime_type", "")).startswith("image/")
    kinds[doc["id"]] = kind
    view = {"id": doc["id"], "title": title, "kind": kind, "text": doc.get("text"),
            "label": "Synthetic document · Generated picture" if is_image else "Synthetic document",
            "image": "/api/v6/asset/tally_sheet.png" if is_image else None}
    if kind == "sheet":
        view["grid"], view["text"] = list(csv.reader(io.StringIO(doc.get("text") or ""))), None
    return view


def _on_the_desk(docs: list, kinds: dict, sources: list) -> tuple:
    """The desk has one slot per kind of paper. The first document of a kind takes the slot; sources follow it by name."""
    slots, alias = {"email": "booking_email", "photo": "quay_photo", "sheet": "gate_csv"}, {}
    for doc in docs:
        slot = slots.get(doc["kind"])
        if slot and slot not in alias.values():
            alias[doc["id"]], doc["id"] = slot, slot
    return {alias.get(key, key): kind for key, kind in kinds.items()}, [{**s, "document": alias.get(s.get("document"), s.get("document"))} for s in sources]


def _outcomes(read_rows: list, final_rows: list) -> list:
    """Each read row's final row, by box id: a box shut out of the final list has none."""
    by_id = {row.get("id"): row for row in final_rows}
    return [by_id.get(row.get("id"), final_rows[i] if len(final_rows) == len(read_rows) else {}) for i, row in enumerate(read_rows)]


def _build_intake(case: Path) -> dict:
    reading = _surface_conflicts(_json(case / "reading.json"))  # no reading, no chapter: the gap is feature()'s
    final, answers = read_optional(case / "final.json", {}), read_optional(case / "answers.json", {})
    metrics = read_optional(case / "metrics.json", {})
    answers = answers if isinstance(answers, dict) else {}
    final_rows = dicts(dig(final, "rows"))
    read_rows = reading["rows"] or final_rows
    if not read_rows:
        raise ValueError("no load list in this recording")
    kinds = {}
    docs = [view for view in (optional(lambda doc=doc: _doc_view(doc, kinds)) for doc in dicts(optional(lambda: _json(case / "documents.json")))) if view]
    kinds, sources = _on_the_desk(docs, kinds, reading["field_sources"])
    reading = {**reading, "rows": read_rows, "field_sources": sources}
    grid = next((doc["grid"] for doc in docs if doc["kind"] == "sheet"), [])
    photo_lines = list(dict.fromkeys(s["quote"] for s in sources if s.get("document") == "quay_photo" and s.get("quote")))
    pods = {r.get("pod") for r in read_rows + final_rows} - {None}
    pods |= {_candidate(c) for q in reading["questions"] for c in q.get("candidates") or [] if isinstance(c, str) and c.startswith('"')}
    outcomes = _outcomes(read_rows, final_rows) if final_rows else []
    questions = _questions(reading, answers, outcomes, kinds, photo_lines, grid) if outcomes else []  # no outcome, no question cards
    rows = [{"id": before.get("id") or "?", "before": _cells(before), "after": _cells(outcomes[i] if outcomes and outcomes[i] else before), "derived": []}
            for i, before in enumerate(read_rows)]
    marks, derived_note, derived_detail = _derived(sources, rows)
    for at, columns in marks.items():
        for column in columns:
            if not rows[at]["before"][column] and not rows[at]["after"][column]:
                rows[at]["before"][column] = rows[at]["after"][column] = "none"
                rows[at]["derived"].append(column)
    counts = {key: sum(q["check"] == key for q in questions) for key, _ in _CHECKS}
    record = next(iter(dicts(reading.get("records"))), {})
    return {"mode": record.get("mode", "recorded"), "label": RECORDED, "model": record.get("model"), "who": "Muse Spark · Meta Model API · cloud",
            "documents": docs, "columns": _COLUMNS, "rows": rows, "flights": _flights(read_rows, sources, pods, photo_lines, grid),
            "photo_lines": photo_lines, "questions": questions, "question_count": len(reading["questions"]), "document_count": len(docs),
            "list_scope": f"{len(rows)}-box demo, not the whole ship's list", "derived_note": derived_note, "derived_detail": derived_detail,
            "checks": [{"id": key, "label": label, "flagged": counts[key]} for key, label in _CHECKS],
            "wall_s": number(dig(metrics, "reading_wall_s")) or number(dig(metrics, "methods", "Muse Spark + software", "wall_s")), "summary": _intake_summary(intake_figures(), len(rows), bool(outcomes) and dig(final, "status") == "complete", dig(metrics, "caption"))}


def intake() -> dict:
    case = _intake_case()
    names = ("documents.json", "reading.json", "final.json", "answers.json", "metrics.json")
    paths = [case / name for name in names] + [FILL, *INTAKE_RESULTS]
    return _memoised("intake", paths, lambda: _build_intake(case))


# ---------------------------------------------------------------- voice and reading
def _clip(suffix: str, identity: str | None = None) -> dict:
    return _json(VOICE / "checkpoints" / f"{identity or _settings()['clip']['id']}-{suffix}.json")


def _peaks(path: Path, n: int = 180) -> list[float]:
    with wave.open(str(path)) as stream:
        frames, width, channels = stream.getnframes(), stream.getsampwidth(), stream.getnchannels()
        raw = stream.readframes(frames)
    if width != 2:
        raise ValueError("expected 16-bit audio")
    samples = memoryview(raw).cast("h")[::channels]
    step = max(1, len(samples) // n)
    peaks = [max(abs(x) for x in samples[i:i + step]) / 32768 for i in range(0, len(samples), step)]
    return [round(x, 3) for x in peaks[:n]]


def _fill() -> dict:
    """Measured story numbers (task 4.2), once they exist. No file, no bars: the slot is absent, not empty."""
    data = read_optional(FILL)
    return data if isinstance(data, dict) else {}


def _voice_stats(fill: dict) -> dict | None:
    keys = ("radio_exact", "radio_clips", "radio_refusals", "radio_wrong_rules")
    return {key: fill[key] for key in keys} if all(key in fill for key in keys) else None


def _refused(radio: dict) -> dict:
    refusal = dig(radio, "answer", "refusal")
    return {"sentence": radio.get("transcript"), "refusal": refusal if isinstance(refusal, dict) else None}


def _radio_is_the_rule() -> bool:
    """True only when the clip on screen is of the sentence the ship replans under. Otherwise it's a separate clip and says so."""
    from app.brief import canonical_key
    pick = optional(hero_wording)
    if pick and pick["radio"]:
        return hero_reading()["sentence"] == pick["sentence"]
    return canonical_key(_clip("clean")["answer"]) == canonical_key(hero_reading()["answer"])


# ---------------------------------------------------------------- the hero wording (D268 as amended, D270 H5)
# Eight wordings of one stored rule, each typed and spoken over a simulated line. The hero is the first, in file order, that the
# reader read exactly both typed and from its radio transcript; with none, the configured template. Every file is named in
# app/v6_hero.json "radio"; a file that's absent or reshaped is a clean gap, and the old separate clip stands in, labelled.
RADIO_MAX_S = 6.0  # shot 4 plays the clip, its transcript and the line's other wordings in 10 s
_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
                 "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
STOCK_VOICE = "Stock text-to-speech voice"


def _radio_rows(data, read: bool = True) -> list[dict]:
    """readings.json's rows (stowlm.radio-readings/1), or with read=False manifest.json's clips, in file order. Any other shape raises."""
    rows = data.get("rows" if read else "clips") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("no radio rows")
    for row in rows:
        if not (isinstance(row, dict) and isinstance(row.get("sentence"), str) and isinstance(row.get("transcript"), str)
                and row.get("condition") in ("clean", "radio") and (not read or isinstance(row.get("binds"), bool))):
            raise ValueError("a radio row is not in the producer's shape")
    return rows


def _typed_rows(data) -> list[dict]:
    """Typed readings: hero-candidates.json ("kept", "misread") or hero-readings.json ("readings"), as {sentence, binds, case, evidence}."""
    if not isinstance(data, dict):
        raise ValueError("not a readings file")
    out = []
    for key in ("kept", "misread", "rows", "readings"):
        for row in data.get(key) or []:
            binds = row.get("binds", row.get("correct")) if isinstance(row, dict) else None
            if isinstance(binds, bool) and isinstance(row.get("sentence"), str):
                out.append({"sentence": row["sentence"], "binds": binds, "case": row.get("case"), "evidence": row.get("evidence"), "reading": row.get("reading")})
    return out


def _rule_bay(row: dict) -> int:
    bay = dig(row.get("expected_rule") or row.get("expected") or row.get("rule"), "constraint", "bay")
    if isinstance(bay, bool) or not isinstance(bay, int):
        raise ValueError("this wording's rule names no bay")
    return bay


def bay_heard(transcript: str, bay: int) -> bool:
    """Did the bay number survive the line: "bay" then the number as digits (leading zeros allowed) or as a word."""
    said = rf"0*{bay}" + (f"|{_NUMBER_WORDS[bay]}" if bay < len(_NUMBER_WORDS) else "")
    return re.search(rf"\bbay[\s-]+(?:{said})\b", transcript, re.I) is not None


def is_refusal(reading) -> bool:
    """A reading, raw JSON text or parsed, that is a refusal: not a rule."""
    if isinstance(reading, str):
        reading = optional(lambda: json.loads(reading))
    return isinstance(reading, dict) and isinstance(reading.get("refusal"), dict)


def select_hero_wording(radio_rows: list[dict], typed_rows: list[dict], fallback: str, case: str | None = None) -> dict:
    """Pure. The first wording, in radio file order, read exactly both typed and from its radio transcript; else ``fallback``.
    A wording with no typed reading can't be chosen. A typed row whose case is this hero's beats another of the same sentence."""
    wordings = list({row["sentence"]: row for row in reversed(radio_rows) if row["condition"] == "radio"}.values())[::-1]
    typed = {}
    for row in typed_rows:
        held = typed.get(row["sentence"])
        if held is None or (case and row.get("case") == case and held.get("case") != case):
            typed[row["sentence"]] = row
    chosen = next((w for w in wordings if w["binds"] is True and dig(typed, w["sentence"], "binds") is True), None)
    reason = "first_exact" if chosen else "fallback"
    chosen = chosen or next((w for w in wordings if w["sentence"] == fallback), None)
    sentence = chosen["sentence"] if chosen else fallback
    known = [typed.get(w["sentence"]) for w in wordings]
    misses = [row for row in known + wordings if row and row["binds"] is not True]
    return {"misses_refused": all(is_refusal(row.get("reading")) for row in misses) if all(known) else None,
            "written": sum(w["sentence"] != fallback for w in wordings), "sentence": sentence, "reason": reason, "radio": chosen, "typed": typed.get(sentence), "wordings": wordings,
            "index": wordings.index(chosen) if chosen else None,
            "typed_exact": sum(row["binds"] for row in known) if wordings and all(known) else None,
            "radio_exact": sum(w["binds"] for w in wordings)}


def wording_counts() -> dict:
    """Shot 5's panel: of the hero rule's wordings, how many the reader read exactly typed and from their radio transcripts. Only under the
    selection rule's own terms: every wording read both ways, and every miss a refusal. Otherwise raises, and the line goes."""
    pick = hero_wording()
    if pick["typed_exact"] is None or pick["misses_refused"] is not True:
        raise ValueError("not every wording was read both ways, or a miss was not a refusal")
    total = len(pick["wordings"])
    return {"total": total, "written": pick["written"], "template": total == pick["written"] + 1, "typed_exact": pick["typed_exact"], "radio_exact": pick["radio_exact"]}


def _radio_settings() -> dict:
    radio = _settings().get("radio")
    if not isinstance(radio, dict) or not radio.get("readings"):
        raise LookupError("no radio files configured")
    return radio


def _radio_paths(radio: dict) -> list[Path]:
    return [ROOT / radio["readings"]] + [ROOT / name for name in radio.get("typed") or []]


def _build_wording(radio: dict, case: str | None) -> dict:
    rows = _radio_rows(_json(ROOT / radio["readings"]))
    typed = [row for path in _radio_paths(radio)[1:] if path.is_file() for row in optional(lambda path=path: _typed_rows(_json(path)), [])]
    return select_hero_wording(rows, typed, radio["fallback_sentence"], case)


def hero_wording() -> dict:
    """The selection over the configured files, once per change of them. No readings.json: LookupError, and the stand-in plays."""
    radio = _radio_settings()
    if not (ROOT / radio["readings"]).is_file():
        raise LookupError("the radio readings have not landed")
    return _memoised(f"hero_wording:{json.dumps(radio, sort_keys=True)}", _radio_paths(radio), lambda: _build_wording(radio, _settings().get("hero_slot")))


AUDIO_ABSENT = "audio not included in this copy"  # the stock-TTS clip isn't shipped (licence unclear): the transcript plays, the waveform and play button don't


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as stream:
        return stream.getnframes() / stream.getframerate()


def _radio_take() -> tuple[Path, str | None]:
    """Where the configured clip would be, and its recorded sha256: nothing is read, so a copy without the audio still names it."""
    radio, pick = _radio_settings(), hero_wording()
    if radio.get("audio"):
        return ROOT / radio["audio"], None
    row = pick["radio"] or {}
    if not row.get("wav_path"):
        raise LookupError("the chosen wording has no radio take")
    return (ROOT / radio["readings"]).parent / row["wav_path"], row.get("wav_sha256")


def radio_wav() -> Path:
    """The configured clip: ``radio.audio`` if set (a recording of your own), else the chosen wording's radio take, hash checked."""
    path, sha = _radio_take()
    if sha and _file_sha256(path) != sha:
        raise ValueError(f"{path.name} does not match its recorded sha256")
    if _wav_seconds(path) > RADIO_MAX_S:
        raise ValueError(f"{path.name} is longer than {RADIO_MAX_S:g} s")
    return path


def _channel(path: Path) -> dict | None:
    """The simulated line's own record (<clip>.audio.json beside the take), or None for a recording with no record."""
    data = read_optional(path.with_name(path.name.removesuffix(".wav") + ".audio.json"))
    return data if isinstance(data, dict) and number(data.get("snr_db")) is not None and data.get("bandpass_hz") else None


def _audio_label(radio: dict, path: Path) -> str:
    if radio.get("audio"):
        return str(radio.get("audio_label") or "Recorded voice")
    line = _channel(path)
    return f"{STOCK_VOICE} · simulated radio: band-limited, {number(line['snr_db']):g} dB noise" if line else f"{STOCK_VOICE} · simulated radio"


def _audio(wav: Path) -> dict:
    """A clip's peaks and its route; for a file this copy doesn't ship (D309: the stock-TTS takes), the transcript alone with the note:
    no peaks, no play button. One rule for the separate clip and the hero wording's take."""
    peaks = optional(lambda: _peaks(wav), [])
    return {"peaks": peaks, "audio": "/api/v6/asset/radio.wav" if peaks else None, "audio_note": None if wav.is_file() else AUDIO_ABSENT}


def _voice_worded(pick: dict, whole: dict) -> dict:
    """The chosen wording's own take, shown as it came back: no staged error, nothing fixed. The planner sees the words first.
    Without the take's file (the public copy, D309) its transcript still plays, under the note."""
    radio, row = whole["radio"], pick["radio"]
    wav = _radio_take()[0]
    wav = radio_wav() if wav.is_file() else wav  # a take that is here must be the recorded one, and short enough
    same = optional(_radio_is_the_rule, False)
    lines = []
    for i, w in enumerate(pick["wordings"]):
        bay, heard = optional(lambda w=w: _rule_bay(w)), optional(lambda w=w: bay_heard(w["transcript"], _rule_bay(w)))
        # Script shot 4: "bay N garbled" or "bay number heard", never "lost": the words reached the transcript, mangled.
        tag = None if heard is None else "bay number heard" if heard else f"bay {bay} garbled"
        lines.append({"transcript": w["transcript"], "bay_heard": heard, "tag": tag, "chosen": i == pick["index"]})
    return {"mode": "recorded", "label": _audio_label(radio, wav), "heard_by": "Muse Voice Transcribe · cloud", "sentence": pick["sentence"],
            "words": [{"heard": word} for word in row["transcript"].split()], "transcript": row["transcript"], "fixed": None, **_audio(wav),
            "seconds": round(_wav_seconds(wav), 2) if wav.is_file() else None, "latency_s": number(row.get("asr_seconds")),
            "role": "hero" if same else "illustrative", "note": None if same else dig(whole, "stand_ins", "radio_wording"),
            "refused_first": {"sentence": None, "refusal": None}, "stats": None, "lines": lines, "line_words": "a noisy line",
            "heard_line": "The planner sees the words first.", "own_recording": bool(radio.get("audio"))}


def voice() -> dict:
    """The radio's own transcript is the chapter; everything else on it is a panel. The chosen wording's take once its files land."""
    pick = optional(hero_wording)
    worded = optional(lambda: _voice_worded(pick, _settings())) if pick and pick["radio"] else None
    if worded:
        return worded
    whole, radio = _settings(), _clip("radio")
    config = whole["clip"]
    clean = optional(lambda: _clip("clean"), {})
    fixes = dig(config, "fixes", default={})
    same = optional(_radio_is_the_rule, False)
    wav = VOICE / "audio" / f"{config['id']}-radio.wav"
    words = [{"heard": word, "fix": fixes.get(word.strip(".,"))} for word in radio["transcript"].split()]
    return {"mode": "recorded", "label": f"{STOCK_VOICE} · simulated radio", "heard_by": "Muse Voice Transcribe · cloud", "lines": None, "line_words": "a noisy line",
            "words": words, "transcript": radio["transcript"], "fixed": clean.get("transcript"), **_audio(wav), "seconds": number(dig(radio, "asr_evidence", "audio_seconds")),
            "latency_s": number(radio.get("asr_latency_s")), "role": "hero" if same else "illustrative",
            "note": None if same else dig(whole, "stand_ins", "radio"), "refused_first": _refused(radio), "stats": _voice_stats(_fill())}


def _reader_bars(numbers: dict) -> dict | None:
    """Same model with and without the fine-tune. The named comparisons only ever sit beside the reader's own number."""
    without, tuned = number(numbers.get("untrained_exact_pct")), number(numbers.get("reader_exact_pct"))
    if without is None or tuned is None:
        return None
    served = figures()["figures"]
    said = lambda name: dig(served, name, "value")
    if not said("reader_sealed_total"):  # a bar with no scope isn't shown
        return None
    spark = f"Muse Spark, cloud: {said('reader_spark_exact')} of {said('reader_spark_total')}" if said("reader_spark_exact") and said("reader_spark_total") else None
    parser = f"hand-written parser: {said('reader_parser_exact')} of {said('reader_sealed_total')}" if said("reader_parser_exact") else None
    return {"without": without, "with": tuned, "shown": {"without": shown_pct(without), "with": shown_pct(tuned)},
            "exact": {"without": exact_pct(without), "with": exact_pct(tuned)},
            "scope": f"Exact reading on {said('reader_sealed_total')} locked test sentences, not written by real planners",
            "comparisons": " · ".join(x for x in (spark, parser) if x)}


def shown_pct(value: float) -> str:
    """A bar's label: whole points, half up (46.875 -> "47 %"). The exact value goes in the title beside it."""
    from decimal import ROUND_HALF_UP, Decimal
    return f"{Decimal(str(value)).quantize(Decimal(1), ROUND_HALF_UP)} %"


def exact_pct(value: float) -> str:
    return f"exact: {repr(float(value)).removesuffix('.0')} %"


NO_READER = {"kind": "none", "on_device": False, "short": "No model", "label": "No model reading recorded · the stored rule, typed by the planner"}


def _on_this_ship(answer: dict, context: dict) -> None:
    from experiments.mvp.reader.schema import validate_answer
    validate_answer(json.dumps(answer), context)


def _recorded_reading(identity: str, context: dict) -> dict:
    """One frozen take of a real reader, validated against this ship. Its evidence names who read it."""
    clean = _clip("clean", identity)
    sentence, answer = clean["transcript"], clean["answer"]
    if not isinstance(sentence, str) or not isinstance(answer, dict):
        raise ValueError("no reading in this recording")
    _on_this_ship(answer, context)
    proof = clean.get("parser_evidence") if isinstance(clean.get("parser_evidence"), dict) else None
    return {"sentence": sentence, "answer": answer, "evidence": proof, "seconds": number(clean.get("parser_latency_s")), "recorded": True}


def _adoption() -> dict:
    return _json(next(path for path in ADOPTIONS if path.is_file()))


def _reading_rows(path: Path) -> dict:
    """{key: row} from reader_final's readings.jsonl, or the folder holding it. A half-written last line isn't a reading."""
    rows = {}
    for line in (path / "readings.jsonl" if path.is_dir() else path).read_text().splitlines():
        row = optional(lambda: json.loads(line))
        if isinstance(row, dict) and row.get("key"):
            rows[row["key"]] = row
    return rows


def _reader_recording(config: dict) -> dict:
    """This hero's reading from ``reader_recordings``, the one line that brings the adopted reader in: ``experiments/mvp/pipeline/reader_final/readings``
    (task 3.2; rows keyed by exact sentence and ship, written by read_all.py). Label, time and memory come from its evidence and its offline-timing.json,
    the bars from the adoption record's own sealed arms. Anything but the adopted adapter's masked on-device reading of this sentence on this ship, to this rule, raises."""
    from app.brief import canonical_key, reader_label
    from experiments.mvp.pipeline.contract.schema import sha256_json
    instruction = config.get("instruction") or {}
    sentence = instruction.get("sentence")
    path = ROOT / config["reader_recordings"]
    row = _reading_rows(path).get(sha256_json({"sentence": sentence, "context": config["context"]}))
    if not row:
        raise LookupError("no recording of this sentence on this ship")
    proof = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if row.get("status") != "read" or row.get("sentence") != sentence:
        raise ValueError("this recording is not a finished reading of this sentence")
    if reader_label(proof)["kind"] != "stowlm_reader" or proof.get("masked") is not True or row.get("on_device", True) is not True:
        raise ValueError("this recording is not the fine-tuned reader, masked, on device")
    adoption = _adoption()
    if proof["adapter_sha256"] != adoption["adopted"]["adapter_sha256"]:
        raise ValueError("this recording is from another adapter than the adopted reader")
    answer = json.loads(proof["raw"])
    try:
        _on_this_ship(answer, config["context"])
    except ValueError as error:
        raise ValueError(f"this recording does not fit this ship: {error}") from error
    if canonical_key(answer) != canonical_key(instruction["rule"]):
        raise ValueError("this recording reads to another rule")
    return {"sentence": sentence, "answer": answer, **_reader_extras(proof, path / "offline-timing.json", adoption)}


def _reader_extras(proof: dict, timing: Path, adoption: dict) -> dict:
    """Time and memory from the timing run of this very adapter, the bars from the adoption record's own sealed arms."""
    timed = optional(lambda: _json(timing), {})
    timed = timed if dig(timed, "adapter_sha256") == proof["adapter_sha256"] else {}
    arm = lambda name: dig(adoption, "sealed_128", name, "exact_match_pct")
    size = optional(lambda: Path(adoption["adopted"]["path"], "adapters.safetensors").stat().st_size)
    seconds = number(timed.get("reader_seconds"))
    median = seconds is not None and seconds == number(timed.get("warm_median_s"))
    note = ", ".join(["warm median"] + (["network blocked for the process"] if "denied" in str(timed.get("network") or "") else [])) if median else None
    return {"evidence": proof, "seconds": seconds or number(proof.get("wall_s")), "seconds_note": note, "recorded": True,
            "peak_gb": number(timed.get("reader_peak_gb")), "adapter_mb": round(size / 1e6, 1) if size else None,
            "numbers": {"reader_exact_pct": arm(adoption.get("outcome")), "untrained_exact_pct": arm("untrained")}}


READER_TIMING = PIPELINE / "reader_final/readings/offline-timing.json"


def _worded_reading(config: dict) -> dict:
    """The chosen hero wording as the adopted reader read it typed, when that reading is this ship's stored rule. Anything less raises,
    and the hero keeps its own instruction: a wording of another rule never replaces the rule the ship replans under."""
    from app.brief import canonical_key, reader_label
    pick = hero_wording()
    typed, proof = pick["typed"] or {}, dig(pick, "typed", "evidence", default={})
    if typed.get("binds") is not True or not isinstance(proof, dict) or not isinstance(proof.get("raw"), str):
        raise LookupError("the hero wording has no exact typed reading")
    if reader_label(proof)["kind"] != "stowlm_reader" or proof.get("masked") is not True:
        raise ValueError("the typed reading is not the fine-tuned reader, masked, on device")
    adoption = _adoption()
    if proof["adapter_sha256"] != adoption["adopted"]["adapter_sha256"]:
        raise ValueError("the typed reading is from another adapter than the adopted reader")
    answer = json.loads(proof["raw"])
    _on_this_ship(answer, config["context"])
    if canonical_key(answer) != canonical_key(config["instruction"]["rule"]):
        raise ValueError("the hero wording reads to another rule than this hero's instruction")
    heard = pick["radio"] or {}
    return {"sentence": pick["sentence"], "answer": answer, **_reader_extras(proof, READER_TIMING, adoption),
            "heard": {"transcript": heard.get("transcript"), "binds": heard.get("binds")} if heard else None}


def hero_reading() -> dict:
    """The sentence the hero replans under and its rule. A recording of a real reader, from ``reader_recordings`` or named by ``readings`` in the config,
    leads if it reads to exactly this rule on this ship. With none, it's the stored rule, and nothing here claims any model read it."""
    from app.brief import canonical_key
    config = hero()
    instruction = config.get("instruction")
    if not instruction:
        raise LookupError("this hero has no instruction fixture yet")
    worded = optional(lambda: _worded_reading(config))
    if worded:
        return worded
    final = optional(lambda: _reader_recording(config)) if config.get("reader_recordings") else None
    if final:
        return final
    named = (config.get("readings") or {}).get(config.get("slot"))
    recording = optional(lambda: _recorded_reading(named, config["context"])) if named else None
    if recording and canonical_key(recording["answer"]) == canonical_key(instruction["rule"]):
        return recording
    return {"sentence": instruction["sentence"], "answer": instruction["rule"], "evidence": None, "seconds": None, "recorded": False}


def _who_read(reading_: dict) -> dict:
    from app.brief import reader_label
    return reader_label(reading_["evidence"]) if reading_["recorded"] else NO_READER


def reading() -> dict:
    """The reading itself is the chapter: with no sentence or no answer there's nothing to confirm, so the beat is a gap."""
    from app.brief import rule_text, safe_evidence
    config = hero()
    if config.get("reader_recordings"):  # configured and refused: a clean gap with the reason, while the replan keeps its stored rule, labelled
        _reader_recording(config)
    now, fill = hero_reading(), _fill()
    sentence, answer, proof = now["sentence"], now["answer"], now["evidence"]
    same = optional(_radio_is_the_rule, False)
    who, numbers = _who_read(now), now.get("numbers") or fill
    return {"mode": "recorded" if now["recorded"] else "stored", "label": RECORDED if now["recorded"] else None, "sentence": sentence,
            "rule": optional(lambda: rule_text(answer), ""), "answer": answer, "reader": who, "evidence": safe_evidence(proof),
            "heard": now.get("heard"),  # the same wording's radio transcript and whether the reader read it to this rule too
            "seconds": now["seconds"], "seconds_note": now.get("seconds_note"), "wordings": optional(wording_counts, None), "refused_first": _refused(optional(lambda: _clip("radio"), {})) if same and "heard" not in now else {"sentence": None, "refusal": None},
            "stand_in_note": None if who["kind"] == "stowlm_reader" else _stand_in_note(now), "bars": _reader_bars(numbers),
            "peak_gb": now["peak_gb"] if "peak_gb" in now else number(fill.get("reader_peak_gb")), "adapter_mb": now.get("adapter_mb"),
            "prepared": optional(lambda: prepared_for(answer, config["context"]), None),  # showcase/reader/recordings.json: this rule's prepared sentences
            "live_read": None,  # nothing here reads live yet; only a finished live read of this sentence may fill it (stage-story readLive)
            "scope_line": "Free wording of four kinds of instruction; built to refuse anything else; it can slip, so the planner confirms.", "binding": optional(reading_binding, None)}


def _stand_in_note(reading_: dict) -> str:
    """Two wordings: a stored rule nobody read, and another model's recording standing in for the reader."""
    return re.sub(r"\s*\(task [^)]*\)", "", str(hero().get("reader_note_recorded" if reading_["recorded"] else "reader_note") or ""))


# ---------------------------------------------------------------- prepared sentences, as the adopted reader read them (showcase/reader)
SHOWCASE_SCHEMA = "stowlm.showcase-reader-recordings/"
AS_EXPECTED = {"exact": "read to its stored rule", "refused_as_expected": "refused, as expected", "no_stored_rule": "no stored rule to compare"}


def _showcase_row(row: dict, adapter: str) -> dict:
    from app.brief import rule_text
    proof = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    if proof.get("adapter_sha256") != adapter or proof.get("masked") is not True:
        raise ValueError("a recording is not the adopted adapter, masked")
    status = row.get("status")
    if status not in AS_EXPECTED and status != "misread":
        raise ValueError(f"unknown status {status!r}")
    reading_ = row.get("reading") if isinstance(row.get("reading"), dict) else {}
    code = dig(reading_, "refusal", "code") if dig(reading_, "refusal", "refused") is True else None
    expected = row.get("expected") if isinstance(row.get("expected"), dict) else None
    return {"kind": row.get("kind"), "sentence": row["sentence"], "source": row.get("source"), "context_sha256": row.get("context_sha256"),
            "status": status, "as_expected": status != "misread", "refused": code is not None, "refusal_code": code,
            "read": None if code else optional(lambda: rule_text(reading_), "") or None, "reading": reading_,
            "stored": optional(lambda: rule_text(expected), "") or None if expected else None, "expected": expected, "seconds": number(proof.get("wall_s"))}


def _build_showcase(path: Path, pinned: str) -> dict:
    """recordings.json, only while its bytes are the pinned ones and every row is the adopted adapter's masked reading. Counts are the rows'."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pinned:
        raise ValueError(f"{path.name} is {digest[:12]}, the pin is {str(pinned)[:12]}")
    data = json.loads(raw)
    if not str(data.get("schema_version", "")).startswith(SHOWCASE_SCHEMA):
        raise ValueError("not a showcase reader recording")
    who, adapter = data.get("adopted") or {}, _adoption()["adopted"]["adapter_sha256"]
    if who.get("adapter_sha256") != adapter or not who.get("name"):
        raise ValueError("these recordings are from another adapter than the adopted reader")
    rows = [_showcase_row(row, adapter) for row in dicts(data.get("recordings"))]
    if not rows:
        raise ValueError("no recordings")
    missed = [row for row in rows if not row["as_expected"]]
    if len(missed) != len(dicts(data.get("misreads"))):
        raise ValueError("the misreads list doesn't match the rows")
    by = {status: sum(row["status"] == status for row in rows) for status in (*AS_EXPECTED, "misread")}
    said = {}
    for kind in (data.get("counts") or {}).values():
        for status, n in (kind or {}).items():
            said[status] = said.get(status, 0) + n
    if {k: v for k, v in by.items() if v} != said:
        raise ValueError("the file's own counts don't match its rows")
    labels = {row.get("label") for row in dicts(data.get("recordings"))}
    return {"label": RECORDED, "source": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path), "sha256": digest, "reader": who["name"], "adapter_sha256": adapter,
            "reader_label": labels.pop() if len(labels) == 1 else None, "file_label": data.get("label"),
            # "as expected" counts only rows compared with a stored rule (read to it, or refused as expected); a row with no stored rule is its own count (D296)
            "counts": {"total": len(rows), "as_expected": len(rows) - len(missed) - by["no_stored_rule"], "missed": len(missed),
                       "refused": sum(row["refused"] for row in missed), "wrong_rule": sum(not row["refused"] for row in missed), **by},
            "rows": rows, "missed": missed}


def showcase_readings() -> dict:
    """app/v6_hero.json ``showcase_readings``: {path, sha256}. Absent, moved or reshaped: raises, and each panel citing it is a clean gap."""
    pin = _settings().get("showcase_readings") or {}
    path = ROOT / pin["path"]
    return _memoised(f"showcase:{path}:{pin['sha256']}", [path], lambda: _build_showcase(path, pin["sha256"]))


def adopted_reading(sentence: str) -> dict:
    """The adopted reader's recorded reading of exactly this sentence, beside another reader's: what it read, or what it refused."""
    data = showcase_readings()
    row = next((r for r in data["rows"] if r["sentence"] == sentence), None)
    if row is None:
        raise LookupError("the adopted reader has no recording of this sentence")
    note = "misread" if not row["as_expected"] else AS_EXPECTED[row["status"]]
    return {**{k: data[k] for k in ("label", "source", "sha256", "reader", "adapter_sha256")},
            **{k: row[k] for k in ("sentence", "status", "as_expected", "refused", "refusal_code", "read", "stored")}, "note": note}


def prepared_for(rule: dict, context: dict) -> dict:
    """The prepared sentences of this rule on this ship, misreads and refusals kept in place; the file's other misses named beside them."""
    from app.brief import canonical_key
    from experiments.mvp.pipeline.contract.schema import sha256_json
    data, key, ship = showcase_readings(), canonical_key(rule), sha256_json(context)
    mine = [row for row in data["rows"] if row["expected"] and row["context_sha256"] == ship and optional(lambda: canonical_key(row["expected"])) == key]
    if not mine:
        raise LookupError("no prepared sentence of this rule on this ship")
    shown = {id(row) for row in mine}
    pick = lambda row: {**{k: row[k] for k in ("kind", "source", "sentence", "status", "as_expected", "refused", "refusal_code", "read", "stored")},
                        "heard": "/radio/" in f"/{row['source'] or ''}"}  # a radio transcript, not a typed wording
    side = lambda heard: {"total": sum(pick(r)["heard"] == heard for r in mine), "as_expected": sum(pick(r)["heard"] == heard and r["as_expected"] for r in mine)}
    return {**{k: data[k] for k in ("label", "source", "sha256", "reader", "adapter_sha256", "reader_label", "counts")},
            "rows": [pick(row) for row in mine], "elsewhere": [pick(row) for row in data["missed"] if id(row) not in shown],
            "here": {"total": len(mine), "as_expected": sum(row["as_expected"] for row in mine), "missed": sum(not row["as_expected"] for row in mine),
                     "typed": side(False), "radio": side(True)}}


# ---------------------------------------------------------------- the film's figures
def _whole(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"not a count: {value!r}")
    return value


def _spoken_count(value: int) -> str:
    return ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")[value] if 0 <= value <= 10 else str(value)


def _intake_development() -> tuple:
    """Always the development comparison, whichever figures the chapter's panel shows, so these captions can't drift into a sealed result's place."""
    path = INTAKE_DEVELOPMENT
    data = _json(path)
    if "development" not in data["label"]:
        raise ValueError("not development figures")
    methods = data["development_methods"]
    parser_name = next(name for name in methods if "parser" in name.lower())
    spark, parser = next(row for name, row in methods.items() if name != parser_name), methods[parser_name]
    share = lambda side: f"{100 * side['fields_correct'] / side['fields_total']:.2f}"
    return path, data["label"], {"intake_dev_spark_pct": share(spark), "intake_dev_parser_pct": share(parser)}


def _intake_demo() -> tuple:
    return _intake_case() / "final.json", "demo list, synthetic documents", {"intake_demo_boxes": len(intake()["rows"])}


def _reader_parser() -> tuple:
    data = _json(READER_PARSER)
    return READER_PARSER, "sealed v3 sentences, not written by real planners", {"reader_parser_exact": _whole(data["exact_match_count"]), "reader_sealed_total": _whole(data["count"])}


MASK_STUDY = ROOT / "experiments/mvp/reader/v3/results-final/mask-study"


def _xray_study() -> tuple:
    """How rare a block is, from the mask study's arm for the adopted reader only. End-token swaps are already excluded there."""
    adopted = _adoption()["adopted"]["adapter_sha256"]
    path = next(p for p in (MASK_STUDY / "report.json", MASK_STUDY / "report-adopted.json") if p.is_file() and dig(_json(p), "adapter", "adapter_sha256") == adopted)
    arm = _json(path)["adapter_arm"]
    return path, f"development, {_whole(arm['sentences'])} validation sentences, the adopted reader", {"xray_study_sentences": _whole(arm["sentences"])}  # draft 5: the scope only


def _xray_before_after() -> tuple:
    """Shot 6's "182 of 200 before fine-tuning, 2 after": sentences whose first choice the mask really blocked, untrained Glimmer beside the adopted adapter."""
    path = MASK_STUDY / "report-base.json"
    data = _json(path)
    if dig(data, "fine_tuned_adapter", "adapter_sha256") != _adoption()["adopted"]["adapter_sha256"]:
        raise ValueError("the before/after study is of another adapter")
    before, after = data["untrained"], data["fine_tuned"]
    if _whole(before["sentences"]) != _whole(after["sentences"]):
        raise ValueError("the two arms read different sentence counts")
    return path, f"development, {before['sentences']} validation sentences, untrained Glimmer and the adopted reader", {
        "xray_before_blocked": _whole(before["sentences_real_block"]), "xray_after_blocked": _whole(after["sentences_real_block"])}


def _reader_adopted() -> tuple:
    """The adopted reader's sealed arm (D266)."""
    path = next(p for p in ADOPTIONS if p.is_file())
    data = _json(path)
    arm = data["sealed_128"][data["outcome"]]
    base = data["sealed_128"]["untrained"]  # shot 6's bars: the same model under the same mask, without the fine-tune
    return path, "sealed v3 sentences, not written by real planners, the adopted reader", {"reader_exact": _whole(arm["exact_match_count"]),
                                                                                         "reader_loosening_said": _spoken_count(_whole(arm["false_acceptance_count"])),
                                                                                         "reader_untrained_exact": _whole(base["exact_match_count"]),
                                                                                         "reader_exact_shown_pct": shown_pct(arm["exact_match_pct"]).removesuffix(" %"),
                                                                                         "reader_untrained_shown_pct": shown_pct(base["exact_match_pct"]).removesuffix(" %")}


SEALED_SUMMARY = ROOT / "experiments/mvp/reader/v3/results-final/sealed-summary.json"


def _reader_spark_gap() -> tuple:
    """Points below Spark's fixed sealed reference, from the gate record of the adopted adapter only."""
    data = _json(SEALED_SUMMARY)
    if dig(data, "selected", "adapter_sha256") != _adoption()["adopted"]["adapter_sha256"]:
        raise ValueError("the gate record is for another adapter")
    gap = -float(data["gates"]["difference_from_spark_pp"])
    return SEALED_SUMMARY, "sealed v3, against Spark's fixed reference, not rerun", {"reader_spark_gap": f"{gap:.0f}"}


def _port_share() -> tuple:
    """Shot 12's one-port line: the same projection inputs, Laem Chabang's calls as a share of the world's."""
    rows = json.loads(FILM_FIGURES.read_text())["figures"]
    port, world = int(rows["stake_calls"]["value"].replace(",", "")), int(rows["impact_calls_k"]["value"]) * 1000
    return FILM_FIGURES, "Projection input, share of container-vessel calls", {"impact_calls": f"{world:,}", "impact_port_share_pct": f"{100 * port / world:.1f}"}


def _lookup_is_a_gap(read):
    """A radio file that hasn't landed is a gap like any other source: figures() names it and its segments drop."""
    def guarded():
        try:
            return read()
        except LookupError as error:
            raise ValueError(str(error)) from error
    return guarded


def _radio_scope(wordings: int) -> str:
    return f"development, {wordings} synthetic wordings of one rule, stock text-to-speech over a simulated line"




def _radio_line() -> tuple:
    """Shot 4: how many wordings, and how many lost the bay number over the line, from the transcripts themselves. readings.json once it
    lands, until then the clip manifest it copies them from (same rows); no reader is involved in either count."""
    radio = _radio_settings()
    path, read = ROOT / radio["readings"], True
    if not path.is_file():
        path, read = ROOT / radio["clips"], False
    heard = list({row["sentence"]: row for row in _radio_rows(_json(path), read) if row["condition"] == "radio"}.values())
    lost = sum(not bay_heard(row["transcript"], _rule_bay(row)) for row in heard)
    return path, _radio_scope(len(heard)), {"radio_wordings": len(heard), "radio_lost_bay": lost}  # shot 4's row-tag words went with the leader's cut (3 Nov)


def _radio_read() -> tuple:
    """Shots 4 and 5: one wording chosen, and the reader's exact readings of all of them, typed and over the line, misses included."""
    pick = hero_wording()
    if pick["radio"] is None or pick["typed_exact"] is None:
        raise ValueError("not every wording has a typed and a radio reading")
    path = ROOT / _radio_settings()["readings"]
    if pick["misses_refused"] is not True:  # the selection rule reads "exact, else refused": a miss that became a wrong rule is a story to retell, so the line goes
        raise ValueError("a reading that was not exact was not a refusal")
    # Shot 5's "N of 8 typed and M of 8 radio transcripts exact" left the script (draft 5, D296): its counts are no longer served, so nothing is served for nothing.
    return path, _radio_scope(len(pick["wordings"])), {"radio_selected": 1, "radio_written": pick["written"], "radio_bay": _rule_bay(pick["radio"])}


def _reader_timing() -> tuple:
    """Shot 5's "about N s a sentence": the timing run's warm median, of the adopted adapter only, network blocked for the process."""
    data = _json(READER_TIMING)
    if data.get("adapter_sha256") != _adoption()["adopted"]["adapter_sha256"]:
        raise ValueError("the timing run is of another adapter")
    seconds = number(data.get("warm_median_s"))
    if seconds is None:
        raise ValueError("no warm median")
    peak = number(data.get("reader_peak_gb"))
    return READER_TIMING, f"warm median of {_whole(data['sentences_read'])} sentences, the adopted reader on this laptop, network blocked for the process", {
        "reader_seconds_said": f"{seconds:.1f}", **({"reader_peak_gb_said": f"{peak:.1f}"} if peak else {})}


def _showcase_counts() -> tuple:
    """Shot 5's "all N prepared sentences in the recording" line: showcase_readings()'s own counts, rows with no stored rule apart."""
    data = showcase_readings()
    c, path = data["counts"], ROOT / data["source"]
    return path, f"{data['label']}; each prepared sentence read once by the adopted reader", {
        "showcase_total": c["total"], "showcase_as_expected": c["as_expected"], "showcase_missed": c["missed"], "showcase_no_rule": c["no_stored_rule"],
        "showcase_source": str(path.relative_to(ROOT / "experiments/mvp")), "reader_adapter_name": data["reader"]}


def _radio_channel() -> tuple:
    """The simulated line's noise, from the take on screen's own channel record. A recording of your own with none: the segment drops."""
    # The record describes the take, so it stays true in a copy without the audio (D309); a WAV that is present is still hash checked.
    path, _ = _radio_take()
    if path.is_file():
        path = radio_wav()
    line = _channel(path)
    if not line:
        raise LookupError("this take has no simulated-line record")
    return path.with_name(path.name.removesuffix(".wav") + ".audio.json"), "the simulated line this take went through", {"radio_snr_db": f"{number(line['snr_db']):g}"}


# ---------------------------------------------------------------- stored hero facts, written by the lattice (shots 7 and 9)
def _count(value) -> int:
    """A count a facts file may write as 143 or 143.0; anything else isn't one."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _whole(value)


def _day(utc: str) -> str:
    moment = datetime.fromisoformat(str(utc)).astimezone(timezone.utc)
    return f"{moment.day} {moment:%b %Y}"


def _in_this_copy(recorded: str) -> Path:
    """An absolute path a facts file recorded in the tree it was written in, read in this checkout: the part from the first top-level
    folder of this repo on. A clean clone lives elsewhere; the bytes are still hash-checked by the caller."""
    path = Path(recorded)
    if not path.is_absolute() or path.resolve().is_relative_to(ROOT):
        return path
    parts = path.parts
    for i, part in enumerate(parts[1:], 1):
        if part in {"experiments", "app", "data", "src", "params"} and (ROOT / part).is_dir():
            return ROOT.joinpath(*parts[i:])
    return path


def _bound_store(row: dict) -> Path:
    """The store entry a facts file was extracted from, only while its bytes are the ones it names."""
    path = ROOT / row["relative_path"] if row.get("relative_path") else _in_this_copy(row["path"])
    if not path.resolve().is_relative_to(ROOT) or _file_sha256(path) != row["raw_sha256"]:
        raise ValueError(f"{path.name} changed since its facts were written")
    return path


def _build_change_facts(config: dict, path: Path) -> dict:
    data = _json(path)
    if not str(data.get("schema_version", "")).startswith("stowlm.film-close-bay-facts/"):
        raise ValueError("the plan-change facts have an unknown shape")
    sources = data["sources"]
    if data["instance_sha256"] != config["instance_sha256"] or data["canonical_key"] != (config.get("keys") or {}).get("after"):
        raise ValueError("the plan-change facts are about another ship or rule")
    if sources["selected"]["plan_sha256"] != config.get("after_sha256") or sources["baseline"]["plan_sha256"] != config.get("before_sha256"):
        raise ValueError("the plan-change facts are about other plans")
    for side in ("selected", "baseline"):
        _bound_store(sources[side])
    forced, changed, lifts, frames = data["pointwise_forced"], data["changed_final_positions"], data["extra_lifts"], data["native_frames"]
    forced_ids = sorted(str(box) for box in forced["container_ids"])
    moved, rearranged = _count(changed["count"]), _count(changed["additional_count"])
    if len(forced_ids) != _count(forced["count"]) or rearranged != moved - len(forced_ids) or not set(forced_ids) <= set(changed["container_ids"]):
        raise ValueError("the plan-change facts don't add up")
    before, after = number(lifts["baseline"]), number(lifts["selected"])
    if None in (before, after) or number(lifts.get("delta")) != after - before:
        raise ValueError("the plan-change lifts don't add up")
    sequence = data.get("sequence_provenance") or {}
    caveat = data["attribution"]
    if not isinstance(caveat, str) or not caveat.strip():
        raise ValueError("the plan-change facts carry no attribution caveat")
    return {"boxes": _count(data["boxes"]["count"]), "forced_ids": forced_ids, "forced": len(forced_ids), "moved": moved, "rearranged": rearranged,
            "lifts_before": before, "lifts_after": after, "extra_lifts": after - before, "later_port_lifts": lifts.get("includes_projected_later_port_lifts") is True,
            "frames": _count(frames["n_frames"]), "frames_passed": frames.get("all_frames_passed") is True and sequence.get("all_frames_passed", True) is True,
            "frame_scope": sequence.get("frame_claim_scope"), "peak_heel_deg": float(frames["peak_abs_heel_deg"]),
            "peak_frame": _count(frames["peak_frame"]) if frames.get("peak_frame") is not None else None, "sequence_label": sequence.get("label"),
            "engine": dig(data, "producer", "engine_version"), "producer": dig(data, "producer", "derived_display_label"), "repaired": sequence.get("repaired") is True,
            "computed_utc": data["computed_utc"], "plan_date": _day(data["computed_utc"]), "caveat": " ".join(caveat.split()),
            "source": str(path.relative_to(ROOT)), "sha256": _file_sha256(path)}


def change_facts() -> dict:
    """Shot 7's figures from the lattice's own facts file (``change_facts`` in app/v6_hero.json), bound to the plans on screen: another
    ship, rule, plan, or a store entry changed since, raises, and ``figures`` and ``replan`` drop what it carried. No number is typed here."""
    config = hero()
    path = ROOT / config["change_facts"]
    stores = [ROOT / row["relative_path"] for row in (dig(read_optional(path, {}), "sources", default={}) or {}).values() if isinstance(row, dict) and row.get("relative_path")]
    return _memoised(f"change_facts:{path}:{config.get('after_sha256')}", [path, *stores], lambda: _build_change_facts(config, path))


def _change_figures() -> tuple:
    """Shot 7's captions: only what a caption names (the chapter reads the rest from ``replan``'s facts)."""
    f = change_facts()
    values = {"change_plan_date": f["plan_date"], "change_engine": f["engine"], "change_peak_heel": f"{f['peak_heel_deg']:.2f}",  # two places, as the script and fill-v6.json give it
              "change_sequence": f["sequence_label"] if f["sequence_label"] in ("crane sequence repaired", "crane sequence not repaired") and (f["sequence_label"] == "crane sequence repaired") == f["repaired"] else None,
              "change_frames": f["frames"] if f["frames_passed"] else None, "change_peak_frame": f["peak_frame"] if f["frames_passed"] else None}
    return ROOT / f["source"], "the stored plan under the hero's instruction", {k: v for k, v in values.items() if v is not None}


def _build_impossible(config: dict, path: Path) -> dict:
    data = _json(path)
    if not str(data.get("schema_version", "")).startswith("stowlm.hero-impossible/") or data.get("status") != "stored_proved_refusal":
        raise ValueError("the stored refusal has an unknown shape")
    if data["instance_sha256"] != config["instance_sha256"]:
        raise ValueError("the stored refusal is about another ship")
    source = data["source"]
    if source.get("status") != "proved_refusal" or source.get("instance_sha256") != config["instance_sha256"]:
        raise ValueError("the stored refusal's source isn't a proved refusal on this ship")
    _bound_store(source)
    proof = dig(data, "arithmetic_proof", "all_requested_rules") or {}
    if proof.get("capacity_shortfall") is not True or dig(proof, "public_proof", "proven_impossible") is not True:
        raise ValueError("the stored refusal isn't proved for the whole request")
    needed, allowed = _count(proof["cells_needed"]), _count(proof["cells_allowed"])
    sentences = [s for s in data["sentences"] if isinstance(s, str) and s.strip()]
    if needed <= allowed or not sentences:
        raise ValueError("the stored refusal's arithmetic doesn't refuse")
    short = proof.get("shortfall_cells")
    return {"sentences": sentences, "needed": needed, "allowed": allowed, "shortfall": _count(short) if short is not None else None, "equation": proof.get("equation") or f"{needed} needed > {allowed} allowed",
            "units": dig(data, "numbers", "units"), "computed_utc": source["computed_utc"], "date": _day(source["computed_utc"]),
            "planner_called": data.get("planner_called") is True, "checker_called": data.get("checker_called") is True,
            "boxes": dig(proof, "public_proof", "load_box_count") or dig(data, "arithmetic_proof", "sufficient_rule_subset", "public_proof", "load_box_count"),
            "source": str(path.relative_to(ROOT)), "sha256": _file_sha256(path)}


def impossible_facts() -> dict:
    """Shot 9's proved refusal on the hero ship, from the lattice's file (``impossible_facts`` in app/v6_hero.json). Read per change of
    the file, never copied: film_refusal_facts.py may rewrite it with a plainer proof."""
    config = hero()
    path = ROOT / config["impossible_facts"]
    store = dig(read_optional(path, {}), "source", "path")
    return _memoised(f"impossible:{path}", [path] + ([_in_this_copy(store)] if isinstance(store, str) else []), lambda: _build_impossible(config, path))


def _impossible_figures() -> tuple:
    f = impossible_facts()
    values = {"impossible_needed": f["needed"], "impossible_left": f["allowed"]}
    if f["shortfall"] is not None:  # the file's own shortfall, only while it is the gap between its two counts
        if f["shortfall"] != f["needed"] - f["allowed"]:
            raise ValueError("the stored refusal's shortfall doesn't add up")
        values["impossible_short"] = f["shortfall"]
    return ROOT / f["source"], f"stored refusal on the hero ship, proved by arithmetic before planning, {f['units'] or 'cells'}", values


def _impossible_tiers() -> tuple:
    """Shot 9's "at most N tier on deck": the tier cap of the stored refusal's own constraint, from its arithmetic proof."""
    f = impossible_facts()
    path = ROOT / f["source"]
    rule = dig(_json(path), "arithmetic_proof", "recorded_arithmetic", "constraint", "constraint", default={})
    if rule.get("type") != "max_tiers" or rule.get("zone") != "deck":
        raise ValueError("the stored refusal is not a deck tier cap")
    return path, "stored refusal on the hero ship, its rule", {"impossible_tiers": _whole(rule["max_tiers"])}


IMPACT = ROOT / "experiments/impact-estimate/fixtures/impact-v6.json"


def _impact_mid() -> tuple:
    """Shot 12's headline: the mid case of the improvement pool, inside the port-call exposure frame, both from the impact model's output."""
    data = _json(IMPACT)
    mid, bill = number(dig(data, "prize", "mid", "total_gross_prize_billion_usd")), number(dig(data, "exposure", "headline_billion_usd"))
    if mid is None or bill is None:
        raise ValueError("no mid case or exposure headline")
    step = number(dig(data, "exposure", "headline_one_percent_billion_usd"))  # shot 12: "every 1 % ≈ $N billion", one percent of the rounded frame
    if step is None:
        raise ValueError("no one-percent step of the exposure headline")
    return IMPACT, "Projection, mid case and port-call exposure, billion USD a year", {"impact_mid_billion": f"{mid:.1f}", "impact_bill_billion": f"{bill:.0f}",
                                                                                       "impact_bill_step_billion": f"{step:.0f}"}


def _package_manifest_figures() -> tuple:
    """Shot 10: the frozen pipeline manifest the shown package was built on, from that package's own receipt."""
    from app.v6_package import package_dir
    path = package_dir() / "receipt.json"
    digest = _json(path).get("pipeline_sha256")
    if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("the shown package's receipt names no pipeline manifest")
    return path, "pipeline manifest the shown package was built on", {"package_manifest": digest[:8] + "…"}


HELD_OUT_FIGURES = ROOT / "experiments/mvp/pipeline/sealed/runs/final-3/story-figures.json"
HELD_OUT_LABEL = "held-out maps, frozen pipeline, run after two interrupted attempts; not the pre-registered sealed claim"  # D284


def _partial_runs() -> tuple:
    """Shot 11's partial-run line (logic7 L1, D286): rows both interrupted attempts share with the final run, and how many agree.
    The two published comparisons as story-figures.json cites them; only under D284's label, and only with both attempts."""
    data = _json(HELD_OUT_FIGURES)
    if data["run"]["label"]["value"] != HELD_OUT_LABEL:
        raise ValueError("not the held-out run D284 labels")
    overlaps = [attempt["overlap"] for attempt in data["interrupted_attempts"]]
    if len(overlaps) != 2:
        raise ValueError("both interrupted attempts are compared, or neither is shown")
    out = {}  # per attempt, never pooled (logic8 F8, D291): "same" is status and lifts; attempt 1 also gives the verdict-only count
    for n, o in enumerate(overlaps, 1):
        rows, same = _whole(o["overlap_rows"]["value"]), _whole(o["same_status_and_pass_lifts"]["value"])
        if rows - _whole(o["disagreements"]["value"]) != same:
            raise ValueError("an overlap's agreement doesn't add up")
        out.update({f"overlap{n}_same": same, f"overlap{n}_rows": rows})
    out["overlap1_verdict"] = _whole(overlaps[0]["same_audited_L0_pass_verdict"]["value"])
    return HELD_OUT_FIGURES, f"{HELD_OUT_LABEL}; each interrupted attempt against the final run, never pooled", out


def _held_out_twostep() -> tuple:
    """Shot 11's no-comparison line: the two-step baseline's own full-checker passes on the same held-out maps, and only while there are no mutual passes."""
    data = _json(HELD_OUT_FIGURES)
    if data["run"]["label"]["value"] != HELD_OUT_LABEL:
        raise ValueError("not the held-out run D284 labels")
    arm = data["cohorts"]["envelope24"]["arms"]["twostep"]
    if _whole(data["lift_claim"]["envelope_mutual_passes"]["value"]) != 0 or data["lift_claim"]["quotable"]["value"] is not False:
        raise ValueError("there are mutual passes: the no-comparison line would be false")
    return HELD_OUT_FIGURES, f"{HELD_OUT_LABEL}; the registered two-step comparator, full checker", {
        "sealed_twostep_pass": _whole(arm["passes_L0"]["value"]), "sealed_twostep_maps": _whole(arm["expected_maps"]["value"])}


def _held_out_manifest() -> tuple:
    """Shot 10: the frozen pipeline manifest the held-out run (3.1) was measured on, from its own story figures."""
    data = _json(HELD_OUT_FIGURES)
    digest = data["run"]["manifest_sha256"]["value"]
    if data["run"]["label"]["value"] != HELD_OUT_LABEL or not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("not the held-out run D284 labels, or no manifest")
    return HELD_OUT_FIGURES, f"{HELD_OUT_LABEL}; the pipeline manifest it was measured on", {"held_out_manifest": digest[:8] + "…"}


def _package_confirmation() -> tuple:
    """Shot 10: who confirmed the shown package's reading, in the package's own words (v4) or mapped to them (v3's raw source)."""
    from app.v6_package import confirmation_label, package_dir, receipt_confirmation
    path = package_dir() / "receipt.json"
    label = confirmation_label(receipt_confirmation(_json(path)))
    if not label:
        raise ValueError("the shown package's receipt names no known confirmation")
    return path, "who confirmed the shown package's reading, from its receipt", {"package_confirmed_by": label}


_FIGURES = {"package_manifest": _package_manifest_figures, "held_out_manifest": _held_out_manifest, "package_confirmation": _package_confirmation, "partial_runs": _partial_runs, "held_out_twostep": _held_out_twostep, "intake_dev": _intake_development, "intake_demo": _intake_demo, "reader_parser": _reader_parser, "xray_study": _xray_study, "xray_before_after": _xray_before_after,
            "reader_adopted": _reader_adopted, "reader_spark_gap": _reader_spark_gap, "port_share": _port_share,
            "radio_line": _lookup_is_a_gap(_radio_line), "radio_read": _lookup_is_a_gap(_radio_read), "radio_channel": _lookup_is_a_gap(_radio_channel),
            "reader_timing": _reader_timing, "showcase": _showcase_counts, "change": _change_figures, "impossible": _impossible_figures,
            "impossible_tiers": _impossible_tiers, "impact_mid": _impact_mid}


def _film_figures() -> dict:
    """The few with no machine-readable source in reach. Each keeps naming where it was read, and goes if that place is gone."""
    digest, out = _file_sha256(FILM_FIGURES), {}
    for name, row in json.loads(FILM_FIGURES.read_text())["figures"].items():
        if (ROOT / row["named_source"].split(",")[0]).exists():
            out[name] = {"value": row["value"], "scope": row["scope"], "source": str(FILM_FIGURES.relative_to(ROOT)), "sha256": digest, "named_source": row["named_source"]}
    return out


def figures() -> dict:
    """Every measured number the film shows: name -> {value, scope, source, sha256}. One reader per source; a source that's gone or reshaped costs its own figures, named in gaps."""
    out, gaps = {}, []
    for group, read in _FIGURES.items():
        try:
            path, scope, values = read()
            digest, source = _file_sha256(path), str(Path(path).relative_to(ROOT))
            out.update({name: {"value": str(value), "scope": scope, "source": source, "sha256": digest} for name, value in values.items()})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration, ZeroDivisionError, IndexError) as error:
            log.info("v6 figures %s unavailable: %s", group, error)
            gaps.append(group)
    try:
        out.update(_film_figures())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        log.info("v6 film figures unavailable: %s", error)
        gaps.append("film")
    return {"figures": out, "gaps": gaps}


# ---------------------------------------------------------------- the planner's confirmation
CONFIRM_SOURCE ="planner_pressed_confirm_in_reading_chapter"
_BINDING = (("sentence_sha256", "sentence"), ("rule_sha256", "rule"), ("instance_sha256", "ship"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _rule_digest(envelope: dict) -> str:
    from app.brief import canonical_key
    return _sha256(canonical_key(envelope))


def _binding_of(reading_: dict) -> dict:
    return {"sentence_sha256": _sha256(reading_["sentence"]), "rule_sha256": _rule_digest(reading_["answer"]), "instance_sha256": _instance_digest()}


def _instance_digest() -> str:
    """The ship on screen and the file the replan runs on are one file: both of the slot's digests, recomputed by ``hero()``."""
    config = hero()
    return _sha256(f"{config['instance_sha256']}|{config['file_sha256']}")


def reading_binding() -> dict:
    """What the planner is looking at, as hashes the server recomputes every time."""
    return _binding_of(hero_reading())


def live_reading() -> dict | None:
    """The off-lattice sentence a live replan runs on: one frozen recorded take, valid on this ship, from ``live_reading`` in the config."""
    config = hero()
    identity = config.get("live_reading")
    return _recorded_reading(identity, config["context"]) if identity else None


def _bindings() -> list[dict]:
    """Every reading the planner can confirm right now: the hero's, and the live run's when there is one."""
    live = optional(live_reading)
    return [reading_binding()] + ([_binding_of(live)] if live else [])


class Confirmations:
    """Confirmation is a server event, not a browser state: a token that exists only if the Confirm request happened, for
    exactly these hashes. It spends once. In memory: a restart forgets every token."""

    def __init__(self):
        self._lock, self._tokens = threading.Lock(), {}

    def confirm(self, fields: dict) -> dict:
        """Refuses unless the hashes the page holds are a reading the server would run. With ``reading``, only that live reading."""
        if fields.get("reading") is not None:
            options = [LIVE_READS.binding(fields["reading"])]
            if not all(fields.get(key) == options[0][key] for key, _ in _BINDING):
                word = next(word for key, word in _BINDING if fields.get(key) != options[0][key])
                raise PermissionError(f"This {word} isn't the live reading on the server. Read it again, then confirm.")
            token = secrets.token_hex(16)
            with self._lock:
                self._tokens[token] = {**options[0], "source": CONFIRM_SOURCE}
                while len(self._tokens) > 16:
                    self._tokens.pop(next(iter(self._tokens)))
            return {"token": token, "source": CONFIRM_SOURCE, "reading": fields["reading"]}
        options = _bindings()
        now = next((row for row in options if all(fields.get(key) == row[key] for key, _ in _BINDING)), None)
        if now is None:
            word = next((word for key, word in _BINDING if fields.get(key) != options[0][key]), "reading")
            raise PermissionError(f"This {word} isn't the reading on the server. Read it again, then confirm.")
        token = secrets.token_hex(16)
        with self._lock:
            self._tokens[token] = {**now, "source": CONFIRM_SOURCE}
            while len(self._tokens) > 16:
                self._tokens.pop(next(iter(self._tokens)))
        return {"token": token, "source": CONFIRM_SOURCE}

    def require(self, token) -> dict:
        with self._lock:
            row = self._tokens.get(token) if isinstance(token, str) else None
        if row is None:
            raise PermissionError("No planner confirmation for this replan. Confirm the reading first.")
        options = [LIVE_READS.binding(row["reading"])] if row.get("reading") else _bindings()
        if not any(all(row[key] == now[key] for key, _ in _BINDING) for now in options):
            word = next((word for key, word in _BINDING if row[key] != options[0][key]), "reading")
            raise PermissionError(f"That confirmation was for another {word}. Confirm the reading on screen.")
        return row

    def spend(self, token: str) -> None:
        with self._lock:
            self._tokens.pop(token, None)


CONFIRMATIONS = Confirmations()


class RecordedShown:
    """The server's own record of each time the recorded answer was shown, chosen by name, in place of a confirmation.
    In memory, like the tokens: what reading (its three hashes), which stored entry, under which label, when."""

    def __init__(self):
        self._lock, self.rows, self._next = threading.Lock(), [], 0

    def add(self, row: dict) -> int:
        with self._lock:
            self._next += 1
            self.rows.append({**row, "id": self._next, "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            del self.rows[:-64]
            return self._next


RECORDED_SHOWN = RecordedShown()


# ---------------------------------------------------------------- the live reading (--reader glimmer)
from app.brief import HERO_CALL  # noqa: E402 - the brief service's name for the hero ship in a v6 launch
_CONTEXT_CODE = ("import json, sys\nfrom experiments.mvp.pipeline.reader_final.read_all import instance_of\n"
                 "print(json.dumps(instance_of(sys.argv[1], {})))")
_HERO_CONTEXT: dict = {}


def hero_reader_context() -> dict:
    """The context reader_final/hero_candidates.py read the hero sentence on: ``read_all.instance_of`` on the slot's own file, run
    as written. In a child process, because importing the runner package here sets STOWAGE_VESSEL_CACHE_DIR (checker drift).
    It must also be the hero source's own context, or nothing is read. Once per file."""
    config = hero()
    key = (config["instance"], config["file_sha256"])
    if _HERO_CONTEXT.get("key") != key:
        done = subprocess.run([sys.executable, "-c", _CONTEXT_CODE, config["instance"]], cwd=ROOT, env=_worker_env(),
                              capture_output=True, text=True, timeout=300, check=True)
        context = json.loads(done.stdout.strip().splitlines()[-1])
        if context != config["context"]:
            raise ValueError("the hero's reader context differs from the hero source's context")
        _HERO_CONTEXT.update(key=key, context=context)
    return _HERO_CONTEXT["context"]


def hero_ship() -> dict:
    """The hero as a brief-service ship: its file, its instance and the reader context above, each checked against the slot."""
    from app.identity import instance_content_digest
    from experiments.mvp.reader.schema import context_from_instance
    from stowage.io import load_instance
    config = hero()
    context, path = hero_reader_context(), ROOT / config["instance"]
    if _file_sha256(path) != config["file_sha256"]:
        raise ValueError("the hero's file changed since its source was written")
    instance = load_instance(path)
    if context_from_instance(instance) != context:
        raise ValueError("the hero's instance gives another reader context")
    call = {"id": context["id"], "input": config["instance"], "input_sha256": config["file_sha256"], "instance_digest": instance_content_digest(instance),
            "vessel": config.get("ship"), "size_class": instance.size_class, "load_boxes": len(instance.load_list), "baseline": None, "context": context}
    return {"key": HERO_CALL, "call": call, "instance": instance, "context": context}


def _context_sha256(context: dict) -> str:
    return _sha256(json.dumps(context, sort_keys=True, separators=(",", ":")))


class LiveReads:
    """Live readings of this launch, kept only when read on the hero's own context. In memory, like the tokens."""

    def __init__(self):
        self._lock, self._rows = threading.Lock(), {}

    def add(self, view: dict, ship: dict, wall_s: float | None = None) -> dict:
        from app.brief import reader_label, safe_evidence
        context = hero_reader_context()
        if ship.get("key") != HERO_CALL or ship.get("context") != context:
            raise PermissionError("This reading was made on another ship's context. Only a reading of this voyage's ship can be confirmed here.")
        if view.get("recorded") is not False:
            raise PermissionError("This reading is a recording, not a live read.")
        evidence = view.get("evidence") or {}
        row = {"id": view["id"], "sentence": view["sentence"], "answer": view["answer"], "status": view["status"], "context_sha256": _context_sha256(context),
               "evidence": evidence}
        confirmable = view["status"] == "constraint_ready"
        if confirmable:
            with self._lock:
                self._rows[row["id"]] = row
                while len(self._rows) > 16:
                    self._rows.pop(next(iter(self._rows)))
        adopted = optional(lambda: _adoption()["adopted"]["adapter_sha256"])
        return {"id": row["id"], "mode": "live", "status": view["status"], "sentence": view["sentence"], "answer": view["answer"],
                "rule": view.get("rule") or "", "reader": view.get("reader") or reader_label(evidence), "evidence": safe_evidence(evidence),
                "adopted": bool(adopted) and evidence.get("adapter_sha256") == adopted,
                "seconds": view.get("seconds"), "wall_s": None if wall_s is None else round(wall_s, 3), "label": None, "card": view.get("card"),
                "live_read": {"status": "done", "sentence": view["sentence"], "id": row["id"]},
                "binding": self.binding(row["id"]) if confirmable else None,
                "stored": stored_for_reading(row) if confirmable else None}

    def get(self, identity) -> dict:
        with self._lock:
            row = self._rows.get(identity) if isinstance(identity, str) else None
        if row is None:
            raise PermissionError("That live reading isn't on the server. Read it again, then confirm.")
        return row

    def binding(self, identity) -> dict:
        """The three hashes of that live reading, only while the hero's context is still the one it was read on."""
        row = self.get(identity)
        if row["context_sha256"] != _context_sha256(hero_reader_context()):
            raise PermissionError("That live reading was made on another ship's context. Read it again, then confirm.")
        return {**_binding_of(row), "reading": row["id"]}


LIVE_READS = LiveReads()


def live_reader_on(options: dict | None) -> bool:
    """A live reader is attached to this launch: the on-device one, or one a test injected."""
    options = options or {}
    return bool(options.get("v6")) and (options.get("reader") == "glimmer" or options.get("parser") is not None)


def read_live(handler, sentence) -> dict:
    """The live reader reads ``sentence`` on the hero's context, through the brief service's one reader and its draft and
    confirm gates (the press on stage sends exactly the sentence on screen). Nothing is planned here."""
    from app.brief import service_for
    options = getattr(handler, "brief_options", None) or {}
    if not live_reader_on(options):
        raise PermissionError("No live reader in this launch: start it with --v6 --reader glimmer --reader-adapter <dir>")
    if not isinstance(sentence, str) or not sentence.strip():
        raise ValueError("sentence must be a nonempty string")
    service = service_for(handler)
    if service is None or service.reader is None:
        raise PermissionError("No live reader in this launch")
    began = monotonic()
    draft = service.draft(sentence, HERO_CALL)
    if draft.get("status") != "awaiting_confirmation":
        return {"mode": "live", "status": draft.get("status"), "message": draft.get("message"), "binding": None}
    view = service.confirm(draft["draft"], sentence)
    if "id" not in view:  # the reader was unavailable or its answer didn't validate: nothing to confirm
        return {"mode": "live", "status": view.get("status"), "message": view.get("message"), "binding": None,
                "seconds": (view.get("timings") or {}).get("parse_s")}
    return LIVE_READS.add(view, service._reading(view["id"])["ship"], monotonic() - began)


def stored_for_reading(row: dict, who: str = "live reading") -> dict:
    """Lattice lookup on a reading's canonical rule. Shown only when it is this hero's stored rule, whose entry the launch regated."""
    from app.brief import canonical_key, rule_text
    from experiments.mvp.pipeline.lattice.store import entry_key
    config = hero()
    instruction, said = config.get("instruction"), optional(lambda: rule_text(row["answer"]), "")
    key = entry_key(config["instance_sha256"], [row["answer"]])
    if instruction and key == config["keys"].get("after") and canonical_key(row["answer"]) == canonical_key(instruction["rule"]):
        return {"shown": True, "key": key, "rule": said, "why": None}
    stored = optional(lambda: rule_text(instruction["rule"]), "") if instruction else ""
    if key in (optional(lambda: _json(LATTICE_STORE / "catalogue.json"), {}) or {}):
        why = f"The {who} is “{said}”. A plan is stored for that rule, but this launch did not recheck it, so it is not shown."
    else:
        why = f"The {who} is “{said}”, not the stored rule “{stored}”. No plan is stored for it, so none is shown."
    return {"shown": False, "key": key, "rule": said, "why": why}


NOT_CONFIRMED = "This reading isn't confirmed. Confirm it in the reading chapter, or choose the recorded answer. No plan under the rule is shown."


def reading_gate(token=None, reading=None, recorded: bool = False) -> dict:
    """What the replan chapter may show for the reading the planner selected: ``reading``, a live reading's id, or None for the
    recorded reading. A plan under the rule only for a confirmation token issued for exactly that reading (sentence, rule and ship),
    or for the recorded answer chosen by name (``recorded``), which is labelled and logged in RECORDED_SHOWN. A reading whose rule
    isn't the stored rule never shows the stored plan, confirmed or not (code review 12, P2)."""
    live = reading is not None
    if recorded:
        row = hero_reading()
        said = {"live": False, "fallback": "recorded", "label": RECORDED, **stored_for_reading(row, "recorded reading")}
        if said["shown"]:
            said["record"] = RECORDED_SHOWN.add({**_binding_of(row), "key": said["key"], "label": RECORDED})
        return said
    refuse = lambda why: {"live": live, "shown": False, "needs": "confirmation", "why": why}
    if token is None:
        return refuse(NOT_CONFIRMED)
    try:
        row = CONFIRMATIONS.require(token)
        if row.get("reading") != reading or (not live and any(row[key] != value for key, value in reading_binding().items())):
            raise PermissionError("That confirmation was for another reading. Confirm the reading on screen.")
        now = LIVE_READS.get(reading) if live else hero_reading()
    except PermissionError as error:
        return refuse(f"{error} The stored plan is not shown.")
    return {"live": live, **stored_for_reading(now, "live reading" if live else "recorded reading")}


def gate_for_reading(gate: dict, said: dict | None) -> dict:
    """A reading not shown (unconfirmed, or not the stored rule) holds the plan before, with its reason; the plan under the rule is never sent."""
    if (said or {}).get("shown") is True or gate.get("status") not in {"shown", "before_only"}:
        return gate
    return {**{key: value for key, value in gate.items() if key not in {"after", "boxes_moved", "extra_lifts", "forced_boxes", "computed_utc"}},
            "status": "before_only", "why": [said["why"]]}


# ---------------------------------------------------------------- the ship
GATE_CAP_S = 2400.0
REGATE_FORMAT = "v6-regate-memo-1"
GATE_SOURCES = ("gates.py", "store.py", "runtime.py")
CACHED_FAILURE = " (cached from an exact check; --no-check-cache reruns it)"


def scoped_catalogue(config: dict) -> dict:
    """The hero's rows of the lattice catalogue, and no others (why: experiments/mvp/ui/RECORDING.md, "Catalogue pin")."""
    catalogue = _json(LATTICE_STORE / "catalogue.json")
    return {key: catalogue[key] for key in sorted(config["keys"].values()) if key in catalogue}


def _scoped_text(rows: dict) -> str:
    from experiments.mvp.pipeline.contract.schema import canonical_json
    return canonical_json(rows) + "\n"


def scoped_catalogue_sha256(config: dict | None = None) -> str:
    """What app/v6_hero.json pins. It moves only when an entry this hero shows changes, not when the campaign adds others."""
    return _sha256(_scoped_text(scoped_catalogue(config or hero())))


def _scoped_store(config: dict) -> Path:
    """A store folder holding exactly the hero's entries, so ``regate`` checks those and its pin holds while the campaign adds more."""
    rows = scoped_catalogue(config)
    folder = HERO_CACHE / "scoped" / _sha256(_scoped_text(rows))[:16]
    folder.mkdir(parents=True, exist_ok=True)
    for key in rows:
        link = folder / f"{key}.json"
        link.unlink(missing_ok=True)
        link.symlink_to(LATTICE_STORE / f"{key}.json")
    (folder / "catalogue.json").write_text(_scoped_text(rows))
    return folder


def _waive_admission_without_a_fleet() -> None:
    """Admission protects a shared machine from the fleet. With no fleet telemetry there's nothing to wait for, and it would wait forever."""
    from experiments.mvp.pipeline.lattice.gates import engine_identity
    engine_identity()  # first import of the binding package, inside its guard: a bare import sets engine variables that read as checker drift
    from experiments.mvp.pipeline.binding import admission
    original = admission.wait_for_capacity

    def wait(**kwargs):
        try:
            fresh = time() - admission.STATE.stat().st_mtime < 300
        except OSError:
            fresh = False
        return original(**kwargs) if fresh else {"waived": "no fleet telemetry", "load1": os.getloadavg()[0]}

    admission.wait_for_capacity = wait


def regate_rules(config: dict) -> dict:
    """{plan: its canonical rules}, for the plans this slot has. No instruction yet means no plan under one."""
    rules = dig(config, "instruction", "canonical_rules")
    return {"before": []} if rules is None else {"before": [], "after": rules}


def regate_parts(config: dict, identity: dict) -> dict:
    """Everything a regate's verdict rests on. All of it is the memo's key: any change is a miss and a real regate runs."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    return {"format": REGATE_FORMAT, "instance_sha256": config["instance_sha256"], "instance_file_sha256": config["file_sha256"],
            "plan_sha256": {"before": config["before_sha256"], "after": config["after_sha256"]}, "rule_set_sha256": sha256_json(regate_rules(config)),
            "lattice_entries": scoped_catalogue(config), "catalogue_pin": config.get("catalogue_sha256"), **identity}


def _code_identity(epoch: str, engine: dict) -> dict:
    """The code half of the key. In the worker: reading it imports the binding and the engine, which the server must not."""
    from experiments.mvp.pipeline.lattice import store
    from app.stage_physics import physics_epoch
    sources = hashlib.sha256()
    for name in GATE_SOURCES:
        sources.update(hashlib.sha256((PIPELINE / "lattice" / name).read_bytes()).digest())
    return {"checker_epoch": epoch, "physics_epoch": physics_epoch(), "binding_fingerprint": store.binding_fingerprint(), "engine_manifest_sha256": engine["engine_manifest_sha256"],
            "engine_version": engine["engine_version"], "reader_identity": store._reader_identity(), "gate_source_sha256": sources.hexdigest()}


def stored_identity_holds(config: dict, store_dir, result: dict, *, identity: dict, store=None) -> bool:
    """What ``lattice.lookup`` checks around its full check, for a memo that skips that check.

    Through the public API it can't be had: lookup regates any entry this process hasn't, and only regate or lookup can fill its
    memo. So this uses the store's read helpers, read-only: the pinned catalogue, the entry's bytes against it, the binding, the
    rule set, the current token, then the preconditions of ``_regate_entry``. Each result row must also be this entry's own plan.
    """
    from experiments.mvp.pipeline.contract.schema import sha256_json
    if store is None:
        from experiments.mvp.pipeline.lattice import store
    catalogue = store._catalogue(store_dir, refresh=True, expected_sha256=config.get("catalogue_sha256"))
    if not identity.get("passed"):
        return False
    entries = result.get("entries") or {}
    for name, rules in regate_rules(config).items():
        row = store._read(store.entry_key(config["instance_sha256"], rules), store_dir, catalogue=catalogue)
        if row is None:
            if name in entries:
                return False
            continue
        if not (store._binding_source_matches(row) and row.get("instance_sha256") == config["instance_sha256"]
                and store.canonical_rule_set(row.get("canonical_rules", [])) == store.canonical_rule_set(rules) and store._current_token(row) is not None
                and row.get("engine_version") == identity["engine_version"] and row.get("engine_manifest_sha256") == identity["engine_manifest_sha256"]
                and row.get("computed_utc") and row.get("status", "checked_plan") == "checked_plan" and row.get("plan") and row.get("instance")
                and sha256_json(row["instance"]) == row["instance_sha256"]):
            return False
        if name in entries and entries[name].get("plan_sha256") != sha256_json(row["plan"]):
            return False
    return True


def regate_or_memo(parts: dict, run, confirm) -> dict:
    """``run()``, or the exact memo of it. A verdict is kept either way, so a cached failure stays a failure until a key part changes
    or ``--no-check-cache`` runs it again. A regate that didn't finish decided nothing and isn't kept."""
    from app.check_cache import _store, enabled
    path = REGATE_CACHE / f"{hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()}.json"
    stored = read_optional(path) if enabled() else None
    if isinstance(stored, dict) and stored.get("parts") == parts and isinstance(stored.get("result"), dict) and optional(lambda: confirm(stored["result"]), False):
        return {**stored["result"], "source": "memo"}
    result = run()
    if not result.get("failed") and isinstance(result.get("summary"), dict) and not result["summary"].get("errors"):
        _store(path, {"parts": parts, "result": result})
    return {**result, "source": "fresh"}


def regate_worker(out: str) -> None:
    """Its own process: the gate patches cache paths process-wide and sets engine variables that read as checker drift in the server."""
    from app.check_cache import checker_epoch
    epoch = checker_epoch()  # first, before anything below touches the environment it hashes
    from experiments.mvp.pipeline import lattice
    from experiments.mvp.pipeline.lattice.gates import engine_identity
    _waive_admission_without_a_fleet()
    config = hero()
    store = _scoped_store(config)
    engine = engine_identity()
    result = regate_or_memo(regate_parts(config, _code_identity(epoch, engine)), lambda: _full_regate(lattice, config, store),
                            lambda memo: stored_identity_holds(config, store, memo, identity=engine))
    print(f"regate source: {result['source']}", flush=True)
    Path(out).write_text(json.dumps(result))


def _full_regate(lattice, config: dict, store: Path) -> dict:
    summary = lattice.regate(config["instance_sha256"], store_dir=store, expected_catalogue_sha256=config.get("catalogue_sha256"))
    entries, frames = {}, {}
    if "catalogue" not in summary["errors"]:
        for name, rules in regate_rules(config).items():
            row = lattice.lookup(config["instance_sha256"], rules, store_dir=store)
            if row:
                gate = row["gate"]
                frames[name] = dig(gate, "gauges", "physics", "frames", default=[])
                entries[name] = {"plan_sha256": gate["plan_sha256"], "passed": gate["passed"] is True, "lifts": gate["lifts"], "checked_utc": gate["checked_utc"],
                                 "wall_s": gate["wall_s"], "frames": len(frames[name]), "moves": len(row["plan"]["moves"]), "computed_utc": row["computed_utc"],
                                 "engine_version": row["engine_version"], "total_lifts_now": gate.get("total_lifts_now")}
    walk = gauge_walk(frames, {name: row["moves"] for name, row in entries.items()}) if frames else None
    return {"summary": summary, "entries": entries, "gauges": walk}


def _regate_command(out: Path) -> list[str]:
    return ["nice", "-n", "5", sys.executable, "-m", "app.v6", "--regate-worker", str(out)]


def _worker_env(**extra: str) -> dict:
    """A worker imports the engine (experiments/mvp/frontier) and lab like the server. The README launch sets no PYTHONPATH,
    so the child gets the same paths v6_package and v6_sandbox give theirs (rehearsal 4.3: without them the regate died on
    ``import engine`` and the hero plans were withheld)."""
    paths = [str(ROOT / p) for p in (".", "src", "experiments/mvp/frontier", "experiments/mvp/frontier/lab")]
    if os.environ.get("PYTHONPATH"):
        paths.append(os.environ["PYTHONPATH"])
    return {**os.environ, **extra, "PYTHONPATH": os.pathsep.join(paths)}


def regate_hero() -> dict:
    """The launch regate, end to end, in a child process. Returns its result; a failure to finish is an answer, not a crash."""
    HERO_CACHE.mkdir(parents=True, exist_ok=True)
    out = HERO_CACHE / f"regate-{os.getpid()}.json"
    out.unlink(missing_ok=True)
    with (HERO_CACHE / "regate.log").open("w") as log_file:
        try:
            subprocess.run(_regate_command(out), cwd=ROOT, env=_worker_env(), stdout=log_file, stderr=subprocess.STDOUT, timeout=GATE_CAP_S, start_new_session=True, check=False)
        except subprocess.TimeoutExpired:
            return {"failed": f"the launch regate did not finish in {GATE_CAP_S:.0f} s"}
    return _json(out) if out.is_file() else {"failed": "the launch regate stopped without a result: see experiments/mvp/ui/cache/v6-hero/regate.log"}


def _pin_line(config: dict) -> str:
    """Printed at launch when the pin doesn't hold, and in the gate's reasons."""
    return f"catalogue_sha256 pinned {config.get('catalogue_sha256')}, current {scoped_catalogue_sha256(config)}: run python -m app.v6 --repin"


def repin() -> str:
    config = json.loads(HERO_FILE.read_text())
    config["catalogue_sha256"] = scoped_catalogue_sha256()
    HERO_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return config["catalogue_sha256"]


class Hero:
    """Stored plans go through the lattice's own gate, once per launch, off the request thread. Only what it passed is shown."""

    def __init__(self):
        self._lock, self._gate, self._thread = threading.Lock(), None, None

    def gate(self, service) -> dict:
        with self._lock:
            gate = self._gate
            if gate is None and self._thread is None:
                self._thread = threading.Thread(target=self._run, args=(service,), name="stowlm-v6-gate", daemon=True)
                self._thread.start()
        if gate is None:
            return {"status": "checking"}
        from app.check_cache import epoch_drift
        drift = epoch_drift()  # a verdict from this launch says nothing about sources changed since
        return {"status": "not_shown", "why": [drift]} if drift else gate

    def withheld(self, service, name: str) -> str | None:
        """Why the stored plan ``name`` can't leave the server right now, or None once its own gates passed. Fails closed."""
        gate = self.gate(service)
        status = gate.get("status")
        if status == "checking":
            return "Stored plan withheld: its launch check is still running."
        if (status == "shown" or status == "before_only" and name == "before") and dig(gate, name, "passed") is True:
            return None
        return f"Stored plan withheld: {(gate.get('why') or ['it has not passed its gates'])[0]}"[:180]

    def wait(self, service, timeout: float = GATE_CAP_S + 300.0) -> dict:
        """Launch warm-up only; request threads use ``gate``."""
        gate = self.gate(service)
        self._thread.join(timeout)
        return self.gate(service) if self._gate is not None else gate  # noqa: the thread settled or timed out

    def _run(self, service) -> None:
        try:
            result = self._check(service)
        except Exception as error:  # noqa: BLE001 - a gate failure is an answer, not a crash
            result = {"status": "not_shown", "why": [f"{type(error).__name__}: {error}"]}
        with self._lock:
            self._gate = result

    @staticmethod
    def _check(service) -> dict:
        """shown: both plans pass and the rule holds. before_only: the chapter holds the stored plan under the standing brief."""
        config = hero()
        ids = plan_ids(service)
        if not ids.get("before"):
            return {"status": "not_shown", "why": ["the hero isn't registered: launch with --v6"]}
        result = regate_hero()
        entries, summary = result.get("entries") or {}, result.get("summary") or {}
        errors = summary.get("errors") or {}
        if result.get("failed") or "catalogue" in errors:  # no entry cleared, so nothing is shown; the launch line says what to do
            return {"status": "not_shown", "why": [result.get("failed") or _pin_line(config)], "catalogue_sha256": optional(lambda: scoped_catalogue_sha256(config))}
        memo = result.get("source") == "memo"
        failure = CACHED_FAILURE if memo else ""
        cleared = lambda name: dig(entries, name, "passed") is True and dig(entries, name, "plan_sha256") == config.get(f"{name}_sha256") == _registered_sha(service, ids[name])
        # Fresh: the gate's own check ran in this launch. Memo: it ran when the entry says, on exactly these inputs.
        word = lambda name: memo_words(entries[name]["checked_utc"]) if memo and cleared(name) else RECHECKS["fresh"] if cleared(name) and dig(entries, name, "checked_utc") else None
        before = service.gate_replay(ids["before"])
        out = {"plan_id": ids["before"], "before": {"plan_id": ids["before"], "passed": _passed(before) and cleared("before"), "lifts": _lifts(before), "recheck": word("before"),
                                                    "engine": dig(entries, "before", "engine_version")},
               "envelope": optional(lambda: _envelope(service, ids["before"]), {}), "recheck": word("before"), "regate": {name: {k: row.get(k) for k in ("checked_utc", "wall_s", "frames")} for name, row in entries.items()},
               "gauges": result.get("gauges")}
        if not out["before"]["passed"]:
            return {**out, "status": "not_shown", "why": ["the stored plan did not pass the launch regate" + failure]}
        if not ids.get("after"):
            return {**out, "status": "before_only", "why": ["no plan under the rule yet"]}
        after = service.gate_replay(ids["after"])
        out["after"] = {"plan_id": ids["after"], "passed": _passed(after) and cleared("after"), "lifts": _lifts(after), "recheck": word("after"), "engine": dig(entries, "after", "engine_version")}
        both = memo and word("before") and word("after")
        out["recheck"] = memo_words(min(entries["before"]["checked_utc"], entries["after"]["checked_utc"])) if both else word("before") if word("before") == word("after") else None
        try:
            from experiments.mvp.reader.pipeline import intrinsic_violations
            from experiments.mvp.reader.schema import context_from_instance, validate_answer
            record = service._records[ids["after"]].inputs
            answer = validate_answer(json.dumps(hero_reading()["answer"]), context_from_instance(record.instance))
            broken = list(intrinsic_violations(record.instance, answer, record.plan))
        except Exception as error:  # noqa: BLE001 - no readable rule means no plan under it, not a dead chapter
            return {**out, "status": "before_only", "why": [f"the rule can't be read: {type(error).__name__}"]}
        out["honoured"], out["instruction_violations"] = not broken, len(broken)
        if not (out["after"]["passed"] and not broken):
            return {**out, "status": "before_only", "why": ["the plan under the rule failed its gate" + failure]}
        moved = moved_boxes(before, after)
        extra = None if None in (_lifts(before), _lifts(after)) else _lifts(after) - _lifts(before)
        return {**out, "status": "shown", "computed_utc": dig(entries, "after", "computed_utc"), "boxes_moved": len(moved), "extra_lifts": extra,
                "forced_boxes": _forced_boxes(record.instance, answer, before)}


def _registered_sha(service, plan_id: str) -> str:
    """The plan this server would actually show: the file it loaded at launch, not the entry the regate read."""
    from experiments.mvp.pipeline.contract.schema import sha256_json
    return sha256_json(service._records[plan_id].inputs.plan.model_dump(mode="json"))


def memo_words(checked_utc: str) -> str:
    moment = datetime.fromisoformat(checked_utc).astimezone(timezone.utc)
    return f"rechecked {moment:%Y-%m-%d %H:%M} UTC (exact memo)"


def _passed(replay: dict) -> bool:
    checker = replay.get("checker") or replay.get("report") or {}
    return checker.get("passed") is True


RECHECKS = {"fresh": "rechecked now", "memo": "rechecked (exact memo)"}


def recheck_words(*replays) -> str | None:
    """What really happened to these plans' full checks in this process. "Rechecked now" only if every one of them ran;
    no claim at all if any of them can't say (a loaded report, a stored flag). The hero's own words come from the launch regate."""
    sources = [(replay.get("checker") or {}).get("check_source") for replay in replays]
    if not sources or any(source not in RECHECKS for source in sources):
        return None
    return RECHECKS["fresh" if all(source == "fresh" for source in sources) else "memo"]


def _lifts(replay: dict):
    """The engine's objective at the end of the plan: lifts now plus projected later-port lifts (D15)."""
    frames = replay.get("frames") or [{}]
    return (frames[-1].get("counts") or {}).get("weighted_nonproductive")


def _final_slots(replay: dict) -> dict:
    placements = ((((replay.get("frames") or [{}])[-1].get("state") or {}).get("ship") or {}).get("placements")) or []
    return {row["container_id"]: row["slot"] for row in placements}


def moved_boxes(before: dict, after: dict) -> list[str]:
    old, new = _final_slots(before), _final_slots(after)
    return sorted(box for box, slot in new.items() if old.get(box) != slot)


def forced_out(slots: dict, forbids) -> list[str]:
    """The boxes the rule itself moves: those sitting, in the plan before, where the rule forbids them. Pure."""
    return sorted(box for box, slot in slots.items() if forbids(box, slot))


def _forced_boxes(instance, answer, before: dict) -> list[str] | None:
    """Through the rule's own code, as the gate reads it. None when that can't be asked: then nothing is credited to the rule."""
    from experiments.mvp.reader.occupancy import apply_reader_constraint
    return optional(lambda: forced_out(_final_slots(before), apply_reader_constraint(instance, answer).forbids), None)


def _envelope(service, plan_id: str) -> dict:
    """The pipeline's own design gate, measured on the hero map; the words on screen come from these numbers."""
    # By file, not through the package: importing the runner sets STOWAGE_VESSEL_CACHE_DIR, which reads as checker drift here.
    spec = importlib.util.spec_from_file_location("v6_runner_envelope", PIPELINE / "runner/envelope.py")
    envelope = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(envelope)
    result = envelope.check(service._records[plan_id].inputs.instance)
    numbers = result["numbers"]
    kind, teu = _ship_kind(numbers)
    return {"inside": result["in_envelope"], "boxes": numbers["boxes_to_load"], "teu": teu, "kind": kind,
            "fill_pct": round(100 * numbers["physical_cell_fill"]), "region": numbers["planned_region"]}


HERO = Hero()


def guard_plans(service) -> None:
    """``--v6``: the hero plans leave ``DemoService`` only through their gate. The break-it plan keeps the plan before's crane order, so it goes with that plan."""
    ids = plan_ids(service)
    covered = {plan_id: name for plan_id, name in ((ids["before"], "before"), (ids["after"], "after"), (ids["sandbox"], "before")) if plan_id}
    service.plan_gate = lambda plan_id: HERO.withheld(service, covered[plan_id]) if plan_id in covered else None


def _withheld(service, plan_id: str | None) -> str | None:
    gate = getattr(service, "plan_gate", None)
    return gate(plan_id) if gate and plan_id else None


def _require_sandbox(handler) -> None:
    service = getattr(handler, "demo_service", None)
    if getattr(service, "plan_gate", None):
        ids = plan_ids(service)
        reason = _withheld(service, ids["sandbox"] or ids["before"])
        if reason:
            raise Withheld(reason)


def plan_ids(service) -> dict:
    """Registered hero plans, matched by the label ``--v6`` gave them."""
    plans = (service.catalog() if service is not None else {"plans": []})["plans"]
    return {key: next((p["id"] for p in plans if p.get("label") == f"v6:{key}"), None) for key in ("before", "after", "sandbox")}


def frame_verdict(frame: dict) -> str | None:
    """None inside limits, else why. Same test as the panel's badge (StageRules.physicsBadge)."""
    if frame.get("error"):
        return "not measured"
    if frame.get("stability_passed") is False or frame.get("strength_passed") is False:
        parts = [name for name, key in (("stability", "stability_passed"), ("strength", "strength_passed")) if frame.get(key) is False]
        return "outside limits: " + " and ".join(parts)
    if frame.get("stability_passed") is True and frame.get("strength_passed") is True:
        return None
    return "not measured"


def gauge_walk(frames_by_plan: dict, lifts_by_plan: dict | None = None) -> dict:
    """Every physics frame of both plans. The caption's words are decided here, never by hand (script branch).

    "Stay" speaks of the gauges the chapter plays, the plan after, so an older plan before can't talk a clean hero down, and it
    needs that plan's every crane step: a stage timeline is keyframes, and a green sample says nothing about the frames between.
    With ``lifts_by_plan`` unknown, nothing proves the walk complete. "Ends" needs both plans: any last frame outside limits drops the line."""
    played = "after" if "after" in frames_by_plan else next(iter(frames_by_plan), None)
    bad, ends, count = [], [], 0
    for name, frames in frames_by_plan.items():
        ordered = sorted(frames, key=lambda f: f.get("step", 0))
        count += len(ordered)
        bad += [{"plan": name, "step": f.get("step"), "why": frame_verdict(f), "criteria": f.get("criteria")} for f in ordered if frame_verdict(f)]
        ends.append(bool(ordered) and frame_verdict(ordered[-1]) is None)
    measured = bool(frames_by_plan) and all(frames_by_plan.values())
    lifts = (lifts_by_plan or {}).get(played)
    every = lifts is not None and [f.get("step") for f in sorted(frames_by_plan.get(played) or [], key=lambda f: f.get("step", 0))] == list(range(lifts + 1))
    stay, end = measured and every and all(row["plan"] != played for row in bad) and all(ends), measured and all(ends)
    return {"frames": count, "played_frames": len(frames_by_plan.get(played) or []), "outside": bad, "every_frame": every, "stay_green": stay, "ends_green": end,
            "line": "The gauges stay green" if stay else "Every gauge ends green" if end else None}


def _gauges(stage, ids: dict) -> dict | None:
    """The walk over what's measured, once the played plan is; None until then. Memoised when both plans' frames are in: a stored plan's
    don't change. A plan before still computing can't blank the caption of the plan on screen."""
    plans = {name: ids[name] for name in ("before", "after") if ids.get(name)}
    keys = tuple(plans.values())
    if stage is None or not keys:
        return None
    if _GAUGES.get("keys") == keys:
        return _GAUGES["walk"]
    statuses = {name: stage.physics_status(plan_id, start=False) for name, plan_id in plans.items()}
    frames = {name: (s["physics"] or {}).get("frames") or [] for name, s in statuses.items() if s.get("status") == "ready"}
    if (list(plans)[-1]) not in frames:
        return None
    walk = gauge_walk(frames)
    if len(frames) == len(plans):
        _GAUGES.update(keys=keys, walk=walk)
    return walk


_GAUGES: dict = {}


def incumbents(path: Path) -> list[dict]:
    """The real run's checked plans with their real seconds: the runner's incumbent events."""
    rows = []
    if not Path(path).is_file():
        return rows
    for line in Path(path).read_text().splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") == "incumbent":
            data = row.get("data") or {}
            if data.get("checked") and data.get("lifts") is not None:
                rows.append({"t_s": row.get("elapsed_s", row.get("t_s")), "lifts": float(data["lifts"]), "lane": data.get("lane")})
    return rows


def rule_counts() -> dict | None:
    """The receipt's active count and how many catalogue entries were off as run. Never a literal: no data, no number."""
    def counts(active, off):
        return {"active": active, "off": off} if all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (active, off)) else None
    coverage = optional(lambda: _json(PACKAGE / "rule-coverage.json"), {})
    summary = optional(lambda: _json(PACKAGE.parent / "verification-summary.json"), {})
    return (counts(dig(coverage, "active_rule_count"), dig(coverage, "inactive_rule_count"))
            or counts(dig(summary, "rule_counts", "checker_active"), dig(summary, "rule_counts", "inactive")))


def first_plan_figure(path: Path = PROFILE) -> dict | None:
    """The live profile's measured first-plan time, once the runner publishes it. Absent or reshaped: nothing on screen."""
    data = optional(lambda: _json(path), {})
    stats = dig(data, "profiles", "live", "measured_first_legal_s")
    median, runs = number(dig(stats, "median")), number(dig(stats, "n"))
    return {"median_s": median, "runs": runs} if dig(data, "measured") is True and median is not None and runs else None


def _replan_rule() -> dict:
    from app.brief import rule_text
    now = hero_reading()
    reader = _who_read(now)  # from evidence: the stand-in label leaves only with a real reader's recording
    return {"sentence": now["sentence"], "text": rule_text(now["answer"]), "reader": reader["label"],
            "stand_in_note": None if reader["kind"] == "stowlm_reader" else _stand_in_note(now)}


def stand_ins() -> dict:
    """Which pieces are standing in right now, each with its own words, decided from evidence. A real piece has no key here."""
    config = optional(hero, {})
    config = config if isinstance(config, dict) else {}
    named, out = config.get("stand_ins") if isinstance(config.get("stand_ins"), dict) else {}, {}
    if named.get("ship"):
        out["ship"] = named["ship"]
    now = optional(hero_reading)
    if now and _who_read(now)["kind"] != "stowlm_reader":
        out["reader"] = _stand_in_note(now)
    worded = bool(dig(optional(hero_wording), "radio"))  # the chosen wording's take plays: its own words, else the separate clip's
    if now and not optional(_radio_is_the_rule, False) and named.get("radio_wording" if worded else "radio"):
        out["radio"] = named["radio_wording" if worded else "radio"]
    return out


def _live_view() -> dict | None:
    """The live run's own sentence, shown beside its button: pressing that button is the planner's confirmation of this reading."""
    from app.brief import rule_text
    now = live_reading()
    return {"sentence": now["sentence"], "rule": rule_text(now["answer"]), "reader": _who_read(now)["label"], "label": RECORDED, "binding": _binding_of(now)} if now else None


def _engine_words(gate: dict) -> tuple[str | None, bool]:
    """(the credit, did two engines make these plans), from each plan's own evidence."""
    engines = [dig(gate, name, "engine") for name in ("before", "after")]
    known = [engine for engine in engines if engine]
    return (f"Frontier Engine {known[-1]} · search software" if known else None), len(set(known)) > 1


def _visible_gate(gate: dict) -> dict:
    """The page gets a rejected plan's status and reason, never its figures."""
    status = gate.get("status")
    if status == "shown":
        return gate
    if status == "before_only":
        return {key: value for key, value in gate.items() if key != "after"}
    return {key: gate[key] for key in ("status", "why", "catalogue_sha256") if key in gate}


def replan(service, stage=None, options=None, token=None, reading=None, recorded: bool = False) -> dict:
    """Every input is read on its own: a reshaped file costs its panel, not the chapter. Only the gate can hold or close it.
    The plan under the rule is sent only for the selected reading confirmed by ``token``, or the recorded answer chosen by name
    (``recorded``), and only when that reading is the stored rule (``reading_gate``)."""
    config = optional(hero, {})
    config = config if isinstance(config, dict) else {}
    ids = optional(lambda: plan_ids(service), {})
    said = optional(lambda: reading_gate(token, reading, recorded), {"shown": False, "why": "The reading can't be read, so no plan under the rule is shown."})
    gate = _visible_gate(gate_for_reading(HERO.gate(service), said))
    if gate.get("status") != "shown" and isinstance(ids, dict) and "after" in ids:
        ids = {**ids, "after": None}  # no id of a plan the page may not show
    note, word = config.get("before_note"), (gate.get("before") or {}).get("recheck")
    credit, mixed = _engine_words(gate)
    live = bool((options or {}).get("live_replan"))
    return {"mode": "stored", "stand_ins": stand_ins(), "ship": config.get("ship"), "gate": gate, "plans": ids, "credits": credit, "mixed_engines": mixed,
            "rule": optional(_replan_rule, None), "before_note": f"{note}, {word}" if note and word else note,
            "gauges": (gate.get("gauges") or optional(lambda: _gauges(stage, ids), None)) if gate.get("status") in {"shown", "before_only"} else None,
            "rule_counts": rule_counts(), "first_plan": first_plan_figure(), "producers": config.get("producers"),
            "heel": optional(lambda: _peak_heel(stage, ids, config)) if gate.get("status") == "shown" else None,
            "live_available": live, "live": optional(_live_view) if live else None,
            "facts": optional(change_facts) if gate.get("status") == "shown" else None,  # the lattice's own figures for these plans; they win on screen
            "reading_gate": said}


HERO_GAUGES = ROOT / "experiments/mvp/ui/fixtures/hero-gauges.json"


def _peak_heel(stage, ids: dict, config: dict) -> dict | None:
    """The largest list over every crane frame of the plan on screen, and the ship's own list limit: both measured, never typed.
    The stage's frames are keyframes, so the peak comes from the full walk (experiments/mvp/ui/dev/v6_gauges.py), bound to this
    plan's bytes, this instance and this physics epoch, with one frame per lift and the first. Anything else: no figure."""
    from app.stage_physics import physics_epoch
    walk = _json(HERO_GAUGES)
    row = dig(walk, "plans", "after") or {}
    if (walk.get("instance_sha256") != config["instance_sha256"] or walk.get("physics_epoch") != physics_epoch()
            or row.get("plan_sha256") != config.get("after_sha256") or row.get("frames") != _whole(row.get("lifts")) + 1):
        return None
    limit = _json(ROOT / config["instance"])["vessel"]["hull"]["max_list_rad"]
    return {"peak_deg": round(float(row["max_abs_heel_deg"]), 3), "limit_deg": round(math.degrees(limit), 3), "frames": row["frames"]}


# ---------------------------------------------------------------- the live replan (--brief-live-replan only)
LIVE_ENGINE_S = 1800.0  # the profile's 720 s found no checked plan on the shared machine (fixtures/live-replan-run.json, attempts)
LIVE_CAP_S = LIVE_ENGINE_S + 900.0  # then release checks and packaging


def _live_command(out: Path) -> list[str]:
    return ["nice", "-n", "5", sys.executable, "-m", "app.v6", "--replan-worker", str(out)]


class LiveReplan:
    """One job in its own process through the runner. Real elapsed seconds, no percentage, incumbents as they arrive."""

    def __init__(self, command=_live_command):
        self.command, self._lock, self._jobs = command, threading.Lock(), {}

    def start(self, handler, token=None) -> dict:
        if not (getattr(handler, "brief_options", None) or {}).get("live_replan"):
            raise PermissionError("Live replans are off; start the app with --brief-live-replan")
        confirmation = CONFIRMATIONS.require(token)
        with self._lock:
            if any(job["status"] == "running" for job in self._jobs.values()):
                raise ConflictError("A live replan is already running")
            CONFIRMATIONS.spend(token)
            identity = uuid4().hex
            out = LIVE_DIR / f"live-{identity[:8]}"
            job = {"id": identity, "status": "running", "started": monotonic(), "out": out, "elapsed_s": None, "result": None, "error": None,
                   "load1_start": round(os.getloadavg()[0], 2), "load1_end": None}
            self._jobs[identity] = job
        out.mkdir(parents=True)
        (out / "confirmation.json").write_text(json.dumps(v6_build.stamp(confirmation)))
        environment = _worker_env(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", VECLIB_MAXIMUM_THREADS="1")
        with (out / "worker.log").open("w") as log_file:
            process = subprocess.Popen(self.command(out), cwd=ROOT, env=environment, stdout=log_file,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        threading.Thread(target=self._watch, args=(job, process, handler.demo_service), name="stowlm-v6-replan", daemon=True).start()
        return self.view(identity)

    def _watch(self, job: dict, process, service) -> None:
        try:
            try:
                code = process.wait(timeout=LIVE_CAP_S)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                raise RuntimeError(f"stopped after {LIVE_CAP_S:.0f} s") from None
            job["elapsed_s"] = round(monotonic() - job["started"], 1)
            job["result"] = v6_build.stamp(self._land(job, code, service))
            (job["out"] / "app-receipt.json").write_text(json.dumps({"elapsed_s": job["elapsed_s"], **job["result"]}, indent=2) + "\n")
            job["status"] = "done"
        except Exception as error:  # noqa: BLE001 - nothing is shown from a failed run
            job.update(status="failed", error=f"{type(error).__name__}: {error}", elapsed_s=round(monotonic() - job["started"], 1))
        job["load1_end"] = round(os.getloadavg()[0], 2)

    @staticmethod
    def _land(job: dict, code: int, service) -> dict:
        from app.inputs import LoadedInputs
        from experiments.mvp.reader.pipeline import intrinsic_violations
        from experiments.mvp.reader.schema import context_from_instance, validate_answer
        from stowage.model import MovePlan
        outcome = _json(job["out"] / "outcome.json")
        result = {"status": outcome.get("status"), "passed": outcome.get("passed") is True, "exit_code": code, "message": outcome.get("message")}
        if result["passed"] and outcome.get("plan"):
            before_id = plan_ids(service)["before"]
            base = service._records[before_id].inputs.instance
            plan = MovePlan.model_validate(outcome["plan"])
            voyage = next(row for row in dig(outcome, "reading", "sentences", default=[]) if row.get("role") == "voyage")
            answer = validate_answer(json.dumps(voyage["parsed"]), context_from_instance(base))
            result["plan_id"] = service.register(LoadedInputs(base, plan, {"label": "v6:live", "planner": "frontier.0.13.0"}))
            replay = service.replay(result["plan_id"])
            broken = list(intrinsic_violations(base, answer, plan))
            result.update(passed=_passed(replay) and not broken, lifts=_lifts(replay), instruction_violations=len(broken), first_checked_s=outcome.get("first_checked_wall_s"),
                          moved=len(moved_boxes(service.replay(before_id), replay)), forced_boxes=_forced_boxes(base, answer, service.replay(before_id)))
        return result

    def view(self, identity: str) -> dict:
        with self._lock:
            job = self._jobs[identity]
        elapsed = job["elapsed_s"] if job["elapsed_s"] is not None else round(monotonic() - job["started"], 1)
        return {"id": identity, "status": job["status"], "elapsed_s": elapsed, "result": job["result"], "error": job["error"],
                "load1_start": job.get("load1_start"), "load1_end": job.get("load1_end"), "incumbents": incumbents(job["out"] / "events.jsonl")}


LIVE = LiveReplan()


class _ConfirmedReader:
    """The one confirmed reading. ``read_brief`` also asks about the empty standing line; nothing was said there, so nothing is read."""

    def __init__(self, fixed):
        self.fixed = fixed

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        if sentence.strip():
            return self.fixed.parse(sentence, context)
        empty = {"schema_version": self.fixed.envelope["schema_version"], "refusal": {"code": "not_an_instruction", "reason": "Empty standing text", "refused": True}}
        return validate_answer(json.dumps(empty), context), dict(self.fixed.evidence)


def replan_worker(out: str) -> None:
    """The live run: the hero map as its own packet, the confirmed reading, Engine 0.13.0 through the runner, its own process.

    The worker re-derives the hashes and passes the runner a callback that accepts only the reading the token was issued
    for. ``run_packet`` then seals that exact reading with ``binding.confirm_brief`` under the token's own source."""
    from datetime import datetime, timezone
    from app.brief import _FixedAnswer, safe_evidence
    from experiments.mvp.pipeline.runner import assemble, run_packet
    from experiments.mvp.reader.schema import context_from_instance
    config = hero()
    confirmation = _json(Path(out) / "confirmation.json")
    choices = [row for row in (optional(hero_reading), optional(live_reading)) if row]
    clean = next((row for row in choices if all(confirmation[key] == _binding_of(row)[key] for key, _ in _BINDING)), None)
    if clean is None:
        raise SystemExit("the confirmation doesn't match this reading")
    packet = {"schema_version": "stowlm.voyage-packet/1", "packet_id": "v6-live-replan", "created_utc": datetime.now(timezone.utc).isoformat(), "synthetic": True,
              "base_instance": {"path": config["instance"], "sha256": config["file_sha256"]}, "load_list": None, "yard_state": None, "standing": "",
              "voyage_sentences": [{"text": clean["sentence"], "origin": "typed"}], "profile": "live"}

    def confirmed(row: dict) -> bool:
        return (_sha256(row["sentence"]) == confirmation["sentence_sha256"]
                and _rule_digest(row["parsed"]) == confirmation["rule_sha256"])

    context = context_from_instance(assemble(packet))
    parser = _ConfirmedReader(_FixedAnswer(clean["sentence"], clean["answer"], safe_evidence(clean["evidence"]), context.get("id")))
    run_packet(packet, parser=parser, out_dir=out, confirm=confirmed, confirmation_source=confirmation["source"], engine_timeout_s=LIVE_ENGINE_S)


# ---------------------------------------------------------------- x-ray, sandbox, copilot, package
# The adopted reader's (swa-last-5) recorded block leads; the last two come from reader/v3/mask_study.py.
_RECORDED_TRACES = ("real-block-trace.json", "real-trace.json", "before-fine-tuning-trace.json")
# Logic review 5, Z4: adapter-v2's trace, older than the freeze and from a reversed adoption. Never searched, never named.
_SUPERSEDED_TRACES = frozenset({"real-block-trace-adopted.json"})


def _trace_path() -> Path:
    """``xray_trace`` in the hero file, then a recording in the fixtures, then the fake, which only the interactive chapter plays."""
    named = _settings().get("xray_trace")
    if named and Path(named).name in _SUPERSEDED_TRACES:
        named = None
    choices = ([ROOT / named] if named else []) + [XRAY / "fixtures" / name for name in _RECORDED_TRACES]
    return next((path for path in choices if path.is_file()), XRAY / "fixtures/fake-illegal-bay.json")


def _trace_facts(trace) -> dict:
    """All this chapter reads of a trace. New fields pass; a shape it can't read raises, and ``feature`` makes that a gap."""
    source = trace.get("source") if isinstance(trace, dict) else None
    steps = trace.get("steps") if isinstance(trace, dict) else None
    if not isinstance(source, dict) or not source.get("label") or not isinstance(steps, list):
        raise ValueError("x-ray trace has an unknown shape")
    ranked = [step.get("top_k") if isinstance(step, dict) else None for step in steps]
    if not steps or not all(isinstance(row, list) and all(isinstance(c, dict) for c in row) for row in ranked):
        raise ValueError("x-ray trace has an unknown step shape")
    # The stop step's end-token swap isn't a wish the mask refused (the study's 2 Nov amendment excludes it), so it never counts as a block.
    tops = [row[0] for step, row in zip(steps, ranked) if row and row[0].get("blocked") is True and not step.get("is_stop")]
    first = tops[0] if tops else None
    adapter = dig(trace, "stage_record", "adapter_sha256")
    adopted = optional(lambda: _adoption()["adopted"]["adapter_sha256"])
    return {"kind": source.get("kind"), "label": source["label"], "tokens": len(steps), "blocked": len(tops), "adopted": bool(adapter) and adapter == adopted,
            "first_block": {"token": first.get("token"), "reason": first.get("reason"), "probability": first.get("probability")} if first else None}


def xray() -> dict:
    from experiments.mvp.pipeline.xray import validate_trace
    trace = _json(_trace_path())
    facts = _trace_facts(trace)
    validate_trace(trace)  # a bad recording is a gap, never the fake in its place
    for name in ("xray-player.js", "xray-player.css"):
        if not ASSETS[name][0].is_file():
            raise FileNotFoundError(name)
    real, label = facts["kind"] == "recorded", facts["label"]
    return {"trace": trace, "real": real, "film": real and facts["adopted"] and facts["blocked"] > 0, "adopted": facts["adopted"], "player_js": "/api/v6/asset/xray-player.js",
            "player_css": "/api/v6/asset/xray-player.css", "source_label": label,
            "caption_label": label if not real or "Recorded answer" in label else f"Recorded answer · {label}", "tokens": facts["tokens"],
            "blocked": facts["blocked"], "first_block": facts["first_block"]}


def sandbox() -> dict:
    from app import v6_sandbox as bench
    if bench.failed():
        raise ValueError(bench.failed())
    demo = bench.read_demo()
    derived = bench.derivation(demo) or {}  # a demo on another instance raises: a clean gap, never a mismatch on screen
    return {"mode": "recorded", "label": RECORDED, "source": demo.get("source_fixture"), "cases": bench.cases_view(demo),
            "plan_label": "v6:sandbox", "stored_label": bench.stored_label(demo), "baseline": bench.baseline(), "measured": bench.measured(), "running_label": bench.FAST_LABEL,
            "challenge_tag": bench.CHALLENGE_TAG,  # D311: the fixture's own label said limits were tightened; only the cargo declaration changed
            "challenge_note": demo.get("label_disclosure"), "derived": derived.get("line"), "replay": optional(replay_page)}


def replay_page() -> dict | None:
    """The static page of the pinned drags (experiments/mvp/ui/replay/build.py), only if it was built from the cache the hero pins."""
    page, pin = ASSETS["replay.html"][0], _settings().get("drag_cache_sha256")
    text = page.read_text() if page.is_file() else ""
    cache = _json(SANDBOX / "drag-cache.index.json") if pin and f"sha256 {pin[:16]}" in text else None
    if not cache or cache.get("cache_sha256") != pin:
        return None
    return {"drags": int(cache["entries_count"]), "page": "/api/v6/asset/replay.html", "label": "Recorded verdict"}


DELIVERED = COPILOT / "hero-shot10-delivered"
_HELD_WORDS = {"unsupported_number": "a number no tool returned", "unsupported_acceptance": "an acceptance or plan reference no tool returned"}


def _first_line(text) -> str:
    text = " ".join(str(text or "").split())
    return (re.split(r"(?<=[.!?])\s+", text, maxsplit=1) or [""])[0]


def copilot_delivered() -> dict:
    """The delivered real-tools take (copilot/RESULTS.md, "delivered shot 10"): every word, number and label is the recording's.

    Spark's raw first line beside what software let through, per turn; the two checked cards ranked by their own lifts; the
    planner's choice (the card whose plan the host accepted); the reader's rule; the stored plan, a tick only on a checker pass.
    Scripted planner replies: the clarifications sent after withheld prose, up to the first turn whose prose went through."""
    from app.brief import rule_text
    take, facts = _json(DELIVERED / "shot10.json"), _json(DELIVERED / "shot10-facts.json")
    turns = [row for row in dicts(take.get("turns")) if isinstance(row.get("turn"), dict)]
    if not turns:
        raise ValueError("no turns in the delivered take")
    held = [list((row.get("gate") or {}).get("reasons") or []) for row in turns]
    through = next((i for i, reasons in enumerate(held) if not reasons), None)
    if through is None:
        raise ValueError("the delivered take let no prose through")
    calls = [(i, call) for i, row in enumerate(turns) for call in dicts(row["turn"].get("tool_calls"))]
    comparison = next((row for _, call in calls for row in dicts(dig(call, "result", "alternative_comparisons", default=[]))), None)
    chosen = facts.get("selected_plan_sha256")
    if chosen not in (facts.get("observed_acceptance") or []):
        raise ValueError("the host never accepted the selected plan")
    cards = sorted(({"id": a.get("id"), "sentence": (a.get("sentences") or [""])[0], "lifts": number(a.get("lifts")), "checked": a.get("label") == "checked",
                     "chosen": a.get("plan_sha256") == chosen} for a in dicts(facts.get("alternatives"))), key=lambda a: (a["lifts"] is None, a["lifts"]))
    if len(cards) < 2 or sum(card["chosen"] for card in cards) != 1:
        raise ValueError("the delivered take doesn't rank two checked cards with one chosen")
    pick = next(i for i, row in enumerate(turns) if any(card["chosen"] and card["id"] in str(row.get("message")) for card in cards))
    read = next((call for i, call in calls if i >= pick and call.get("name") == "read_instruction"), {})
    parsed = dig(read, "result", "proof", "parsed") or dig(read, "result", "proposed_reading", "parsed")
    check = next((call for i, call in calls if i > pick and call.get("name") == "check_plan"), {})
    result = check.get("result") if isinstance(check.get("result"), dict) else {}
    evidence = next((a.get("evidence") for a in dicts(facts.get("alternatives")) if a.get("plan_sha256") == chosen), {}) or {}
    plan = None
    if result.get("passed") is True and dig(result, "checker", "passed") is not False and result.get("plan_sha256") == chosen and evidence.get("computed_utc"):
        checked = str(result.get("checked_utc") or evidence.get("checked_utc") or "")[:10]
        plan = {"label": f"Stored plan, computed on {evidence['computed_utc'][:10]}" + (f" · Checked on {checked}" if checked else ""), "lifts": number(result.get("lifts")),
                "lifts_label": result.get("lifts_label"), "checker": {"passed": True}}
    confirmation = take.get("host_confirmation") or {}
    recommend = next((line for line in re.split(r"(?<=[.!?])\s+|\n", str(turns[through]["turn"].get("say") or "")) if "recommend" in line.lower()), None)
    gap = number(dig(comparison, "right_minus_left_lifts")) if comparison else None
    # The take's own request and its proof, so its "143 needed > 60 open" reads beside the instruction it answers (logic14 p22)
    asked = (turns[0].get("host_request") or {}).get("selected_sentence") or next((dig(call, "arguments", "sentence") for _, call in calls if call.get("name") == "read_instruction"), "")
    request = [line.strip() for line in str(asked or "").splitlines() if line.strip()]
    return {"id": "delivered", "request": request, "request_equation": dig(facts, "proof", "equation"), "real_tools": True, "mode": "recorded", "label": take.get("label") or RECORDED, "reader_label": take.get("reader") or facts.get("reader"),
            "facts_label": facts.get("facts_label"), "scripted_replies": through, "scripted_replies_said": _spoken_count(through), "lift_gap": int(gap) if gap is not None and gap == int(gap) else gap,
            "comparison_label": dig(comparison, "label") if comparison else None, "recommendation": recommend, "cards": cards,
            "turns": [{"planner": _first_line(row.get("message")), "raw": _first_line(row.get("raw_say") or (row.get("gate") or {}).get("raw_say")),
                       "shown": _first_line(row["turn"].get("say")), "held": [_HELD_WORDS.get(r, r.replace("_", " ")) for r in reasons], "through": not reasons}
                      for row, reasons in zip(turns, held)],
            "steps": [{"kind": "tool", "name": call.get("name"), "wall_s": number(call.get("wall_s")), "turn": i} for i, call in calls],
            "rule": optional(lambda: rule_text(parsed), "") if parsed else "",
            "confirmed": confirmation.get("status") == "confirmed" and confirmation.get("planner_confirmed") is True, "plan": plan}


def copilot() -> dict:
    """The recorded negotiation, the attack and the real-tool bay refusal, each carrying its own fixture's label.

    A real-tool recording leads only if it holds a counter-offer. ``real-negotiation.json`` is the bay closure, whose proof
    leaves no alternative, so the doubles recording of the tier case plays the "No" (shot 9a, labelled test tools) and the
    real one stays a beat of its own. The doubles' unchecked counter-offer was a stand-in and is retired: the negotiation is
    ``delivered``, the real-tools take (shot 9b, ``copilot_delivered``). No delivered take: the "No" holds.
    """
    import re
    from app.brief import rule_text
    from experiments.mvp.pipeline.copilot import replay

    def locked_tools(used: set) -> list[dict]:
        return [{"kind": "locked", "name": name, "note": "not called: no confirmed reading"}
                for name in ("check_plan", "try_ladder", "plan_lookup") if name not in used]

    def steps_of(rows: list, tools: list) -> list[dict]:
        steps, calls = [], iter(tools)
        for row in rows:
            trace = dicts(row.get("trace"))
            for n, entry in enumerate(trace):
                if entry.get("role") == "model":
                    raw = str(entry.get("model_raw") or "")
                    rejected = n + 1 < len(trace) and trace[n + 1].get("role") == "protocol"
                    action = dig(_action(raw), "tool")
                    note = "prose around the JSON" if rejected else (f"picks {action}" if action else "replies")
                    steps.append({"kind": "model", "name": "Muse Spark", "note": note, "raw": raw[:280]})
                elif entry.get("role") == "protocol":
                    steps.append({"kind": "guard", "name": "guard", "note": "rejected: " + str(entry.get("content"))})
                elif entry.get("role") == "tool":
                    call = next(calls, {})
                    steps.append({"kind": "tool", "name": entry.get("name"), "wall_s": call.get("wall_s")})
                    if (call.get("result") or {}).get("arithmetic"):
                        steps.append({"kind": "proof", "name": "arithmetic", "note": f"inside {entry.get('name')}"})
        return steps + locked_tools({call.get("name") for call in tools})

    def _action(raw: str):
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def view(data: dict, real: bool, name: str) -> dict:
        rows = dicts(data.get("turns") if real else dig(data, "conversation", "turns"))
        if not rows:
            raise ValueError("no turns in this recording")
        turns = [row.get("turn") if isinstance(row.get("turn"), dict) else {} for row in rows]
        raw_calls = [call for turn in turns for call in dicts(turn.get("tool_calls"))]
        tools = [{"name": call.get("name"), "arguments": call.get("arguments"), "wall_s": number(call.get("wall_s")),
                  "result": _tool_view(call.get("result"))} for call in raw_calls]
        # A recording that refuses before any tool runs has no read: the planner's own message is the sentence.
        first = next((call for call in tools if call["name"] == "read_instruction"), tools[0] if tools else {"name": None, "arguments": {}, "result": _tool_view(None)})
        result = first["result"]
        arithmetic, say = result.get("arithmetic"), " ".join(str(turn.get("say") or "") for turn in turns)
        raw_result = next((call.get("result") for call in raw_calls if call.get("name") == first["name"]), None)
        raw_result = raw_result if isinstance(raw_result, dict) else {}
        offer = None
        if result["alternatives"]:
            alternative = dicts(raw_result.get("alternatives"))[0]
            static = alternative.get("static_proof") or {}
            tag = "Unchecked alternative; requires planner choice" if "Unchecked alternative; requires planner choice" in say else None
            offer = {**result["alternatives"][0], "tag": tag, "rule": optional(lambda: rule_text(alternative.get("constraint")), ""),
                     "open_cells": static.get("open_cells"), "proven_impossible": static.get("proven_impossible"),
                     "instance_sha256": (alternative.get("stored_witness") or {}).get("input_sha256")}
        sentences, seen_offer = [], False
        for text in re.split(r"(?<=[.!?])\s+", say.strip()):
            seen_offer = seen_offer or "lternative" in text
            if "QZXU" in text or not text:
                continue  # the ids of the boxes with no slot: the card counts them
            place = "card" if "TEU of cells" in text or "Container-specific" in text else ("offer" if seen_offer else "no")
            sentences.append({"text": text.replace("Unchecked alternative; requires planner choice: ", ""), "place": place})  # the card carries that tag
        reading = dig(turns[0], "proposed_reading", "parsed") or raw_result.get("recorded_reading")
        reading = reading if isinstance(reading, dict) else None
        conversation = data.get("conversation") or {}
        numbers = dig(data, "source", "envelope", "numbers", default={})
        count = arithmetic or {}
        trace = dicts(rows[0].get("trace"))
        refusal = dig(reading, "refusal")
        return {"id": name, "real_tools": real, "label": data.get("label", RECORDED),
                "reader_label": data.get("reader_label") or dig(conversation, "label"),
                "scope": data.get("recording_scope"), "sentence": dig(first, "arguments", "sentence") or rows[0].get("message") or "",
                "rule": optional(lambda: rule_text(reading), ""),
                "refusal": refusal if isinstance(refusal, dict) else None, "sentences": sentences, "arithmetic": arithmetic, "offer": offer,
                "steps": steps_of(rows, tools), "instance_sha256": dig(trace[0], "content", "instance_sha256") if trace else None,
                "section": {"boxes": numbers.get("boxes_to_load") or count.get("boxes"), "cells": numbers.get("physical_cell_capacity") or count.get("physical"),
                            "size": dig(data, "source", "envelope", "size_class")},
                "wall_s": number(rows[0].get("wall_s")), "plan_sha256": next((t.get("plan_sha256") for t in turns if t.get("plan_sha256")), None), "mode": "recorded",
                "adopted": optional(lambda: adopted_reading(dig(first, "arguments", "sentence") or rows[0].get("message") or ""), None)}

    def attempt(load):
        try:
            return load()
        except Exception as error:  # noqa: BLE001 - one missing recording only drops its own beat
            log.info("v6 copilot recording unavailable: %s", error)
            return None

    def offer_plan(conversation: dict):
        """The lattice's stored plan for the offer on this very instance. Anything short of a checker pass is no plan."""
        offer = conversation.get("offer")
        if not offer:
            return None
        try:
            from experiments.mvp.pipeline.lattice import lookup
        except ImportError:
            return None
        for key in (conversation.get("instance_sha256"), offer.get("instance_sha256")):
            stored = attempt(lambda: lookup(key, [offer.get("constraint")])) if key else None
            if isinstance(stored, dict) and (stored.get("checker") or {}).get("passed") is True and stored.get("computed_utc"):
                return {"label": f"Stored plan, computed on {stored['computed_utc'][:10]}, checked when computed", "lifts": stored.get("lifts"), "checker": {"passed": True}}
        return None

    def scores():
        rows = _json(COPILOT / "revalidated-scenarios.json")["results"]
        count = lambda key: sum(1 for row in rows if row["scores"].get(key))
        return {"total": len(rows), "no_unchecked": count("no_unchecked_plan"), "sequence": count("right_tool_sequence"),
                "impossible": sum(1 for row in rows if row["kind"].startswith("impossible")), "scope": "development control-flow scenarios, not sealed"}

    real = attempt(lambda: view(replay.replay_real_conversation("negotiation"), True, "real-negotiation"))
    doubles = None if real and real["offer"] else attempt(lambda: view(replay.replay_conversation("negotiation"), False, "negotiation"))
    main = doubles or real
    if main is None:
        raise FileNotFoundError("no copilot negotiation recording")
    second = real if doubles and real else None
    if main is doubles:  # its counter-offer was an unchecked stand-in on test tools, retired: the "No" stays, the delivered take negotiates
        main = {**main, "offer": None, "sentences": [row for row in main["sentences"] if row["place"] != "offer"]}
    delivered = attempt(copilot_delivered)
    attack = attempt(lambda: view(replay.replay_real_conversation("attack"), True, "real-attack")) or attempt(lambda: view(replay.replay_conversation("attack"), False, "attack"))
    if second:
        second["no_offer_note"] = "No same-family alternative in the declared search survives the screen. No replacement instruction was invented."
    return {"mode": "recorded", "label": main["label"], "real_tools": main["real_tools"], "who": "Muse Spark · Meta Model API · cloud",
            "main": main, "attack": attack, "second": second, "offer_plan": offer_plan(main), "delivered": delivered, "scores": attempt(scores),
            "proved": attempt(impossible_facts)}  # the hero ship's own stored, proved refusal: shot 9's numbers


def _tool_view(result) -> dict:
    """What the page may show of one tool result: never local paths or plan references, and the proof's numbers by name."""
    result = result if isinstance(result, dict) else {}
    keep = ("status", "text", "passed", "lifts", "label", "evidence_kind", "reader_only")
    view = {key: result[key] for key in keep if key in result}
    view["alternatives"] = [{k: a.get(k) for k in ("id", "sentence", "label", "checked", "relaxation", "certificate", "requires_user_choice", "applied", "constraint")}
                            for a in dicts(result.get("alternatives"))]
    proof = dig(result, "proof", "proof", default={})
    proof = proof if isinstance(proof, dict) else {}
    view["arithmetic"] = None
    if "demand_teu" in proof and "open_cells" in proof:
        view["arithmetic"] = {"needed": proof["demand_teu"], "left": proof["open_cells"], "physical": proof.get("physical_cells"),
                              "boxes": proof.get("load_box_count"), "no_fit": len(proof.get("nofit_ids") or []),
                              "proven_impossible": proof.get("proven_impossible"), "scope": proof.get("scope")}
    return view


def package() -> dict:
    from app.v6_package import view
    return view()


# ---------------------------------------------------------------- launch
def register_hero(inputs, comparison_inputs: list) -> list[str]:
    """``--v6``: label the active plan and add the plan under the rule and the break-it source, each with the same identity gates."""
    from app.inputs import load_inputs
    config, notes = hero(), []
    inputs.metadata["label"] = "v6:before"
    if config.get("after_plan"):
        other = load_inputs(instance_path=config["instance"], plan_path=config["after_plan"], selected_instance=inputs.instance)
        other.metadata["label"] = "v6:after"
        comparison_inputs.append(other)
    else:
        notes.append(f"no stored plan under a rule for hero slot {config.get('slot')}: the replan chapter holds the plan before the rule")
    from app.v6_sandbox import register
    notes.extend(register(config, comparison_inputs))
    return notes


def handle_post(handler) -> None:
    """Live, offline paths only: confirm a reading, try a move, verify a package. Same-origin, JSON and size gates as the brief flow."""
    from urllib.parse import urlsplit
    from app.brief import _fields, _same_origin
    route = urlsplit(handler.path).path.removeprefix("/api/v6/")
    if not _same_origin(handler):
        handler.json_response(403, {"error": "Cross-origin requests are not accepted"})
        return
    try:
        if route == "replan":
            handler.json_response(202, LIVE.start(handler, _fields(handler, {"token"}, set()).get("token")))
        elif route == "confirm":
            keys = {key for key, _ in _BINDING}
            handler.json_response(200, CONFIRMATIONS.confirm(_fields(handler, keys | {"reading"}, keys)))
        elif route == "read":
            handler.json_response(200, read_live(handler, _fields(handler, {"sentence"}, {"sentence"})["sentence"]))
        elif route == "try":
            from app.v6_sandbox import start_try
            _require_sandbox(handler)
            handler.json_response(202, start_try(handler, _fields(handler, {"box", "slot", "case"}, {"box", "slot"})))
        elif route == "verify":
            from app.v6_package import start_verify
            handler.json_response(202, start_verify(_fields(handler, {"tamper"}, set())))
        else:
            handler.json_response(404, {"error": "not found"})
    except (PermissionError, Withheld) as error:
        handler.json_response(403, {"error": str(error)})
    except ConflictError as error:
        handler.json_response(409, {"error": str(error)})
    except (ValueError, KeyError, TypeError) as error:
        handler.json_response(400, {"error": str(error)})


# ---------------------------------------------------------------- routes
_CHAPTERS = {"intake": intake, "voice": voice, "reading": reading, "xray": xray, "sandbox": sandbox, "copilot": copilot, "package": package}


def _replan_block(handler, query: dict | None = None) -> dict:
    hero()  # no ship to replan is this chapter's gap, with the reason
    query = query or {}
    first = lambda name: (query.get(name) or [None])[0]
    return replan(handler.demo_service, handler.stage_service(), getattr(handler, "brief_options", None), first("token"), first("reading"), first("recorded") == "1")


def _chapter(handler, name: str) -> dict:
    if name == "sandbox":
        _require_sandbox(handler)
    block = _CHAPTERS[name]()
    if name == "reading":  # the button to read it live appears only with a live reader attached
        block["live_reader"] = live_reader_on(getattr(handler, "brief_options", None))
    return block


def _explain(handler, query: dict) -> dict:
    from app.v6_sandbox import explain
    _require_sandbox(handler)
    return explain(handler, query.get("box", [""])[0], query.get("lang", ["en"])[0], query.get("slot", [None])[0])


def state(handler) -> dict:
    """Always an answer: shared hero data that can't be read costs the chapters that need it, never the boot."""
    blocks = {name: feature(lambda name=name: _chapter(handler, name)) for name in _CHAPTERS}
    blocks["replan"] = feature(lambda: _replan_block(handler))
    config = optional(hero, {})
    config = config if isinstance(config, dict) else {}
    return {"version": "v6", "synthetic": SYNTHETIC, "build": v6_build.current(), "stand_ins": optional(stand_ins, {}), "ship": config.get("ship"),
            "rule_counts": optional(rule_counts), "features": {name: {"available": block.get("available"), "reason": block.get("reason")} for name, block in blocks.items()},
            "modes": {"reading": (getattr(handler, "brief_options", None) or {}).get("reader", "recorded"),
                      "live_replan": bool((getattr(handler, "brief_options", None) or {}).get("live_replan"))}}


def _radio_asset() -> Path | None:
    """The clip the voice chapter plays: the hero wording's take while that wording is the chapter, else the separate clip.
    None when the wording plays without its take (D309): never the other clip in its place."""
    pick = optional(hero_wording)
    if pick and pick["radio"] and optional(lambda: _voice_worded(pick, _settings())):
        return optional(radio_wav)
    return VOICE / "audio" / f"{_settings()['clip']['id']}-radio.wav"


def _send_asset(handler, name: str) -> None:
    if name == "radio.wav":
        path, mime = _radio_asset(), "audio/wav"
    elif name in ASSETS:
        path, mime = ASSETS[name]
    else:
        handler.json_response(404, {"error": "not found"})
        return
    try:
        if path is None:
            raise FileNotFoundError(name)
        body = path.read_bytes()
    except FileNotFoundError:  # a file this copy doesn't ship (the stock-TTS clip): a clean 404, never a 500
        handler.json_response(404, {"error": AUDIO_ABSENT if name == "radio.wav" else "not included in this copy"})
        return
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    # The bay plan is a self-contained page with its own styles; it's framed by the stage page only.
    handler.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'")
    handler.end_headers()
    handler.wfile.write(body)


def handle_get(handler, route: str, query: dict) -> None:
    name = route.removeprefix("/api/v6/")
    if name == "state":
        handler.json_response(200, state(handler))
    elif name == "figures":
        handler.json_response(200, feature(figures))
    elif name == "replan":
        handler.json_response(200, feature(lambda: _replan_block(handler, query)))
    elif name.startswith("replan/"):
        handler.json_response(200, LIVE.view(name.removeprefix("replan/")))
    elif name in _CHAPTERS:
        handler.json_response(200, feature(lambda: _chapter(handler, name)))
    elif name.startswith("asset/"):
        _send_asset(handler, name.removeprefix("asset/"))
    elif name.startswith("try/"):
        from app.v6_sandbox import job as try_job
        _require_sandbox(handler)
        handler.json_response(200, try_job(name.removeprefix("try/")))
    elif name == "explain":
        handler.json_response(200, feature(lambda: _explain(handler, query)))
    elif name.startswith("verify/"):
        from app.v6_package import job as verify_job
        handler.json_response(200, verify_job(name.removeprefix("verify/")))
    else:
        handler.json_response(404, {"error": "not found"})


# ---------------------------------------------------------------- launch warm-up
def warm(handler_class) -> str:
    """--v6: everything the first page asks for, before it asks. Returns the startup line; a gap is named, never fatal."""
    began, demo, stage = monotonic(), handler_class.demo_service, handler_class.stage_service()
    ids = plan_ids(demo)
    gate = HERO.wait(demo)
    notes = [f"gate {gate.get('status')}"] + list(gate.get("why") or [])
    for name in ("before", "after"):
        if not ids.get(name):
            continue
        reason = _withheld(demo, ids[name])
        if reason:
            notes.append(f"{name} plan withheld")
            continue
        ready = stage.physics_status(ids[name], start=False).get("status") == "ready"
        stage.bundle(ids[name])
        notes.append(f"{name} plan physics {'ready' if ready else 'MISSING: run precompute_timeline.py'}")
    if ids.get("sandbox") and not _withheld(demo, ids["sandbox"]):
        stage.bundle(ids["sandbox"])
    from app.v6_package import ensure_verified
    notes.append(optional(ensure_verified, "package gate unavailable"))
    if live_reader_on(getattr(handler_class, "brief_options", None)):
        notes.append("live reader context ready" if optional(hero_reader_context) else "live reader context MISSING")
    return f"StowLM v6 warm in {monotonic() - began:.0f} s: " + ", ".join(notes)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--replan-worker":
        replan_worker(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--regate-worker":
        regate_worker(sys.argv[2])
    elif sys.argv[1:] == ["--repin"]:
        print(repin())
    else:
        raise SystemExit("usage: python -m app.v6 --replan-worker OUT | --regate-worker OUT | --repin")
