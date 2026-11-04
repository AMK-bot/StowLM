"""Release-safe brief API. Frozen ``brief_planner`` is benchmark-only history.

A read receipt is immutable and authenticated. Constraints are reconstructed
from its exact reading, never supplied separately by callers. Persisted replay
requires an application-owned signing key, independent of provider credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
from time import perf_counter

from llm.instructions.apply import NotApplicable
from llm.instructions.schema import Refusal
from experiments.mvp.reader.constraints import InfeasibleInstruction
from experiments.mvp.reader.schema import OnePortPerHatch, answer_envelope, context_from_instance, validate_answer
from experiments.mvp.reader.pipeline import ParserOutputError, compile_constraint
from experiments.mvp.reader.frontier_driver import plan_and_check

_PROCESS_KEY = secrets.token_bytes(32)
_RECEIPT_SCHEMA = 'stowlm.brief-receipt/2'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _binding(instance):
    raw = instance if isinstance(instance, dict) else instance.model_dump(mode='json')
    return dict(input_sha256=_digest(raw), context_sha256=_digest(context_from_instance(instance)))


def _mac(payload, key):
    return hmac.new(key, (_RECEIPT_SCHEMA + '\n' + payload).encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class VoyageBrief:
    standing: str = 'One port per hatch.'
    voyage: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.standing, str) or not isinstance(self.voyage, tuple) or not all(isinstance(s, str) for s in self.voyage):
            raise TypeError('Use standing text and a tuple of voyage sentences')


@dataclass(frozen=True)
class ReadBrief:
    """Opaque receipt; constructing or editing one does not authorize execution."""
    payload: str
    authentication: str

    def evidence(self):
        return _open(self)


def _open(receipt):
    if not isinstance(receipt, ReadBrief) or not isinstance(receipt.payload, str) or not isinstance(receipt.authentication, str) or not hmac.compare_digest(_mac(receipt.payload, _PROCESS_KEY), receipt.authentication):
        raise ValueError('An authenticated reader-produced brief is required before planning')
    return json.loads(receipt.payload)


def _seal(evidence):
    payload = _json(evidence)
    return ReadBrief(payload, _mac(payload, _PROCESS_KEY))


def _verify(instance, receipt):
    evidence = _open(receipt)
    if evidence.get('schema_version') != _RECEIPT_SCHEMA or evidence.get('binding') != _binding(instance):
        raise ValueError('Reader receipt does not match this input/context')
    if not evidence.get('sentences'):
        raise ValueError('A reader-produced sentence is required')
    return evidence


def _checkpoint_key(key):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('Replay requires a dedicated application signing key of at least 32 bytes')
    return key


def export_checkpoint(receipt, *, key):
    """Authenticate an exact reading for another process. Never use an API key."""
    _open(receipt)
    return dict(payload=receipt.payload, authentication=_mac(receipt.payload, _checkpoint_key(key)))


def load_checkpoint(instance, checkpoint, *, key):
    """Verify authenticated input, sentences, readings and provenance before replay."""
    key = _checkpoint_key(key)
    if not isinstance(checkpoint, dict) or set(checkpoint) != {'payload', 'authentication'}:
        raise ValueError('Invalid reader checkpoint')
    payload, authentication = checkpoint['payload'], checkpoint['authentication']
    if not isinstance(payload, str) or not isinstance(authentication, str) or not hmac.compare_digest(_mac(payload, key), authentication):
        raise ValueError('Invalid reader checkpoint authentication')
    receipt = ReadBrief(payload, _mac(payload, _PROCESS_KEY))
    _verify(instance, receipt)
    return receipt


def _read_sentence(parser, sentence, context):
    # Provider/accounting failures are request errors, even for optional standing
    # text. Only malformed outputs may be represented as a rejected reading.
    answer, provider = parser.parse(sentence, context)
    try:
        if not isinstance(provider, dict) or not isinstance(provider.get('parser'), str) or not provider['parser']:
            raise ValueError('Parser provenance is required')
        if 'adapter_sha256' not in provider or (provider['adapter_sha256'] is not None and
                (not isinstance(provider['adapter_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', provider['adapter_sha256']))):
            raise ValueError('Explicit adapter provenance is required')
        answer = validate_answer(_json(answer_envelope(answer)), context)
    except (ValueError, TypeError):
        raise ParserOutputError(provider if isinstance(provider, dict) else {}) from None
    return answer, provider


def read_brief(instance, brief, *, parser):
    """Read every sentence. Plain strings are rejected to keep roles explicit."""
    if not isinstance(brief, VoyageBrief):
        raise TypeError('Use VoyageBrief(standing=..., voyage=(sentence,)); plain strings have no implicit role')
    binding = _binding(instance)
    context = context_from_instance(instance)
    records, blocked = [], False
    for role, sentences in (('standing', brief.standing.splitlines() or ['']), ('voyage', brief.voyage)):
        for sentence in sentences:
            start = perf_counter()
            row = dict(role=role, sentence=sentence, binds=False)
            try:
                answer, provider = _read_sentence(parser, sentence, context)
                row['parser'] = provider
                row['parsed'] = answer_envelope(answer)
                if isinstance(answer, Refusal):
                    row['status'] = 'refused'
                elif role == 'standing':
                    row['status'] = 'preference_enabled' if isinstance(answer, OnePortPerHatch) else 'unsupported_preference'
                    blocked |= isinstance(answer, OnePortPerHatch)
                else:
                    row.update(status='accepted', binds=True)
            except ParserOutputError as error:
                row.update(status='malformed', parsed=None, error_type=type(error).__name__)
                row['parser'] = getattr(error, 'evidence', row.get('parser', {}))
            row['wall_s'] = perf_counter() - start
            records.append(row)
    # A parser must not mutate the instance while producing a reading.
    if binding != _binding(instance):
        raise ValueError('Input/context changed during reading')
    return _seal(dict(schema_version=_RECEIPT_SCHEMA, binding=binding, sentences=records,
                      port_blocked_start=blocked, reader_required=True, standing_is_preference=True))


@dataclass
class Intersection:
    instance: object
    parts: list

    def forbids(self, box, slot):
        return next((reason for part in self.parts if (reason := part.forbids(box, slot))), None)

    def violations(self, plan):
        return [reason for part in self.parts for reason in part.violations(plan)]


def execute_brief(instance, receipt, *, search_s=60., finalize_s=300., seed=0,
                  workers=1, constructor_s=120.):
    """Execute only a validated receipt; there is no external hard-constraint list."""
    parsed = _verify(instance, receipt)
    started = perf_counter()
    record = dict(parsed, receipt_sha256=hashlib.sha256(receipt.payload.encode()).hexdigest(),
                  repaired=False, passed=False, plan_released=False, claim_scope='parsed_constraint_execution',
                  sentence_compliance_verified=False, compilation=[])
    unresolved = [i for i, row in enumerate(parsed['sentences']) if row['role'] == 'voyage' and row['status'] != 'accepted']
    if unresolved:
        record.update(status='needs_confirmation', unresolved_sentence_indices=unresolved,
                      question='Correct or explicitly remove each unresolved voyage sentence, then submit a new brief.',
                      existing_plan_satisfies_request=False, requires_reading_resolution=True,
                      release_condition='explicit_sentence_correction_or_removal', wall_s=perf_counter()-started)
        return None, record
    # Reconstruct from authenticated exact readings, not a caller-owned list.
    context = context_from_instance(instance)
    hard = [(row, validate_answer(_json(row['parsed']), context)) for row in parsed['sentences']
            if row['role'] == 'voyage' and row['status'] == 'accepted']
    parts = []
    try:
        for row, answer in hard:
            compiled, evidence = compile_constraint(instance, answer, workers=workers, seed=seed)
            record['compilation'].append(dict(evidence, source_sentence=row['sentence'],
                                              parser=row['parser'], binding=parsed['binding']))
            from experiments.mvp.reader.select_binding import per_box_slot_screen
            if hasattr(compiled, 'capacity_shortfall'):
                shortfall = compiled.capacity_shortfall()
                if shortfall:
                    raise InfeasibleInstruction(shortfall)
            no_slots = per_box_slot_screen(instance, compiled)
            if no_slots:
                raise InfeasibleInstruction('no instruction-allowed physical footprint: ' + ', '.join(no_slots))
            parts.append(compiled)
    except (InfeasibleInstruction, NotApplicable) as error:
        record.update(status='constraint_unavailable', error=str(error), wall_s=perf_counter()-started)
        return None, record
    result, details = plan_and_check(instance, Intersection(instance, parts) if parts else None,
                                     search_s=search_s, finalize_s=finalize_s, seed=seed, workers=workers,
                                     constructor_s=constructor_s, enable_port_blocked=parsed['port_blocked_start'])
    record.update(planner=details, passed=result is not None, plan_released=result is not None,
                  status='checked_plan' if result is not None else 'no_checked_plan', wall_s=perf_counter()-started)
    return result, record


def plan_brief(instance, brief, *, parser, **kwargs):
    """English -> reader -> release gate -> constraints -> Frontier -> full checker."""
    started = perf_counter()
    receipt = read_brief(instance, brief, parser=parser)
    result, evidence = execute_brief(instance, receipt, **kwargs)
    evidence['wall_s'] = perf_counter()-started
    return result, evidence
