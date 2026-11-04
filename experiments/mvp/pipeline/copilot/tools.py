"""Software-owned tools. Only authenticated, planner-confirmed readings bind.

Injected services are application dependencies, never model-supplied callables.
Landed services are loaded lazily; explicit unavailable adapters remain injectable.
"""
from __future__ import annotations
IMPORT_SOURCE_SHA256 = __import__('hashlib').sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()

from copy import deepcopy
import json
import os
from uuid import uuid4
from typing import Any

from experiments.mvp.pipeline.contract.schema import canonical_json, sha256_json
from experiments.mvp.reader import public_brief
from experiments.mvp.reader.refusal_evidence import build_refusal_evidence
from experiments.mvp.reader.schema import context_from_instance, validate_answer

REFUSAL_TEMPLATES = {
    'loosening': 'The reader refused this request as a relaxation. No constraint was applied.',
    'ambiguous': 'The reader marked the request ambiguous. No constraint was guessed or applied.',
    'unsupported': 'The reader cannot express this request with the supported instruction types. No constraint was applied.',
    'not_an_instruction': 'The reader did not identify an instruction. No constraint was applied.',
    'malformed': 'The reader returned an invalid reading. Restate the instruction.',
}


def _safe_reader_output(value):
    if isinstance(value, dict):
        reader_provider = isinstance(value.get('parser'), str) and 'adapter_sha256' in value
        out = {key: _safe_reader_output(item) for key, item in value.items()
               if not (reader_provider and key == 'raw')}
        if out.get('refused') is True and out.get('code') in REFUSAL_TEMPLATES:
            out['reason'] = REFUSAL_TEMPLATES[out['code']]
        return out
    if isinstance(value, list):
        return [_safe_reader_output(item) for item in value]
    return value


def _json(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    return deepcopy(value)


def _without_hashes(value):
    if isinstance(value, dict):
        return {k: _without_hashes(v) for k, v in value.items() if k != 'plan_sha256'}
    if isinstance(value, list):
        return [_without_hashes(v) for v in value]
    return value


def _report_summary(report):
    """Model-facing verdict, never an input to the native acceptance gate."""
    raw = _json(report)
    summary = {key: _without_hashes(raw[key]) for key in
               ('passed', 'count', 'violations', 'shut_out') if key in raw}
    summary['report_sha256'] = sha256_json(raw)
    summary['count_units'] = {
        'loads': 'current-port lifts', 'discharges': 'current-port lifts',
        'relocations': 'current-port lifts', 'restow_lifts': 'current-port lifts',
        'hatch_cover_lifts': 'current-port lifts', 'gate_moves': 'moves',
        'future.restowed_boxes': 'projected later-port boxes',
        'future.restow_lifts': 'projected later-port lifts',
        'future.hatch_cover_lifts': 'projected later-port lifts',
        'weighted_nonproductive': 'weighted lifts; includes projected later-port lifts',
    }
    summary['parts'] = {}
    for name, part in (raw.get('parts') or {}).items():
        violations = part.get('violations', [])
        item = dict(ran=part.get('ran'), error=part.get('error'),
                    violations=_without_hashes(violations))
        item['passed'] = (part['passed'] if isinstance(part.get('passed'), bool) else
                          part.get('ran') is True and not part.get('error') and
                          not any(v.get('severity', 'hard') == 'hard' for v in violations))
        for group in ('metrics', 'margins'):
            # Keep departure scalar gauges. Per-condition/per-move tables and
            # notes remain in the private native report for the explainer.
            values = part.get(group) or {}
            keys = sorted(key for key, value in values.items()
                          if key.isidentifier() and len(key) <= 64
                          and not key.startswith(('intermediate_', 'condition_', 'note_'))
                          and type(value) in (int, float))[:32]
            item[group] = {key: values[key] for key in keys}
        summary['parts'][name] = item
    provenance = raw.get('provenance')
    if isinstance(provenance, dict):
        summary['provenance'] = {key: deepcopy(provenance[key]) for key in
                                 ('versions', 'stubs', 'created_by', 'git_commit') if key in provenance}
    return summary


def _explanation_summary(explanation):
    """Keep statements and scalar references; link aggregate report projections.

    A projected aggregate is never substituted for an exact JSON-pointer value.
    Its original identity/path and content hash remain available, while the
    complete source object stays in ToolSet._explanation for host inspection.
    """
    def compact(value):
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        if value.get('source') == 'report' and value.get('path') in ('/parts', '', '/') and 'value' in value:
            out = {key: compact(item) for key, item in value.items() if key != 'value'}
            out.update(value_omitted=True, value_sha256=sha256_json(value['value']),
                       value_ref='/checker/report/parts' if value['path'] == '/parts' else '/checker/report',
                       value_label='Exact source retained privately; value_ref is a compact projection.')
            return out
        return {key: compact(item) for key, item in value.items()}
    return _without_hashes(compact(_json(explanation)))


def guarded_envelope(instance):
    """Fail closed local gate until runner.envelope is available."""
    previous_vessel_cache = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
    try:
        try:
            from experiments.mvp.pipeline.runner.envelope import check
        except ImportError:
            check = None
    finally:
        # Importing the pure envelope also imports runner.core, whose CLI
        # setup changes this environment key. Preserve the caller's numerical
        # identity before any checker epoch or receipt is inspected.
        if previous_vessel_cache is None:
            os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
        else:
            os.environ['STOWAGE_VESSEL_CACHE_DIR'] = previous_vessel_cache
    if check is not None:
        if isinstance(instance, dict):
            from stowage.model import Instance
            instance = Instance.model_validate(instance)
        return check(instance)
    return _local_envelope(instance)


def _local_envelope(instance):
    raw = _json(instance)
    size = raw.get('size_class')
    vessel = raw.get('vessel', {})
    boxes = {b['id']: b for b in raw.get('containers', [])}
    load = raw.get('load_list', [])
    capacity = len(vessel.get('cells', []))
    demand = sum(1 if boxes[b]['length_ft'] == 20 else 2 for b in load)
    fill = demand / capacity if capacity else None
    reasons = []
    if len(load) > 195:
        reasons.append(f'Load is {len(load)} boxes; the limit is 195 boxes.')
    family = raw.get('family', {}).get('vessel', vessel.get('id'))
    if size not in ('tiny', 'small') and not (size == 'medium' and family in ('feeder_500', 'feeder_800')):
        reasons.append('Ship or section size is outside the supported envelope.')
    if fill is None:
        reasons.append('Physical capacity is unknown; the fill limit is 70 percent.')
    elif fill > .7:
        reasons.append(f'Load needs {demand} TEU across {capacity} physical cells; fill is {fill * 100:g} percent and the limit is 70 percent.')
    if raw.get('stage') != 'A' or raw.get('onboard', {}).get('placements') or raw.get('discharge_list') or raw.get('plan_calls'):
        reasons.append('Only an empty ship and one loading call are supported.')
    return dict(in_envelope=not reasons, size_class=size, reasons=reasons,
                numbers=dict(load_boxes=len(load), max_boxes=195, demand_teu=demand,
                             physical_cells=capacity, fill=fill, max_fill=.7),
                implementation='copilot.guarded_envelope')


def lattice_unavailable(*args, **kwargs):
    return None


def sandbox_unavailable(*args, **kwargs):
    return dict(status='unavailable', text='The move sandbox is not connected.')


def explainer_unavailable(*args, **kwargs):
    return dict(status='unavailable', text='The box explainer is not connected.')


def real_explainer(*args, **kwargs):
    from experiments.mvp.pipeline.explain import explain_box
    return explain_box(*args, **kwargs)


def real_lattice(*args, **kwargs):
    from experiments.mvp.pipeline.lattice import lookup
    return lookup(*args, **kwargs)


def real_refusal_lookup(*args, **kwargs):
    from experiments.mvp.pipeline.lattice import refusal_lookup
    return refusal_lookup(*args, **kwargs)


def real_release_gate(instance, plan, rules):
    from experiments.mvp.pipeline.lattice import gate_plan
    return gate_plan(instance, plan, rules)


def real_sandbox(*args, **kwargs):
    from experiments.mvp.pipeline.sandbox import try_move
    return try_move(*args, **kwargs)


def recorded_refusal_card(case_id, *, parser):
    """Curated historical card; no current ToolSet plan is installed or accepted.

    The existing loader verifies its own frozen example, live reader agreement
    and proof binding. It is deliberately separate from the model tool registry.
    """
    from experiments.mvp.reader.refusal_card import read_recorded_card
    return _safe_reader_output(_without_hashes(read_recorded_card(case_id, parser=parser)))


class ToolSet:
    """Trusted host boundary; ``confirm_reading`` is never a model tool.

    A new sentence supersedes any unconfirmed draft and includes every earlier
    confirmed sentence. A refused draft blocks publication until replaced by a
    newly read, confirmed brief; saying "confirm" to the model does not do it.
    """
    names = ('read_instruction', 'check_plan', 'plan_lookup', 'replan', 'wait_replan',
             'try_ladder', 'try_move', 'explain_box', 'list_limits')

    def __init__(self, instance, parser=None, plan=None, *, checker=None,
                 replanner=None, envelope=None, lattice=None, sandbox=None,
                 explainer=None, ladder=None, standing='', planning_options=None,
                 refusal_witness=None, job_manager=None, job_history_limit=16,
                 alternative_limit=4, lattice_store_dir=None, catalogue_alternatives=None,
                 refusal_lookup=None, lookup_fallback=True, release_gate=None):
        if type(job_history_limit) is not int or job_history_limit < 1:
            raise ValueError('job_history_limit must be a positive integer')
        if type(alternative_limit) is not int or alternative_limit < 1:
            raise ValueError('alternative_limit must be a positive integer')
        if isinstance(instance, dict):
            from stowage.model import Instance
            instance = Instance.model_validate(instance)
        if isinstance(plan, dict) and checker is None:
            from stowage.model import MovePlan
            plan = MovePlan.model_validate(plan)
        self.instance, self.parser, self.plan = instance, parser, plan
        self.checker, self.replanner = checker, replanner
        self.envelope = envelope or guarded_envelope
        self.lattice = lattice or real_lattice
        self.lattice_store_dir = lattice_store_dir
        self.catalogue_alternatives = deepcopy(catalogue_alternatives or [])
        self.refusal_lookup = refusal_lookup or real_refusal_lookup
        self.lookup_fallback = lookup_fallback
        self.release_gate = release_gate or real_release_gate
        self._requires_release_gate = checker is None or release_gate is not None
        self.sandbox = sandbox or real_sandbox
        self.explainer = explainer or real_explainer
        self.ladder = ladder
        if not isinstance(standing, str):
            raise TypeError('Standing instructions must be text')
        self._standing = standing
        self._standing_revision = 0
        self.planning_options = dict(planning_options or {})
        self.refusal_witness = refusal_witness
        self._alternative_limit = alternative_limit
        self._receipts = {}
        self._standing_sources = {}
        self._confirmed_standing_source = None
        self._confirmed_receipt = None
        self._confirmed = None
        self._pending = None
        self._sentences = ()
        self._report = None
        self._explanation = None
        self._negotiation = None
        self._generation = 0
        self.job_manager = job_manager
        self._active_job = None
        self._job_states = {}
        self._job_history_limit = job_history_limit
        self._planning_request_id = None
        self._planning_generation = 0
        self._acceptance_blocked = False

    @property
    def standing(self):
        return self._standing

    @standing.setter
    def standing(self, value):
        if not isinstance(value, str):
            raise TypeError('Standing instructions must be text')
        if value != self._standing:
            self._standing = value
            self._standing_revision += 1
            self._generation += 1
            self._negotiation = None
            self._confirmed_receipt = None

    def _standing_source(self):
        return dict(text=self.standing, revision=self._standing_revision)

    def _standing_rows_valid(self, reading):
        rows = [row for row in reading['sentences'] if row['role'] == 'standing']
        return ([row['sentence'] for row in rows] == (self.standing.splitlines() or [''])
                and all(not row['sentence'].strip() or row['status'] == 'preference_enabled'
                        for row in rows))

    def _standing_ready(self):
        if self._confirmed is None:
            return not self.standing.strip() and self._standing_revision == 0
        if self._confirmed_standing_source != self._standing_source():
            return False
        if not self.standing.strip():
            return True
        reading = public_brief._verify(self.instance, self._receipts[self._confirmed])
        return self._standing_rows_valid(reading)

    @staticmethod
    def _standing_block():
        return dict(status='needs_confirmation', standing_pending=True, passed=False,
                    text='The current standing text must pass the reader and be confirmed through the planner before any plan operation. Submit a complete draft and review every row.')

    @staticmethod
    def _row_signature(row):
        provider = row.get('parser') or {}
        return dict(role=row.get('role'), sentence=row.get('sentence'),
                    parsed=row.get('parsed'), status=row.get('status'), binds=row.get('binds'),
                    parser={key: provider.get(key) for key in ('parser', 'model', 'adapter_sha256')})

    @staticmethod
    def _public_row(row):
        provider = row.get('parser') or {}
        out = {key: deepcopy(row[key]) for key in ('role', 'sentence', 'parsed', 'status', 'binds') if key in row}
        out['parser'] = {key: provider.get(key) for key in ('parser', 'model', 'adapter_sha256')}
        parsed = out.get('parsed') or {}
        code = (parsed.get('refusal') or {}).get('code')
        if row.get('status') == 'malformed':
            code = 'malformed'
        if code in REFUSAL_TEMPLATES and row.get('sentence', '').strip():
            out.update(refusal_code=code, refusal_template=REFUSAL_TEMPLATES[code])
        return _safe_reader_output(out)

    def _reading_issues(self, reading, reading_id):
        standing_pending = (self._standing_sources.get(reading_id) != self._standing_source()
                            or not self._standing_rows_valid(reading))
        changed = []
        if self._confirmed is not None:
            old = public_brief._verify(self.instance, self._receipts[self._confirmed])
            for role in ('standing', 'voyage'):
                if role == 'standing' and self._confirmed_standing_source != self._standing_source():
                    continue  # Host-edited standing text requires a new complete reading and confirmation.
                old_rows = [row for row in old['sentences'] if row['role'] == role
                            and (role != 'standing' or row['sentence'].strip())]
                new_rows = [row for row in reading['sentences'] if row['role'] == role
                            and (role != 'standing' or row['sentence'].strip())]
                for index, before in enumerate(old_rows):
                    after = new_rows[index] if index < len(new_rows) else None
                    if after is None or canonical_json(self._row_signature(before)) != canonical_json(self._row_signature(after)):
                        changed.append(dict(role=role, row=index, before=self._public_row(before),
                                            after=self._public_row(after) if after is not None else None))
        return standing_pending, changed

    def _proposed_reading(self, reading_id, reading):
        row = reading['sentences'][-1]
        standing_pending, changed = self._reading_issues(reading, reading_id)
        return dict(reading_id=reading_id, sentence=row['sentence'], parsed=_safe_reader_output(deepcopy(row.get('parsed'))),
                    status=row['status'], planner_confirmed=False,
                    rows=[self._public_row(item) for item in reading['sentences']],
                    reading_consistent=not changed, changed_rows=changed,
                    standing_pending=standing_pending)

    def context(self):
        current = None
        if self._pending in self._receipts:
            reading = public_brief._verify(self.instance, self._receipts[self._pending])
            current = self._proposed_reading(self._pending, reading)
        return _safe_reader_output(dict(instance_sha256=sha256_json(_json(self.instance)),
                    envelope=_json(self.envelope(self.instance)),
                    pending_reading_id=self._pending, confirmed_reading_id=self._confirmed,
                    confirmed_rules=self._rules() if self._pending is None else [],
                    current_reading=current,
                    standing_pending=not self._standing_ready(),
                    has_plan=self.plan is not None, active_job_id=self._active_job,
                    tools=list(self.names)))

    def call(self, name, args):
        result = _safe_reader_output(self._dispatch(name, args))
        result.update(planning_request_id=self._planning_request_id,
                      planning_generation=self._planning_generation)
        return result

    def _begin_request(self, *, blocks_acceptance):
        self._planning_request_id = uuid4().hex
        self._planning_generation += 1
        self._generation += 1
        self._acceptance_blocked = blocks_acceptance

    def _dispatch(self, name, args):
        if name not in self.names:
            return dict(status='rejected', text='Unknown or host-only tool.', passed=False)
        gate = _json(self.envelope(self.instance))
        if gate.get('in_envelope') is not True:
            reasons = [r.get('message', 'Envelope condition failed.') if isinstance(r, dict) else str(r)
                       for r in gate.get('reasons', [])]
            return dict(status='out_of_envelope', text='Outside the supported envelope. ' + ' '.join(reasons),
                        envelope=gate, passed=False)
        if not isinstance(args, dict):
            return dict(status='rejected', text='Tool arguments must be an object.', passed=False)
        expected = {'read_instruction': {'sentence'}, 'check_plan': set(),
                    'plan_lookup': {'rules'}, 'replan': {'reading_id'}, 'wait_replan': {'job_id'},
                    'try_ladder': set(), 'try_move': {'box', 'slot'},
                    'explain_box': {'box'}, 'list_limits': set()}[name]
        if set(args) != expected:
            return dict(status='rejected', text='Tool arguments do not match the contract.', passed=False)
        try:
            if name not in ('read_instruction', 'list_limits') and not self._standing_ready():
                return self._standing_block()
            if name in ('check_plan', 'plan_lookup', 'try_move', 'explain_box'):
                if self._active_job is not None:
                    return _json(self._wait_replan(self._active_job))
                from .jobs import native_guard
                with native_guard() as acquired:
                    if not acquired:
                        return dict(status='running', text='Native planning or checking is running. No new plan has been accepted.', passed=False)
                    return _json(getattr(self, '_' + name)(**args))
            return _json(getattr(self, '_' + name)(**args))
        except Exception as error:
            # Provider bodies and arbitrary exception strings can contain secrets.
            return dict(status='error', text='The tool failed; no plan was accepted.',
                        error_type=type(error).__name__, passed=False)

    def _read_instruction(self, sentence):
        if not isinstance(sentence, str) or not sentence.strip():
            return dict(status='rejected', text='Provide a nonempty instruction.', passed=False)
        # A failed read must not let a later check publish the previous plan as
        # satisfying this unresolved request.
        self._pending = 'unresolved-reader-request'
        self._begin_request(blocks_acceptance=False)
        self._negotiation = None
        if self.parser is None:
            return dict(status='unavailable', text='The instruction reader is not connected.', passed=False)
        standing_source = self._standing_source()
        receipt = public_brief.read_brief(self.instance,
                    public_brief.VoyageBrief(standing_source['text'], self._sentences + tuple(sentence.splitlines())), parser=self.parser)
        reading = receipt.evidence()
        reading_id = sha256_json(reading)
        self._receipts[reading_id] = receipt
        self._standing_sources[reading_id] = standing_source
        self._pending = reading_id
        row = reading['sentences'][-1]
        proposed = self._proposed_reading(reading_id, reading)
        result = dict(status='needs_confirmation', text='Review the reader interpretation and confirm it through the planner before replanning.',
                      proposed_reading=proposed, passed=False)
        code = ((row.get('parsed') or {}).get('refusal') or {}).get('code')
        if row['status'] == 'malformed':
            code = 'malformed'
        if code in REFUSAL_TEMPLATES:
            result.update(refusal_code=code, refusal_template=REFUSAL_TEMPLATES[code],
                          text=REFUSAL_TEMPLATES[code])
        if not proposed['reading_consistent']:
            result['text'] = 'An earlier confirmed reading changed when the reader ran again. The entire draft is blocked; earlier confirmed rules remain in force. Review every row.'
            return result
        if proposed['standing_pending']:
            result['text'] = 'Standing text needs correction or movement into voyage instructions. Review every row before any replan.'
            return result
        if row.get('parsed') is not None:
            answer = validate_answer(canonical_json(row['parsed']), context_from_instance(self.instance))
            try:
                refusal = build_refusal_evidence(self.instance, answer,
                                                 stored_witness=self.refusal_witness)
            except ValueError:
                refusal = None  # One-port-per-hatch has no static arithmetic screen.
            catalogue_refusal = self._catalogue_refusal(reading)
            if catalogue_refusal is not None:
                refusal = catalogue_refusal
            if refusal is not None:
                result['refusal'] = _without_hashes(refusal)
                result['proof'] = result['refusal']
                candidates = (self._catalogue_candidates() if catalogue_refusal is not None and self.catalogue_alternatives
                              else self._proof_candidates(answer, refusal))
                result['alternatives'] = self._negotiation_alternatives(candidates)
                result['alternative_count'] = len(result['alternatives'])
                result['checked_alternative_count'] = sum(a['checked'] for a in result['alternatives'])
                result['alternative_comparisons'] = self._alternative_comparisons(result['alternatives'])
                result['refusal']['alternatives'] = deepcopy(result['alternatives'])
                if refusal['status'] != 'screen_passed':
                    result['text'] = ' '.join(f['text'] for f in refusal['facts'])
                    if result['alternatives']:
                        result['text'] += ' ' + ' '.join(a['text'] for a in result['alternatives'])
                    if refusal['status'] == 'static_impossible':
                        if not any(a['checked'] for a in result['alternatives']):
                            result['text'] += ' No alternative has been fully checked; no replacement plan is accepted.'
                        self._negotiation = dict(reading_id=reading_id,
                                                instance_sha256=sha256_json(_json(self.instance)),
                                                proof=deepcopy(result['refusal']),
                                                alternatives=deepcopy(result['alternatives']))
        return result

    def confirm_reading(self, reading_id):
        """Host planner event. Never expose this method in the model registry."""
        gate = _json(self.envelope(self.instance))
        if gate.get('in_envelope') is not True:
            return dict(status='out_of_envelope', passed=False)
        if reading_id != self._pending or reading_id not in self._receipts:
            return dict(status='rejected', text='Only the current reading can be confirmed.', passed=False)
        receipt = self._receipts[reading_id]
        reading = public_brief._verify(self.instance, receipt)
        standing_pending, changed = self._reading_issues(reading, reading_id)
        if standing_pending or changed:
            return dict(status='needs_confirmation',
                        text='Correct the standing text or changed earlier reading, then submit a new complete draft.',
                        proposed_reading=self._proposed_reading(reading_id, reading), passed=False)
        rows = [r for r in reading['sentences'] if r['role'] == 'voyage']
        if any(r['status'] != 'accepted' for r in rows):
            return dict(status='needs_confirmation', text='Correct the unresolved instruction and submit it to the reader.', passed=False)
        from experiments.mvp.pipeline.binding.confirmation import confirm_brief
        capability = confirm_brief(receipt, source='copilot_planner_confirmed:' + reading_id)
        self._confirmed, self._pending = reading_id, None
        self._confirmed_receipt = capability
        self._confirmed_standing_source = deepcopy(self._standing_sources[reading_id])
        self._generation += 1
        self._sentences = tuple(r['sentence'] for r in rows)
        return dict(status='confirmed', reading_id=reading_id, planner_confirmed=True, passed=False)

    def _rules(self):
        if self._confirmed is None:
            return []
        receipt = self._receipts[self._confirmed]
        reading = public_brief._verify(self.instance, receipt)
        return [r['parsed'] for r in reading['sentences'] if r['role'] == 'voyage' and r['status'] == 'accepted']

    def _audit(self, plan):
        from experiments.mvp.reader.pipeline import intrinsic_violations
        context = context_from_instance(self.instance)
        return [violation for raw in self._rules()
                for violation in intrinsic_violations(self.instance, validate_answer(canonical_json(raw), context), plan)]

    def _full_check(self, plan):
        """Fresh native checker with the same taint boundary on every path."""
        from .jobs import native_guard
        with native_guard() as acquired:
            if not acquired:
                raise RuntimeError('Native planning or checking is already running')
            return self._native_check(plan)

    def _native_check(self, plan):
        if self.checker is None:
            from stowage.checker import check
            from stowage.frozen import active_stubs, require_clean, StubTaintError
            if active_stubs():
                raise StubTaintError('Active checker stubs cannot certify a copilot plan')
            require_clean(self.instance, plan, what='copilot checker input')
            checker = check
        else:
            checker = self.checker
        report = checker(self.instance, plan)
        if self.checker is None:
            require_clean(self.instance, plan, report, what='copilot plan publication')
            if active_stubs():
                raise StubTaintError('Active checker stubs cannot certify a copilot plan')
        return report

    def _proof_candidates(self, answer, refusal):
        """Expand only the reader proof's declared same-family alternative set."""
        candidates = deepcopy(refusal['alternatives'])
        if refusal.get('status') != 'static_impossible':
            return candidates[:self._alternative_limit]
        from experiments.mvp.reader.refusal_evidence import _candidates, _sentence, build_static_proof
        context = context_from_instance(self.instance)
        seen = {canonical_json(candidate['constraint']) for candidate in candidates}
        for body, rank in sorted(_candidates(answer, context), key=lambda item: item[1]):
            if len(candidates) >= self._alternative_limit:
                break
            envelope = dict(schema_version='stowlm.reader/2', constraint=body)
            parsed = validate_answer(canonical_json(envelope), context)
            key = canonical_json(envelope)
            if key in seen:
                continue
            proof = build_static_proof(self.instance, parsed)
            if proof['proven_impossible']:
                continue
            seen.add(key)
            candidates.append(dict(id=f'alternative-{len(candidates)+1}', constraint=envelope,
                                   sentence=_sentence(parsed), numeric_distance=rank[0],
                                   screen_passed=True, static_proof=proof, relaxation=True,
                                   origin='reader.refusal_evidence.same_family_candidate'))
        return candidates[:self._alternative_limit]

    def _lookup(self, rules):
        kwargs = ({'store_dir': self.lattice_store_dir}
                  if self.lattice is real_lattice and self.lattice_store_dir is not None else {})
        return self.lattice(sha256_json(_json(self.instance)), deepcopy(rules), **kwargs)

    def _catalogue_refusal(self, reading):
        rows = [row for row in reading['sentences'] if row['role'] == 'voyage']
        if not rows or any(row['status'] != 'accepted' for row in rows):
            return None
        # Legacy injected services stay offline unless the host explicitly
        # supplies a refusal service or catalogue choices.
        if self.lattice is not real_lattice and self.refusal_lookup is real_refusal_lookup and not self.catalogue_alternatives:
            return None
        rules = [row['parsed'] for row in rows]
        identity = sha256_json(_json(self.instance))
        kwargs = ({'store_dir': self.lattice_store_dir}
                  if self.refusal_lookup is real_refusal_lookup and self.lattice_store_dir is not None else {})
        stored = self.refusal_lookup(identity, deepcopy(rules), **kwargs)
        same = lambda values: sorted({canonical_json(value) for value in values})
        if (not isinstance(stored, dict) or stored.get('status') != 'proved_refusal'
                or stored.get('instance_sha256') != identity or stored.get('proof_rechecked') is not True
                or stored.get('planning_permitted') is not False or stored.get('checker') is not None
                or same(stored.get('canonical_rules', [])) != same(rules)
                or (stored.get('proof') or {}).get('proven_impossible') is not True):
            return None
        proof = _without_hashes(deepcopy(stored['proof']))
        text = 'The authenticated catalogue refusal was rechecked; the complete proposed instruction is proved impossible.'
        if proof.get('demand_cells') is not None and proof.get('open_cells') is not None:
            text += (f' A necessary screen gives at most {proof["open_cells"]:g} open physical cells '
                     f'for {proof["demand_cells"]:g} required; even that upper bound falls short.')
        elif proof.get('reason'):
            text += ' ' + proof['reason']
        return dict(status='static_impossible', source='lattice.refusal_lookup', proof_rechecked=True,
                    canonical_rules=deepcopy(rules), proof=proof, alternatives=[],
                    proof_scope=proof.get('scope'), open_cells_interpretation='necessary_screen_upper_bound',
                    checker_called=False, checker_report=None, planning_permitted=False,
                    facts=[dict(id='catalogue-refusal-proof', text=text)],
                    required_fact_ids=['catalogue-refusal-proof'])

    def _catalogue_candidates(self):
        candidates = []
        context = context_from_instance(self.instance)
        for option in self.catalogue_alternatives:
            sentences, rules = option.get('sentences'), option.get('canonical_rules')
            if (not isinstance(option.get('id'), str) or not isinstance(sentences, list) or not sentences
                    or not all(isinstance(sentence, str) and sentence.strip() for sentence in sentences)
                    or not isinstance(rules, list) or len(rules) != len(sentences)):
                continue
            try:
                for rule in rules:
                    answer = validate_answer(canonical_json(rule), context)
                    if hasattr(answer, 'refused') or 'refusal' in rule:
                        raise ValueError('Catalogue alternatives must contain executable reader envelopes')
            except (ValueError, TypeError):
                continue
            candidates.append(dict(id=option['id'], sentences=deepcopy(sentences), sentence='\n'.join(sentences),
                                   canonical_rules=deepcopy(rules), relaxation=True,
                                   screen_passed=False, origin='authenticated_lattice_catalogue',
                                   reader_required_for_application=True))
        return candidates[:self._alternative_limit]

    def _stored_report(self, stored, plan, rules, *, required=False):
        """Validate the real lookup's full native attestation without rerunning physics."""
        if self.checker is None:
            from stowage.frozen import active_stubs, StubTaintError
            if active_stubs():
                raise StubTaintError('Active checker stubs cannot certify stored plans')
        gate = stored.get('gate') if isinstance(stored, dict) else None
        if gate is None:
            if required or self.checker is None:
                raise ValueError('A stored plan requires its complete native lattice gate')
            return None  # Explicit injected checker seam for legacy offline tests.
        identity, digest = sha256_json(_json(self.instance)), sha256_json(_json(plan))
        same = lambda values: sorted({canonical_json(value) for value in values})
        gauges = gate.get('gauges') or {}
        frames = (gauges.get('physics') or {}).get('frames', [])
        rows = gate.get('rules') or []
        if (gate.get('passed') is not True or stored.get('instance_sha256') != identity
                or gate.get('instance_sha256') != identity or gate.get('plan_sha256') != digest
                or same(stored.get('canonical_rules', [])) != same(rules)
                or same(row.get('envelope') for row in rows) != same(rules)
                or any(row.get('passed') is not True or row.get('violations') for row in rows)
                or (gate.get('engine_identity') or {}).get('passed') is not True
                or gauges.get('passed') is not True or gauges.get('all_frames') is not True
                or gauges.get('outside_limits_steps') or gauges.get('unavailable_steps')
                or gauges.get('n_frames') != len(plan.moves) + 1
                or [frame.get('step') for frame in frames] != list(range(len(plan.moves) + 1))
                or any(frame.get('stability_passed') is not True or frame.get('strength_passed') is not True
                       or frame.get('error') for frame in frames)
                or (gate.get('checker') or {}).get('passed') is not True):
            raise ValueError('Stored candidate failed its instance, instruction or all-crane-frame gate')
        if self.checker is not None:
            return None
        from stowage.model import CheckReport
        from stowage.checker import report_matches_inputs
        from stowage.frozen import active_stubs, require_clean
        report = CheckReport.model_validate(gate['checker'])
        require_clean(self.instance, plan, report, what='copilot stored full gate')
        if (active_stubs() or not report_matches_inputs(report, self.instance, plan)
                or not report.passed or report.count is None
                or any(not part.ran or part.error or any(v.severity == 'hard' for v in part.violations)
                       for part in report.parts.values())
                or any(v.severity == 'hard' for v in report.violations)):
            raise ValueError('Stored checker evidence is tainted or not input-bound')
        return report

    @staticmethod
    def _stored_gauges(stored):
        gauges = (stored.get('gate') or {}).get('gauges') or {}
        return {key: deepcopy(gauges[key]) for key in
                ('passed', 'all_frames', 'n_frames', 'outside_limits_steps', 'unavailable_steps') if key in gauges}

    @staticmethod
    def _alternative_comparisons(alternatives):
        checked = [row for row in alternatives if row.get('checked') is True
                   and type(row.get('lifts')) in (int, float)]
        return [dict(left_id=left['id'], right_id=right['id'], left_lifts=left['lifts'],
                     right_lifts=right['lifts'], right_minus_left_lifts=right['lifts']-left['lifts'],
                     label='Arithmetic over checked weighted lifts; includes projected later-port lifts')
                for index, left in enumerate(checked) for right in checked[index+1:]]

    @staticmethod
    def _stored_metadata(stored):
        out = {key: deepcopy(stored[key]) for key in
               ('computed_utc', 'rechecked_utc', 'plan_producer', 'producer_label',
                'reused_recorded', 'freshly_rechecked', 'gate_source', 'native_handoff') if key in stored}
        gate = stored.get('gate') or {}
        out.update({key: deepcopy(gate[key]) for key in
                    ('base_lifts', 'lift_delta', 'boxes_moved', 'total_lifts_now', 'checked_utc') if key in gate})
        return out

    def _negotiation_alternatives(self, candidates):
        """Inspect proof-owned hypotheses without confirming or installing them."""
        from experiments.mvp.reader.pipeline import intrinsic_violations
        context = context_from_instance(self.instance)
        alternatives = []
        for candidate in candidates:
            proposal = dict(_without_hashes(candidate), checked=False, label='screen_only',
                            certificate='static_screen_only', would_work=False,
                            applied=False, requires_user_choice=True, passed=False,
                            hypothetical=True, verification_status='unchecked')
            proposal['evidence_id'] = 'refusal.' + proposal['id']
            proposal['text'] = (f'Unchecked alternative requiring your choice: {proposal["sentence"]} '
                                'It is not proved impossible by the static screen; '
                                'we must plan and check it before saying it works.')
            if proposal.get('relaxation'):
                proposal['text'] += ' This relaxes part of the requested instruction and is not applied automatically.'
            alternatives.append(proposal)
            if self.lattice is lattice_unavailable or not self._standing_ready():
                continue
            try:
                # These rules come exclusively from authenticated previous
                # readings plus the refusal proof's validated same-family candidate.
                hypothetical_rules = self._rules() + proposal.get('canonical_rules', [proposal.get('constraint')])
                answers = [validate_answer(canonical_json(raw), context) for raw in hypothetical_rules]
                stored = self._lookup(hypothetical_rules)
                if stored is None:
                    proposal['verification_reason'] = 'not_found'
                    continue
                plan = stored.get('plan') if isinstance(stored, dict) else stored.plan
                if isinstance(plan, dict):
                    from stowage.model import MovePlan
                    plan = MovePlan.model_validate(plan)
                verified = self._stored_report(stored, plan, hypothetical_rules,
                                               required=proposal.get('origin') == 'authenticated_lattice_catalogue')
                report = verified if verified is not None else self._full_check(plan)
                report_json = _report_summary(report)
                violations = [violation for answer in answers
                              for violation in intrinsic_violations(self.instance, answer, plan)]
                proposal.update(checker=dict(passed=report_json.get('passed') is True,
                                             screen='full', report=report_json),
                                instruction_recheck=dict(passed=not violations, violations=violations,
                                                         confirmed_rules=self._rules(),
                                                         hypothetical_rules=deepcopy(hypothetical_rules)))
                if report_json.get('passed') is not True or violations:
                    proposal['verification_reason'] = 'check_failed'
                    continue
                proposal.update(checked=True, label='checked', certificate='fresh_full_check',
                                verification_status='checked', would_work=True, passed=True,
                                plan_sha256=sha256_json(_json(plan)))
                if isinstance(stored, dict) and stored.get('gate') is not None:
                    proposal['gauges'] = self._stored_gauges(stored)
                    proposal['stored_evidence'] = self._stored_metadata(stored)
                proposal['text'] = (f'Checked alternative requiring your choice: {proposal["sentence"]} '
                                    'A stored candidate passed a fresh full checker and satisfies this alternative '
                                    'together with every previously confirmed instruction.')
                if isinstance(stored, dict) and stored.get('reused_recorded') is True:
                    proposal.update(certificate='recorded_native_handoff', reused_recorded=True,
                                    freshly_rechecked=False)
                    proposal['text'] = (f'Checked alternative requiring your choice: {proposal["sentence"]} '
                                        'Reused recorded native gate, native checked at '+
                                        str(stored.get('rechecked_utc') or 'date unavailable')+
                                        '; current source, epoch, inputs and reader rules verified now. '
                                        'The candidate satisfies this alternative together with every '
                                        'previously confirmed instruction.')
                lifts = (report_json.get('count') or {}).get('weighted_nonproductive')
                if lifts is not None:
                    proposal.update(lifts=lifts, lifts_label='measured; includes projected later-port lifts')
                    proposal['cost_reference'] = dict(source='checker.report.count.weighted_nonproductive',
                                                      value=lifts, plan_sha256=proposal['plan_sha256'],
                                                      label=proposal['lifts_label'])
                    proposal['text'] += f' Weighted nonproductive lifts: {lifts}; this includes projected later-port lifts.'
                else:
                    proposal['text'] += ' Its lift cost is unavailable.'
                proposal['text'] += (' The planner must choose it, send its sentence through the reader, and confirm '
                                     'the new reading before applying it. It is not applied automatically.')
            except Exception as error:
                proposal['verification_reason'] = 'check_unavailable'
                proposal['verification_error_type'] = type(error).__name__
        return alternatives

    @staticmethod
    def _label_recorded_release(result, release):
        if release is not None and release.get('reused_recorded') is True:
            result.update(mode='stored', reused_recorded=True, freshly_rechecked=False,
                          checked_utc=release.get('checked_utc'),
                          current_identity_verified=release.get('current_identity_verified') is True,
                          gate_source=release.get('gate_source', 'recorded_native_release'))
            result['text'] = ('Reused recorded native gate, native checked at '+
                              str(release.get('checked_utc') or 'date unavailable')+
                              '; current identity and inputs verified now. '
                              'No new native numerical check was run. '+result['text'])
        return result

    def _gate_plan(self, plan, *, mode='live', evidence=None, verified_report=None):
        if not self._standing_ready():
            return self._standing_block()
        release = None
        if verified_report is None and self._requires_release_gate:
            if self.checker is None:
                from stowage.frozen import active_stubs, require_clean, StubTaintError
                if active_stubs():
                    raise StubTaintError('Active checker stubs cannot certify a copilot plan')
                require_clean(self.instance, plan, what='copilot full release gate input')
            release = self.release_gate(self.instance, plan, self._rules())
            if release.get('passed') is not True:
                gauges = self._stored_gauges({'gate': release})
                text = 'The plan failed the required original-instance and all-crane-frame release gate.'
                if gauges.get('outside_limits_steps'):
                    text += ' Outside limits at steps ' + ', '.join(map(str, gauges['outside_limits_steps'])) + '.'
                result = dict(status='no_checked_plan', text=text, passed=False,
                            checker=dict(passed=(release.get('checker') or {}).get('passed') is True,
                                         screen='full', report=_report_summary(release.get('checker') or {})),
                            gauges=gauges, confirmed_rules=self._rules(),
                            instruction_recheck=dict(passed=all(row.get('passed') is True for row in release.get('rules', [])),
                                                     confirmed_rules=self._rules(), rows=release.get('rules', [])))
                return self._label_recorded_release(result, release)
            stored = dict(gate=release, instance_sha256=sha256_json(_json(self.instance)), canonical_rules=self._rules())
            verified_report = self._stored_report(stored, plan, self._rules(), required=True)
        report = verified_report if verified_report is not None else self._full_check(plan)
        report_json = _report_summary(report)
        physical_pass = report_json.get('passed') is True
        violations = self._audit(plan)
        if not self._standing_ready():
            return self._standing_block()
        passed = physical_pass and not violations and self._pending is None
        self._report = report
        result = dict(status='checked_plan' if passed else ('needs_confirmation' if self._pending else 'no_checked_plan'),
                      text='The plan passed the full checker and every confirmed instruction.' if passed else 'The plan cannot be accepted for this request.',
                      checker=dict(passed=physical_pass, screen='full', report=report_json),
                      instruction_recheck=dict(passed=not violations, violations=violations,
                                               confirmed_rules=self._rules()),
                      confirmed_rules=self._rules(),
                      passed=passed, mode=mode)
        if release is not None:
            result['gauges'] = self._stored_gauges({'gate': release})
        if evidence is not None:
            result['evidence'] = _without_hashes(_json(evidence))
        if passed:
            result['plan_sha256'] = sha256_json(_json(plan))
            self.plan = plan
            self._acceptance_blocked = False
            count = report_json.get('count', {})
            if count.get('weighted_nonproductive') is not None:
                result['lifts'] = count['weighted_nonproductive']
                result['lifts_label'] = 'measured; includes projected later-port lifts'
                result['text'] += f' Weighted nonproductive lifts: {result["lifts"]}; this includes projected later-port lifts.'
        return self._label_recorded_release(result, release)

    def _check_plan(self):
        if self._acceptance_blocked:
            return dict(status='no_checked_plan', text='The latest planning request has no checked replacement. The earlier plan is retained but is not accepted for this request.', passed=False)
        if self.plan is None:
            return dict(status='no_checked_plan', text='There is no current plan to check.', passed=False)
        return self._gate_plan(self.plan)

    def _state_token(self):
        return sha256_json(dict(instance=_json(self.instance), plan=_json(self.plan),
                               confirmed=self._confirmed, pending=self._pending,
                               confirmation=(sha256_json(vars(self._confirmed_receipt))
                                             if self._confirmed_receipt is not None else None),
                               standing=self._standing_source(),
                               generation=self._generation))

    def _replan(self, reading_id):
        return self._start_replan(reading_id)

    def _start_replan(self, reading_id, *, replanner=None):
        if self._pending or reading_id != self._confirmed or reading_id is None:
            self._begin_request(blocks_acceptance=True)
            return dict(status='needs_confirmation', text='The planner must confirm the current reader interpretation first.', passed=False)
        public_brief._verify(self.instance, self._receipts[reading_id])
        identity = self._state_token()
        if self._active_job is not None:
            active = self._job_states[self._active_job]
            if active['identity'] == identity:
                return self._wait_replan(self._active_job)
            self.job_manager.cancel(self._active_job)
            self._active_job = None
        if self.job_manager is None:
            from .jobs import JobManager
            self.job_manager = JobManager(history_limit=self._job_history_limit)
        # Submission represents a new request even when the confirmed reading
        # and current plan are unchanged. Older publications cannot reappear
        # after this newer job fails or is discarded.
        self._begin_request(blocks_acceptance=True)
        identity = self._state_token()
        # Only detached inputs enter the native worker. Neither the worker nor
        # its binding callbacks may mutate this ToolSet or publish to the model.
        detached = ToolSet(deepcopy(self.instance), checker=self.checker,
                           replanner=replanner or self.replanner, envelope=self.envelope,
                           plan=deepcopy(self.plan), standing=self.standing,
                           lattice=self.lattice, lattice_store_dir=self.lattice_store_dir,
                           catalogue_alternatives=self.catalogue_alternatives,
                           refusal_lookup=self.refusal_lookup, lookup_fallback=self.lookup_fallback,
                           release_gate=self.release_gate if self._requires_release_gate else None,
                           planning_options=deepcopy(self.planning_options))
        detached._receipts = {reading_id: self._receipts[reading_id]}
        detached._standing_revision = self._standing_revision
        detached._standing_sources = {reading_id: deepcopy(self._standing_sources[reading_id])}
        detached._confirmed_standing_source = deepcopy(self._confirmed_standing_source)
        detached._confirmed_receipt = self._confirmed_receipt
        detached._confirmed = reading_id
        detached._sentences = tuple(self._sentences)
        def operation():
            result = detached._execute_replan(reading_id)
            return dict(turn_result=result,
                        plan=detached.plan if result.get('passed') is True else None,
                        report=detached._report if result.get('passed') is True else None)
        progress = self.job_manager.start(operation, identity=identity,
                                          context=dict(reading_id=reading_id))
        job_id = progress['job_id']
        self._job_states[job_id] = dict(identity=identity, reading_id=reading_id)
        self._active_job = job_id
        self._prune_job_history()
        if progress.get('status') == 'completed':
            return self._wait_replan(job_id)
        return dict(_without_hashes(progress), passed=False)

    def _wait_replan(self, job_id):
        if not isinstance(job_id, str) or job_id not in self._job_states:
            return dict(status='not_found', text='The background replan job is not available.', passed=False)
        if self._active_job is not None and self._active_job != job_id:
            return dict(status='running', job_id=self._active_job, superseded_job_id=job_id,
                        text='A newer background replan is running or awaiting publication. Poll the current job; the earlier result is not accepted for this request.',
                        passed=False)
        state = self._job_states[job_id]
        identity = self._state_token()
        published = state.get('published_result')
        if published is not None and state.get('published_identity') == identity:
            return deepcopy(published)
        if state['identity'] != identity:
            return self._reject_job(job_id, status='stale')
        try:
            progress = self.job_manager.poll(job_id, identity=identity)
            if not isinstance(progress, dict) or not isinstance(progress.get('status'), str):
                return self._reject_job(job_id)
            canonical_json(progress)
        except Exception:
            return self._reject_job(job_id)
        if progress.get('status') != 'completed':
            result = dict(_without_hashes(progress), passed=False)
            if progress.get('status') in ('error', 'stale', 'not_found'):
                self._discard_job(job_id)
                if self._active_job == job_id:
                    self._active_job = None
                state.update(published_result=deepcopy(result), published_identity=identity)
                self._prune_job_history()
            return result
        try:
            payload = self.job_manager.result(job_id, identity=identity)
            if identity != self._state_token():
                return self._reject_job(job_id, status='stale')
            if not isinstance(payload, dict) or not isinstance(payload.get('turn_result'), dict):
                return self._reject_job(job_id)
            result = deepcopy(payload['turn_result'])
            if (not isinstance(result.get('passed'), bool) or
                    not isinstance(result.get('status'), str) or
                    not isinstance(result.get('text'), str)):
                return self._reject_job(job_id)
            canonical_json(result)
            if result['passed']:
                from .safety import checked_digest
                plan, report = payload.get('plan'), payload.get('report')
                digest = checked_digest(result)
                if (digest is None or plan is None or report is None or
                        sha256_json(_json(plan)) != digest or
                        result.get('instruction_recheck', {}).get('passed') is not True):
                    return self._reject_job(job_id)
        except Exception:
            return self._reject_job(job_id)
        if result['passed']:
            self.plan, self._report = plan, report
            self._acceptance_blocked = False
            self._generation += 1
        result.update(job_id=job_id, job_status='completed')
        if 'elapsed_s' in progress:
            result.update(elapsed_s=progress['elapsed_s'], elapsed_label='measured')
        result = self._compact_publication(result)
        self._active_job = None if self._active_job == job_id else self._active_job
        state.update(published_result=deepcopy(result), published_identity=self._state_token())
        self._forget_job(job_id)
        self._prune_job_history()
        return result

    @staticmethod
    def _compact_publication(result):
        keys = ('status', 'text', 'passed', 'mode', 'plan_sha256', 'lifts', 'lifts_label', 'confirmed_rules',
                'job_id', 'job_status', 'elapsed_s', 'elapsed_label')
        compact = {key: deepcopy(result[key]) for key in keys if key in result}
        checker = result.get('checker')
        if isinstance(checker, dict):
            compact['checker'] = {key: deepcopy(checker[key]) for key in ('passed', 'screen', 'report') if key in checker}
        audit = result.get('instruction_recheck')
        if isinstance(audit, dict):
            compact['instruction_recheck'] = {key: deepcopy(audit[key]) for key in
                                               ('passed', 'confirmed_rules', 'violations') if key in audit}
        compact['native_evidence_sha256'] = sha256_json(result)
        return compact

    def _prune_job_history(self):
        for job_id in tuple(self._job_states):
            if len(self._job_states) <= self._job_history_limit:
                break
            if job_id != self._active_job:
                self._discard_job(job_id)
                del self._job_states[job_id]

    def _forget_job(self, job_id):
        forget = getattr(self.job_manager, 'forget', None)
        if callable(forget):
            try:
                forget(job_id)
            except Exception:
                pass

    def _discard_job(self, job_id):
        # Older injected scheduler doubles need only the original cancel API.
        # The production manager also releases its terminal native payload.
        try:
            self.job_manager.cancel(job_id)
        except Exception:
            pass
        self._forget_job(job_id)

    def _reject_job(self, job_id, *, status='error'):
        self._discard_job(job_id)
        if self._active_job == job_id:
            self._active_job = None
        result = dict(status=status, job_id=job_id, job_status=status, passed=False,
                      text='The background job did not supply a complete fresh checker certificate. No plan was accepted. A fresh replan can be requested.')
        self._job_states[job_id].update(published_result=deepcopy(result),
                                        published_identity=self._state_token())
        self._prune_job_history()
        return result

    def _execute_replan(self, reading_id):
        receipt = self._receipts[reading_id]
        if self.replanner is None:
            from experiments.mvp.pipeline.binding import execute_brief
            from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
            capability = self._confirmed_receipt
            if (reading_id != self._confirmed or capability is None or
                    capability.payload != receipt.payload or capability.authentication != receipt.authentication):
                raise ValueError('The current reading requires an authenticated host confirmation')
            verify_confirmation(capability)
            receipt = capability
            replanner = execute_brief
        else:
            replanner = self.replanner
        public_brief._verify(self.instance, receipt)
        candidate, evidence = replanner(self.instance, receipt, **self.planning_options)
        if candidate is None:
            return dict(status=evidence.get('status', 'no_checked_plan'), text=evidence.get('reason', 'No checked plan was found.'),
                        evidence=_without_hashes(_json(evidence)), passed=False)
        plan = candidate.plan if hasattr(candidate, 'plan') else candidate
        previous = self.plan
        result = self._gate_plan(plan, evidence=evidence)
        if evidence.get('status') == 'relaxed_plan' or evidence.get('passed') is not True:
            result.pop('plan_sha256', None)
            self.plan = previous
            result.update(status='relaxed_plan' if evidence.get('status') == 'relaxed_plan' else 'no_checked_plan', passed=False,
                          text='The candidate is not a full passing plan; inspect the checker evidence and broken rules.')
        return result

    def _plan_lookup(self, rules):
        if self._pending:
            return dict(status='needs_confirmation', text='Confirm the reader interpretation before requesting a stored plan.', passed=False)
        if not isinstance(rules, list) or canonical_json(rules) != canonical_json(self._rules()):
            return dict(status='rejected', text='Lookup rules must exactly match the confirmed reader output.', passed=False)
        stored = self._lookup(rules)
        if stored is None:
            if ((self.lattice is real_lattice or getattr(self.lattice, 'native_authenticated', False) is True)
                    and self.lookup_fallback and self._confirmed is not None):
                result = self._start_replan(self._confirmed)
                result['lookup_miss'] = True
                return result
            return dict(status='unavailable' if self.lattice is lattice_unavailable else 'not_found',
                        text='No stored checked plan is available for these confirmed readings.', passed=False)
        raw = stored.get('plan') if isinstance(stored, dict) else stored.plan
        if isinstance(raw, dict):
            from stowage.model import MovePlan
            raw = MovePlan.model_validate(raw)
        verified = self._stored_report(stored, raw, rules)
        result = self._gate_plan(raw, mode='stored', verified_report=verified)
        if isinstance(stored, dict) and stored.get('gate') is not None:
            result['gauges'] = self._stored_gauges(stored)
            result['stored_evidence'] = self._stored_metadata(stored)
        source = stored if isinstance(stored, dict) else vars(stored)
        computed = next((source[key] for key in ('computed_utc', 'created_utc', 'computed_at')
                         if isinstance(source.get(key), str) and source[key].strip()), None)
        if source.get('reused_recorded') is True:
            label = ('Reused recorded native gate, computed on '+str(computed or 'date unavailable')+
                     ', native checked at '+str(source.get('rechecked_utc') or 'date unavailable')+
                     '; current source, epoch, inputs and reader rules verified now.')
            result['reused_recorded'] = True
            result['freshly_rechecked'] = False
        elif computed is not None:
            result['computed_utc'] = computed
            label = f'Stored plan, computed on {computed}, rechecked now.'
        else:
            label = 'Stored plan, computation date unavailable; rechecked now.'
        result['text'] = label + ' ' + result['text']
        return result

    def _try_ladder(self):
        if self._pending:
            return dict(status='needs_confirmation', text='Resolve the current reader interpretation before trying the ladder.', passed=False)
        if self._confirmed is None:
            return dict(status='needs_confirmation', text='Read and confirm a brief before trying the ladder.', passed=False)
        # Binding's default includes the instruction-preserving ladder. A host
        # override still runs detached in the same background job boundary.
        return self._start_replan(self._confirmed, replanner=self.ladder)

    def _try_move(self, box, slot):
        if not isinstance(box, str) or not isinstance(slot, str):
            return dict(status='rejected', text='Box and slot must be strings.', passed=False)
        if self.plan is None:
            return dict(status='no_checked_plan', text='There is no plan to move a box in.', passed=False)
        verdict = _without_hashes(_json(self.sandbox(self.instance, self.plan, box, slot)))
        if verdict.get('status') == 'unavailable':
            return dict(verdict, passed=False)
        audit = None
        invalid_input = (verdict.get('gauges') or {}).get('input_error') is True
        if self.sandbox is real_sandbox and verdict.get('screen') == 'full' and not invalid_input:
            from experiments.mvp.pipeline.sandbox import move_plan
            candidate = move_plan(self.instance, self.plan, box, slot)
            violations = self._audit(candidate)
            audit = dict(passed=not violations, violations=violations)
            if violations:
                verdict['legal'] = False
                verdict.setdefault('broken', []).extend(dict(rule_id='instructions.confirmed', message=message,
                                                            source='reader_instruction_recheck')
                                                        for message in violations)
        text = ('The move request was invalid; no candidate was accepted.' if invalid_input else
                'The move has a full checker verdict.' if verdict.get('screen') == 'full' else
                'Fast screen; a full check is still required.')
        return dict(status='move_verdict', text=text,
                    box=box, slot=slot,
                    reference_label='Exact attempted move identities; the verdict determines legality.',
                    verdict=verdict, instruction_recheck=audit, passed=False)

    def _explain_box(self, box):
        if not isinstance(box, str):
            return dict(status='rejected', text='Box must be a string.', passed=False)
        if self.plan is None:
            return dict(status='no_checked_plan', text='There is no current plan to explain.', passed=False)
        checked = self._check_plan()
        if not checked['passed']:
            return checked
        self._explanation = self.explainer(self.instance, self.plan, self._report, box)
        explanation = _explanation_summary(self._explanation)
        return dict(status=explanation.get('status', 'explanation'), text=explanation.get('text', ''),
                    explanation=explanation, checker=deepcopy(checked['checker']),
                    instruction_recheck=deepcopy(checked['instruction_recheck']),
                    confirmed_rules=deepcopy(checked['confirmed_rules']),
                    source_explanation_sha256=sha256_json(_json(self._explanation)), passed=False)

    def _list_limits(self):
        raw = _json(self.instance)
        hull = raw['vessel'].get('hull', {})
        hull_limits = {key: hull[key] for key in ('min_gm_m', 'max_draught_m', 'max_trim_m', 'max_list_rad', 'min_draught_fwd_m') if key in hull}
        text = f'Synthetic ship facts: {len(raw["load_list"])} boxes to load; {len(raw["vessel"]["cells"])} physical cells.'
        for key, label in (('min_gm_m', 'Minimum GM (metres)'), ('max_draught_m', 'Maximum draught (metres)'),
                           ('max_trim_m', 'Maximum trim (metres)'), ('max_list_rad', 'Maximum list (radians)')):
            if key in hull_limits:
                text += f' {label}: {hull_limits[key]}.'
        port = raw.get('port', {})
        for key, label in (('max_crane_height_tiers', 'Port crane height (tiers)'), ('yard_max_tiers', 'Yard stack height (tiers)')):
            if port.get(key) is not None:
                text += f' {label}: {port[key]}.'
        text += ' Limits require checking and cannot be waived by the copilot.'
        result = dict(status='limits', text=text,
                    ship=dict(id=raw['vessel'].get('id'), physical_cells=len(raw['vessel']['cells']),
                              reader_context=context_from_instance(self.instance),
                              limits=hull_limits),
                    port=port, rule_overrides=raw.get('rule_overrides', {}),
                    envelope=_json(self.envelope(self.instance)), passed=False)
        negotiation = self._negotiation
        if (negotiation is not None and
                negotiation['instance_sha256'] == sha256_json(raw) and
                negotiation['reading_id'] in (self._pending, self._confirmed)):
            result.update(proof=deepcopy(negotiation['proof']),
                          alternatives=deepcopy(negotiation['alternatives']),
                          negotiation_reading_id=negotiation['reading_id'])
            result['alternative_count'] = len(result['alternatives'])
            result['checked_alternative_count'] = sum(a['checked'] for a in result['alternatives'])
            result['alternative_comparisons'] = self._alternative_comparisons(result['alternatives'])
            result['text'] += ' ' + ' '.join(f['text'] for f in result['proof']['facts'])
            if result['alternatives']:
                result['text'] += ' ' + ' '.join(a['text'] for a in result['alternatives'])
            if not any(a['checked'] for a in result['alternatives']):
                result['text'] += ' No alternative has been fully checked; no replacement plan is accepted.'
        return result


Tools = ToolSet
