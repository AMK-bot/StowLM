"""Resumable generations; CPU verification overlaps the next map's GPU decode."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import resource
import signal
import time

from llm.exit.outcome_epoch import process_snapshot, assert_current as assert_audit_epoch, epoch_id
_LOADED_AUDIT_EPOCH=process_snapshot()  # Before importing model/evaluation modules.

from llm.exit.data import CorpusSchedule, build_training_dataset
from llm.exit.metrics import append_learning_curve, compare_validation, write_learning_plot
from llm.exit.selection import Candidate, load_gold_candidate, select_winner
from llm.exit.outcomes import checker_unavailable, outcome_counts, selection_checker_rows

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = ROOT / 'experiments/mvp/exit'


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def adapter_identity(parent):
    return {name: hashlib.sha256((Path(parent) / name).read_bytes()).hexdigest()
            for name in ('adapters.safetensors', 'adapters.safetensors.provenance.json', 'adapter_config.json')}


def source_pins():
    """A generation cannot silently resume across concurrent production edits."""
    paths = [p for base in ('src/llm', 'src/stowage') for p in (ROOT / base).rglob('*')
             if p.suffix in ('.py', '.json') and '__pycache__' not in p.parts]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def saved_gold_records(gold_root, split='train', excluded=()):
    excluded = {str(Path(p).resolve()) for p in excluded}
    result = []
    for rung in ('tiny', 'small'):
        for path in sorted((Path(gold_root) / split / rung).rglob('result.json')):
            row = read_json(path)
            if (row.get('passed') is True and (path.parent / 'case.lock').exists()
                    and str(Path(row.get('input_path', '')).resolve()) not in excluded):
                result.append(path)
    return result


def gold_win_labels(winner,score,gold_scores):
    beats=bool(winner and gold_scores and score<min(gold_scores))
    own=bool(winner and winner.source=='model')
    polished=bool(winner and winner.source=='polished' and winner.metadata.get('parent_source')=='model')
    return dict(model_beat_gold=own and beats,model_own_beat_gold=own and beats,
        model_plus_lns_beat_gold=polished and beats,
        winner_source_detail=('polished_from_'+winner.metadata.get('parent_source','unknown')
                             if winner and winner.source=='polished' else winner.source if winner else None))


def teacher_summary(selections):
    rows=[r for s in selections for r in s.get('teachers',[])]
    return dict(map_count=len(selections),
        gold_passes=sum(r.get('passed') is True and r['source']=='gold' for r in rows),
        two_step_passes=sum(r.get('passed') is True and r['source']=='baseline_twostep' for r in rows),
        two_step_attempts=sum(r.get('attempted') is True and r['source']=='baseline_twostep' for r in rows),
        verified_teacher_maps=sum(any(r.get('passed') is True for r in s.get('teachers',[])) for s in selections),
        unavailable=sum(bool(r.get('unavailable')) for r in rows),
        per_map=[dict(instance_id=s['instance_id'],teachers=s.get('teachers',[]),
            winner=s.get('source'),winner_moves=s.get('weighted_moves')) for s in selections])


def teacher_job(input_path, corpus_root, gold_root, directory, planning_s=120., ballast_s=120.):
    """D137 parallel CPU teachers, dispatched before model decoding begins."""
    from llm.exit.selection import prepare_teacher_candidates,verify_candidate
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    instance=load_instance(input_path)
    identity_guard(corpus_root).require_allowed(instance,role='train',input_path=input_path,
        raw_sha=hashlib.sha256(Path(input_path).read_bytes()).hexdigest())
    directory=Path(directory);target=directory/'teacher-manifest.json'
    binding=dict(sources=source_pins(),input_sha256=hashlib.sha256(Path(input_path).read_bytes()).hexdigest(),
        planning_s=planning_s,ballast_s=ballast_s)
    if target.exists():
        if read_json(target)['binding']!=binding:raise RuntimeError('teacher cache epoch/input/budget changed')
        return str(target)
    started=time.monotonic()
    candidates,cache,records=prepare_teacher_candidates(instance,
        load_gold_candidate(input_path,corpus_root,gold_root),planning_s=planning_s,ballast_s=ballast_s,
        verifier=lambda i,c:verify_candidate(i,c,ballast_s=ballast_s,
            expected_outcome_epoch=_LOADED_AUDIT_EPOCH))
    serialized=[]
    for candidate in candidates:
        checked=cache[candidate.candidate_id]
        serialized.append(dict(plan=candidate.plan.model_dump(mode='json'),source=candidate.source,
            candidate_id=candidate.candidate_id,metadata=candidate.metadata,
            verification=dict(passed=checked.passed,weighted_moves=checked.weighted_moves,
                plan=checked.plan.model_dump(mode='json') if checked.plan is not None else None,
                details=checked.details)))
    if binding['sources']!=source_pins() or binding['input_sha256']!=hashlib.sha256(Path(input_path).read_bytes()).hexdigest():
        raise RuntimeError('teacher code/input drifted during verification')
    write_json(target,dict(binding=binding,candidates=serialized,teachers=records,wall_s=time.monotonic()-started))
    return str(target)


def read_teacher_manifest(path,input_path):
    from llm.exit.selection import Verification
    from stowage.model import MovePlan
    manifest=read_json(path)
    if (manifest['binding']['sources']!=source_pins()
            or manifest['binding']['input_sha256']!=hashlib.sha256(Path(input_path).read_bytes()).hexdigest()):
        raise RuntimeError('teacher proof no longer belongs to the current training epoch/input')
    candidates,cache=[],{}
    for raw in manifest['candidates']:
        candidates.append(Candidate(MovePlan.model_validate(raw['plan']),raw['source'],raw['candidate_id'],raw['metadata']))
        checked=raw['verification']
        cache[raw['candidate_id']]=Verification(checked['passed'],
            MovePlan.model_validate(checked['plan']) if checked['plan'] is not None else None,
            checked['weighted_moves'],checked['details'])
    return candidates,cache,manifest


def select_job(input_path, raw_candidates, corpus_root, gold_root, directory, options, fitness_adapters=(),
               raw_samples=(), preference_max_pairs=0, teacher_manifest=None):
    """Process worker; persists all candidates and only checked winner records."""
    cpu_started=time.process_time()
    children_started=resource.getrusage(resource.RUSAGE_CHILDREN)
    from stowage.io import load_instance
    instance = load_instance(input_path)
    from llm.exit.identity import identity_guard
    identity_guard(corpus_root).require_allowed(instance, input_path=input_path,
        raw_sha=hashlib.sha256(Path(input_path).read_bytes()).hexdigest())
    directory = Path(directory)
    target = directory / 'selection.json'
    if target.exists():
        return read_json(target)
    candidates = [Candidate(**row) for row in raw_candidates]
    teacher_cache,teacher_records,teacher_wall={},[],0.
    if teacher_manifest is not None:
        teachers,teacher_cache,teacher_data=read_teacher_manifest(teacher_manifest,input_path)
        candidates.extend(teachers);teacher_records=teacher_data['teachers'];teacher_wall=teacher_data['wall_s']
    else:
        gold = load_gold_candidate(input_path, corpus_root, gold_root)
        if gold is not None:candidates.append(gold)
    if teacher_manifest is None and gold is None:
        from llm.exit.selection import baseline_candidate
        baseline=baseline_candidate(instance,time_limit_s=120.)
        if baseline is not None:candidates.append(baseline)
    from llm.exit.selection import verify_candidate
    fitness_started = time.monotonic()
    greedy_results, cache = {}, dict(teacher_cache)
    for adapter in fitness_adapters:
        greedy = next((c for c in candidates if c.source == 'model'
                       and c.metadata.get('adapter') == adapter and c.metadata.get('sample') == 0), None)
        checked = verify_candidate(instance, greedy, ballast_s=options.get('ballast_s', 90)) if greedy else None
        greedy_results[adapter] = dict(instance_id=instance.id,
            passed=bool(checked and checked.passed), weighted_moves=checked.weighted_moves if checked else None,
            verification=checked.details if checked else {'error': 'greedy sample incomplete'})
        if greedy:
            cache[greedy.candidate_id] = checked
            if checked.passed:
                # Reuse the finalized greedy ballast for scoring/polishing.
                greedy.plan = checked.plan
    fitness_s = time.monotonic()-fitness_started
    def verifier(case, candidate):
        return cache.get(candidate.candidate_id) or verify_candidate(case, candidate,
                    ballast_s=options.get('ballast_s', 90))
    result = select_winner(instance, candidates, verifier=verifier, **options)
    result.timings['greedy_verify_s'] = fitness_s
    result.timings['teacher_worker_s']=teacher_wall
    directory.mkdir(parents=True, exist_ok=True)
    serialized = []
    for n, candidate in enumerate(result.candidates):
        path = directory / f'candidate-{n:02d}.json'
        write_json(path, candidate.plan.model_dump(mode='json'))
        serialized.append(dict(source=candidate.source, candidate_id=candidate.candidate_id,
                               metadata=candidate.metadata, plan_path=str(path)))
    record_path = None
    if result.winner:
        report = next(v['report'] for v in result.verifications
                      if v.get('selected_winner') is True)
        record_path = directory / 'winner.record.json'
        from llm.exit.replay import checker_epoch
        write_json(record_path, dict(instance_path=str(Path(input_path).resolve()),
            plan=result.winner.plan.model_dump(mode='json'), report=report,
            exit_checker_epoch=checker_epoch()))
    gold_scores = [v['weighted_moves'] for v in result.verifications
                   if v.get('source')=='gold' and v.get('passed') is True
                   and v.get('weighted_moves') is not None]
    summary = dict(instance_id=instance.id, instance_path=str(input_path),
        passed=result.winner is not None, weighted_moves=result.weighted_moves,
        source=result.winner.source if result.winner else None,
        winner_record=str(record_path) if record_path else None, candidates=serialized,
        verifications=result.verifications, greedy_fitness=greedy_results, timings=result.timings,
        gold_moves=min(gold_scores) if gold_scores else None,
        **gold_win_labels(result.winner,result.weighted_moves,gold_scores))
    summary['teachers']=teacher_records
    summary['verification_counts']=outcome_counts(selection_checker_rows(summary))
    summary['greedy_fitness_counts']=outcome_counts(greedy_results.values())
    summary['verification_started_monotonic']=fitness_started
    if preference_max_pairs:
        from llm.exit.preferences import prepare_training_pairs,write_pairs
        preferences=prepare_training_pairs(summary,samples=raw_samples,
            max_pairs=preference_max_pairs,corpus_root=corpus_root,
            prefer_model_costlier_only=teacher_manifest is not None)
        write_pairs(directory/'preferences.jsonl',preferences['pairs'])
        summary['preferences']=preferences
    children_finished=resource.getrusage(resource.RUSAGE_CHILDREN)
    summary['cpu_s']=time.process_time()-cpu_started+children_finished.ru_utime+children_finished.ru_stime-children_started.ru_utime-children_started.ru_stime
    summary['verification_finished_monotonic']=time.monotonic()
    write_json(target, summary)
    return summary


def checked_incumbent_job(input_path, corpus_root, gold_root, target, ballast_s):
    """CPU seed for pruning; saved gold still passes every current admission gate."""
    from llm.exit.identity import identity_guard
    from llm.exit.selection import verify_candidate
    from stowage.io import load_instance
    started=time.monotonic()
    cpu_started=time.process_time()
    instance=load_instance(input_path)
    identity_guard(corpus_root).require_allowed(instance,input_path=input_path)
    candidate=load_gold_candidate(input_path,corpus_root,gold_root)
    checked=verify_candidate(instance,candidate,ballast_s=ballast_s) if candidate else None
    result=dict(passed=bool(checked and checked.passed),source='gold',
        weighted_moves=checked.weighted_moves if checked and checked.passed else None,
        verification=checked.details if checked else None,
        plan=checked.plan.model_dump(mode='json') if checked and checked.passed else None,
        wall_s=time.monotonic()-started,cpu_s=time.process_time()-cpu_started)
    write_json(target,result)
    return result


def incumbent_provider(future):
    """Never block the GPU on CPU admission, and never promote an unchecked bound."""
    def get():
        from llm.exit.pruning import Incumbent
        if future is None or not future.done():
            return None
        row=future.result()
        return Incumbent(row['weighted_moves'],row['source']) if row['passed'] else None
    return get


def _raw_generation(path, parent, args, seed, directory, *, k=None, temperature=None,
                    incumbent=None):
    from llm.exit.runtime import generate
    target = Path(directory) / 'generation.json'
    if target.exists():
        raw = read_json(target)
        from stowage.model import MovePlan
        for row in raw['candidates']:
            row['plan'] = MovePlan.model_validate(row['plan'])
        return raw
    raw = generate(path, parent, k=args.k if k is None else k,
                   temperature=args.temperature if temperature is None else temperature, seed=seed,
                   max_tokens=args.max_tokens,corpus_root=args.corpus,
                   batch_size=getattr(args,'batch_size',None),
                   early_pruning=getattr(args,'early_pruning',True),incumbent_provider=incumbent)
    persisted = {**raw, 'candidates': [{**row, 'plan': row['plan'].model_dump(mode='json')}
                                     for row in raw['candidates']]}
    write_json(target, persisted)
    return raw


def audit_verify_job(path,raw,target,ballast_s,expected_outcome_epoch=None):
    expected_outcome_epoch=expected_outcome_epoch or _LOADED_AUDIT_EPOCH
    if _LOADED_AUDIT_EPOCH!=expected_outcome_epoch:
        raise RuntimeError('audit worker loaded outcome epoch differs from parent')
    assert_audit_epoch(expected_outcome_epoch)
    from llm.exit.selection import verify_candidate
    from stowage.io import load_instance
    instance=load_instance(path)
    verified=verify_candidate(instance,Candidate(raw['plan'],'model'),ballast_s=ballast_s,
        expected_outcome_epoch=expected_outcome_epoch) if raw['plan'] else None
    row=dict(instance_id=instance.id,instance_path=str(path),
        passed=bool(verified and verified.passed),weighted_moves=verified.weighted_moves if verified else None,
        generation_timings={k:v for k,v in raw.items() if k!='plan'},
        verification=verified.details if verified else {'error':'no complete own greedy plan'})
    row['unavailable']=checker_unavailable(row)
    row['verification_counts']=outcome_counts([row])
    assert_audit_epoch(expected_outcome_epoch)  # Child checker/loaded-parent epoch handshake.
    row['audit_outcome_epoch_id']=epoch_id(expected_outcome_epoch)
    write_json(target,row)
    return row


def evaluate(parent, paths, directory, args):
    """Batched own greedy decodes; six parallel checker jobs overlap decoding."""
    from llm.exit.audit_runtime import generate_audit_batch
    pins=_LOADED_AUDIT_EPOCH
    assert_audit_epoch(pins)
    epoch=epoch_id(pins)
    directory = Path(directory)/f'epoch-{epoch}'
    directory.mkdir(parents=True,exist_ok=True)
    identity=dict(adapter=adapter_identity(parent),sources=pins,
        maps=[(str(p),hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in paths],
        max_tokens=args.max_tokens,batch_size=args.audit_batch_size,
        source_scope='D132 audit outcome dependencies; full training epoch remains separate',
        recipe=dict(greedy=True,temperature=0.,samples=1,backtracking=False,global_screen=0,
                    layers=['production','lookahead','securing'],ballast_s=args.ballast_s,checker_watchdog_s=300.))
    identity=json.loads(json.dumps(identity))
    identity_path=directory/'identity.json'
    if identity_path.exists() and read_json(identity_path)!=identity:
        raise RuntimeError('audit cache adapter, input or decoder/checker identity changed')
    write_json(identity_path,identity)
    rows = [None]*len(paths)
    pending=[]
    for index, path in enumerate(paths):
        target = directory / f'{index:03d}.json'
        if target.exists():
            rows[index]=read_json(target)
        else:pending.append((index,path))
    if pending:
        with ProcessPoolExecutor(args.workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            jobs={}
            def completed(local_index,raw):
                index,path=pending[local_index]
                jobs[index]=pool.submit(audit_verify_job,str(path),raw,str(directory/f'{index:03d}.json'),args.ballast_s,pins)
            generate_audit_batch([p for _,p in pending],parent,batch_size=args.audit_batch_size,
                max_tokens=args.max_tokens,corpus_root=args.corpus,on_result=completed)
            for index,job in jobs.items():rows[index]=job.result()
    if any(row is None for row in rows):raise RuntimeError('audit lacks completed own-plan outcomes')
    assert_audit_epoch(pins)
    if adapter_identity(parent)!=identity['adapter']:
        raise RuntimeError('audit source or adapter changed during measurement')
    write_json(directory / 'summary.json', rows)
    write_json(directory / 'availability-summary.json',outcome_counts(rows))
    return rows


def sample_allocation(k, population):
    if k < population:
        raise ValueError('each population member needs at least one greedy sample')
    return [k // population + (i < k % population) for i in range(population)]


def population_allocation(k,population):
    children=sum(not row.get('elite',False) for row in population)
    if not children:
        if len(population)!=1:raise ValueError('population needs children or one sole parent')
        return [k]
    allocations=iter(sample_allocation(k,children))
    return [1 if row.get('elite',False) else next(allocations) for row in population]


def rank_preference_pairs(pairs,seed,limit):
    import random
    pairs=list(pairs);random.Random(seed).shuffle(pairs)
    priority={'strictly_more_moves':0,'checker_failure':1,'mask_failure':1,'dead_end':2}
    pairs.sort(key=lambda pair:priority.get(pair.get('evidence',{}).get('kind'),3))
    return pairs[:limit]


def mutate_children(parent_config, population, *, preference=False):
    children = []
    for index in range(population):
        config = dict(parent_config)
        # APPROXIMATION: bounded multiplicative LR mutation avoids runaway values
        # across many generations; the third child mutates the replay mixture.
        config['learning_rate'] = min(1e-4, max(1e-6,
            config['learning_rate'] * ((.5, 2., 1.)[index])))
        if index == 2:
            config['replay_ratio'] = .6 if config.get('replay_ratio', .5) <= .5 else .4
        if preference and index < 2:
            # Compare methods at the same inherited LR; method wins are still
            # observational fitness results on fresh maps, not a causal proof.
            config['learning_rate']=parent_config['learning_rate']
            config['method']=('sft','sft_dpo')[index]
        else:
            config['method']=config.get('method','sft') if preference else 'sft'
        children.append(config)
    return children


def ensure_baseline(args,validation):
    assert_audit_epoch(_LOADED_AUDIT_EPOCH)
    target=args.out/'audit-baseline'/'summary.json'
    from llm.exit.replay import checker_epoch
    binding=dict(adapter=adapter_identity(args.parent),baseline_adapter=str(Path(args.parent).resolve()),
        checker_epoch=checker_epoch(),audit_outcome_epoch=_LOADED_AUDIT_EPOCH,
        audit_recipe=dict(greedy=True,temperature=0.,samples=1,batch_size=args.audit_batch_size,
                          max_tokens=args.max_tokens,ballast_s=args.ballast_s,checker_watchdog_s=300.),
        maps=[dict(path=str(p),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in validation])
    if getattr(args,'baseline_label','8.1')!='8.1':
        binding['baseline_label']=args.baseline_label
    binding_path=target.with_name('baseline-binding.json')
    if target.exists():
        if not binding_path.exists() or read_json(binding_path)!=binding:
            raise RuntimeError('initial audit baseline parent, maps or checker changed')
        return read_json(target)
    write_json(binding_path,binding)
    started=time.monotonic()
    baseline=evaluate(args.parent,validation,args.out/'audit-baseline',args)
    write_json(target,baseline)
    baseline_passes=[r['weighted_moves'] for r in baseline
                     if r.get('passed') is True and not checker_unavailable(r)]
    write_json(args.out/'audit-baseline'/'availability-summary.json',dict(
        **outcome_counts(baseline),
        mean_weighted_moves=sum(baseline_passes)/len(baseline_passes) if baseline_passes else None))
    write_json(args.out/'baseline-timing.json',dict(audit_s=time.monotonic()-started))
    return baseline


def clock_audit(args,population):
    from llm.exit.audit import frozen_audit_paths,audit_decision,persist_stop
    validation=frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))
    baseline=ensure_baseline(args,validation)
    survivor=args.out/'survivor.json'
    latest=read_json(survivor) if survivor.exists() else dict(adapter=str(args.parent),generation=-1)
    candidates={row['adapter']:row for row in population}
    # A complete child checkpoint can exist when its generation is interrupted
    # before summary publication. Include it in the clock audit, never discard
    # the newest inheritance merely because fresh-map decoding is unfinished.
    parents=sorted(args.out.glob('gen-*/training-parent.json'))
    if parents:
        latest_parent=read_json(parents[-1])
        candidates={latest_parent['adapter']:latest_parent}
        for ready in sorted(parents[-1].parent.glob('child-*-ready.json')):
            row=read_json(ready);candidates[row['adapter']]=row
    if not candidates:candidates={latest['adapter']:dict(adapter=latest['adapter'])}
    best,best_rows=None,None;outcomes={}
    for index,config in enumerate(candidates.values()):
        key=adapter_identity(config['adapter'])['adapters.safetensors'][:16]
        rows=evaluate(config['adapter'],validation,args.out/'clock-audit'/f'member-{index}-{key}',args)
        outcomes[config['adapter']]=rows
        if best is None or compare_validation(best_rows,rows)['accepted']:
            best,best_rows=config,rows
    audit=audit_decision(baseline,best_rows,baseline_label=getattr(args,'baseline_label','8.1'))
    write_json(args.out/'first-gate.json',dict(generation=latest['generation'],audit=audit,
        clock=True,adapter=best['adapter'],rows_by_adapter=outcomes))
    if not audit['accepted']:
        persist_stop(args.out,generation=latest['generation'],adapter=best['adapter'],audit=audit,
                     fallback_adapter=args.parent,
                     reason='clock_audit_no_detectable_gain')
    else:
        write_json(args.out/'last-audit.json',dict(adapter=best['adapter'],rows=best_rows,
            generation=latest['generation'],config=best))


def final_population_audit(population,args):
    """No final child can be published without its own fixed-set outcomes."""
    from llm.exit.audit import frozen_audit_paths,audit_decision,persist_stop
    from llm.exit.final_recipe import evaluate_recipe
    validation=frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))
    # Final model choice compares the 8.1 baseline and every child under the SAME 9.1
    # recipe. The separate learning-curve gate remains the registered greedy one.
    baseline=evaluate_recipe(args.parent,validation,args.out/'final-audit'/'baseline-row-d',args)
    best,best_rows=dict(adapter=str(args.parent)),baseline
    rows_by_adapter={}
    started=time.monotonic()
    for index,config in enumerate(population):
        key=adapter_identity(config['adapter'])['adapters.safetensors'][:16]
        rows=evaluate_recipe(config['adapter'],validation,args.out/'final-audit'/f'member-{index}-{key}',args)
        rows_by_adapter[config['adapter']]=rows
        if best is None or compare_validation(best_rows,rows)['accepted']:
            best,best_rows=config,rows
    audit=audit_decision(baseline,best_rows,baseline_label=getattr(args,'baseline_label','8.1'))
    report=dict(adapter=best['adapter'],audit=audit,rows_by_adapter=rows_by_adapter,
                recipe='9.1: greedy single sequential decode, securing+lookahead, rewinds32, V4 finisher; no solver polish',
                wall_s=time.monotonic()-started,all_children_audited=True)
    write_json(args.out/'final-audit.json',report)
    if audit['accepted']:
        write_json(args.out/'survivor.json',dict(adapter=best['adapter'],frozen=True,
                                               evidence=str(args.out/'final-audit.json')))
    else:persist_stop(args.out,generation=args.gens-1,adapter=best['adapter'],audit=audit,
                      fallback_adapter=args.parent,
                      reason='final_audit_no_detectable_gain')


def resize_after_pilot(summary,args):
    """D137 map-linear projection; unfinished DPO timings remain unmeasured."""
    from llm.exit.sizing import recommend
    result=recommend(summary,args)
    args.tiny,args.small,args.k=result['tiny'],result['small'],result['k']
    write_json(args.out/'sizing-after-gen0.json',result)
    return result


def recover_generation(paths, samples, selections, args, directory):
    from llm.exit.recovery import RecoveryBudget,recover_dead_end
    from llm.exit.identity import identity_guard
    from stowage.io import load_instance
    from stowage.actions import get_action_space
    started=time.monotonic()
    budget=RecoveryBudget(sum(s.get('cpu_s',0.) for s in selections),
                          fraction=args.recovery_fraction,max_cases=args.recovery_max_cases)
    progress=directory/'recovery-progress.json'
    signature=hashlib.sha256(json.dumps(dict(
        inputs=[(str(p),hashlib.sha256(Path(p).read_bytes()).hexdigest()) for p in paths],
        samples=samples,base_cpu_s=budget.base_cpu_s,fraction=budget.fraction,
        max_cases=budget.max_cases,time_limit=args.recovery_time_limit),sort_keys=True).encode()).hexdigest()
    state=(read_json(progress) if progress.exists() else dict(signature=signature,
        charged_cpu_upper_s=0.,cases=0,records=[],attempts=[],seen=[]))
    if state['signature']!=signature:
        raise RuntimeError('recovery resume inputs or budget changed')
    budget.spent_s=state['charged_cpu_upper_s'];budget.cases=state['cases']
    records,attempts=[Path(p) for p in state['records']],state['attempts']
    guard=identity_guard(args.corpus)
    for index,(path,rows) in enumerate(zip(paths,samples)):
        for sample in rows:
            if sample.get('status')!='dead_end' or not sample.get('prefix_actions'):
                continue
            identity=f'{index}:{sample["candidate_id"]}'
            if identity in state['seen']:
                continue
            if budget.allowance(args.recovery_time_limit)<.1:
                break
            # Reserve before starting work: a crash cannot mint fresh CPU credit.
            # Normal completion replaces this reservation with its actual debit.
            state.update(charged_cpu_upper_s=budget.spent_s+budget.allowance(args.recovery_time_limit),
                         cases=budget.cases+1)
            state['seen'].append(identity)
            write_json(progress,state)
            instance=load_instance(path)
            result=recover_dead_end(instance,sample['prefix_actions'],budget=budget,
                time_limit_s=args.recovery_time_limit,input_path=path,guard=guard)
            attempts.append(dict(instance_path=str(path),sample=sample['candidate_id'],
                                 passed=result.passed,details=result.details))
            if result.passed:
                record=directory/'recovery'/f'{index:03d}-{len(records):02d}.record.json'
                write_json(record,dict(instance_path=str(path),plan=result.plan.model_dump(mode='json'),
                    report=result.details['verification']['report'],exit_recovery=dict(
                        prefix_actions=[get_action_space().encode(a) for a in result.prefix_actions],
                        admitted_example=result.details['admitted_example'],
                        sample=sample['candidate_id'],details=result.details)))
                records.append(record)
            state.update(charged_cpu_upper_s=budget.spent_s,cases=budget.cases,
                         records=[str(p) for p in records],attempts=attempts)
            write_json(progress,state)
    report=dict(enabled=True,wall_s=time.monotonic()-started,base_cpu_s=budget.base_cpu_s,
                charged_cpu_upper_s=budget.spent_s,cpu_credit_fraction=budget.fraction,
                max_cases=budget.max_cases,rows=len(records),attempts=attempts)
    write_json(directory/'recovery-summary.json',report)
    return records,report


def train_selected(number, parent_config, selections, paths, args, directory, tracker, *,
                   all_samples=None, training_receipt, original_selections, model_identity):
    """Only a current, content-bound training receipt admits a child update."""
    from llm.exit.runtime import train
    from llm.exit.reverify import assert_training_receipt
    from llm.exit.audit import check_clock
    parent = parent_config['adapter']
    timings = {}
    all_samples = all_samples or [[] for _ in paths]
    winner_records = [s['winner_record'] for s in selections if s['passed']]
    validation = frozen_validation(args)
    epoch = training_receipt['training_epoch']
    def assert_current_training():
        assert_training_receipt(training_receipt, selections=original_selections,
            training_epoch=epoch, model_identity=model_identity)
        extra_checks=getattr(args,'_training_extra_checks',None)
        if extra_checks is not None:extra_checks()
        if adapter_identity(parent) != model_identity['adapter']:
            raise RuntimeError('training parent changed after re-verification')
    assert_current_training()
    recovery_records,recovery_report=(recover_generation(paths,all_samples,selections,args,directory)
                                      if args.dagger else ([],{'enabled':False}))
    timings['recovery_s']=recovery_report.get('wall_s',0.)
    pairs=[pair for selection in selections for pair in selection.get('preferences',{}).get('pairs',[])]
    over_length_pairs=sum(any(len(pair[side]['prompt'])+len(pair[side]['tokens'])>args.dpo_max_length
                              for side in ('chosen','rejected')) for pair in pairs) if args.dpo else 0
    if args.dpo:
        pairs=[pair for pair in pairs if all(len(pair[side]['prompt'])+len(pair[side]['tokens'])<=args.dpo_max_length
                                           for side in ('chosen','rejected'))]
    pairs=rank_preference_pairs(pairs,number,args.dpo_max_pairs)
    timings['preference_cpu_worker_s']=sum(s.get('preferences',{}).get('wall_s',0.) for s in selections)
    from llm.exit.preferences import write_pairs
    write_pairs(directory/'preferences.jsonl',pairs)
    replay_target=directory/'replay-selection.json'
    if replay_target.exists():
        frozen=read_json(replay_target)
        replay=[Path(p) for p in frozen['selected_records']]
        if (not isinstance(frozen.get('selected_sha256'),dict)
                or set(frozen['selected_sha256'])!={str(p) for p in replay}) or any(
            hashlib.sha256(p.read_bytes()).hexdigest()!=frozen['selected_sha256'][str(p)] for p in replay):
            raise RuntimeError('pinned replay records changed during generation resume')
    else:
        replay = [p for p in args.out.glob('gen-*/**/winner.record.json')
                  if directory not in p.parents]
        replay += saved_gold_records(args.gold, excluded=paths)
        from llm.exit.replay import choose_replay
        replay_pool_count=len(replay)
        replay=choose_replay(replay,paths,number,max(len(winner_records)*3,16))
        write_json(replay_target,dict(pool_records=replay_pool_count,
            selected_records=[str(p) for p in replay],seed=number,
            selected_sha256={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in replay},
            strategy='whole-pool seeded rung/ship stratification; pinned within a generation',replay_ratio=.67))
    # Loss-only validation is disjoint from the fixed survival set.
    loss_val = saved_gold_records(args.gold, split='val', excluded=validation)[:2]
    if not loss_val:
        raise RuntimeError('need at least one val gold record outside fixed survival set for trainer loss evaluation')
    children, builds = [], {}
    write_json(directory/'training-parent.json',parent_config)
    timings['dataset_s'] = 0.
    for child_index, child_config in enumerate(mutate_children(parent_config, args.population,preference=args.dpo)):
        check_clock(args)
        child = directory / f'child-{child_index}'
        ratio = child_config['replay_ratio']
        if ratio not in builds:
            t0 = time.monotonic()
            dataset = directory / f'data-replay-{ratio:g}'
            extra={'recovery_records':recovery_records} if recovery_records else {}
            from llm.exit.dataset_pool import choose_workers
            dataset_capacity = choose_workers(args)
            write_json(directory/'dataset-capacity.json', dataset_capacity)
            with tracker.phase('dataset',replay_ratio=ratio,**dataset_capacity):
                build = build_training_dataset(winner_records, replay,
                    dataset, validation_records=loss_val, seed=number, replay_ratio=ratio,
                    workers=dataset_capacity['workers'],**extra)
            builds[ratio] = (dataset, build)
            timings['dataset_s'] += time.monotonic()-t0
        check_clock(args)
        assert_current_training()
        t0 = time.monotonic()
        with tracker.phase(f'child_{child_index}_train',steps=args.steps):
            train(parent, builds[ratio][0], child, steps=args.steps,
                  lr=child_config['learning_rate'], seed=number)
        timings[f'child_{child_index}_train_s'] = time.monotonic()-t0
        write_json(directory/f'child-{child_index}-ready.json',{
            **child_config,'adapter':str(child),'method':'sft','generation':number})
        check_clock(args)
        published_child=child
        if child_config.get('method')=='sft_dpo':
            if pairs:
                assert_current_training()
                from llm.exit.dpo import train_preference
                t0=time.monotonic()
                try:
                    with tracker.phase(f'child_{child_index}_dpo',pairs=len(pairs),steps=args.dpo_steps):
                        dpo=train_preference(parent,child,pairs,child/'preference',steps=args.dpo_steps,
                            lr=child_config['learning_rate'],beta=child_config.get('dpo_beta',args.dpo_beta),
                            sft_weight=args.dpo_nll_weight,seed=number,corpus_root=args.corpus,
                            max_sequence_length=args.dpo_max_length,
                            reference_cache_path=child/'dpo-reference.json',
                            on_phase=lambda event,**metrics:tracker.update(dpo_stage=event,**metrics))
                except (RuntimeError,ValueError,OSError) as error:
                    # The separately published, checked-data SFT child survives
                    # a preference phase failure; no failed DPO weights compete.
                    child_config.update(method='sft',requested_method='sft_dpo',
                        preference_error=f'{type(error).__name__}: {error}')
                    write_json(child/'preference-error.json',child_config)
                else:
                    child_config['preference_training']=dpo
                    published_child=child/'preference'
                timings[f'child_{child_index}_dpo_s']=time.monotonic()-t0
            else:
                child_config['method']='sft'
                child_config['requested_method']='sft_dpo'
                child_config['preference_skipped']='no admitted informative pairs'
        children.append({**child_config, 'adapter': str(published_child), 'inherited_from': parent})
        write_json(directory/f'child-{child_index}-ready.json',children[-1])
    assert_current_training()
    return dict(children=children, builds=builds, pairs=pairs,
        over_length_pairs=over_length_pairs, recovery_report=recovery_report, timings=timings)


def frozen_validation(args):
    from llm.exit.audit import frozen_audit_paths
    return frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))


def run_generation(number, population, args, schedule):
    from llm.exit.runtime import train
    from llm.exit.fitness import population_fitness
    from llm.exit.replay import adapter_training_hashes
    directory = args.out / f'gen-{number:03d}'
    summary_path = directory / 'summary.json'
    if summary_path.exists():
        return read_json(summary_path)['next_population']
    started = time.monotonic()
    directory.mkdir(parents=True, exist_ok=True)
    from llm.exit.progress import start_generation
    tracker=(getattr(args,'_generation0_tracker',None) if number==0 else None) or start_generation(
        args.out,number,workers=args.workers,maps=args.tiny+args.small,k=args.k,
        parent_adapters=[row['adapter'] for row in population])
    args._active_tracker=tracker
    args._active_generation=number
    manifest_path = directory / 'instances.json'
    pins = getattr(args,'_loaded_source_pins',None) or source_pins()
    identity = {row['adapter']: adapter_identity(row['adapter']) for row in population}
    config = {k: v for k, v in vars(args).items() if k not in ('parent', 'gens','resume_boundary')
              and not k.startswith('_')}
    config = json.loads(json.dumps(config, default=str))
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        if (manifest['source_pins'] != pins or manifest['population'] != population
                or manifest['adapter_identity'] != identity or manifest['config'] != config):
            raise RuntimeError('generation source/parent changed; use a fresh output directory')
        paths = [Path(p) for p in manifest['instances']]
    else:
        key = str(number)
        # Recover a reservation committed immediately before an interrupted manifest write.
        paths = ([schedule.root / e['path'] for e in schedule.state['generations'][key]]
                 if key in schedule.state['generations'] else
                 schedule.next_train(args.tiny, args.small, number,gold_root=args.gold,
                     seen_adapter_hashes=adapter_training_hashes([row['adapter'] for row in population]),
                     gold_fraction=getattr(args,'gold_map_fraction',.5)))
        write_json(manifest_path, dict(population=population, source_pins=pins, adapter_identity=identity, config=config,
            instances=[str(p) for p in paths],map_novelty={e['path']:e.get('novelty','new_to_loop')
                for e in schedule.state['generations'].get(key,[])},
            map_strata={e['path']:e.get('map_stratum') for e in schedule.state['generations'].get(key,[])},
            teacher_gold_evidence={e['path']:e.get('teacher_gold_evidence')
                for e in schedule.state['generations'].get(key,[])}))
    from llm.exit.audit import frozen_audit_paths,check_clock,clock_due,ClockAuditDue
    validation = frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))
    check_clock(args)
    timings = {}
    t0 = time.monotonic()
    jobs, decode_s,decode_wall_s,decoded_calls = [], 0.,0.,0
    securing_evaluations=0
    # APPROXIMATION: pipeline maps within a generation, rather than cross-generation
    # adapters; children depend on this generation's checked winners.
    context = multiprocessing.get_context('spawn')
    with tracker.phase('decode_verify_pipeline'),ProcessPoolExecutor(args.workers, mp_context=context) as pool:
        # Teachers start on CPU before GPU decode, including maps with stored gold.
        teacher_jobs=[pool.submit(teacher_job,str(path),str(args.corpus),str(args.gold),
            str(directory/'cases'/f'{i:03d}'/'teachers'),args.teacher_planning_s,args.ballast_s)
            for i,path in enumerate(paths)]
        # APPROXIMATION: only currently reverified gold seeds the online bound.
        # Model plans remain unchecked until pooled selection; this avoids a
        # duplicate model-checker pass solely for pruning.
        incumbent_jobs = ([pool.submit(checked_incumbent_job,str(path),str(args.corpus),
            str(args.gold),str(directory/'cases'/f'{i:03d}'/'pruning-incumbent.json'),args.ballast_s)
            for i,path in enumerate(paths)] if getattr(args,'early_pruning',True) else [])
        # Decode adapter-major: one LoRA swap per member instead of P swaps per map.
        # Verify each map as soon as its last member contributes candidates.
        pooled = [[] for _ in paths]
        all_samples=[[] for _ in paths]
        allocations=population_allocation(args.k,population)
        for member, (config, allocation) in enumerate(zip(population, allocations)):
            for index, path in enumerate(paths):
                if clock_due(args.audit_clock,args.out):
                    for job in teacher_jobs+incumbent_jobs+jobs:job.cancel()
                    raise ClockAuditDue('clock audit due between maps')
                case = directory / 'cases' / f'{index:03d}'
                decode_call_started=time.monotonic()
                raw = _raw_generation(path, config['adapter'], args,
                    number*100000+member*1000+index, case / f'decode-{member}',
                    k=allocation, temperature=config['temperature'],
                    incumbent=incumbent_provider(incumbent_jobs[index]) if incumbent_jobs else None)
                decode_s += raw['timings']['wall_s']
                decode_wall_s+=time.monotonic()-decode_call_started
                decoded_calls+=1
                securing_evaluations+=raw.get('securing',{}).get('evaluations',0)
                tracker.update(decoded_calls=decoded_calls,total_decode_calls=len(paths)*len(population),
                    decode_wall_s=decode_wall_s,verification_jobs_submitted=len(jobs),
                    securing_evaluations=securing_evaluations,
                    securing_last_decode=raw.get('securing'),memory_config=raw.get('memory_config'))
                all_samples[index].extend({**s,'adapter':config['adapter'],
                    'candidate_id':f'model-{member}-{s["sample"]}'} for s in raw.get('samples',[]))
                pooled[index].extend(dict(plan=c['plan'], source='model',
                    candidate_id=f'model-{member}-{c["sample"]}', metadata={
                        **{k: v for k, v in c.items() if k not in ('plan', 'source')},
                        'adapter': config['adapter']}) for c in raw['candidates'])
                if member == len(population)-1:
                    # A complete current-epoch teacher proof is required before selection.
                    # CPU jobs were queued first and overlap adapter-major decoding.
                    teacher_manifest=teacher_jobs[index].result()
                    jobs.append(pool.submit(select_job, str(path), pooled[index], str(args.corpus),
                        str(args.gold), str(case), dict(polish_top=args.polish_top,
                        polish_s=args.polish_s, ballast_s=args.ballast_s,verify_all=True),
                        [row['adapter'] for row in population],all_samples[index],
                        args.dpo_pairs_per_map if args.dpo else 0,teacher_manifest))
        decode_finished=time.monotonic()
        with tracker.phase('verify_drain'):
            selections = [job.result() for job in jobs]
            incumbent_results=[job.result() for job in incumbent_jobs]
        verification_tail_wall_s=time.monotonic()-decode_finished
    measured_checks=[s for s in selections if s.get('verification_started_monotonic') is not None]
    verification_span_wall_s=(max(s['verification_finished_monotonic'] for s in measured_checks)
        -min(s['verification_started_monotonic'] for s in measured_checks)) if measured_checks else None
    timings.update(decode_s=decode_s, decode_verify_pipeline_s=time.monotonic()-t0,
        decode_wall_s=decode_wall_s,verification_tail_wall_s=verification_tail_wall_s,
        verification_span_wall_s=verification_span_wall_s,
        verification_worker_s=sum(s['timings']['verify_s'] for s in selections),
        greedy_verification_worker_s=sum(s['timings']['greedy_verify_s'] for s in selections),
        polish_worker_s=sum(s['timings']['polish_s'] for s in selections),
        pruning_incumbent_worker_s=sum(s['wall_s'] for s in incumbent_results),
        teacher_worker_s=sum(s['timings'].get('teacher_worker_s',0.) for s in selections),
        teacher_planning_worker_s=sum((r.get('planning') or {}).get('planning_wall_s',
            r.get('planning_wall_s',0.)) for s in selections for r in s.get('teachers',[])))
    teacher_report=teacher_summary(selections)
    write_json(directory/'teacher-coverage.json',teacher_report)
    # Never silently return to self-imitation if the teacher pipeline is broken.
    if not teacher_report['verified_teacher_maps'] or (
            getattr(args,'gold_map_fraction',.5)>0 and not teacher_report['gold_passes']):
        raise RuntimeError('D137 teacher coverage gate failed; saved per-map diagnostics, no child trained')
    pruning_counts=Counter(s.get('status','unknown') for rows in all_samples for s in rows)
    # Measure only the fully queued drain, excluding the decode-limited interval.
    # APPROXIMATION: a drain is a useful CPU-only load group, not evidence for
    # throughput under a saturated GPU. Never compare the two groups.
    tail=[s for s in selections if s.get('verification_started_monotonic',0)>=decode_finished]
    from llm.exit.capacity import CapacitySizer,gpu_busy_observation
    sizer=CapacitySizer(args.out/'capacity.json',max_workers=args.workers)
    from llm.exit.resources import background_cores
    background=background_cores()
    busy_observation=gpu_busy_observation(selections,workers=args.workers,
        decode_started=t0,decode_finished=decode_finished,background_workers=background)
    if busy_observation:
        sizer.observe(**{k:busy_observation[k] for k in
            ('workers','completed_maps','wall_s','background_workers','gpu_saturated')})
    if len(tail)>=args.workers:
        sizer.observe(
            workers=args.workers,completed_maps=len(tail),gpu_saturated=False,
            background_workers=background,
            wall_s=max(s['verification_finished_monotonic'] for s in tail)
                   -min(s['verification_started_monotonic'] for s in tail))
    fitness_started=time.monotonic()
    with tracker.phase('fitness'):
        fitness = population_fitness({row['adapter']: [s['greedy_fitness'][row['adapter']]
                                      for s in selections] for row in population})
    parent = fitness['best_adapter']
    parent_config = next(row for row in population if row['adapter'] == parent)
    fitness['best_method']=parent_config.get('method','sft')
    fitness['methods']={row['adapter']:row.get('method','sft') for row in population}
    timings['fitness_s']=time.monotonic()-fitness_started
    tracker.update(winners=sum(s['passed'] for s in selections),fitness_parent=parent)
    t0 = time.monotonic()
    audit_state = args.out / 'last-audit.json'
    if not audit_state.exists():
        baseline = ensure_baseline(args,validation)
        write_json(audit_state, dict(adapter=str(args.parent), rows=baseline, generation=-1,
            config=dict(adapter=str(args.parent), learning_rate=args.learning_rate,
                        replay_ratio=.67, temperature=args.temperature)))
        append_learning_curve(args.out / 'learning_curve.csv', dict(generation=-1,
            adapter=str(args.parent), **compare_validation(baseline, baseline)))
        write_learning_plot(args.out / 'learning_curve.csv', args.out / 'learning_curve.svg')
    audit = None
    if (number+1) % args.audit_every == 0:
        from llm.exit.audit import audit_decision,persist_stop
        previous = read_json(audit_state)
        audited_adapter=parent
        with tracker.phase('audit'):
            audited_rows = evaluate(parent, validation, directory / 'audit', args)
        baseline=read_json(args.out/'audit-baseline'/'summary.json')
        audit = audit_decision(baseline, audited_rows,baseline_label=getattr(args,'baseline_label','8.1'))
        control_report=None
        if number == 1:
            from llm.exit.control import ensure_gold_control
            from llm.exit.outcomes import checker_unavailable
            history=[read_json(p) for p in sorted(args.out.glob('gen-*/summary.json'))]
            control_loss_val=saved_gold_records(args.gold,split='val',excluded=validation)[:2]
            control_started=time.monotonic()
            with tracker.phase('gold_control'):
                control_report=ensure_gold_control(history,parent,args,directory/'gold-control',
                    evaluator=evaluate,excluded=paths,validation_records=control_loss_val,
                    audit_paths=validation,fitness=fitness)
            timings['gold_control_s']=time.monotonic()-control_started
            control_comparison=compare_validation(control_report['audit_rows'],audited_rows)
            control_available=not any(checker_unavailable(r)
                for r in control_report['audit_rows']+audited_rows)
            audit['gold_control']=dict(adapter=control_report['adapter'],
                phase_count=control_report['phase_count'],schedule_matched=control_report['schedule_matched'],
                schedule_reference_adapter=control_report['schedule_reference_adapter'],
                compared_adapter_schedule_matched=control_report['compared_adapter_schedule_matched'],
                trained_control=control_report['trained_control'],
                example_count_matched=control_report['example_count_matched'],
                dpo_updates_unmatched=control_report['dpo_updates_unmatched'],
                comparison=control_comparison,verification_available=control_available,
                loop_beats_gold_control=bool(audit['accepted'] and control_available
                    and control_comparison['accepted']),
                attribution_eligible=bool(control_report['example_count_matched']
                    and control_report['trained_control'] and control_report['schedule_matched']
                    and control_report['compared_adapter_schedule_matched']
                    and not control_report['dpo_updates_unmatched']),
                evidence='weak paired evidence; matched SFT schedule, fresh Adam; DPO extra updates disclosed')
        regression = compare_validation(audited_rows, previous['rows'])['accepted']
        audit['rolled_back'] = regression
        audit['audited_adapter']=audited_adapter
        if regression:
            parent, parent_config = previous['adapter'], previous['config']
        else:
            write_json(audit_state, dict(adapter=parent, rows=audited_rows,
                                        generation=number, config=parent_config))
        append_learning_curve(args.out / 'learning_curve.csv', dict(generation=number,
            adapter=audited_adapter,training_parent_after_rollback=parent, **audit))
        write_learning_plot(args.out / 'learning_curve.csv', args.out / 'learning_curve.svg')
        write_json(args.out/'first-gate.json',dict(generation=number,audit=audit))
        if not audit['accepted']:
            timings.update(audit_s=time.monotonic()-t0-timings.get('gold_control_s',0.),wall_s=time.monotonic()-started)
            terminal=persist_stop(args.out,generation=number,adapter=parent,audit=audit,
                                  fallback_adapter=args.parent)
            write_json(summary_path,{**terminal,'parent':parent,'next_population':[parent_config],
                'fitness':fitness,'audit':audit,'timings':timings})
            tracker.finish(status='stopped',reason='no_detectable_audit_gain')
            return [parent_config]
    timings['audit_s'] = time.monotonic()-t0-timings.get('gold_control_s',0.)
    winner_records = [s['winner_record'] for s in selections if s['passed']]
    if not winner_records:
        # APPROXIMATION: no checked targets means no gradient update; keep the
        # measured winning/audited parent and try fresh maps next generation.
        timings['wall_s'] = time.monotonic()-started
        summary = dict(generation=number, parent=parent, next_population=[parent_config],
            fitness=fitness, audit=audit, skipped_training='no checked winners',
            winner_count=0, instance_count=len(paths), timings=timings,
            generations_per_hour=3600/timings['wall_s'])
        write_json(summary_path, summary)
        tracker.finish(status='completed',skipped_training='no checked winners')
        print(json.dumps(summary), flush=True)
        return [parent_config]
    # D129: all training labels are readmitted under this process's source epoch.
    # Unknown drift requires a fresh process; never rewrite loaded source pins.
    if source_pins() != pins:
        write_json(directory/'training-boundary-required.json', dict(generation=number,
            phase='selection_complete_before_training', loaded_sources=pins,
            current_sources=source_pins(), selection_paths=[str(directory/'cases'/f'{i:03d}'/'selection.json')
                for i in range(len(paths))], parent_config=parent_config,
            action='fresh-process reverify before training; no children trained'))
        raise RuntimeError('source changed before training; preserved selections need fresh-process re-verification')
    from llm.exit.reverify import reverify_training_rows
    original_selections = selections
    model_identity = dict(adapter=adapter_identity(parent))
    with tracker.phase('training_reverification'):
        rechecked = reverify_training_rows(original_selections,
            output_dir=directory/'training-epoch', training_epoch=pins,
            model_identity=model_identity, corpus_root=args.corpus, ballast_s=args.ballast_s)
    if rechecked['status'] != 'ready':
        raise RuntimeError('no selected winner survived training-epoch re-verification')
    selections = rechecked['selections']
    winner_records = rechecked['winner_records']
    timings['training_reverification_s'] = rechecked['wall_s']
    inherited = train_selected(number,parent_config,selections,paths,args,directory,tracker,
        all_samples=all_samples,training_receipt=rechecked['receipt'],
        original_selections=original_selections,model_identity=model_identity)
    children,builds,pairs = inherited['children'],inherited['builds'],inherited['pairs']
    over_length_pairs,recovery_report = inherited['over_length_pairs'],inherited['recovery_report']
    timings.update(inherited['timings'])
    from llm.exit.code_epoch import boundary_plan
    disk_pins=source_pins()
    source_change=boundary_plan(pins,disk_pins)
    if source_change['kind']=='other' or any(adapter_identity(p) != identity[p] for p in identity):
        raise RuntimeError('unapproved source or adapter changed during generation; no child admitted')
    timings['wall_s'] = time.monotonic()-started
    gold_cases = [s for s in selections if s['gold_moves'] is not None]
    next_population=[{**parent_config,'elite':True}]+[{**child,'elite':False} for child in children]
    from llm.exit.finisher_outcomes import finisher_outcome_counts
    summary = dict(generation=number, parent=str(parent), next_population=next_population, fitness=fitness, audit=audit,
        source_epoch=pins,pending_source_change=source_change,
        map_novelty_counts=dict(Counter(read_json(manifest_path).get('map_novelty',{}).values())),
        teachers=teacher_report,
        winners_by_source=dict(Counter(s['source'] for s in selections if s['passed'])),
        winner_count=len(winner_records), instance_count=len(paths),
        gold_comparison_count=len(gold_cases), model_beat_gold_count=sum(s['model_beat_gold'] for s in gold_cases),
        model_beat_gold_share=(sum(s['model_beat_gold'] for s in gold_cases)/len(gold_cases) if gold_cases else None),
        model_own_beat_gold_count=sum(s.get('model_own_beat_gold',s['model_beat_gold']) for s in gold_cases),
        model_plus_lns_beat_gold_count=sum(s.get('model_plus_lns_beat_gold',False) for s in gold_cases),
        winners_by_source_detail=dict(Counter(s.get('winner_source_detail',s['source']) for s in selections if s['passed'])),
        verification_counts=outcome_counts([check for s in selections for check in selection_checker_rows(s)]),
        greedy_fitness_counts={adapter:outcome_counts([s['greedy_fitness'][adapter] for s in selections])
                              for adapter in fitness.get('pass_counts',{})},
        finisher_outcomes=finisher_outcome_counts([v for s in selections for v in s.get('verifications',[]) ]),
        training_reverification=rechecked['counts'],
        children=children, timings=timings, datasets={str(r): b[1] for r,b in builds.items()},
        preference_pairs=len(pairs),preference_over_length_pairs=over_length_pairs,recovery=recovery_report,
        sample_status_counts=dict(pruning_counts),
        pruning=dict(enabled=getattr(args,'early_pruning',True),incumbent_source='currently_checked_gold',
            verified_incumbents=sum(r['passed'] for r in incumbent_results),
            gpu_time_saved_s=None,greedy_fitness_exempt=True),
        generations_per_hour=3600/timings['wall_s'])
    # Teacher elapsed work and selection elapsed work are disjoint jobs; their
    # sum is pooled CPU demand. Never add that to the overlapping decode wall.
    summary['sizing_observation']=dict(maps=len(paths),workers=args.workers,k=args.k,
        population_groups=len(population),model_batch_calls=decoded_calls,
        decode_wall_s=decode_wall_s,pipeline_wall_s=timings['decode_verify_pipeline_s'],
        map_cpu_worker_s=timings['teacher_worker_s']+sum(
            max(0.,s.get('verification_finished_monotonic',0.)-s.get('verification_started_monotonic',0.))
            for s in original_selections),
        dataset_s=timings.get('dataset_s',0.),
        training_reverification_s=timings.get('training_reverification_s',0.),
        sft_steps=args.steps,sft_child_s=[v for k,v in timings.items() if k.endswith('_train_s')],
        dpo_completed=any(c.get('preference_training') for c in children),
        dpo_reference_s=sum(c.get('preference_training',{}).get('reference_prepass_s',0.) for c in children),
        # Complete phase elapsed time includes its reference prepass. Record it
        # exactly once, conservatively under update if the internal split is absent.
        dpo_update_s=max(0.,sum(v for k,v in timings.items() if k.endswith('_dpo_s'))-sum(
            c.get('preference_training',{}).get('reference_prepass_s',0.) for c in children)),
        audit_s=timings.get('audit_s',0.),
        gold_control_trained=bool(audit and audit.get('gold_control',{}).get('trained_control')),
        gold_control_s=timings.get('gold_control_s'),
        baseline_audit_s=read_json(args.out/'baseline-timing.json').get('audit_s',0.)
            if (args.out/'baseline-timing.json').exists() else 0.)
    append_learning_curve(args.out / 'generation_times.csv', dict(generation=number,
        **timings, generations_per_hour=summary['generations_per_hour']))
    append_learning_curve(args.out/'method_history.csv',dict(generation=number,
        fittest_adapter=fitness['best_adapter'],fittest_method=fitness['best_method'],
        training_parent=parent,audit_rollback=bool(audit and audit['rolled_back']),
        preference_pairs=len(pairs)))
    write_json(summary_path, summary)
    write_json(args.out / 'survivor.json', dict(adapter=parent, generation=number))
    tracker.finish(status='completed',preference_pairs=len(pairs),winners_by_source=summary['winners_by_source_detail'])
    print(json.dumps(summary, allow_nan=False), flush=True)
    return next_population


def dry_run(args):
    """Saved training gold is a real candidate, never relabelled as model output."""
    records = saved_gold_records(args.gold)
    paths = []
    for rung, n in (('tiny', args.tiny), ('small', args.small)):
        eligible = [read_json(p)['input_path'] for p in records if rung in p.parts]
        if len(eligible) < n:
            raise RuntimeError(f'need {n} saved {rung} plans, found {len(eligible)}')
        paths.extend(eligible[:n])
    started = time.monotonic()
    rows = [select_job(path, [], args.corpus, args.gold, args.out / 'cpu-dry-run' / f'{n:03d}',
                       dict(polish_top=0, ballast_s=args.ballast_s)) for n, path in enumerate(paths)]
    result = dict(mode='cpu_saved_gold_only', cases=len(rows), passed=sum(r['passed'] for r in rows),
        wall_s=time.monotonic()-started,
        phase_s={key: sum(r['timings'][key] for r in rows) for key in ('rank_s', 'polish_s', 'verify_s')},
        rows=rows)
    write_json(args.out / 'cpu-dry-run' / 'summary.json', result)
    print(json.dumps({k:v for k,v in result.items() if k != 'rows'}))
    if result['passed'] != len(rows):
        raise RuntimeError('some saved plans failed current layered gates; see summary')


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--parent', type=Path)
    ap.add_argument('--gens', type=int, default=1)
    ap.add_argument('--population', '--P', '-P', type=int, choices=(1, 2, 3), default=2)
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--corpus', type=Path, default=ROOT / 'experiments/mvp/corpus')
    ap.add_argument('--gold', type=Path, default=ROOT / 'experiments/mvp/gold')
    ap.add_argument('--tiny', type=int, default=16)
    ap.add_argument('--small', type=int, default=32)
    ap.add_argument('--maps', type=int, help='override tiny/small counts with 1/3 tiny, 2/3 small')
    ap.add_argument('--val-tiny', type=int, default=10)
    ap.add_argument('--val-small', type=int, default=20)
    ap.add_argument('--k', type=int, choices=range(6, 9), default=8)
    ap.add_argument('--temperature', type=float, default=.8)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--dataset-workers', type=int, default=0,
                    help='0=auto; process-pool replay, capped by free ledger cores and existing lease')
    ap.add_argument('--adaptive-workers',action=argparse.BooleanOptionalAction,default=True,
                    help='choose only comparably measured settings within the reserved worker cap')
    ap.add_argument('--batch-size','--B','-B',type=int,default=8,
                    help='maximum stochastic decode rows per batch; greedy has its own batch')
    ap.add_argument('--early-pruning',action=argparse.BooleanOptionalAction,default=False,
                    help='optional D109; default off to avoid duplicate gold admission cost')
    ap.add_argument('--cached-activations',action=argparse.BooleanOptionalAction,default=False,
                    help='reserved speed integration; fails before GPU admission until supported')
    ap.add_argument('--steps', type=int, default=120)
    ap.add_argument('--audit-every', type=int, default=2)
    ap.add_argument('--audit-list',type=Path,default=ROOT/'experiments/mvp/valsplit/audit_small30.txt')
    ap.add_argument('--audit-batch-size',type=int,default=4)
    ap.add_argument('--audit-clock',default='2026-10-30T05:30:00-04:00')
    ap.add_argument('--target-generation-minutes',type=float,default=75.)
    ap.add_argument('--final-recipe-time-limit',type=float,default=600.)
    ap.add_argument('--control-max-gold',type=int,default=64,help='cap gold-only control unique targets per inherited phase')
    ap.add_argument('--learning-rate', type=float, default=5e-5)
    ap.add_argument('--max-tokens', type=int, default=20000)
    ap.add_argument('--polish-top', type=int, choices=(0, 1, 2), default=1)
    ap.add_argument('--polish-s', type=float, default=15.)
    ap.add_argument('--gold-map-fraction',type=float,default=.5)
    ap.add_argument('--teacher-planning-s',type=float,default=120.)
    ap.add_argument('--baseline-label',default='8.1')
    ap.add_argument('--ballast-s', type=float, default=float(os.environ.get('FINALIZE_S','120')))
    ap.add_argument('--dpo',action=argparse.BooleanOptionalAction,default=False,
                    help='compare child A SFT against child B SFT+DPO')
    ap.add_argument('--dpo-steps',type=int,default=16)
    ap.add_argument('--dpo-beta',type=float,default=.1)
    ap.add_argument('--dpo-nll-weight',type=float,default=.1)
    ap.add_argument('--dpo-max-pairs',type=int,default=16)
    ap.add_argument('--dpo-max-length',type=int,default=2048,
                    help='APPROXIMATION: cap full-sequence DPO graphs to tiny/small lengths')
    ap.add_argument('--dpo-pairs-per-map',type=int,default=2)
    ap.add_argument('--dagger',action=argparse.BooleanOptionalAction,default=False)
    ap.add_argument('--recovery-fraction',type=float,default=.14)
    ap.add_argument('--recovery-max-cases',type=int,default=4)
    ap.add_argument('--recovery-time-limit',type=float,default=30.)
    ap.add_argument('--cpu-dry-run', action='store_true')
    ap.add_argument('--gpu-smoke', action='store_true')
    ap.add_argument('--stop-file',type=Path,help='cooperative stop between generations')
    ap.add_argument('--recover-prepared',type=Path,help='D129 CPU-reverified saved generation; inherit then continue')
    ap.add_argument('--final-recipe-finalize-s',type=float,default=120.)
    ap.add_argument('--resume-boundary',type=Path,help='controller-owned completed-generation code-epoch checkpoint')
    return ap


def main():
    args = parser().parse_args()
    if args.resume_boundary is not None:
        args.recover_prepared = None  # completed inheritance cannot rerun on a later source boundary
        from llm.exit.code_epoch import restore_boundary
        boundary=restore_boundary(args.resume_boundary,source_pins())
        if args.out.resolve()!=Path(boundary['args']['out']).resolve():
            raise RuntimeError('source restart output directory changed')
        for name in ('tiny','small','k','workers','steps','batch_size'):
            setattr(args,name,boundary['args'][name])
        args.maps=None
        args._resume_population=boundary['population']
        args._start_generation=boundary['next_generation']
    os.environ['STOWAGE_IN_PORT_WORKERS']='1'
    if args.maps is not None:
        args.tiny, args.small = args.maps // 3, args.maps - args.maps // 3
    args.out = args.out.resolve()
    if args.recover_prepared is not None:
        from llm.exit.salvage import validate_prepared
        validate_prepared(args.recover_prepared,args)
    if args.dpo and (args.population<2 or min(args.dpo_steps,args.dpo_max_pairs,args.dpo_pairs_per_map,args.dpo_max_length)<1
                     or args.dpo_beta<=0 or args.dpo_nll_weight<0):
        raise SystemExit('DPO needs population >=2, positive steps/pairs/beta and nonnegative NLL weight')
    if (args.gens < 1 or args.workers < 1 or args.audit_every < 1 or args.steps < 1
            or args.tiny + args.small < 1 or min(args.tiny, args.small) < 0
            or (args.batch_size is not None and args.batch_size<1)):
        raise SystemExit('positive generations/workers and at least one map required')
    if not 0<=args.gold_map_fraction<=1 or args.teacher_planning_s<=0 or args.ballast_s<=0:
        raise SystemExit('gold fraction must be in [0,1]; teacher/finisher budgets positive')
    if args.cached_activations and not args.cpu_dry_run:
        raise SystemExit('--cached-activations awaits the speed prototype integration/numerical checks; no GPU started')
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.cpu_dry_run and not args.gpu_smoke:
        from llm.exit.audit import reconcile_failed_gate
        reconcile_failed_gate(args.out,fallback_adapter=args.parent)
    if not args.cpu_dry_run and not args.gpu_smoke and (args.out/'terminal.json').exists():
        print(json.dumps(read_json(args.out/'terminal.json')),flush=True)
        return
    with (args.out / '.runner.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from llm.exit.resources import reservation,background_cores
        if args.cpu_dry_run:
            # 29 Oct CPU probes measured 14.98s tiny and 14.96s small, including
            # cold load. Scale linearly with 30% scheduling overhead.
            with reservation(args.out, cores=1, ram_gb=2, gpu=False,
                             estimate_s=15*1.3*(args.tiny+args.small)):
                dry_run(args)
            return
        if args.parent is None:
            raise SystemExit('--parent <adapter> required')
        args.parent = args.parent.resolve()
        from llm.exit.resources import require_current_parent
        require_current_parent(args.parent)
        if not args.gpu_smoke:
            from llm.exit.review_gate import require_review_gate
            require_review_gate()
            from llm.exit.smoke_once import attempt_path
            marker=attempt_path(args.parent,source_pins())
            if not marker.exists() or read_json(marker).get('status')!='done':
                raise RuntimeError('current adapter/code GPU smoke has not passed; no generation started')
        reserved_verifiers=1 if args.gpu_smoke else args.workers
        # APPROXIMATION: D123 reserves one host/verification core for the serial
        # smoke; generation pools retain their two-core host margin.
        with reservation(args.out, cores=1 if args.gpu_smoke else reserved_verifiers+2,
                         ram_gb=64+reserved_verifiers*2,
                         load_policy='gpu_smoke' if args.gpu_smoke else 'gpu_generation',
                         gpu=True, estimate_s=600 if args.gpu_smoke else
                         (4500+600*args.dpo)*args.gens*max(1, args.population/2)) as allocation:
            if args.gpu_smoke:
                from llm.exit.runtime import preflight_mlx_device
                # Infrastructure failure precedes the idempotent decode claim.
                # A host without Metal has attempted no model sampling.
                preflight_mlx_device()
                from llm.exit.smoke_once import attempt_path, claim_attempt
                with claim_attempt(attempt_path(args.parent, source_pins())):
                    smoke(args)
                return
            from llm.exit.runtime import release_model
            args._reservation=allocation
            try:
                if args.recover_prepared is not None:
                    from llm.exit.salvage import inherit_prepared
                    inherit_prepared(args)
                run_loop(args)
            except BaseException as error:
                if not isinstance(error,SystemExit) or error.code!=75:
                    import traceback
                    write_json(args.out/'generation-error.json',dict(error=f'{type(error).__name__}: {error}',
                        generation=getattr(args,'_active_generation',None),traceback=traceback.format_exc()))
                    if getattr(args,'_active_tracker',None) is not None:
                        args._active_tracker.finish(status='failed',error=str(error))
                raise
            finally:release_model()


def run_loop(args):
    from llm.exit.review_gate import require_review_gate
    from llm.exit.resources import background_cores
    from llm.exit.audit import frozen_audit_paths,ClockAuditDue,check_clock
    from llm.exit.capacity import CapacitySizer
    from llm.exit.code_epoch import boundary_plan,save_boundary,RESTART_EXIT_CODE
    if (args.out/'terminal.json').exists():return
    loaded_pins=source_pins()
    args._loaded_source_pins=loaded_pins
    validation=frozen_audit_paths(ROOT,args.corpus,args.audit_list,args.out,baseline_label=getattr(args,'baseline_label','8.1'))
    if getattr(args,'_start_generation',0)==0 and not (args.out/'gen-000'/'summary.json').exists():
        # D113: generation0 starts with its initial audit. Publish only inside
        # the admitted GPU job; this includes baseline time in the120min watch.
        from llm.exit.progress import start_generation
        tracker=start_generation(args.out,0,workers=args.workers,maps=args.tiny+args.small,k=args.k,
                                 phase='baseline_audit')
        args._generation0_tracker=tracker;args._active_tracker=tracker;args._active_generation=0
        with tracker.phase('baseline_audit'):
            ensure_baseline(args,validation)
    else:ensure_baseline(args,validation)
    population = getattr(args,'_resume_population',None) or [dict(adapter=str(args.parent), learning_rate=args.learning_rate,
                       temperature=args.temperature,replay_ratio=.67,method='sft',dpo_beta=args.dpo_beta)]
    worker_ceiling=6
    def restart_if_needed(next_number):
        current=source_pins();change=boundary_plan(loaded_pins,current)
        if change['kind']=='other':raise RuntimeError('unapproved source change at generation boundary')
        if change['kind'] in ('mask_speedup','checker_watchdog'):
            save_boundary(args.out/'epoch-boundary.json',number=next_number,population=population,
                args={k:v for k,v in vars(args).items() if not k.startswith('_')},
                loaded_pins=loaded_pins,current_pins=current)
            raise SystemExit(RESTART_EXIT_CODE)
    for number in range(getattr(args,'_start_generation',0),args.gens):
        if (args.out/'terminal.json').exists():return
        if args.stop_file is not None and args.stop_file.exists():return
        require_review_gate()
        restart_if_needed(number)
        try:
            check_clock(args)
            if args.adaptive_workers:
                recommendation=CapacitySizer(args.out/'capacity.json',max_workers=worker_ceiling).recommend(
                    current_workers=args.workers,gpu_saturated=True,background_workers=background_cores())
                proposed=recommendation['workers']
                allocation=getattr(args,'_reservation',None)
                if proposed!=args.workers and allocation is not None:
                    if not allocation.resize_verifiers(proposed):
                        recommendation.update(proposed_workers=proposed,workers=args.workers,
                            admission='resize deferred; existing reservation retained')
                args.workers=recommendation['workers']
                write_json(args.out/'capacity-recommendation.json',recommendation)
            schedule = CorpusSchedule(args.corpus, args.out / 'schedule.json')
            population = run_generation(number, population, args, schedule)
        except ClockAuditDue:
            clock_audit(args,population)
            if (args.out/'terminal.json').exists():return
            schedule=CorpusSchedule(args.corpus,args.out/'schedule.json')
            population=run_generation(number,population,args,schedule)
        if (args.out/'terminal.json').exists():return
        if number==0:resize_after_pilot(read_json(args.out/'gen-000'/'summary.json'),args)
        restart_if_needed(number+1)
    try:check_clock(args)
    except ClockAuditDue:
        clock_audit(args,population)
        if (args.out/'terminal.json').exists():return
    final_population_audit(population,args)


def smoke(args):
    from llm.exit.runtime import clear_model_cache, generate
    from llm.exit.selection import verify_candidate
    from stowage.io import load_instance
    schedule = CorpusSchedule(args.corpus, args.out / 'smoke-schedule.json')
    paths = ([schedule.root / e['path'] for e in schedule.state['generations']['0']]
             if '0' in schedule.state['generations'] else schedule.next_train(2, 0, 0))
    rows = []
    started = time.monotonic()
    class SmokeDeadline(BaseException):
        pass
    def expired(*_):
        raise SmokeDeadline()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 600)
    timed_out = False
    try:
        winner_records = []
        for index, path in enumerate(paths):
            raw = generate(path, args.parent, k=4, max_tokens=args.max_tokens)
            results = [verify_candidate(load_instance(path), Candidate(c['plan'], 'model'),
                        ballast_s=args.ballast_s) for c in raw['candidates']]
            rows.append(dict(instance_path=str(path), generation=raw['timings'], samples=raw['samples'],
                securing=raw['securing'],memory_config=raw['memory_config'],
                gates=[dict(passed=r.passed, weighted_moves=r.weighted_moves, **r.details) for r in results]))
            candidates = [dict(plan=c['plan'], source='model', candidate_id=f'model-{c["sample"]}')
                          for c in raw['candidates']]
            selected = select_job(path, candidates, args.corpus, args.gold,
                args.out / 'smoke-cases' / f'{index:03d}', dict(polish_top=0, ballast_s=args.ballast_s))
            if selected['passed']:
                winner_records.append(selected['winner_record'])
        training = None
        if len(winner_records) == 2:
            # APPROXIMATION: smoke holds one of its two TRAIN maps out of updates
            # for loss-only evaluation; it never touches fixed audit/test maps.
            from llm.exit.runtime import train
            build_training_dataset(winner_records[:1], [], args.out / 'smoke-data',
                validation_records=winner_records[1:2])
            training = train(args.parent, args.out / 'smoke-data', args.out / 'smoke-child',
                             steps=4, lr=args.learning_rate)
            # Exercise swapping back after training, with no additional decode.
            from llm.exit.runtime import _resident_for
            from llm.stowlm.planner import StowLMPlanner
            _resident_for(StowLMPlanner(adapter_path=args.parent))
        from llm.exit.audit_runtime import generate_audit_batch
        batched=generate_audit_batch(paths,args.parent,batch_size=2,max_tokens=args.max_tokens,
                                    corpus_root=args.corpus,identity_role='train')
        audit_batch=[]
        for path,raw in zip(paths,batched):
            checked=verify_candidate(load_instance(path),Candidate(raw['plan'],'model'),
                ballast_s=args.ballast_s) if raw['plan'] else None
            audit_batch.append(dict(instance_path=str(path),status=raw['status'],
                securing=raw['securing'],memory_config=raw['memory_config'],
                passed=bool(checked and checked.passed),
                verification=checked.details if checked else None))
    except SmokeDeadline:
        timed_out = True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        clear_model_cache()
        write_json(args.out / 'gpu-smoke.json', dict(rows=rows, timed_out=timed_out,
            training=locals().get('training'),
            batched_audit=locals().get('audit_batch'),
            wall_s=time.monotonic()-started))
    securing_rows=[r['securing'] for r in rows+locals().get('audit_batch',[])]
    if (not securing_rows or sum(r['evaluations'] for r in securing_rows)<=0
            or any(r['fail_open'] or r['unavailable'] for r in securing_rows)):
        raise RuntimeError('GPU smoke securing inactive or unavailable; no generation admitted')
    if (timed_out or len(rows) != 2 or training is None or len(audit_batch)!=2
            or not all(row['passed'] for row in audit_batch)):
        raise RuntimeError('GPU smoke incomplete within ten-minute deadline')


if __name__ == '__main__':
    main()
