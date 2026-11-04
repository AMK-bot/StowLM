"""Same-start 2x2 search ablation plus unmodified Frontier search reference.

Uses the frozen lab sets and ordered stored starts; writes candidates for later
full checker admission. Candidate scores in this file are NOT passes.
"""
import argparse
import json
from pathlib import Path
from time import perf_counter

from alns import Problem, search as frontier_search
from catalog import load_baseline, load_set
from stowage.actions.space import Place
from stowage.io import load_instance
from search import run
from recombine import recombine

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget-s', type=float, default=10.)
    parser.add_argument('--set', default='quick_small')
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--out', type=Path, default=HERE/'results'/'ablation')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    baseline = load_baseline()
    for rel in load_set(args.set):
        ins = load_instance(rel)
        target = args.out/f'{ins.id}.json'
        if target.exists():
            continue
        base = baseline[ins.id]
        actions = base.get('ordered_actions')
        if not actions:
            target.write_text(json.dumps(dict(id=ins.id, input=rel, missing_start=True)))
            continue
        start = tuple(Place(a['box'], a['slot']) for a in actions)
        p = Problem(ins)
        rows, bank = [], []
        # Rotate arm order by map to reduce systematic machine-load bias.
        arms = [('swap', 'replay'), ('cycle', 'replay'), ('swap', 'fast'), ('cycle', 'fast')]
        offset = len(list(args.out.glob('*.json'))) % len(arms)
        arms = arms[offset:] + arms[:offset]
        for neighborhood, scorer in arms:
            plans, stats = run(p, [start], budget_s=args.budget_s, seed=args.seed,
                               neighborhood=neighborhood, scorer=scorer)
            bank += plans
            row = dict(neighborhood=neighborhood, scorer=scorer, **stats)
            rows.append(row)
            print(ins.id, neighborhood, scorer, 'best', stats.get('best'),
                  'eval', stats['evaluations'], 's', round(stats['wall_s'], 2), flush=True)
        began = perf_counter()
        plans, stats = frontier_search(ins, start, time_limit_s=args.budget_s,
                                       seed=args.seed, problem=p, keep=6)
        bank += [s.actions for s in plans]
        rows.append(dict(neighborhood='frontier09', scorer='replay', best=plans[0].objective,
                         iterations=stats.iterations, wall_s=perf_counter()-began,
                         accepted=stats.accepted, improved=stats.improved,
                         infeasible=stats.infeasible))
        fused = recombine(p, bank[:12], deadline=__import__('time').monotonic()+min(5.,args.budget_s), seed=args.seed)
        bank += fused
        fused_stats = recombine.last_stats
        unique = {}
        for acts in bank:
            scored = p.score(acts)
            if scored is not None:
                key = tuple((a.box,a.slot) for a in acts)
                unique[key] = dict(objective=scored.objective,
                                   actions=[dict(box=a.box,slot=a.slot) for a in acts])
        candidates = sorted(unique.values(),key=lambda s:s['objective'])
        record = dict(id=ins.id,input=rel,seed=args.seed,budget_s=args.budget_s,
                      stored_lifts=base['lifts'],start_objective=p.score(start).objective,
                      arms=rows,recombination=fused_stats,candidates=candidates)
        target.write_text(json.dumps(record,indent=2)+'\n')
        print(ins.id,'completed',len(candidates),'candidate plans',flush=True)


if __name__ == '__main__':
    main()
