"""Release gate around the unchanged final engine; no language parsing here."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ENGINE = ROOT / 'experiments/mvp/frontier/engine'
MANIFEST = ENGINE / 'source-manifest-0.13.0-review-fixes.json'
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
              'MKL_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[_name] = '1'
os.environ.setdefault('STOWAGE_VESSEL_CACHE_DIR', str(HERE / 'cache'))

from engine import VERSION, NAME
from engine.config import Config
from engine import planner as engine_planner
from engine.peel import inspect
from llm.instructions.apply import NotApplicable
from llm.instructions.schema import CloseBay, DgKeepAway, MaxTiers, Refusal
from stowage.checker import check
from stowage.frozen import active_stubs, require_clean, StubTaintError
from stowage.model import MovePlan, PlannerResult
from experiments.mvp.reader.constraints import InfeasibleInstruction
from experiments.mvp.reader.schema import (OnePortPerHatch, answer_envelope,
                                          context_from_instance, validate_answer)
from experiments.mvp.reader.pipeline import compile_constraint as _reader_compile_constraint
from experiments.mvp.reader.select_binding import per_box_slot_screen
from .admission import wait_for_capacity
from .runtime import within_runtime_cache
from .provenance import same_identity


@dataclass
class Intersection:
    """Pickleable predicate carried explicitly into engine spawn workers."""
    instance: object
    parts: list

    def forbids(self, box, slot):
        return next((reason for part in self.parts if (reason := part.forbids(box, slot))), None)

    def violations(self, plan):
        return [reason for part in self.parts for reason in part.violations(plan)]

    def capacity_shortfall(self):
        for part in self.parts:
            if hasattr(part, 'capacity_shortfall') and (reason := part.capacity_shortfall()):
                return reason
        # Intersect universal closures too: separately feasible instructions may
        # jointly close the vessel. This is a necessary TEU screen, never a pass.
        closed = set().union(*(getattr(part, '_all_boxes', set()) for part in self.parts))
        available = len(self.instance.vessel.cells) - len(closed)
        wanted = set(self.instance.load_list)
        # A 45-foot box occupies the same two-cell footprint as a 40-foot box.
        demand = sum(1 if b.length_ft == 20 else 2 for b in self.instance.containers if b.id in wanted)
        if demand > available:
            return f'the load list needs {demand:g} TEU of cells but only {available} remain open after all instructions'
        return None


def compile_constraint(instance, answer, *, workers=1, seed=0, base=None):
    if isinstance(answer, OnePortPerHatch):
        from .allocation import derive_hatch_allocation
        return derive_hatch_allocation(instance, base=base, workers=workers, seed=seed)
    return _reader_compile_constraint(instance, answer, workers=workers, seed=seed)


def verify_manifest():
    """Read-only verification of the exact pinned engine and its native inputs."""
    from .provenance import verify
    return verify(ENGINE, ROOT, MANIFEST)


def _evidence(budget_s, finalize_s):
    identity = verify_manifest()
    return dict(status='no_checked_plan', engine_version=VERSION,
                engine_manifest_sha256=identity['manifest_sha256'], engine_manifest=identity,
                native_manifest_sha256=identity['native_manifest_sha256'],
                lane=None, stages={}, rules=[], checker=None, lifts=None, tier=None,
                level=None, broken_rules=[], boxes_ashore=[], wall_s=0., budget_s=budget_s,
                finalize_s=finalize_s, passed=False, plan_released=False,
                incumbent_streaming=True, instruction_recheck_passed=False)


def _validated_answers(instance, answers):
    if isinstance(answers, (str, bytes, dict)):
        raise TypeError('answers must contain validated reader schema objects, not JSON or dictionaries')
    context = context_from_instance(instance)
    result = []
    for answer in answers:
        if not isinstance(answer, (CloseBay, MaxTiers, DgKeepAway, OnePortPerHatch, Refusal)):
            raise TypeError('answers must contain validated reader schema objects')
        result.append(validate_answer(json.dumps(answer_envelope(answer)), context))
    return result


def _audit_rules(parts, rows, plan):
    errors = []
    for part, row in zip(parts, rows):
        try:
            violations = list(part.violations(plan))
        except Exception as error:
            violations = [f'instruction audit failed: {type(error).__name__}: {error}']
        row['instruction_recheck'] = dict(passed=not violations, violations=violations)
        errors.extend(violations)
    return errors


def _relaxed_plans(details):
    # The engine's public relaxed-plan path. replay_candidate_plan alone is an
    # unchecked fallback and is deliberately ineligible for release here.
    seam = details.get('stages', {}).get('peel_seam', {})
    for name in ('relaxed_plan', 'plan'):
        if seam.get(name) is not None:
            yield seam[name]
            return


def _set_verdict(evidence, report, row):
    evidence.update(checker=report.model_dump(mode='json'), lifts=row['lifts'],
                    level=row['level'], tier=row['column'], broken_rules=row['broken_rules'],
                    boxes_ashore=row['boxes_left_ashore'], completion=row,
                    boxes_ashore_count=len(row['boxes_left_ashore']),
                    instruction_recheck_passed=True)


@within_runtime_cache
def plan_with_rules(instance, answers, *, budget_s=None, finalize_s=None, seed=0,
                    config=None, on_incumbent=None):
    """Plan under confirmed schema objects, then audit on the ORIGINAL instance.

    ``None`` budgets retain the engine defaults (including historical small-map
    floors). A relaxed result has status=dead_end and evidence passed=false.
    Callers MUST use evidence.status/passed, never result-is-not-None as a pass.
    """
    began = perf_counter()
    for value in (budget_s, finalize_s):
        if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value < 0):
            raise ValueError('budgets must be finite and nonnegative')
    if config is not None and not isinstance(config, Config):
        raise TypeError('config must be engine.config.Config')
    if on_incumbent is not None and not callable(on_incumbent):
        raise TypeError('on_incumbent must be callable')
    answers = _validated_answers(instance, answers)
    evidence = _evidence(budget_s, finalize_s)
    def done(result=None):
        final_identity = verify_manifest()
        evidence['engine_manifest_after'] = final_identity
        if not same_identity(evidence['engine_manifest'], final_identity):
            evidence.update(status='constraint_unavailable', passed=False, plan_released=False,
                            reason='pinned engine/native source changed during invocation')
            result = None
        evidence['wall_s'] = perf_counter()-began
        return result, evidence
    if not evidence['engine_manifest']['passed']:
        evidence.update(status='constraint_unavailable', reason='pinned engine/native source manifest does not match')
        return done()
    try:
        if active_stubs():
            raise StubTaintError('active checker stubs are not a full native checker')
        require_clean(instance, what='instruction binding input')
    except StubTaintError as error:
        evidence.update(status='constraint_unavailable', reason=str(error))
        return done()
    parts = []
    for answer in answers:
        evidence['rules'].append(dict(canonical_envelope=answer_envelope(answer),
            how_bound='pending compilation', instruction_recheck=dict(passed=None, violations=[])))
    if any(isinstance(a, Refusal) for a in answers):
        evidence.update(status='needs_confirmation', reason='a refused reading is not an executable instruction')
        return done()
    evidence['admission'] = wait_for_capacity()
    try:
        indexed = {}
        # Derive the conservative cover mapping after other confirmed masks so
        # it respects their available footprints regardless of sentence order.
        for index in sorted(range(len(answers)), key=lambda i: isinstance(answers[i], OnePortPerHatch)):
            answer, row = answers[index], evidence['rules'][index]
            compiled, compilation = compile_constraint(instance, answer, workers=1, seed=seed,
                                                       base=Intersection(instance, list(indexed.values())))
            indexed[index] = compiled
            row.update(compilation=compilation, how_bound=(
                'scoped static box/slot mask plus strict physical-cover POD allocation'
                if isinstance(answer, OnePortPerHatch) else 'scoped static box/slot mask and constructor placement screen'))
        parts = [indexed[i] for i in range(len(answers))]
        constrained = Intersection(instance, parts)
        if reason := constrained.capacity_shortfall():
            raise InfeasibleInstruction(reason)
        if no_slots := per_box_slot_screen(instance, constrained):
            raise InfeasibleInstruction('no instruction-allowed physical footprint: ' + ', '.join(no_slots))
    except (InfeasibleInstruction, NotApplicable) as error:
        evidence.update(status='constraint_unavailable', reason=str(error), error=str(error))
        return done()

    # Every checked improving candidate is observed through the unchanged full
    # checker call sites, including publication paths and finishers. The
    # callback receives only a separately audited original-instance pass.
    incumbent_lifts = math.inf
    incumbent_result = None
    def observed(plan, _report):
        nonlocal incumbent_lifts, incumbent_result
        publication_identity = verify_manifest()
        if not same_identity(evidence['engine_manifest'], publication_identity):
            evidence.setdefault('rejected_publications', []).append(dict(
                reason='pinned engine/native identity changed before incumbent publication',
                identity=publication_identity))
            return
        if constrained.violations(plan):
            return
        # The hook guarantees this report was freshly produced by the full
        # native checker on this exact original instance (never a peeled copy).
        # Final release below still performs its separate full check.
        report = _report
        if not report.passed or report.count is None:
            return
        require_clean(instance, plan, report, what='instruction incumbent publication')
        if active_stubs():
            return
        lifts = float(report.count.weighted_nonproductive)
        if lifts >= incumbent_lifts:
            return
        incumbent_lifts = lifts
        incumbent_result = PlannerResult(status='ok', plan=plan, planner=NAME,
                                         planner_version=VERSION, seed=seed)
        if on_incumbent is not None:
            event = dict(status='checked_plan', passed=True, engine_version=VERSION,
                         engine_manifest_sha256=evidence['engine_manifest_sha256'],
                         native_manifest_sha256=evidence['native_manifest_sha256'],
                         publication_identity=publication_identity,
                         lifts=lifts, wall_s=perf_counter()-began,
                         instruction_recheck_passed=True,
                         rules=[dict(canonical_envelope=row['canonical_envelope'],
                                     instruction_recheck=dict(passed=True, violations=[]))
                                for row in evidence['rules']],
                         stage_attribution='native_full_checker_observer',
                         checker=report.model_dump(mode='json'), plan=plan.model_dump(mode='json'))
            try:
                on_incumbent(event)
            except Exception as error:
                evidence.setdefault('incumbent_callback_errors', []).append(f'{type(error).__name__}: {error}')
    from .hooks import binding_scope
    with binding_scope(instance, constrained, on_checked=observed) as binding_audit:
        try:
            result, details = engine_planner.plan_and_check(instance, budget_s=budget_s,
                finalize_s=600. if finalize_s is None else finalize_s, seed=seed,
                config=Config() if config is None else config)
        except Exception as error:
            # Publication already established caller ownership. Unexpected late
            # engine exceptions must not erase that independently checked plan.
            result, details = None, dict(stages={}, pipeline_errors=[f'{type(error).__name__}: {error}'])
    native_scope = details.get('proof_scope')
    proof_domain = ('compiled instruction masks, including any chosen physical-cover/POD allocation'
                    if answers else 'original instance without confirmed instruction masks')
    evidence.update(engine=details, stages=details.get('stages', {}),
                    lane=details.get('winner_lane'), stage_attribution=details.get('winner'),
                    binding_audit=binding_audit, proof_domain=proof_domain,
                    native_proved_optimal=bool(details.get('proved_optimal')),
                    proof_scope=(f'{native_scope}; restricted to {proof_domain}'
                                 if native_scope and answers else native_scope))
    # A full passing publication cannot be erased by a later optional lane.
    candidates = [(result, False)] if result is not None and result.plan is not None else []
    if incumbent_result is not None:
        candidates.append((incumbent_result, False))
    def candidate_cost(pair):
        value = (incumbent_lifts if pair[0].plan is getattr(incumbent_result, 'plan', None)
                 else details.get('lifts'))
        # Attribution can order checks, but cannot admit a plan. Avoid an
        # expensive full check merely to sort, then checking it again to release.
        return float(value) if isinstance(value, (float, int)) and math.isfinite(value) else math.inf
    candidates.sort(key=candidate_cost)
    for raw in _relaxed_plans(details):
        try:
            plan = raw if isinstance(raw, MovePlan) else MovePlan.model_validate(raw)
            candidates.append((PlannerResult(status='dead_end', plan=plan, planner=NAME,
                                             planner_version=VERSION, seed=seed), True))
        except (ValueError, TypeError) as error:
            evidence.setdefault('rejected_candidates', []).append(
                dict(reason='invalid_relaxed_plan', error=f'{type(error).__name__}: {error}'))
    for candidate, relaxed in candidates:
        errors = _audit_rules(parts, evidence['rules'], candidate.plan)
        try:
            report = check(instance, candidate.plan)
            evidence['checker'] = report.model_dump(mode='json')
            if report.count is None:
                raise ValueError('native checker could not replay/count this plan')
            row = inspect(instance, candidate.plan, report)
            require_clean(instance, candidate.plan, report, what='instruction plan release')
            if active_stubs():
                raise StubTaintError('active checker stubs are not a full native checker')
        except Exception as error:
            evidence.setdefault('rejected_candidates', []).append(
                dict(reason='original_instance_checker', error=f'{type(error).__name__}: {error}'))
            continue
        _set_verdict(evidence, report, row)
        if errors:
            evidence['instruction_recheck_passed'] = False
            evidence.setdefault('rejected_candidates', []).append(dict(reason='instruction_recheck', violations=errors))
            continue
        if report.passed and row['level'] == 0 and row['completed']:
            candidate = candidate.model_copy(update={'status': 'ok'})
            candidate._record_full_check(instance, report)
            evidence.update(status='checked_plan', passed=True, plan_released=True)
            if candidate.plan is getattr(incumbent_result, 'plan', None) and candidate.plan is not getattr(result, 'plan', None):
                evidence.update(lane='checked_incumbent', stage_attribution='native_full_checker_observer')
            elif evidence['lane'] is None:
                evidence['lane'] = 'native_recheck' if relaxed else 'checked_incumbent'
            return done(candidate)
        if relaxed and row['level'] is not None and row['level'] > 0:
            evidence.update(status='relaxed_plan', passed=False, plan_released=True,
                            lane='peel_ladder', reason='instruction-compliant tier relaxation; see broken_rules')
            return done(candidate)
    evidence.update(status='no_checked_plan', reason='no original-instance checked plan obeying every confirmed instruction was found')
    return done()


def execute_brief(instance, receipt, *, budget_s=None, finalize_s=None, seed=0,
                  config=None, on_incumbent=None):
    """Require authenticated host confirmation, then rebuild its exact reading.

    Ordinary ReadBrief receipts intentionally fail loudly; use confirm_brief
    only at the host's explicit approval boundary, never implicitly here.
    """
    # public_brief's Frontier 0.9 import defines functions only: no installation
    # of hooks, planning or model loading occurs. Never invoke its old driver.
    from experiments.mvp.reader import public_brief
    from .confirmation import verify_confirmation
    began = perf_counter()
    confirmation = verify_confirmation(receipt)
    parsed = public_brief._verify(instance, receipt)
    unresolved = [i for i, row in enumerate(parsed['sentences'])
                  if row['role'] == 'voyage' and row['status'] != 'accepted']
    unresolved_standing = [i for i, row in enumerate(parsed['sentences'])
        if row['role'] == 'standing' and row['sentence'].strip()
        and row['status'] != 'preference_enabled']
    if unresolved or unresolved_standing:
        evidence = _evidence(budget_s, finalize_s)
        evidence.update(status='needs_confirmation',
            unresolved_sentence_indices=sorted(unresolved + unresolved_standing),
            unresolved_voyage_sentence_indices=unresolved,
            unresolved_standing_sentence_indices=unresolved_standing,
            reason=('Correct or explicitly remove unsupported standing text, or move it to voyage instructions and confirm a new reading.'
                if unresolved_standing else 'Correct or explicitly remove each unresolved voyage sentence, then submit a new authenticated brief.'),
            requires_reading_resolution=True, release_condition='explicit_sentence_correction_or_removal',
            reading=parsed, receipt_sha256=hashlib.sha256(receipt.payload.encode()).hexdigest(),
            confirmation=confirmation, confirmation_source=confirmation['source'],
            confirmation_sha256=confirmation['confirmation_sha256'],
            wall_s=perf_counter()-began)
        return None, evidence
    context = context_from_instance(instance)
    rows = [row for row in parsed['sentences'] if row['role'] == 'voyage' and row['status'] == 'accepted']
    answers = [validate_answer(public_brief._json(row['parsed']), context) for row in rows]
    if on_incumbent is not None and not callable(on_incumbent):
        raise TypeError('on_incumbent must be callable')
    def confirmed_incumbent(event):
        on_incumbent(dict(event, confirmation=dict(confirmation),
            confirmation_source=confirmation['source'],
            confirmation_sha256=confirmation['confirmation_sha256'],
            receipt_sha256=confirmation['receipt_sha256']))
    result, evidence = plan_with_rules(instance, answers, budget_s=budget_s,
        finalize_s=finalize_s, seed=seed, config=config,
        on_incumbent=confirmed_incumbent if on_incumbent is not None else None)
    evidence.update(reading=parsed, receipt_sha256=hashlib.sha256(receipt.payload.encode()).hexdigest(),
                    confirmation=confirmation, confirmation_source=confirmation['source'],
                    confirmation_sha256=confirmation['confirmation_sha256'],
                    standing_is_preference=True, wall_s=perf_counter()-began)
    for source, rule in zip(rows, evidence['rules']):
        rule.update(source_sentence=source['sentence'], parser=source['parser'], binding=parsed['binding'])
    return result, evidence
