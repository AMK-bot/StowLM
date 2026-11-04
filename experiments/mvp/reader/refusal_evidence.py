"""Software-owned refusal facts; no model, planner, repair, or checker calls.

Passing this necessary geometry screen is never a feasibility certificate.
Alternatives are separate proposals and never change the accepted instruction.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal
from stowage.model import MovePlan

from experiments.mvp.reader.occupancy import apply_reader_constraint
from experiments.mvp.reader.schema import answer_envelope, context_from_instance, validate_answer
from experiments.mvp.reader.select_binding import physical_slots_for_box

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_VERSION = 'stowlm.refusal-evidence/1'


def _answer(value, context):
    if isinstance(value, dict):
        return validate_answer(json.dumps(value), context)
    return validate_answer(json.dumps(answer_envelope(value)), context)


def _screen(instance, answer):
    compiled = apply_reader_constraint(instance, answer)
    boxes = {box.id: box for box in instance.containers}
    rows = []
    for bid in instance.load_list:
        box = boxes[bid]
        slots = physical_slots_for_box(instance, box)
        rows.append(dict(container_id=bid, length_ft=box.length_ft,
                         demand_cells=1 if box.length_ft == 20 else 2,
                         size_compatible_footprints=len(slots),
                         allowed_footprints=sum(compiled.forbids(bid, slot) is None for slot in slots)))
    conflicts = []
    discharged = set(instance.discharge_list)
    for placement in instance.onboard.placements:
        if placement.container_id not in discharged and answer.type != 'close_bay':
            reason = compiled.forbids(placement.container_id, placement.slot)
            if reason:
                conflicts.append(dict(container_id=placement.container_id, slot=placement.slot, reason=reason))
    demand = sum(row['demand_cells'] for row in rows)
    open_cells = len(compiled.open_cells())
    nofit = [row['container_id'] for row in rows if not row['allowed_footprints']]
    reasons = []
    if demand > open_cells:
        reasons.append('total_open_cell_shortfall')
    if nofit:
        reasons.append('no_size_compatible_allowed_footprint')
    if conflicts:
        reasons.append('fixed_retained_conflict')
    return dict(load_boxes=rows, load_box_count=len(rows), demand_cells=demand,
                demand_teu=demand, physical_cells=len(instance.vessel.cells),
                open_cells=open_cells, nofit_ids=nofit, retained_conflicts=conflicts,
                proven_impossible=bool(reasons), proof_reasons=reasons,
                per_box_open_cells={bid: len(compiled.open_cells(bid)) for bid in instance.load_list}), compiled


def build_static_proof(instance, answer, *, stored_witness=None):
    """Count necessary-condition failures for the parsed, unchanged instruction.

    ``stored_witness`` is accepted for API symmetry but is never needed for a
    static impossibility proof. A preplanning failure has no checker report.
    """
    answer = _answer(answer, context_from_instance(instance))
    if not isinstance(answer, (CloseBay, MaxTiers, DgKeepAway)):
        raise ValueError('Static refusal proofs support CloseBay, MaxTiers, and DgKeepAway')
    proof, _ = _screen(instance, answer)
    return dict(proof, constraint=answer_envelope(answer), planning_attempted=False,
                checker_called=False, checker_report=None,
                scope='necessary geometry only; screen pass does not guarantee a plan')


def _candidates(answer, context):
    """Deterministic same-family candidates and an explicit numeric distance."""
    body = answer.model_dump(mode='json')
    if isinstance(answer, CloseBay):
        for bay in context['bays']:
            if bay != answer.bay:
                yield body | {'bay': bay}, (abs(bay-answer.bay)/2, bay)
    elif isinstance(answer, MaxTiers):
        cap = context['max_tiers'][answer.zone]
        if answer.bay is not None:
            cap = context.get('max_tiers_by_bay', {}).get(str(answer.bay), {}).get(answer.zone, cap)
        for count in range(cap+1):
            if count != answer.max_tiers:
                yield body | {'max_tiers': count}, (abs(count-answer.max_tiers), count)
    else:
        # Preserve the named class and reference. Changing either would change
        # the request's meaning, rather than offer the nearest distance limit.
        for distance in range(1, 21):
            if distance != answer.min_bays:
                yield body | {'min_bays': distance}, (abs(distance-answer.min_bays), distance)


def _witness(instance, path):
    if path is None:
        return None
    path = Path(path)
    data = path.read_bytes()
    row = json.loads(data)
    if row.get('instance_id') != instance.id or row.get('passed') is not True or not row.get('plan'):
        raise ValueError('Stored witness must be a checked plan for this instance')
    provenance = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                      instance_id=instance.id, full_checker_passed=True, fresh_audit=False)
    if row.get('input') and row.get('input_sha256'):
        source = Path(row['input'])
        source = source if source.is_absolute() else ROOT/source
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['input_sha256'] or json.loads(raw).get('id') != instance.id:
            raise ValueError('Stored witness input provenance does not match')
        provenance.update(input_path=str(source), input_sha256=row['input_sha256'])
    else:
        raise ValueError('Stored checked witness needs input path and hash provenance')
    plan = MovePlan.model_validate(row['plan'])
    if plan.instance_id != instance.id:
        raise ValueError('Stored plan belongs to another instance')
    provenance['plan_sha256'] = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    return plan, provenance


def _sentence(answer):
    if isinstance(answer, CloseBay):
        return f'Do not load any containers in bay {answer.bay:02d}.'
    if isinstance(answer, MaxTiers):
        scope = f'bay {answer.bay:02d}' if answer.bay is not None else 'every bay'
        return f'In {scope}, use at most {answer.max_tiers} occupied tiers per {answer.zone} stack.'
    anchor = f'bay {answer.away_from.bay:02d}' if answer.away_from.kind == 'bay' else 'the accommodation'
    return f'Keep all IMDG class {answer.imdg_class} containers at least {answer.min_bays} 20-foot bays away from {anchor}.'


def _alternatives(instance, answer, context, witness):
    original = apply_reader_constraint(instance, answer)
    proposals = []
    for body, rank in sorted(_candidates(answer, context), key=lambda item: item[1]):
        candidate = validate_answer(json.dumps({'schema_version': 'stowlm.reader/2', 'constraint': body}), context)
        proof, compiled = _screen(instance, candidate)
        if proof['proven_impossible']:
            continue
        relaxation = any(original.forbids(box.id, slot) is not None and compiled.forbids(box.id, slot) is None
                         for box in instance.containers for slot in physical_slots_for_box(instance, box))
        checked = witness is not None and not compiled.violations(witness[0])
        sentence = _sentence(candidate)
        if checked:
            text = (f'Alternative requiring your choice: {sentence} A stored full-checked witness satisfies this alternative. '
                    'That is a historical witness, not a guarantee for a new plan.')
        else:
            text = (f'Alternative requiring your choice: {sentence} It is not proved impossible by the static screen; '
                    'we must plan and check it before saying it works.')
        if relaxation:
            text += ' This relaxes at least part of your original instruction; it will not be applied automatically.'
        proposal = dict(id=f'alternative-{len(proposals)+1}', text=text, sentence=sentence,
                        constraint=answer_envelope(candidate), numeric_distance=rank[0],
                        screen_passed=True, certificate='stored_checked_witness' if checked else 'static_screen_only',
                        would_work=False, requires_user_choice=True, applied=False, relaxation=relaxation,
                        static_proof=proof)
        if checked:
            proposal['stored_witness'] = witness[1]
            proposal['plan_sha256'] = witness[1]['plan_sha256']
        proposals.append(proposal)
    # Preserve the nearest screen-passing candidate and, if different, the
    # nearest candidate with a stored witness. The explainer chooses explicitly.
    selected = proposals[:1]
    witnessed = next((p for p in proposals if p['certificate'] == 'stored_checked_witness'), None)
    if witnessed is not None and witnessed not in selected:
        selected.append(witnessed)
    return selected


def build_refusal_evidence(instance, answer, *, stored_witness=None, rule_details=None,
                           clarification_question=None):
    """Build a JSON-safe, factual envelope for a grounded refusal explanation.

    ``rule_details`` may name an actual existing rule with ``name`` and
    ``description``. Without it no existing rule or checker finding is invented.
    Stored witness flags describe historical evidence and need a fresh audit
    before being promoted to a fresh plan certificate.
    """
    context = context_from_instance(instance)
    answer = _answer(answer, context)
    out = dict(schema_version=SCHEMA_VERSION, parsed=answer_envelope(answer), facts=[],
               required_fact_ids=[], alternatives=[], clarifications=[], nearest_alternative_id=None,
               checker_called=False, checker_report=None, automatically_applied=False)
    def fact(identifier, text):
        out['facts'].append(dict(id=identifier, text=text))
        out['required_fact_ids'].append(identifier)
    if isinstance(answer, Refusal):
        out['status'] = answer.code
        if answer.code == 'loosening':
            fact('reader-refusal', 'The reader refused this request as a relaxation. No constraint was applied.')
            if rule_details is not None:
                if not isinstance(rule_details, dict) or not all(isinstance(rule_details.get(k), str) and rule_details[k].strip()
                                                                 for k in ('name', 'description')):
                    raise ValueError('rule_details needs an explicit rule name and description')
                out['rule_details'] = dict(rule_details)
                fact('existing-rule', f'The supplied existing rule is {rule_details["name"]}: {rule_details["description"]}')
            else:
                fact('rule-unprovided', 'No existing rule details were supplied, so this evidence does not identify a specific rule.')
        elif answer.code == 'ambiguous':
            fact('reader-refusal', 'The reader marked the request ambiguous. No constraint was guessed or applied.')
            question = clarification_question or 'Which exact restriction should apply, including the named bay and any required tier zone or distance?'
            if not isinstance(question, str) or not question.strip():
                raise ValueError('clarification_question must be nonempty text')
            out['clarifications'] = [dict(id='clarification-1', text=question)]
        else:
            fact('reader-refusal', f'The reader refused the request with code {answer.code}. No constraint was applied.')
        return out
    proof = build_static_proof(instance, answer)
    out.update(status='static_impossible' if proof['proven_impossible'] else 'screen_passed', proof=proof)
    if proof['proven_impossible']:
        fact('preplanning-proof', 'The parsed instruction is proved impossible by a necessary geometry condition before planning; no checker report exists.')
    else:
        fact('screen-limit', 'The static screen did not prove this instruction impossible. A plan and full checker are still required.')
    fact('cell-capacity', f'The load needs {proof["demand_cells"]} TEU of cells; {proof["open_cells"]} physical cells remain after restrictions that close cells for every box. Container-specific restrictions are counted separately.')
    if proof['nofit_ids']:
        fact('no-fit-boxes', 'Load containers with no size-compatible allowed footprint: ' + ', '.join(proof['nofit_ids']) + '.')
    if proof['retained_conflicts']:
        fact('retained-conflicts', 'Fixed retained containers conflicting with the instruction: ' +
             ', '.join(p['container_id'] for p in proof['retained_conflicts']) + '.')
    if proof['proven_impossible']:
        out['alternatives'] = _alternatives(instance, answer, context, _witness(instance, stored_witness))
        if out['alternatives']:
            out['nearest_alternative_id'] = out['alternatives'][0]['id']
    return out
