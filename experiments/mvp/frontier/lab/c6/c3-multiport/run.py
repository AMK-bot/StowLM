"""Manifest-only cold workers with unchanged checker and frozen tier policy.

The observer retains concrete checked incumbents before a bounded engine call
returns. It never changes the checker result or suppresses a violation. Tier
classification is applied after full-enforcement search, never to save lifts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter, time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
             'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'STOWAGE_IN_PORT_WORKERS'):
    os.environ[name] = '1'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

ARMS = ('engine12', 'port_lp')
CAP_S = 600.
POLICY = dict(proposal_s=240., finalize_s=120., finish_candidates=2,
              whole_arm_map_s=CAP_S, seed=0, numerical_threads=1,
              cold=True, tier_search='classify checked candidates after full-enforcement work',
              checkpoint='retain concrete independently checked incumbent even if outer cap interrupts')


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(value, indent=2, default=str)+'\n')
    tmp.replace(path)


if __name__ == '__mp_main__' and '--worker' in sys.argv:
    value = sys.argv[sys.argv.index('--out')+1] if '--out' in sys.argv else 'results/main'
    output = (HERE/value).resolve()
    output.relative_to(HERE)
    write_json(output/'pids'/f'native-{os.getpid()}.json', dict(pid=os.getpid(), parent_pid=os.getppid(),
               argv=sys.argv, started=time(), kind='our native spawned worker'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(paths):
    h = hashlib.sha256()
    for path in sorted(set(paths)):
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def bindings(arm):
    folders = ['frozen_frontier', 'frozen_reporting']
    folders += ['frozen_engine12'] if arm == 'engine12' else ['frozen_securing']
    files = [HERE/'run.py', HERE/'frozen_env.py', HERE/'frozen_checks.py']
    files += [p for folder in folders for p in (HERE/folder).rglob('*.py')]
    files += [HERE/'frozen_reporting/tiers.toml']
    if arm == 'port_lp':
        files += [HERE/'method.py', HERE/'port_order.py']
    return dict(implementation_sha256=tree_digest(files),
                native_sha256=tree_digest(list((ROOT/'src/stowage').rglob('*.py'))),
                params_sha256=tree_digest([p for p in (ROOT/'params').rglob('*') if p.is_file()]),
                cohort_sha256=digest(HERE/'cohort-manifest.json'), policy=POLICY)


def config_for(entry, arm):
    return dict(bindings(arm), input=entry['path'], size=entry['size'], ports=entry['ports'],
                vessel=entry['vessel'], fill_pct=entry['fill_pct'], development=entry['development'])


def run_key(config, input_sha):
    return hashlib.sha256(json.dumps(dict(config=config, input_sha256=input_sha), sort_keys=True).encode()).hexdigest()


def parts(report):
    c = report.count
    return dict(relocations=c.relocations, hatch_now=c.hatch_cover_lifts,
                hatch_later=sum(f.hatch_cover_lifts for f in c.future), restow_now=c.restow_lifts,
                restow_later=sum(f.restow_lifts for f in c.future))


class Capture:
    def __init__(self, instance, target, base):
        from frozen_reporting.tiers import load_table
        self.instance, self.target, self.base = instance, target, base
        self.table = load_table()
        self.best, self.partial, self.invalid = None, None, None
        self.seen, self.summaries = set(), []
        self.started = perf_counter()

    def observe(self, instance, plan, report, *args, **kwargs):
        from frozen_reporting.tiers import analyze_report, rank_analysis
        from stowage.actions import get_action_space
        from stowage.actions.space import Place
        if instance.id != self.instance.id:
            return
        if instance is not self.instance:
            # A diagnostic trial may alter an instance. Its result cannot be
            # credited against the original cohort input without this check.
            from stowage.checker import check
            report = check(self.instance, plan)
        data = plan.model_dump(mode='json')
        signature = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
        if signature in self.seen:
            return
        self.seen.add(signature)
        checked_epoch = time()
        deadline = os.environ.get('C6_WORKER_DEADLINE')
        if deadline is not None and checked_epoch > float(deadline):
            return  # Never replace an in-budget checkpoint with a late check.
        analysis = analyze_report(report, self.table, instance=self.instance)
        actions = tuple(get_action_space('boxslot').from_plan(self.instance, plan))
        placements = [a for a in actions if isinstance(a, Place)]
        assignment = {a.box: a.slot for a in placements}
        all_aboard = (len(placements) == len(assignment) and set(self.instance.load_list) <= set(assignment)
                      and analysis['ashore_count'] == 0)
        row = dict(signature=signature, checked_s=perf_counter()-self.started,
                   checked_epoch=checked_epoch, level=analysis['level'],
                   passed=bool(report.passed), all_aboard=all_aboard,
                   analysis=analysis, parts=parts(report) if report.count is not None else None,
                   lifts=float(report.count.weighted_nonproductive) if report.count is not None else None,
                   assignment=assignment, plan=data, report=report.model_dump(mode='json'))
        self.summaries.append({k: row[k] for k in ('signature', 'checked_s', 'checked_epoch', 'level', 'passed', 'all_aboard', 'lifts', 'analysis')})
        if analysis['eligible'] and all_aboard:
            key = (analysis['level'], rank_analysis(analysis))
            if self.best is None or key < self.best[0]:
                self.best = key, row
        elif analysis['eligible'] and analysis['level'] <= 3:
            key = rank_analysis(analysis)
            if self.partial is None or key < self.partial[0]:
                self.partial = key, row
        elif self.invalid is None or rank_analysis(analysis) < rank_analysis(self.invalid['analysis']):
            self.invalid = row
        self.save(interim=True)

    def save(self, *, interim=False, **extra):
        selected = self.best[1] if self.best else None
        row = dict(self.base, complete=not interim, passed=bool(selected and selected['passed']),
                   completed=None if selected is None else dict(level=selected['level'],
                       column=('T5','T4','T3','T2','T1','T0 only')[selected['level']],
                       checked_s=selected['checked_s'], checked_epoch=selected['checked_epoch'],
                       analysis=selected['analysis']),
                   best_safe_partial=None, observed_full_checks=len(self.summaries),
                   checked_candidates=self.summaries, wall_s=perf_counter()-self.started,
                   plan=selected['plan'] if selected else None, report=selected['report'] if selected else None,
                   assignment=selected['assignment'] if selected else None,
                   lifts=selected['lifts'] if selected else None, parts=selected['parts'] if selected else None,
                   best_ineligible=self.invalid, **extra)
        if self.partial:
            p = self.partial[1]
            row['best_safe_partial'] = dict(p, boxes_aboard_percent=100*(1-p['analysis']['ashore_count']/len(self.instance.load_list)))
        row['replay_legal_complete'] = bool(selected or any(s['all_aboard'] and not s['analysis']['broken_by_tier']['T0'] for s in self.summaries))
        row['status'] = 'passed' if row['passed'] else 'completed_relaxed' if selected else 'not_completed'
        checkpoint = self.target.parent.parent/'checkpoints'/self.target.parent.name/self.target.name
        write_json(checkpoint if interim else self.target, row)
        return row


def worker(args):
    import frozen_env  # noqa: F401
    import frozen_checks
    from stowage.io import load_instance
    from stowage.checker import check
    from frozen_frontier.alns import Problem
    manifest = json.loads((HERE/'cohort-manifest.json').read_text())
    entry = next(r for r in manifest['maps'] if r['path'] == args.input)
    path = (ROOT/entry['path']).resolve()
    path.relative_to(HERE/'cohort')
    if digest(path) != entry['sha256'] or (args.size, args.ports) != (entry['size'], entry['ports']):
        raise ValueError('Changed input or stratum outside frozen cohort')
    config = config_for(entry, args.arm)
    if args.phase == 'frozen':
        frozen = json.loads((HERE/'FREEZE.json').read_text())
        if bindings(args.arm) != frozen['bindings'][args.arm]:
            raise ValueError('Frozen runtime/source/cohort bindings changed')
    key = run_key(config, entry['sha256'])
    output = (HERE/args.out).resolve()
    output.relative_to(HERE)
    target = output/args.arm/f"{entry['instance_id']}.json"
    if target.exists():
        old = json.loads(target.read_text())
        if old.get('complete') and old.get('run_key') == key:
            print(json.dumps(dict(arm=args.arm, id=entry['instance_id'], cached=True)), flush=True)
            return
    write_json(output/'pids'/f"{args.arm}-{entry['instance_id']}.json", dict(pid=os.getpid(), parent_pid=os.getppid(),
               arm=args.arm, instance_id=entry['instance_id'], started=time(), argv=sys.argv))
    instance = load_instance(path)
    if instance.id != entry['instance_id']:
        raise ValueError('Instance identity mismatch')
    base = dict(instance_id=instance.id, input=entry['path'], input_sha256=entry['sha256'],
                arm=args.arm, size=entry['size'], ports=entry['ports'], vessel=entry['vessel'],
                fill_pct=entry['fill_pct'], actual_fill=entry['fill'], development=entry['development'],
                n_boxes=len(instance.load_list), run_key=key, config=config, cold=True,
                loadavg_start=list(os.getloadavg()), capture_started_epoch=time())
    capture = Capture(instance, target, base)
    # The delegate only observes copied code's real full-check calls. The
    # callback cannot make a rejected plan pass or change native enforcement.
    frozen_checks.set_observer(capture.observe)
    meta, failure, interrupted = {}, None, False
    print(json.dumps(dict(arm=args.arm, id=instance.id, pid=os.getpid(), started=time(), budget=POLICY)), flush=True)
    try:
        if args.arm == 'engine12':
            from frozen_engine12.planner import plan_and_check
            result, meta = plan_and_check(instance, budget_s=POLICY['proposal_s'], finalize_s=POLICY['finalize_s'], seed=0)
            if result is not None and result.plan is not None:
                capture.observe(instance, result.plan, check(instance, result.plan))
        else:
            import method
            context = dict(problem=Problem(instance), stored=None, stored_plan=None, stored_lifts=None,
                           method_metadata={}, finalize_s=POLICY['finalize_s'], tries=POLICY['finish_candidates'])
            meta = context['method_metadata']
            actions = method.propose(instance, seed=0, budget_s=POLICY['proposal_s'], context=context)
            plans = list(context.get('_candidate_plans', {}).values())
            for plan in plans:
                capture.observe(instance, plan, check(instance, plan))
            for k, candidate in enumerate(actions[:POLICY['finish_candidates']]):
                scored = context['problem'].score(candidate)
                if scored is None:
                    continue
                plan, finish_meta = method.finish(instance, scored.plan, seed=0, finalize_s=POLICY['finalize_s'],
                                                  label=f'c6-{k}', context=context)
                if plan is not None:
                    capture.observe(instance, plan, check(instance, plan))
                meta.setdefault('finishing', []).append(finish_meta)
                if capture.best and capture.best[1]['passed']:
                    break
            meta.update(context['method_metadata'])
    except KeyboardInterrupt:
        interrupted = True
        failure = 'Declared whole-arm/map work limit or owned-controller interruption'
    except Exception as error:
        import traceback
        failure = f'{type(error).__name__}: {error}'
        meta['traceback'] = traceback.format_exc()
    finally:
        frozen_checks.set_observer(None)
    row = capture.save(method_metadata=meta, failure_reason=failure, interrupted=interrupted,
                       loadavg_end=list(os.getloadavg()),
                       outer_timeout=interrupted, returned_normally=not interrupted and failure is None)
    print(json.dumps(dict(arm=args.arm, id=instance.id, passed=row['passed'], level=(row['completed'] or {}).get('level'),
               lifts=row['lifts'], wall_s=round(row['wall_s'],2), reason=failure)), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worker', action='store_true')
    p.add_argument('--arm', choices=ARMS)
    p.add_argument('--input')
    p.add_argument('--size', default='medium')
    p.add_argument('--ports', type=int)
    p.add_argument('--phase', choices=('dev','frozen'), default='dev')
    p.add_argument('--out', default='results/dev')
    p.add_argument('--freeze', action='store_true')
    args = p.parse_args()
    if args.freeze:
        manifest = json.loads((HERE/'cohort-manifest.json').read_text())
        if len(manifest['maps']) != 32:
            raise ValueError('Need all32 fixed maps before freezing')
        write_json(HERE/'FREEZE.json', dict(frozen_at=time(), bindings={a:bindings(a) for a in ARMS},
                   development_ids=[r['instance_id'] for r in manifest['maps'] if r['development']]))
        return
    if not args.worker:
        p.error('Use controller.py for the manifest sweep, or --worker / --freeze')
    worker(args)


if __name__ == '__main__':
    main()
