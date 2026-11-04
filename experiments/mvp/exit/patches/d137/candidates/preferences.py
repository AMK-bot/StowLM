"""Conservative preference admission and CPU replay for outcome training.

A failed *attempt to verify* is not proof a plan is invalid (a solver may time
out). Admit only explicit objective evidence. Dead-end prefixes have no legal
termination token: their emitted actions can be scored, never a fabricated EOS.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

from llm.exit.outcomes import checker_unavailable


def layer_identity():
    """Bind pairs to the current production, securing and lookahead source."""
    root = Path(__file__).resolve().parents[1]
    paths = [root/'stowlm'/name for name in ('mask.py','mask_mlx.py','format.py','format_prompt.py','sft_data.py')]
    for name in ('securing', 'lookahead'):
        paths.extend(sorted((root / name).rglob('*.py')))
    return {'layers': ['production', 'lookahead', 'securing'], 'sha256': {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths}}


def _load(path):
    return json.loads(Path(path).read_text())


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def definitive_checker_failure(report):
    """A hard plan violation is evidence; a failed calculation is uncertainty.

    ``check`` deliberately marks infrastructure errors as hard ``checker.*``
    violations so they block shipping. That conservative shipping verdict must
    not become a preference label teaching the model that a timeout is bad cargo.
    APPROXIMATION: discard mixed reports too, even if another part found a real
    violation. Fewer preference pairs are preferable to uncertain outcome labels.
    Content/version binding is additionally required by the admission caller.
    """
    if checker_unavailable(report):
        return False
    from stowage.model import CheckReport
    try:
        report = report if isinstance(report, CheckReport) else CheckReport.model_validate(report)
    except (ValueError, TypeError):
        return False
    expected_parts = {'moves', 'rules', 'voyage', 'stability', 'strength',
                      'visibility', 'lashing', 'constraints'}
    if report.passed is not False or not expected_parts.issubset(report.parts):
        return False
    if any(not part.ran or part.error for part in report.parts.values()):
        return False
    violations = [*report.violations,
                  *(violation for part in report.parts.values() for violation in part.violations)]
    if any(violation.rule_id.startswith('checker.') for violation in violations):
        return False
    return any(violation.severity == 'hard' for violation in violations)


def build_preference_pairs(selection, *, samples=(), layers=None, max_pairs=4,
                           prefer_model_costlier_only=False):
    """Read a persisted ``select_job`` artifact and return JSON-ready pairs.

    ``samples`` retains incomplete generation rows with ``token_ids``, ``status``
    and a globally unique ``candidate_id``. These can provide known dead ends.
    D137 sets prefer_model_costlier_only=True: own fully checked costlier plans
    come first, then own dead ends. Unchecked, teacher, tied, and unavailable
    plans never become these new pairs. The default preserves legacy admission
    so historical re-verification/replay artifacts retain their original meaning.
    """
    if max_pairs < 0:
        raise ValueError('max_pairs must be nonnegative')
    if (checker_unavailable(selection) or not selection.get('passed')
            or not selection.get('winner_record')):
        return []
    record = _load(selection['winner_record'])
    if checker_unavailable(record) or record.get('report', {}).get('passed') is not True:
        raise ValueError('chosen record lacks full checker pass')
    winner_checks = [v for v in selection.get('verifications', ())
                     if v.get('stage') == 'verify' and v.get('passed') is True]
    if not winner_checks:
        raise ValueError('chosen lacks current layered mask pass')
    winner_check = next((v for v in winner_checks if v.get('selected_winner') is True), winner_checks[0])
    if checker_unavailable(winner_check):
        raise ValueError('chosen full checker unavailable')
    if winner_check.get('mask', {}).get('passed') is not True:
        raise ValueError('chosen lacks current layered mask pass')
    score = selection.get('weighted_moves')
    if not _finite(score):
        raise ValueError('chosen lacks measured objective')
    instance_path = str(record['instance_path'])
    if Path(instance_path).resolve() != Path(selection['instance_path']).resolve():
        raise ValueError('chosen instance mismatch')
    checks = selection.get('verifications', ())
    candidates = list(selection.get('candidates', ())) + list(samples)
    pairs, seen = [], set()
    for candidate in candidates:
        from llm.exit.pruning import exclude_from_preferences
        if exclude_from_preferences(candidate):
            continue
        if prefer_model_costlier_only and candidate.get('source', 'model') != 'model':
            continue
        cid = candidate.get('candidate_id')
        if not cid or cid == winner_check.get('candidate_id') or cid in seen:
            continue
        seen.add(cid)
        metadata = candidate.get('metadata', candidate)
        status = metadata.get('status', candidate.get('status'))
        tokens = metadata.get('token_ids', candidate.get('token_ids'))
        rows = [v for v in checks if v.get('candidate_id') == cid]
        # D124: expiry is latched uncertainty, even with completed violations,
        # more-moves metadata, or a mask failure from the interrupted check.
        if (checker_unavailable(candidate) or checker_unavailable(metadata)
                or any(checker_unavailable(row) for row in rows)):
            continue
        if prefer_model_costlier_only:
            evidence = None
            priority = 2
            if status == 'dead_end':
                evidence, priority = {'kind': 'dead_end', 'status': status}, 1
            cost = metadata.get('weighted_moves')
            if status != 'dead_end' and _finite(cost) and cost > score:
                verified = next((check for check in rows
                    if check.get('stage') == 'verify' and check.get('passed') is True
                    and check.get('mask', {}).get('passed') is True
                    and check.get('report', {}).get('passed') is True
                    and check.get('weighted_moves') == cost), None)
                if verified is not None:
                    evidence, priority = {'kind': 'strictly_more_moves', 'chosen_moves': score,
                        'rejected_moves': cost, 'legal_move_replay': True,
                        'verification': verified, 'fully_checked': True}, 0
        else:
            move_failed = any(v.get('stage') == 'move_score' and v.get('passed') is False for v in rows)
            evidence = None
            priority = 3
            if status == 'dead_end':
                evidence, priority = {'kind': 'dead_end', 'status': status}, 2
            for check in rows:
                if status == 'dead_end':
                    break
                if check.get('stage') != 'verify' or check.get('passed') is not False:
                    continue
                if definitive_checker_failure(check.get('report')):
                    evidence, priority = {'kind': 'checker_failure', 'verification': check}, 1
                    break
                if check.get('mask', {}).get('passed') is False:
                    evidence, priority = {'kind': 'mask_failure', 'verification': check}, 1
                    break
            cost = metadata.get('weighted_moves')
            # D110: prefer complete, measured objective comparisons over failures.
            if status != 'dead_end' and not move_failed and _finite(cost) and cost > score:
                evidence, priority = {'kind': 'strictly_more_moves', 'chosen_moves': score,
                                      'rejected_moves': cost, 'legal_move_replay': True}, 0
        if evidence is None:
            continue
        rejected = {'candidate_id': cid, 'source': candidate.get('source', 'model'), 'status': status}
        if tokens is not None:
            if not tokens:  # no emitted action -> no likelihood or useful gradient
                continue
            rejected['token_ids'] = list(tokens)
        if candidate.get('plan_path'):
            rejected['plan'] = _load(candidate['plan_path'])
        elif candidate.get('plan') is not None:
            rejected['plan'] = candidate['plan']
        elif tokens is None:
            continue
        pairs.append((priority, {'instance_path': instance_path, 'chosen': {'plan': record['plan'],
            'candidate_id': winner_check.get('candidate_id'), 'winner_record': selection['winner_record']},
            'rejected': rejected, 'evidence': evidence, 'layers': layers or layer_identity()}))
    pairs.sort(key=lambda row: row[0])
    return [pair for _, pair in pairs[:max_pairs]]


def replay_tokens(decoder, tokens, *, end_plan_tokens=(), require_complete=False):
    """CPU-only support check; exclude terminal tokens from the action loss.

    Never append a stop token to a dead end: its allowed set is empty, so that
    event has no finite masked likelihood. Partial legal action tokens remain
    scoreable. Zero-length action prefixes are refused.
    """
    tokens = list(tokens)
    sets = []
    action_ends = []
    for index, token in enumerate(tokens):
        if not isinstance(token, int) or isinstance(token, bool) or token < 0:
            raise ValueError('invalid token id')
        allowed = list(decoder.allowed_token_ids())
        if token not in allowed:
            raise ValueError(f'token {index} outside current layered mask support')
        sets.append(allowed)
        action = decoder.advance(token)
        if action is not None:
            action_ends.append(index+1)
    # Query once more so a prefix that reached an unsatisfiable state is labelled.
    decoder.allowed_token_ids()
    if require_complete and decoder.status != 'complete':
        raise ValueError('chosen replay is not complete')
    n_action = len(tokens)
    end = list(end_plan_tokens)
    if decoder.status == 'complete' and end and tokens[-len(end):] == end:
        n_action -= len(end)
    if n_action == 0:
        raise ValueError('no action tokens to score')
    loss_start = 0
    if decoder.status == 'dead_end':
        # The last completed action caused the dead end if the trace ends at its
        # boundary. Otherwise only the unfinished action prefix is blamed. All
        # earlier actions remain context, without negative gradient on them.
        if action_ends and action_ends[-1] == len(tokens):
            loss_start = action_ends[-2] if len(action_ends) > 1 else 0
        elif action_ends:
            loss_start = action_ends[-1]
    result = {'token_ids': tokens, 'allowed_sets': sets,
              'action_mask': [loss_start <= i < n_action for i in range(len(tokens))],
              'status': decoder.status, 'decision_tokens': sum(
                  len(s) > 1 for s in sets[loss_start:n_action])}
    if decoder.status == 'dead_end':
        result['dead_end_loss_start'] = loss_start
    return result


def prepare_masked_sequence(instance, *, tokens=None, plan=None, tokenizer=None,
                            require_complete=False, decoder_factory=None):
    """Canonicalize a plan (or a retained prefix) with current production layers."""
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.sft_data import codec_for, plan_actions
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.model import MovePlan
    if (tokens is None) == (plan is None):
        raise ValueError('provide exactly one of tokens or plan')
    tokenizer = tokenizer or load_tokenizer()
    codec = codec_for(instance, tokenizer)
    if plan is not None:
        plan = MovePlan.model_validate(plan) if isinstance(plan, dict) else plan
        tokens = codec.encode_plan(plan_actions(instance, plan))
    if decoder_factory is None:
        from llm.exit.selection import decoder_factory
    engine = make_feasibility_engine(instance, get_action_space(),
                                    config=FeasibilityConfig(extra={'global_screen': 0}))
    decoder = decoder_factory()(instance, engine, codec, None, 1)[0]
    return {'prompt': list(encode_prompt(instance, tokenizer)), **replay_tokens(decoder, tokens,
        end_plan_tokens=codec.end_plan_tokens, require_complete=require_complete)}


def write_pairs(path, pairs):
    """Atomically publish JSONL pairs, with no NaN or Infinity evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(''.join(json.dumps(p, allow_nan=False) + '\n' for p in pairs))
    temporary.replace(path)


def prepare_training_pairs(selection, *, samples=(), max_pairs=2, corpus_root=None,
                           prefer_model_costlier_only=False):
    """Admit checked chosen plans and replay both sides into DPO-ready traces.

    Failures are recorded as refusals; they never silently become training data.
    No new checker runs are needed: full checker evidence is content-bound and
    every action token is replayed under the current production layers.
    """
    from llm.exit.identity import DEFAULT_CORPUS, identity_guard
    from llm.exit.selection import Candidate, score_candidate
    from stowage.checker import CHECKER_VERSION, report_matches_inputs
    from stowage.frozen import require_clean
    from stowage.io import load_instance
    from stowage.model import CheckReport, MovePlan
    from llm.exit.dpo import pair_identity, validate_pair

    started = perf_counter()
    if max_pairs < 0:
        raise ValueError('max_pairs must be nonnegative')
    result = {'pairs': [], 'refusals': []}
    def refuse(error, candidate_id=None):
        result['refusals'].append({'candidate_id': candidate_id, 'reason': str(error)})
    try:
        ipath = Path(selection['instance_path'])
        instance = load_instance(ipath)
        guard = identity_guard(corpus_root or DEFAULT_CORPUS)
        canonical = guard.require_allowed(instance, role='train', input_path=ipath,
            raw_sha=hashlib.sha256(ipath.read_bytes()).hexdigest())
        if not selection.get('passed'):
            return {**result, 'wall_s': perf_counter() - started}
        record = _load(selection['winner_record'])
        chosen_plan = MovePlan.model_validate(record['plan'])
        report = CheckReport.model_validate(record['report'])
        require_clean(instance, chosen_plan, report, what='ExIt preference chosen')
        if (report.passed is not True or not report_matches_inputs(report, instance, chosen_plan)
                or report.provenance.versions.get('checker') != CHECKER_VERSION):
            raise ValueError('chosen checker pass lacks current version/content binding')
        chosen_cost = score_candidate(instance, Candidate(chosen_plan, 'preference'))
        if chosen_cost != selection.get('weighted_moves'):
            raise ValueError('chosen objective disagrees with legal move replay')
        chosen_trace = prepare_masked_sequence(instance, plan=chosen_plan, require_complete=True)
        # Fetch enough raw pairs so an unsupported rejection cannot hide a later
        # valid example. The bound only limits emitted training pairs.
        raw_pairs = build_preference_pairs(selection, samples=samples,
            max_pairs=len(selection.get('candidates', ())) + len(samples),
            prefer_model_costlier_only=prefer_model_costlier_only)
        for raw in raw_pairs:
            if len(result['pairs']) >= max_pairs:
                break
            reject = raw['rejected']
            try:
                if raw['evidence']['kind'] == 'strictly_more_moves':
                    if 'plan' not in reject:
                        raise ValueError('move preference needs a complete plan for legal replay')
                    rejected_plan = MovePlan.model_validate(reject['plan'])
                    rejected_cost = score_candidate(instance, Candidate(rejected_plan, 'preference'))
                    if prefer_model_costlier_only:
                        proof = raw['evidence'].get('verification', {})
                        rejected_report = CheckReport.model_validate(proof['report'])
                        require_clean(instance, rejected_plan, rejected_report,
                            what='D137 fully checked costlier model rejection')
                        if (not rejected_report.passed
                                or rejected_report.provenance.versions.get('checker') != CHECKER_VERSION
                                or not report_matches_inputs(rejected_report, instance, rejected_plan)
                                or proof.get('mask', {}).get('passed') is not True):
                            raise ValueError('costlier model rejection lacks content-bound current full pass')
                        if (rejected_report.count is None
                                or rejected_report.count.weighted_nonproductive != rejected_cost):
                            raise ValueError('costlier rejection checker/move objective differs')
                    if rejected_cost <= chosen_cost:
                        raise ValueError('rejected plan is not strictly more moves after legal replay')
                    raw['evidence']['rejected_moves'] = rejected_cost
                if prefer_model_costlier_only and raw['evidence']['kind'] == 'strictly_more_moves':
                    # The proof binds this plan, not arbitrary retained decode
                    # token metadata. Canonicalize precisely the checked plan.
                    trace = prepare_masked_sequence(instance, plan=rejected_plan, require_complete=True)
                    reject = dict(reject)
                    reject.pop('token_ids', None)
                else:
                    trace = prepare_masked_sequence(instance,
                        tokens=reject.get('token_ids'), plan=None if 'token_ids' in reject else reject.get('plan'))
                if raw['evidence']['kind'] == 'dead_end' and trace['status'] != 'dead_end':
                    raise ValueError('retained prefix does not replay to an actual dead end')
                pair = {**raw, 'chosen_verified': True, 'instance_sha256': canonical,
                        'chosen': {**chosen_trace, 'tokens': chosen_trace['token_ids']},
                        'rejected': {**trace, 'tokens': trace['token_ids']},
                        'rejected_witness': reject,
                        'chosen_witness': {'winner_record': selection['winner_record'],
                                           'plan': record['plan'], 'report': record['report']}}
                if trace['status'] != 'complete':
                    # APPROXIMATION: a dead-end action prefix is a partial-sequence
                    # surrogate; unequal lengths can bias preference likelihoods.
                    pair['approximation'] = 'partial-sequence rejected prefix; summed action likelihood has length bias'
                validate_pair(pair)
                pair['pair_id'] = pair_identity(pair)
                result['pairs'].append(pair)
            except (ValueError, TypeError, KeyError) as error:
                refuse(error, reject.get('candidate_id'))
    except (ValueError, TypeError, KeyError, OSError) as error:
        refuse(error)
    return {**result, 'wall_s': perf_counter() - started}
