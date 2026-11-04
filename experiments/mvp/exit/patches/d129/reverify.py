"""D129 CPU-only, content-bound training admission after a checker/layer epoch.

Original selection files are immutable evidence. Fresh targets, traces and a
receipt are published separately; no historical pass/failure flag admits a row.
"""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import perf_counter

SCHEMA = 'exit-training-epoch-v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def _read(path):
    return json.loads(Path(path).read_text())


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def _epoch():
    from llm.exit.code_epoch import source_snapshot
    return source_snapshot()


def _services():
    from llm.exit.selection import Candidate, verify_candidate, score_candidate
    from llm.exit.checker_watchdog import run_full_check
    from llm.exit.preferences import (prepare_masked_sequence, definitive_checker_failure,
                                     layer_identity)
    from llm.exit.outcomes import checker_unavailable
    from llm.exit.replay import checker_epoch
    from llm.exit.dpo import validate_pair, pair_identity
    from llm.exit.identity import identity_guard, DEFAULT_CORPUS
    from stowage.io import load_instance
    from stowage.model import MovePlan
    from stowage.frozen import require_clean
    return locals()


def _bound_instance(services, path, corpus_root, inline=(), role='train'):
    path = Path(path).resolve()
    instance = services['load_instance'](path)
    guard = services['identity_guard'](corpus_root or services['DEFAULT_CORPUS'])
    canonical = guard.require_allowed(instance, role=role, input_path=path,
                                     raw_sha=hashlib.sha256(path.read_bytes()).hexdigest())
    for value in inline:
        if value is not None:
            actual = guard.require_allowed(value, role=role, input_path=path)
            if actual != canonical:
                raise ValueError('inline instance differs from source instance')
    return instance, canonical


def _inputs(selections):
    """Snapshot all row and referenced plan bytes before potentially expensive work."""
    rows, files = [], {}
    def include(path):
        path = Path(path).resolve()
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for value in selections:
        if isinstance(value, (str, Path)):
            include(value)
            row = _read(value)
        else:
            row = json.loads(json.dumps(value, allow_nan=False))
        include(row['instance_path'])
        if row.get('winner_record'):
            include(row['winner_record'])
        for candidate in row.get('candidates', ()):
            if candidate.get('plan_path'):
                include(candidate['plan_path'])
        rows.append(row)
    return rows, dict(rows_sha256=_digest(rows), files=files)


def assert_training_receipt(receipt, *, selections, training_epoch, model_identity, _allow_blocked=False):
    """Call immediately before a child update; stale code/records never reuse a pass."""
    receipt = _read(receipt) if isinstance(receipt, (str, Path)) else receipt
    payload = {key: value for key, value in receipt.items() if key != 'receipt_sha256'}
    if receipt.get('schema') != SCHEMA or receipt.get('receipt_sha256') != _digest(payload):
        raise ValueError('training receipt content/schema mismatch')
    if not model_identity or receipt['model_identity'] != model_identity:
        raise ValueError('training receipt model identity mismatch')
    if receipt['training_epoch'] != training_epoch or _epoch() != training_epoch:
        raise ValueError('training receipt source epoch mismatch')
    if receipt['inputs'] != _inputs(selections)[1]:
        raise ValueError('training receipt input identity mismatch')
    for path, digest in receipt['outputs'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('training receipt output identity mismatch')
    if receipt['status'] != 'ready' and not _allow_blocked:
        raise ValueError('training receipt has no verified winners')
    return receipt


def reverify_training_rows(selections, *, output_dir, training_epoch, model_identity,
                           corpus_root=None, ballast_s=90., check_timeout_s=300., workers=1):
    """Rebuild selected targets and DPO proofs under this exact loaded source epoch.

    The caller must use a fresh process after drift. This checks disk epoch at
    entry and exit and never hot-reloads production modules. Every complete
    checker-failure rejection is checked even if its chosen winner is lost.
    APPROXIMATION: unsupported/stale mask-failure rejections are dropped, never
    guessed. No fresh decode or model/GPU import is performed.
    """
    if not isinstance(training_epoch, dict) or not training_epoch or _epoch() != training_epoch:
        raise ValueError('fresh training process does not match requested epoch')
    if not isinstance(model_identity, dict) or not model_identity:
        raise ValueError('content-bound model identity required')
    if type(workers) is not int or not 1<=workers<=4:
        raise ValueError('reverification workers must be an integer from1to4')
    selections=list(selections)
    rows, input_identity = _inputs(selections)
    output_dir = Path(output_dir).resolve()
    receipt_path = output_dir / 'receipt.json'
    if receipt_path.exists():
        receipt = assert_training_receipt(receipt_path, selections=selections,
            training_epoch=training_epoch, model_identity=model_identity, _allow_blocked=True)
        return {**_read(output_dir / 'result.json'), 'receipt':receipt, 'cached':True}
    if workers>1:
        return _reverify_parallel(selections, rows, input_identity, output_dir,
            training_epoch, model_identity, corpus_root, ballast_s, check_timeout_s, workers)
    s = _services()
    started = perf_counter()
    counts = dict(winners_seen=0, winners_kept=0, winner_now_failed=0,
                  rejection_now_passed=0, rejection_still_failed=0,
                  unavailable=0, pairs_seen=0, pairs_kept=0, pairs_dropped=0)
    events, fresh_selections, winner_records, pairs = [], [], [], []
    outputs = []
    for index, old in enumerate(rows):
        case = output_dir / f'case-{index:04d}'
        instance, canonical = _bound_instance(s, old['instance_path'], corpus_root,
                                              inline=(old.get('instance'),))
        fresh = {**old, 'passed':False, 'winner_record':None, 'weighted_moves':None,
                 'preferences':{'pairs':[], 'refusals':[]},
                 'training_epoch':training_epoch, 'verifications':[]}
        chosen_plan, chosen_trace, chosen_report, chosen_score = None, None, None, None
        if old.get('passed'):
            counts['winners_seen'] += 1
            record = _read(old['winner_record'])
            if Path(record['instance_path']).resolve() != Path(old['instance_path']).resolve():
                raise ValueError('winner record instance mismatch')
            _bound_instance(s, old['instance_path'], corpus_root, inline=(record.get('instance'),))
            plan = s['MovePlan'].model_validate(record['plan'])
            verified = s['verify_candidate'](instance, s['Candidate'](plan, old.get('source') or 'reverify'),
                ballast_s=ballast_s, check_timeout_s=check_timeout_s)
            unavailable = s['checker_unavailable'](verified.details)
            admitted = bool(verified.passed and not unavailable and verified.plan is not None
                and verified.details.get('report', {}).get('passed') is True
                and verified.details.get('mask', {}).get('passed') is True)
            fresh['verifications'].append(dict(stage='verify', selected_winner=admitted,
                passed=admitted, **verified.details))
            if unavailable:
                counts['unavailable'] += 1
            if admitted:
                chosen_plan, chosen_report = verified.plan, verified.details['report']
                chosen_score = verified.weighted_moves
                chosen_trace = s['prepare_masked_sequence'](instance, plan=chosen_plan, require_complete=True)
                target = case / 'winner.record.json'
                _write(target, dict(instance_path=str(Path(old['instance_path']).resolve()),
                    plan=chosen_plan.model_dump(mode='json'), report=chosen_report,
                    exit_checker_epoch=s['checker_epoch'](), exit_training_epoch=training_epoch))
                outputs.append(target)
                winner_records.append(str(target))
                fresh.update(passed=True, winner_record=str(target), weighted_moves=chosen_score)
                counts['winners_kept'] += 1
                events.append(dict(kind='winner_kept', instance_path=old['instance_path'],
                    old_moves=old.get('weighted_moves'), new_moves=chosen_score))
            else:
                counts['winner_now_failed'] += 1
                events.append(dict(kind='winner_now_failed', instance_path=old['instance_path'],
                                   verification=verified.details))
        for prior in old.get('preferences', {}).get('pairs', ()):
            counts['pairs_seen'] += 1
            rejected = prior.get('rejected_witness') or prior.get('rejected', {})
            kind = prior.get('evidence', {}).get('kind')
            event = dict(kind='pair_dropped', evidence_kind=kind,
                candidate_id=rejected.get('candidate_id'), instance_path=old['instance_path'])
            try:
                if Path(prior['instance_path']).resolve() != Path(old['instance_path']).resolve():
                    raise ValueError('pair instance mismatch')
                _bound_instance(s, old['instance_path'], corpus_root, inline=(prior.get('instance'),
                    rejected.get('instance'), (prior.get('chosen_witness') or {}).get('instance')))
                proof = None
                rejected_plan = None
                trace = None
                if kind in ('checker_failure', 'strictly_more_moves'):
                    rejected_plan = s['MovePlan'].model_validate(rejected['plan'])
                    s['require_clean'](instance, rejected_plan, what='D129 rejected plan')
                if kind == 'checker_failure':
                    try:
                        checked = s['run_full_check'](instance, rejected_plan, timeout_s=check_timeout_s)
                    except Exception as error:
                        counts['unavailable'] += 1
                        event['checker'] = dict(checker_available=False,
                            reason=f'{type(error).__name__}: {error}')
                        raise ValueError('rejected checker process unavailable') from error
                    event['checker'] = checked.details()
                    if not checked.available:
                        counts['unavailable'] += 1
                        raise ValueError('rejected checker unavailable')
                    report = checked.report.model_dump(mode='json')
                    # Replay even if this map lost its winner or the historical
                    # rejection now passes: both verdict changes are evidence.
                    try:
                        trace = s['prepare_masked_sequence'](instance, plan=rejected_plan)
                        event['current_mask_status'] = trace['status']
                    except (ValueError, TypeError, KeyError) as error:
                        event['current_mask_error'] = f'{type(error).__name__}: {error}'
                    if report['passed']:
                        counts['rejection_now_passed'] += 1
                        event['outcome_change'] = 'old_failure_now_passes'
                        raise ValueError('historical checker rejection now passes')
                    if not s['definitive_checker_failure'](report):
                        raise ValueError('rejected plan lacks a completed current hard violation')
                    counts['rejection_still_failed'] += 1
                    proof = dict(kind='checker_failure', verification={'passed':False, 'report':report})
                    if trace is None:
                        raise ValueError('current checker rejection lacks replayable action support')
                if chosen_plan is None:
                    raise ValueError('chosen winner failed current epoch')
                kwargs = ({'plan':rejected_plan} if rejected_plan is not None
                          else {'tokens':rejected.get('token_ids', prior.get('rejected', {}).get('token_ids'))})
                trace = trace or s['prepare_masked_sequence'](instance, **kwargs)
                if rejected_plan is not None and rejected.get('token_ids') is not None:
                    if trace['token_ids'] != rejected['token_ids']:
                        raise ValueError('rejected plan differs from sampled token witness')
                if kind == 'strictly_more_moves':
                    cost = s['score_candidate'](instance, s['Candidate'](rejected_plan, 'reverify'))
                    if cost <= chosen_score:
                        raise ValueError('rejected plan no longer costs strictly more')
                    proof = dict(kind=kind, chosen_moves=chosen_score, rejected_moves=cost,
                                 legal_move_replay=True)
                elif kind == 'dead_end':
                    if trace['status'] != 'dead_end':
                        event['outcome_change'] = 'old_dead_end_now_supported'
                        raise ValueError('historical dead end is not a current dead end')
                    proof = dict(kind=kind, status='dead_end')
                elif kind != 'checker_failure':
                    raise ValueError('unsupported historical rejection kind')
                pair = {**prior, 'instance_sha256':canonical, 'layers':s['layer_identity'](),
                    'chosen_verified':True, 'evidence':proof,
                    'chosen':{**chosen_trace, 'tokens':chosen_trace['token_ids']},
                    'rejected':{**trace, 'tokens':trace['token_ids']},
                    'chosen_witness':dict(winner_record=fresh['winner_record'],
                        plan=chosen_plan.model_dump(mode='json'), report=chosen_report),
                    'rejected_witness':rejected, 'training_epoch':training_epoch}
                s['validate_pair'](pair)
                pair['pair_id'] = s['pair_identity'](pair)
                pairs.append(pair)
                fresh['preferences']['pairs'].append(pair)
                counts['pairs_kept'] += 1
                event['kind'] = 'pair_kept'
            except (ValueError, TypeError, KeyError, OSError) as error:
                counts['pairs_dropped'] += 1
                event['reason'] = f'{type(error).__name__}: {error}'
                fresh['preferences']['refusals'].append(event)
            events.append(event)
        selection_path = case / 'selection.json'
        _write(selection_path, fresh)
        outputs.append(selection_path)
        fresh_selections.append(fresh)
    if _epoch() != training_epoch or _inputs(selections)[1] != input_identity:
        raise RuntimeError('source or source records changed during training re-verification')
    result = dict(status='ready' if winner_records else 'blocked_no_current_winners',
        selections=fresh_selections, winner_records=winner_records, pairs=pairs,
        counts=counts, events=events, wall_s=perf_counter()-started, cached=False)
    result_path = output_dir / 'result.json'
    _write(result_path, result)
    outputs.append(result_path)
    receipt = dict(schema=SCHEMA, status=result['status'], training_epoch=training_epoch,
        model_identity=model_identity, inputs=input_identity,
        outputs={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs})
    receipt['receipt_sha256'] = _digest(receipt)
    _write(receipt_path, receipt)
    return {**result, 'receipt':receipt}


def _reverify_map_job(payload):
    """Spawn-safe one-map job; no forked MLX state and no nested process pool."""
    index, old, kwargs=payload
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
                'MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    return index,reverify_training_rows([old],workers=1,**kwargs)


def _reverify_parallel(selections, rows, input_identity, output_dir, training_epoch,
                       model_identity, corpus_root, ballast_s, check_timeout_s, workers):
    started=perf_counter()
    payloads=[(index,old,dict(output_dir=output_dir/'maps'/f'{index:04d}',
        training_epoch=training_epoch,model_identity=model_identity,corpus_root=corpus_root,
        ballast_s=ballast_s,check_timeout_s=check_timeout_s)) for index,old in enumerate(rows)]
    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=multiprocessing.get_context('spawn')) as pool:
        # map preserves original ordering regardless of worker completion order.
        completed=list(pool.map(_reverify_map_job,payloads,chunksize=1))
    counts,events,fresh_selections,winner_records,pairs,outputs={},[],[],[],[],{}
    for index,result in sorted(completed):
        for key,value in result['counts'].items():counts[key]=counts.get(key,0)+value
        events.extend(result['events']);fresh_selections.extend(result['selections'])
        winner_records.extend(result['winner_records']);pairs.extend(result['pairs'])
        outputs.update(result['receipt']['outputs'])
        child_receipt=output_dir/'maps'/f'{index:04d}'/'receipt.json'
        outputs[str(child_receipt)]=hashlib.sha256(child_receipt.read_bytes()).hexdigest()
    if _epoch()!=training_epoch or _inputs(selections)[1]!=input_identity:
        raise RuntimeError('source or source records changed during parallel re-verification')
    result=dict(status='ready' if winner_records else 'blocked_no_current_winners',
        selections=fresh_selections,winner_records=winner_records,pairs=pairs,counts=counts,
        events=events,wall_s=perf_counter()-started,cached=False,workers=workers,
        parallelism='spawn one map per worker; native threads1; checker watchdog subprocess per check')
    result_path=output_dir/'result.json';_write(result_path,result)
    outputs[str(result_path)]=hashlib.sha256(result_path.read_bytes()).hexdigest()
    receipt=dict(schema=SCHEMA,status=result['status'],training_epoch=training_epoch,
        model_identity=model_identity,inputs=input_identity,outputs=outputs)
    receipt['receipt_sha256']=_digest(receipt)
    _write(output_dir/'receipt.json',receipt)
    return {**result,'receipt':receipt}


def _plan_from_tokens(instance, tokens):
    """Rebuild an audit's own decoded plan without model inference or token edits."""
    from llm.exit.selection import decoder_factory
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskConfig
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    space = get_action_space()
    engine = make_feasibility_engine(instance, space,
        config=FeasibilityConfig(extra={'global_screen':0}))
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    decoder = decoder_factory()(instance, engine, codec, MaskConfig(), 1)[0]
    if not tokens:
        raise ValueError('audit has no own emitted tokens')
    for token in tokens:
        if token not in decoder.allowed_token_ids():
            raise ValueError('saved audit token outside current layered mask')
        decoder.advance(token)
    if decoder.status != 'complete':
        raise ValueError('saved own audit trace is incomplete under current layers')
    return decoder.engine.to_plan()


def reverify_audit_rows(rows, *, frozen_paths, output_dir, training_epoch,
                        model_identity, corpus_root=None, ballast_s=90., check_timeout_s=300.):
    """Readmit saved own traces on the fixed30 without rewriting the old baseline.

    This is evidence migration, not fresh model performance: old failed/incomplete
    traces stay failures unless their saved *complete own trace* now passes. The
    original 20/30,34.05 result remains separately recorded by the caller.
    """
    if not model_identity or _epoch() != training_epoch:
        raise ValueError('audit recheck requires bound model and fresh source epoch')
    frozen = [str(Path(path).resolve()) for path in frozen_paths]
    actual = [str(Path(row['instance_path']).resolve()) for row in rows]
    if len(frozen) != 30 or len(set(frozen)) != 30 or len(actual) != 30 or set(actual) != set(frozen):
        raise ValueError('audit recheck requires exactly the frozen30 maps')
    s, fresh, events = _services(), [], []
    input_hash = _digest(rows)
    map_hashes = {path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in frozen}
    for old in rows:
        instance, _ = _bound_instance(s, old['instance_path'], corpus_root,
                                      inline=(old.get('instance'),), role='audit')
        row = {**old, 'passed':False, 'weighted_moves':None,
               'reverification_epoch':training_epoch, 'original_passed':bool(old.get('passed')),
               'reverification_kind':'saved complete cargo actions; current V4 ballast rederived',
               'original_weighted_moves':old.get('weighted_moves')}
        try:
            generation = old.get('generation_timings', {})
            if generation.get('status') != 'complete':
                raise ValueError('original own audit decode was incomplete; never promote it')
            plan = _plan_from_tokens(instance, generation.get('tokens', ()))
            verified = s['verify_candidate'](instance, s['Candidate'](plan, 'audit_reverification'),
                ballast_s=ballast_s, check_timeout_s=check_timeout_s)
            admitted = bool(verified.passed and not s['checker_unavailable'](verified.details)
                and verified.plan is not None and verified.details.get('report', {}).get('passed') is True
                and verified.details.get('mask', {}).get('passed') is True)
            row.update(passed=admitted,
                weighted_moves=verified.weighted_moves if admitted else None,
                verification=verified.details,
                plan=verified.plan.model_dump(mode='json') if admitted else None)
        except (ValueError, TypeError, KeyError) as error:
            row['verification'] = {'error':f'{type(error).__name__}: {error}'}
        events.append(dict(instance_path=old['instance_path'], old_passed=bool(old.get('passed')),
            new_passed=row['passed'], unavailable=s['checker_unavailable'](row['verification'])))
        fresh.append(row)
    if (_epoch() != training_epoch or _digest(rows) != input_hash or any(
            hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
            for path,digest in map_hashes.items())):
        raise RuntimeError('audit source or inputs changed during re-verification')
    def measured_summary(values):
        passed = [row for row in values if row.get('passed')]
        moves = [float(row['weighted_moves']) for row in passed]
        return dict(maps=len(values), pass_count=len(passed),
                    mean_weighted_moves=sum(moves)/len(moves) if moves else None)
    result = dict(schema='exit-audit-epoch-v1', training_epoch=training_epoch,
        model_identity=model_identity, original_rows_sha256=input_hash, maps=map_hashes,
        original_measurement=measured_summary(rows),
        current_epoch_reverification=measured_summary(fresh),
        rows=fresh, events=events,
        changed_to_fail=sum(e['old_passed'] and not e['new_passed'] for e in events),
        changed_to_pass=sum(not e['old_passed'] and e['new_passed'] for e in events),
        unavailable=sum(e['unavailable'] for e in events))
    _write(Path(output_dir)/'audit-reverification.json', result)
    return result
