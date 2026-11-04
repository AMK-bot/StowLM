"""Shared pipeline shapes (docs/pipeline.md). Tier 1 features import these; they never fork them.

Everything here is JSON-able, so a stage's input and output hash the same way in every process. ``Mode`` is the
stored/recorded/live label, set by the code that produced a result, never by a caller.
"""
from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PACKET_SCHEMA = 'stowlm.voyage-packet/1'
EVENT_SCHEMA = 'stowlm.events/1'
PACKAGE_SCHEMA = 'stowlm.plan-package/1'
RECEIPT_SCHEMA = 'stowlm.pipeline-receipt/1'
GENESIS = '0' * 64


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path) -> str:
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Mode(str, Enum):
    live = 'live'
    stored = 'stored'
    recorded = 'recorded'
    generated = 'generated'


class Status(str, Enum):
    checked_plan = 'checked_plan'
    relaxed_plan = 'relaxed_plan'
    out_of_envelope = 'out_of_envelope'
    needs_answers = 'needs_answers'
    needs_confirmation = 'needs_confirmation'
    constraint_unavailable = 'constraint_unavailable'
    no_checked_plan = 'no_checked_plan'
    error = 'error'


class FileRef(Strict):
    path: str
    sha256: str


class Sentence(Strict):
    text: str
    origin: Literal['typed', 'voice', 'planner_edit']


class LoadList(Strict):
    origin: Literal['intake', 'intake_private', 'generator']
    rows: list[dict]  # stowage.model.cargo.Container dumps
    intake_receipt_sha256: str | None = None


class YardInput(Strict):
    origin: Literal['generator', 'vision']
    state: dict  # stowage.model.yard.YardState dump
    vision_receipt_sha256: str | None = None


class VoyagePacket(Strict):
    schema_version: Literal['stowlm.voyage-packet/1'] = PACKET_SCHEMA
    packet_id: str
    created_utc: str
    synthetic: Literal[True] = True
    base_instance: FileRef
    load_list: LoadList | None = None
    yard_state: YardInput | None = None
    standing: str = 'One port per hatch.'
    voyage_sentences: list[Sentence] = Field(default_factory=list)
    profile: Literal['live', 'standard'] = 'live'


class StageRecord(Strict):
    stage: str
    mode: Mode
    input_sha256: str
    output_sha256: str
    model: str | None = None
    adapter_sha256: str | None = None
    prompt_sha256: str | None = None
    source_sha256: str | None = None
    wall_s: float
    cost_usd: float = 0.
    notes: str = ''
    prev_sha256: str = GENESIS


class Event(Strict):
    schema_version: Literal['stowlm.events/1'] = EVENT_SCHEMA
    seq: int
    t_s: float
    stage: str
    kind: Literal['stage_start', 'stage_end', 'incumbent', 'question', 'refusal', 'relaxed', 'error', 'done']
    data: dict = Field(default_factory=dict)


class Question(Strict):
    """Asked instead of guessing. ``row`` is a load-list index, or None for a document-level question."""
    id: str
    row: int | None = None
    field: str | None = None
    prompt: str
    candidates: list[str] = Field(default_factory=list)
    reason: str


class IntakeResult(Strict):
    status: Literal['complete', 'needs_answers', 'rejected']
    rows: list[dict]
    questions: list[Question] = Field(default_factory=list)
    field_sources: list[dict] = Field(default_factory=list)  # row, field, document, quote or cell
    records: list[StageRecord] = Field(default_factory=list)


class YardRead(Strict):
    status: Literal['complete', 'needs_answers', 'rejected']
    state: dict | None = None
    questions: list[Question] = Field(default_factory=list)
    records: list[StageRecord] = Field(default_factory=list)


class MoveVerdict(Strict):
    legal: bool
    screen: Literal['fast', 'full']  # a fast screen is labelled as one; only 'full' is a checker verdict
    broken: list[dict] = Field(default_factory=list)  # rule_id, message, measured, limit, boxes
    gauges: dict = Field(default_factory=dict)
    extra_lifts: int | None = None
    wall_s: float


class Explanation(Strict):
    language: Literal['en', 'th']
    text: str
    statements: list[dict]  # id, text, evidence: checker lines the statement rests on
    mode: Mode


class CopilotTurn(Strict):
    say: str
    tool_calls: list[dict] = Field(default_factory=list)  # name, arguments, result_sha256, wall_s
    proposed_reading: dict | None = None  # awaits the planner's confirmation
    plan_sha256: str | None = None  # set only for a plan that passed the checker
    mode: Mode


class Receipt(Strict):
    schema_version: Literal['stowlm.pipeline-receipt/1'] = RECEIPT_SCHEMA
    pipeline_sha256: str | None = None  # the 2.1 manifest, once frozen
    engine_version: str
    engine_manifest_sha256: str
    packet_sha256: str
    instance_sha256: str
    plan_sha256: str | None = None
    steps: list[StageRecord]
    rules: list[dict]
    checker: dict
    lifts: float | None = None
    label: Mode
    synthetic: Literal[True] = True
    signature: str | None = None
    public_key: str | None = None


def chain(records: list[StageRecord]) -> list[StageRecord]:
    """Link rows in order, so editing any step breaks every later hash."""
    out, previous = [], GENESIS
    for record in records:
        row = record.model_copy(update={'prev_sha256': previous})
        previous = sha256_json(row.model_dump(mode='json'))
        out.append(row)
    return out


def chain_head(records: list[StageRecord]) -> str:
    """Digest of the last row. Sign or retain it: the links alone can't see an edited or dropped tail."""
    return sha256_json(records[-1].model_dump(mode='json')) if records else GENESIS


def chain_intact(records: list[StageRecord], *, head: str | None = None) -> bool:
    """Check the links. Pass the signed or retained ``head`` to also catch a changed last row or a truncation."""
    previous = GENESIS
    for record in records:
        if record.prev_sha256 != previous:
            return False
        previous = sha256_json(record.model_dump(mode='json'))
    return head is None or previous == head
