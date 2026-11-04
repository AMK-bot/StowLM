"""Outcome selection: actual D11 move counts rank; fresh gates decide validity.

No historical ``passed`` flag admits a training target. All candidates, including
saved gold and LNS output, must pass the same current gates. CPU workers should
be processes: the reused gold ballast scope temporarily changes an environment
variable and is intentionally not thread-safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import hashlib
import os
from pathlib import Path
from functools import lru_cache
import importlib.util
from time import monotonic
from typing import Any, Callable

from stowage.model import MovePlan, PlannerResult


@dataclass
class Candidate:
    plan: MovePlan
    source: str
    candidate_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verification:
    passed: bool
    plan: MovePlan | None = None
    weighted_moves: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectionResult:
    winner: Candidate | None
    candidates: list[Candidate]
    verifications: list[dict[str, Any]]
    timings: dict[str, float]
    weighted_moves: float | None = None


@lru_cache(maxsize=1)
def _gold_driver():
    """Load repository experiment helpers without requiring experiments on sys.path."""
    path = Path(__file__).resolve().parents[3] / "experiments/mvp/gold_tools/driver.py"
    spec = importlib.util.spec_from_file_location("_exit_gold_driver", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decoder_factory():
    """Exactly the production securing layer followed by necessary lookahead."""
    from llm.lookahead import wrap_engine as lookahead_wrap
    from llm.securing import securing_decoder_factory
    from llm.exit.securing_guard import guarded_factory
    # Engine order is production -> lookahead -> securing. Both layers expose
    # engine_wrapper composition and retain independent per-sample state.
    return guarded_factory(securing_decoder_factory(engine_wrapper=lookahead_wrap))


def mask_replay(instance, plan, details=None):
    """Token replay using the same factory used for generation and SFT admission."""
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig, MASK_VERSION
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    details = {} if details is None else details
    space = get_action_space()
    actions = list(space.from_plan(instance, plan))
    engine = make_feasibility_engine(instance, space,
        config=FeasibilityConfig(extra={"global_screen": 0}))
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    factory = decoder_factory()
    decoder = factory(instance, engine, codec, MaskConfig(), 1)[0]
    details.update(mask_version=MASK_VERSION, decoder_backend=factory.name,
                   securing=True, lookahead=True, actions_expected=len(actions), actions_completed=0)
    for index, action in enumerate(actions):
        for token in codec.encode_action(action):
            decoder.advance(token)
        details['actions_completed'] = index + 1
    for token in codec.end_plan_tokens:
        decoder.advance(token)
    from llm.exit.securing_guard import securing_metrics
    details['securing_metrics'] = securing_metrics([decoder])
    details.update(status=decoder.status, actions_equal=decoder.actions == actions,
                   tokens=len(decoder.tokens),
                   passed=decoder.status == 'complete' and decoder.actions == actions)
    return details


def score_candidate(instance, candidate: Candidate) -> float:
    from stowage.moves import apply_plan
    replay = apply_plan(instance, candidate.plan)
    if not replay.legal:
        raise ValueError('illegal move replay')
    value = float(replay.count.weighted_nonproductive)
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid measured move count')
    return value


def verify_candidate(instance, candidate: Candidate, *, ballast_s: float = 90.,
                     check_timeout_s: float = 300., expected_outcome_epoch=None) -> Verification:
    """Scoped V4 finisher, unchanged full checker, and complete token mask replay."""
    scoped_ballast_v4 = _gold_driver().scoped_ballast_v4
    from stowage.frozen import require_clean
    from stowage.planner import finalize
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    details: dict[str, Any] = {'timings': {}}
    started = monotonic()
    try:
        if os.environ.get('STOWAGE_BALLAST_V4') not in (None, '', '0'):
            raise ValueError('STOWAGE_BALLAST_V4 must be unset outside the scoped finisher')
        if rule_freeze_v1_enabled():
            raise ValueError('Expert Iteration requires the demo rule-freeze switch OFF')
        if instance.plan_calls:
            raise ValueError('later-call plans are outside MVP Expert Iteration scope')
        require_clean(instance, candidate.plan, what='Expert Iteration candidate')
        result = PlannerResult(status='ok', plan=candidate.plan, planner='exit.' + candidate.source,
                               planner_version='1', seed=instance.seed,
                               provenance=candidate.plan.provenance)
        phase = monotonic()
        with scoped_ballast_v4(True):
            result = finalize(instance, result, time_limit_s=ballast_s)
        details['timings']['ballast_s'] = monotonic() - phase
        details['finisher'] = dict(status=result.status, ballast_status=result.metrics.get('ballast_status'),
            not_found_cause=result.metrics.get('not_found_cause'), budget_s=ballast_s)
        from llm.exit.finisher_outcomes import finisher_timed_out
        details['finisher_timeout'] = finisher_timed_out(details['finisher'])
        if result.status != 'ok' or result.plan is None:
            raise ValueError('ballast finisher: ' + str(result.message or result.status))
        phase = monotonic()
        from llm.exit.checker_watchdog import run_full_check
        try:
            epoch_options=({'expected_outcome_epoch':expected_outcome_epoch}
                           if expected_outcome_epoch is not None else {})
            checked = run_full_check(instance, result.plan, timeout_s=check_timeout_s, **epoch_options)
        except Exception as error:
            details.update(checker_available=False, unavailable=True,
                unavailable_reason='checker_process_or_transport_error',
                checker_watchdog=dict(expired=False, deadline_s=check_timeout_s,
                    error=f'{type(error).__name__}: {error}'))
            details['timings']['checker_s'] = monotonic() - phase
            return Verification(False, details=details)
        details['timings']['checker_s'] = monotonic() - phase
        details['checker_watchdog'] = dict(checked.details(), deadline_s=check_timeout_s,
            expired=bool(checked.reason and checked.reason.startswith('watchdog_')))
        details['checker_available'] = checked.available
        if not checked.available:
            # APPROXIMATION: a 300-second operational deadline is availability,
            # never a physical verdict or a preference-learning rejection.
            details.update(unavailable=True, unavailable_reason=checked.reason)
            return Verification(False, details=details)
        report = checked.report
        details['report'] = report.model_dump(mode='json')
        details['checker_passed'] = report.passed
        details['violations'] = [v.model_dump(mode='json') for v in report.violations]
        result._record_full_check(instance, report)
        if not result.shippable:
            raise ValueError('full checker or provenance shipping gate failed')
        phase = monotonic()
        details['mask'] = mask_replay(instance, result.plan)
        details['timings']['mask_s'] = monotonic() - phase
        if not details['mask']['passed']:
            raise ValueError('production layered mask replay failed')
        moves = score_candidate(instance, Candidate(result.plan, candidate.source))
        if report.count is None or moves != report.count.weighted_nonproductive:
            raise ValueError('checker and move-engine counts disagree')
        require_clean(instance, result, report, what='Expert Iteration winner')
        return Verification(True, result.plan, moves, details)
    except Exception as error:
        details['error'] = f'{type(error).__name__}: {error}'
        return Verification(False, details=details)
    finally:
        details['timings']['total_s'] = monotonic() - started


def load_gold_candidate(input_path, corpus_root, gold_root) -> Candidate | None:
    """Locate only the corresponding train/val gold artifact, without trusting flags."""
    from stowage.io import load_plan
    relative = Path(input_path).resolve().relative_to(Path(corpus_root).resolve())
    if relative.parts[0] not in ('train', 'val'):
        raise ValueError('test/showcase gold is forbidden')
    path = Path(gold_root) / relative.with_suffix('') / 'plan.json'
    if not path.is_file():
        return None
    return Candidate(load_plan(path), 'gold', 'gold', {'path': str(path)})


def baseline_candidate(instance, *, time_limit_s=.3, seed=None) -> Candidate | None:
    """D110 two-step v4.4 proposal for a map without stored gold.

    Match ``gold_warmstart.solve_gold``'s C2-off planner setup, but defer its
    ballast/checker/mask work to lazy selection. This is a *baseline proposal*,
    never gold or an admitted target. No model inference is involved.
    """
    from stowage.feasibility import FeasibilityConfig
    from stowage.frozen import require_clean
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver.twostep import TwoStepPlanner
    if not math.isfinite(time_limit_s) or time_limit_s <= 0:
        raise ValueError('baseline planning budget must be finite and positive')
    if instance.plan_calls or rule_freeze_v1_enabled():
        raise ValueError('baseline supports current-call demo rules with freeze off only')
    require_clean(instance, what='Expert Iteration two-step baseline input')
    planner = TwoStepPlanner(feasibility_config=FeasibilityConfig(extra={'global_screen':0}))
    if planner.version != '4.4.0':
        raise ValueError(f'D110 requires frozen two-step 4.4.0, found {planner.version}')
    def source_identity():
        files = {}
        for name in ('twostep','twostep_adapter','twostep_terminal','twostep_construct'):
            spec = importlib.util.find_spec('stowage.solver.' + name)
            if spec is None or spec.origin is None:
                raise ValueError(f'baseline source unavailable: {name}')
            files['src/stowage/solver/' + name + '.py'] = hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
        return files
    files = source_identity()
    # Bind the actually loaded entry-point bytecode as well as files: another
    # executive may edit a module after this long-lived worker imported it.
    import marshal
    implementation_sha = hashlib.sha256(marshal.dumps(type(planner).plan.__code__)).hexdigest()
    seed = instance.seed if seed is None else seed
    # APPROXIMATION: 0.3-second bounded two-step search may find no plan. Full
    # checker validity is never approximated; indivisible oracle overruns are logged.
    started = monotonic()
    result = planner.plan(instance, seed=seed, time_limit_s=time_limit_s)
    elapsed = monotonic()-started
    if result.status != 'ok' or result.plan is None:
        return None
    require_clean(instance, result, result.plan, what='Expert Iteration two-step baseline proposal')
    if source_identity() != files:
        raise ValueError('baseline source changed during planning; refuse ambiguous provenance')
    plan = result.plan.model_copy(update={'provenance':result.plan.provenance.merge(result.provenance)})
    return Candidate(plan, 'baseline_twostep', 'baseline-twostep-v4.4', {
        'label':'two-step v4.4 baseline (0.3 s)' if time_limit_s == .3 else f'two-step v4.4 baseline ({time_limit_s:g} s)',
        'planner':result.planner, 'planner_version':result.planner_version,
        'model':None, 'model_inference':False, 'source_sha256':files,
        'planner_implementation_sha256':implementation_sha,
        'provenance':plan.provenance.model_dump(mode='json'),
        'planning_budget_s':time_limit_s, 'planning_wall_s':elapsed,
        'planning_budget_overrun_s':max(0.,elapsed-time_limit_s),
        'planner_metrics':result.metrics, 'requires_current_verification':True})


def polish_candidate(instance, candidate: Candidate, *, time_limit_s=15., ballast_s=90.) -> Candidate | None:
    """LNS starts at a freshly verified, finalized winner; its output is gated again."""
    from stowage.solver.gold_warmstart import improve_checked
    if candidate.metadata.get('exit_verified') is not True:
        raise ValueError('LNS requires the freshly verified selection incumbent')
    result = PlannerResult(status='ok', plan=candidate.plan, planner='exit.' + candidate.source,
                           planner_version='1', seed=instance.seed,
                           provenance=candidate.plan.provenance)
    # Verification already finalized ballast. Re-finalizing here wastes the
    # finisher budget and could change the incumbent before local search.
    result = improve_checked(instance, result, time_limit_s=time_limit_s, seed=instance.seed)
    if result.status != 'ok' or result.plan is None:
        return None
    return Candidate(result.plan, 'polished', 'polished-' + candidate.candidate_id,
                     {'parent_source': candidate.source, 'parent_id': candidate.candidate_id,
                      'lns_metrics': result.metrics})


def select_winner(instance, candidates, *, polish_top=1, polish_s=15., ballast_s=90.,
                  verifier: Callable | None = None, scorer: Callable | None = None,
                  polisher: Callable | None = None) -> SelectionResult:
    """Rank cheaply, verify lazily, then polish the best verified winner once.

    APPROXIMATION: bounded 15-second LNS and best-of-k sampling find a best-known
    winner; neither search claims optimality. Equal moves retain input order.
    ``polish_top=2`` is kept for CLI compatibility and uses the same single best
    verified slot as 1. Invalid cheaper proposals never consume that slot.
    """
    if polish_top not in (0, 1, 2):
        raise ValueError('polish_top must be 0, 1, or 2')
    started = monotonic()
    verifier = verifier or (lambda i, c: verify_candidate(i, c, ballast_s=ballast_s))
    scorer = scorer or score_candidate
    polisher = polisher or (lambda i, c: polish_candidate(i, c, time_limit_s=polish_s, ballast_s=ballast_s))
    candidates = list(candidates)
    records, ranked = [], []
    rank_s = polish_elapsed = verify_elapsed = 0.

    def rank(candidate):
        nonlocal rank_s
        phase = monotonic()
        try:
            value = float(scorer(instance, candidate))
            if not math.isfinite(value) or value < 0:
                raise ValueError('invalid measured score')
            candidate.metadata['weighted_moves'] = value
            ranked.append((value, len(ranked), candidate))
            return value
        except Exception as error:
            records.append({'candidate_id': candidate.candidate_id, 'source': candidate.source,
                            'stage': 'move_score', 'passed': False, 'error': str(error)})
            return None
        finally:
            rank_s += monotonic()-phase

    def verify(candidate, expected_score):
        nonlocal verify_elapsed
        phase = monotonic()
        try:
            verification = verifier(instance, candidate)
        except Exception as error:
            verification = Verification(False, details={'error':f'{type(error).__name__}: {error}'})
        finally:
            verify_elapsed += monotonic()-phase
        record = {'candidate_id': candidate.candidate_id, 'source': candidate.source,
                  **verification.details, 'stage':'verify', 'passed':verification.passed,
                  'weighted_moves':verification.weighted_moves}
        records.append(record)
        if not verification.passed:
            return None, None
        if verification.plan is None or verification.weighted_moves != expected_score:
            raise ValueError('verification changed the ranked move objective')
        metadata = dict(candidate.metadata, exit_verified=True,
                        verified_weighted_moves=verification.weighted_moves)
        return Candidate(verification.plan, candidate.source, candidate.candidate_id, metadata), record

    for candidate in candidates:
        rank(candidate)
    winner, winner_score, winner_record = None, None, None
    for value, _, candidate in sorted(ranked, key=lambda row:row[:2]):
        checked, record = verify(candidate, value)
        if checked is not None:
            winner, winner_score, winner_record = checked, value, record
            break
    if winner is not None and polish_top:
        phase = monotonic()
        try:
            polished = polisher(instance, winner)
        except Exception as error:
            polished = None
            records.append({'candidate_id':winner.candidate_id, 'source':winner.source,
                            'stage':'polish', 'passed':False, 'error':str(error)})
        polish_elapsed += monotonic()-phase
        if polished is not None:
            candidates.append(polished)
            value = rank(polished)
            if value is not None and value < winner_score:
                try:
                    checked, record = verify(polished, value)
                    if checked is not None:
                        winner, winner_score, winner_record = checked, value, record
                except ValueError as error:
                    records.append({'candidate_id':polished.candidate_id, 'source':polished.source,
                                    'stage':'polish', 'passed':False, 'error':str(error)})
            elif value is not None:
                records.append({'candidate_id':polished.candidate_id, 'source':polished.source,
                    'stage':'polish', 'passed':False, 'reason':'not_strictly_better',
                    'weighted_moves':value, 'incumbent_weighted_moves':winner_score})
    for record in records:
        if record.get('stage') == 'verify':
            record['selected_winner'] = record is winner_record
    return SelectionResult(winner, candidates, records,
        {'rank_s':rank_s, 'polish_s':polish_elapsed, 'verify_s':verify_elapsed,
         'total_s':monotonic()-started}, winner_score)
