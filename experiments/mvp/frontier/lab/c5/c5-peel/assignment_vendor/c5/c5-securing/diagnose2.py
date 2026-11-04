"""Part A, second pass: exact chain table for the closest saved stow of each of the 35 failed maps and the 13 passes."""
import json
from pathlib import Path
import sys

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path[:0] = [str(HERE)]
from chainlab import chain_ratios, condition_terms, motions
from stowage.checker import lashing
from stowage.checker.full import departure_condition
from stowage.model import Instance, MovePlan


def stow_table(instance, plan):
    ship = departure_condition(instance, plan)
    terms = condition_terms(instance, ship)
    m = motions(terms)
    graph = lashing._load_graph(instance, ship, m)
    rows = []
    for chain in lashing._chains(graph):
        qs = [q for q, _ in chain]
        if not qs[0].on_deck:
            continue
        r, j = chain_ratios(chain)
        key = max(r, key=r.get)
        planned = sum(not getattr(q.container, 'background', False) for q in qs)
        rows.append(dict(root=qs[0].slot, n=len(qs), planned=planned, base=round(qs[0].floor, 2), ratio=r[key], key=key,
                         joint=j.get(key), ratios={k: round(v, 3) for k, v in r.items()},
                         masses=[round(q.mass/1000, 1) for q in qs], heights=[round(q.height, 2) for q in qs],
                         lengths=[q.container.length_ft for q in qs], at=[round(q.at, 2) for q in qs],
                         oog=sum(q.container.oog is not None for q in qs)))
    gm = {}
    worst = {}
    for g in (terms['gm'], .5, 1., 1.5, 2., 2.5):
        mm = motions(terms, g)
        gg = lashing._load_graph(instance, ship, mm)
        worst[round(float(g), 3)] = float(max([max(chain_ratios(c)[0].values()) for c in lashing._chains(gg) if c[0][0].on_deck], default=0.))
    nowind = float(max([max(chain_ratios(c, wind=False)[0].values()) for c in lashing._chains(graph) if c[0][0].on_deck], default=0.))
    return dict(gm=terms['gm'], draught=terms['draught'], period=m.period_roll, phi=m.phi, roll=m.roll, sway=m.sway,
                z0=m.rotation_z, chains=rows, worst_by_gm=worst, worst_no_wind=nowind)


def main():
    trials = json.loads((HERE/'diagnosis/trials.json').read_text())
    # Closest trial per map: fewest rule families, then lowest worst-ratio.
    best = {}
    for t in trials:
        rank = (len(t['rules']), max(t['worst'].values(), default=9.))
        if t['instance'] not in best or rank < best[t['instance']][0]:
            best[t['instance']] = (rank, t)
    L = ROOT/'experiments/mvp/frontier/lab/c4/c2-layout/results'
    rows = []
    cache = {}
    for run in ('ladder60-70-dense', 'f80-dense', 'medium12-dense'):
        for path in sorted((L/run).glob('*.json')):
            row = json.loads(path.read_text())
            if 'instance_id' not in row:
                continue
            i = row['instance_id']
            instance = Instance.model_validate_json((ROOT/row['input']).read_text())
            if row.get('passed'):
                plan, kind, rung = MovePlan.model_validate(row['plan']), 'pass', None
            else:
                plan = MovePlan.model_validate_json(Path(best[i][1]['plan']).read_text())
                kind, rung = 'fail', best[i][1]['rung']
            t = stow_table(instance, plan)
            hold_cap = sum(not s.on_deck for s in instance.vessel.stacks)
            t.update(instance=i, kind=kind, boxes=len(instance.load_list), run=run, lifts=row.get('lifts') if row.get('passed') else best[i][1]['lifts'],
                     rules=None if row.get('passed') else best[i][1]['rules'])
            rows.append(t)
            print(i[-34:], kind, round(t['gm'], 2), len([c for c in t['chains'] if c['ratio'] > 1]), round(max(c['ratio'] for c in t['chains']), 2), flush=True)
    (HERE/'diagnosis/chains.json').write_text(json.dumps(rows, indent=0)+'\n')


main()
