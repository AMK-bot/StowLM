"""Replaceable sentence parser -> strict constraint compiler -> shared frontier.

No expected label is supplied to the parser or planner. A future Glimmer reader
implements the same parse(sentence, context) method returning a validated answer.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
from unittest.mock import patch

from llm.instructions.schema import Refusal
from stowage.conventions import cells_of_slot
from stowage.moves import apply_plan

from experiments.mvp.reader.schema import OnePortPerHatch, answer_envelope, messages, validate_answer
from experiments.mvp.reader.occupancy import apply_reader_constraint

HERE = Path(__file__).resolve().parent
PURPOSE = 'reader-d199-binding-parser'


class ParserOutputError(ValueError):
    """Malformed model output, with safe provenance and no guessed constraint.

    Do not persist ``str`` of a schema exception: its diagnostics can repeat
    arbitrary output. The original model text is available only as evidence.
    """
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('Reader returned malformed or context-invalid output')


class ParserUnavailableError(RuntimeError):
    """The requested reader has no attached inference implementation."""


def _validated_output(text, context, evidence):
    try:
        answer = validate_answer(text, context)
    except (ValueError, TypeError):
        raise ParserOutputError(evidence) from None
    return answer, evidence


class GlimmerParser:
    """Inject the fine-tuned reader without loading a model at import time.

    ``generate(sentence, context)`` must return the model's untouched JSON text.
    The adapter's SHA256 is required when attaching a callback, so a validated
    constraint can always be traced to the exact adapter. A bare instance is an
    explicit unavailable stub; it never falls back to Spark or a rules parser.
    """
    def __init__(self, generate=None, *, adapter_sha256=None, model='muse-glimmer-30b'):
        if generate is not None and not callable(generate):
            raise TypeError('Glimmer generate must be callable')
        if adapter_sha256 is not None and (not isinstance(adapter_sha256, str)
                or re.fullmatch(r'[0-9a-fA-F]{64}', adapter_sha256) is None):
            raise ValueError('adapter_sha256 must be a SHA256 hexadecimal digest')
        if generate is not None and adapter_sha256 is None:
            raise ValueError('A Glimmer inference callback requires adapter_sha256')
        if not isinstance(model, str) or not model.strip():
            raise ValueError('Glimmer model must be a nonempty identifier')
        self.generate = generate
        self.adapter_sha256 = adapter_sha256.lower() if adapter_sha256 else None
        self.model = model

    def parse(self, sentence: str, context: dict):
        if self.generate is None:
            raise ParserUnavailableError('Glimmer reader adapter is not attached')
        text = self.generate(sentence, context)
        evidence = {'parser': 'GlimmerParser', 'model': self.model,
                    'adapter_sha256': self.adapter_sha256, 'raw': text}
        return _validated_output(text, context, evidence)


class SparkParser:
    """Sequential paid parser with a restart-safe, purpose-scoped allocation."""
    def __init__(self, *, purpose=PURPOSE, allocation_usd=Decimal('1')):
        from experiments.mvp.reader.baselines import load_credentials
        from llm.spark.client import SparkClient
        if not isinstance(purpose, str) or not purpose.strip() or len(purpose) > 200:
            raise ValueError('purpose must be a nonempty tag of at most 200 characters')
        allocation = Decimal(str(allocation_usd))
        if not allocation.is_finite() or allocation < 0:
            raise ValueError('allocation_usd must be finite and nonnegative')
        self.purpose, self.allocation_usd = purpose, allocation
        load_credentials()
        self.client = SparkClient(max_retries=0, timeout=60)
        # Seed immediately before the first call, under the same lock as its
        # reservation. Calls between construction and use must not add headroom.
        # Keep this seed fixed afterward: allocation_budget tracks this ledger
        # instance's subsequent spend, so adding it again would double-count it.
        self.prior = None

    def parse(self, sentence: str, context: dict):
        import llm.spark.client as client_module
        from llm.spark.planner_budget import allocation_budget
        prompt = '\n\n'.join(m['content'] for m in messages(sentence, context))
        # Bound input by UTF-8 bytes, leaving >8k tokens for protocol framing.
        # A byte-level tokenizer cannot require more ordinary tokens than bytes.
        # This process-local request bound avoids the stock million-token reserve
        # (>$1) for a ~700-token prompt. API endpoint/model/cap/ledger are unchanged.
        if len(prompt.encode('utf-8')) > 24576:
            raise ValueError('reader prompt exceeds bounded 32k-token request budget')
        with self.client.ledger.locked():
            if self.prior is None:
                records = [row for row in self.client.ledger._records()
                           if row.get('purpose') == self.purpose]
                actual, held = self.client.ledger._totals(records)
                self.prior = actual + held
            with patch.object(client_module, 'CONTEXT_TOKENS', 32768):
                with allocation_budget(self.client.ledger, allocation_usd=self.allocation_usd,
                                       prior_job_usd=self.prior):
                    response = self.client.complete(prompt, purpose=self.purpose, max_output_tokens=1024,
                                                    reasoning_effort='minimal')
        evidence = {'raw': response.text, 'request_id': response.request_id,
                    'cost_usd': str(response.cost_usd), 'usage': vars(response.usage),
                    'parser': 'SparkParser', 'model': client_module.MODEL,
                    'adapter_sha256': None, 'purpose': self.purpose,
                    'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                    'reservation_context_bound_tokens': 32768}
        return _validated_output(response.text, context, evidence)


def compile_constraint(instance, answer, *, workers=1, seed=0):
    if isinstance(answer, Refusal):
        raise ValueError('refused sentence has no executable constraint')
    if isinstance(answer, OnePortPerHatch):
        from experiments.mvp.reader.allocation import derive_hatch_allocation
        return derive_hatch_allocation(instance, workers=workers, seed=seed)
    return apply_reader_constraint(instance, answer, reject_retained=True), {
        'method': 'production tighten-only apply plus final occupancy audit',
        'constraint': answer_envelope(answer)}


def intrinsic_violations(instance, answer, plan):
    """Instruction semantics independent of a software-chosen hatch allocation.

An otherwise single-port cover with the opposite software mapping does not
violate the sentence and MUST NOT be called a binding replan.
"""
    if not isinstance(answer, OnePortPerHatch):
        return apply_reader_constraint(instance, answer).violations(plan)
    replay = apply_plan(instance, plan)
    if not replay.legal:
        return ['plan failed physical move replay']
    stacks = {s.id: s for s in instance.vessel.stacks}
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    listed = {sid: h.id for h in instance.vessel.hatch_covers for sid in h.hold_stack_ids + h.deck_stack_ids}
    ports = defaultdict(set)
    for placement in replay.final_state.ship.placements:
        for cell in cells_of_slot(placement.slot):
            stack = stacks[cells[cell].stack_id]
            cover = stack.hatch_cover_id or listed.get(stack.id)
            if cover is not None:
                ports[cover].add(str(boxes[placement.container_id].pod))
    return [f'{cover}: mixed discharge ports {sorted(pods)}' for cover, pods in sorted(ports.items()) if len(pods) > 1]


def plan_signature(plan):
    # Ballast metadata alone cannot demonstrate a changed container plan.
    return hashlib.sha256(json.dumps([m.model_dump(mode='json') for m in plan.moves],
                                      sort_keys=True, separators=(',', ':')).encode()).hexdigest()
