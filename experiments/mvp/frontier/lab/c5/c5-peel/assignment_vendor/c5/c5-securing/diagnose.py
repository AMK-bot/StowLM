"""Part A: read every saved failed stow's violations and join them to the map. Runs no checker."""
import collections
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[6]
L = ROOT/'experiments/mvp/frontier/lab/c4/c2-layout'
OUT = Path(__file__).parent/'diagnosis'
RUNS = ['ladder60-70-dense', 'f80-dense', 'medium12-dense']
KEY = re.compile(r'\] (max_\w+) exceeds')
COND = re.compile(r'^\[(\S+) (\w+)\]')


def rung(row):
    i = row['instance_id']
    for f in ('f60', 'f70', 'f80'):
        if f'-{f}-' in i:
            return f
    return 'd185'


def slot_parts(slot):
    return int(slot[:3]), int(slot[3:5]), int(slot[5:7])


def trial_row(row, t, inst):
    rep = json.loads(Path(t['report']).read_text())
    plan = json.loads(Path(t['plan']).read_text())
    boxes = {b['id']: b for b in inst['containers']}
    planned = {p['container_id'] if 'container_id' in p else None for p in []}
    load_ids = set(inst['load_list'])
    per_key, per_cond, chains, over_by = collections.Counter(), collections.Counter(), {}, collections.Counter()
    worst = collections.defaultdict(float)
    for v in t['violations']:
        if v['rule_id'] != 'iacs.member_irs.container_securing':
            continue
        m = KEY.search(v['message'])
        c = COND.match(v['message'])
        if not m or not c:
            per_key['other:'+v['message'][:60]] += 1
            continue
        k, phase = m.group(1), c.group(2)
        worst[k] = max(worst[k], v['measured'] or 0.)
        per_key[k] += 1
        per_cond[phase] += 1
        ident = tuple(v['slots'])
        planned_here = len(v['containers'])
        prior = chains.get(ident)
        if prior is None or (v['measured'] or 0) > prior[0]:
            chains[ident] = (v['measured'] or 0., k, phase, planned_here)
    cond = {}
    for name, part in rep['parts'].items():
        if name.startswith('condition:') and name.endswith(':lashing'):
            m = part['metrics']
            phase = part['name'].split()[-1].rstrip(']')
            cond[phase] = {k: m.get(k) for k in ('gm_m', 'roll_period_s', 'roll_angle_rad', 'physical_box_count',
                                                 'max_racking_ratio', 'max_longitudinal_racking_ratio',
                                                 'max_corner_post_ratio', 'max_corner_tension_ratio',
                                                 'max_lashing_ratio', 'max_fitting_ratio', 'max_cell_guide_ratio')}
    return dict(instance=row['instance_id'], rung=rung(row), boxes=len(load_ids), lane=t['lane'], lifts=t['lifts'],
                rules=[r.split('.')[-1] for r in t['rules']], violations=len(t['violations']), per_key=dict(per_key),
                per_cond=dict(per_cond), worst=dict(worst), conditions=cond,
                chains=[dict(slots=list(s), ratio=c[0], key=c[1], phase=c[2], planned=c[3]) for s, c in
                        sorted(chains.items(), key=lambda kv: -kv[1][0])],
                plan=t['plan'], report=t['report'])


def main():
    OUT.mkdir(exist_ok=True)
    rows = []
    for run in RUNS:
        for path in sorted((L/'results'/run).glob('*.json')):
            row = json.loads(path.read_text())
            if 'instance_id' not in row or row.get('passed'):
                continue
            inst = json.loads((ROOT/row['input']).read_text())
            for t in row['method_metadata'].get('trials', []):
                if not t.get('full_check'):
                    rows.append(trial_row(row, t, inst))
    (OUT/'trials.json').write_text(json.dumps(rows, indent=1)+'\n')
    print(len(rows), len({r['instance'] for r in rows}))


main()
