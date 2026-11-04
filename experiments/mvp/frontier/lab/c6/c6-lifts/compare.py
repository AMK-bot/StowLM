"""Paired fresh-arm comparison; never called by a solver method."""
import argparse
import importlib.util
import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
PARTS = ('relocations', 'hatch_now', 'hatch_later', 'restow_now', 'restow_later')


def rows(directory):
    result = {}
    for path in sorted(Path(directory).glob('*.json')):
        row = json.loads(path.read_text())
        if isinstance(row, dict) and row.get('instance_id'):
            result[row['instance_id']] = row
    return result


def comparison(a, b, selected=None):
    ids = sorted((set(a) | set(b)) if selected is None else selected)
    missing = [i for i in ids if i not in a or i not in b]
    both = [i for i in ids if i in a and i in b]
    paired = [i for i in both if a[i]['passed'] and b[i]['passed']]
    changes = [b[i]['lifts']-a[i]['lifts'] for i in paired]
    out = dict(maps=len(ids), measured_pairs=len(both), missing=missing,
               baseline_passes=sum(a[i]['passed'] for i in both),
               candidate_passes=sum(b[i]['passed'] for i in both), mutual_passes=len(paired),
               passes_lost=[i for i in both if a[i]['passed'] and not b[i]['passed']],
               passes_gained=[i for i in both if b[i]['passed'] and not a[i]['passed']],
               wins=sum(x < -1e-9 for x in changes), ties=sum(abs(x) <= 1e-9 for x in changes),
               losses=sum(x > 1e-9 for x in changes),
               baseline_mean_lifts=mean(a[i]['lifts'] for i in paired) if paired else None,
               candidate_mean_lifts=mean(b[i]['lifts'] for i in paired) if paired else None,
               mean_change=mean(changes) if changes else None,
               baseline_mean_wall_s=mean(a[i]['wall_s'] for i in both) if both else None,
               candidate_mean_wall_s=mean(b[i]['wall_s'] for i in both) if both else None)
    out['parts'] = {name: {part: mean(data[i]['parts'][part] for i in paired) if paired else None
                          for part in PARTS} for name, data in [('baseline', a), ('candidate', b)]}
    out['loadavg_start'] = {name: [data[i].get('method_metadata', {}).get('loadavg_start') for i in both]
                            for name, data in [('baseline', a), ('candidate', b)]}
    out['by_map'] = [dict(instance_id=i, baseline_passed=a[i]['passed'], candidate_passed=b[i]['passed'],
                          baseline_lifts=a[i]['lifts'], candidate_lifts=b[i]['lifts'],
                          baseline_parts=a[i]['parts'], candidate_parts=b[i]['parts'],
                          baseline_wall_s=a[i]['wall_s'], candidate_wall_s=b[i]['wall_s']) for i in both]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    a, b = rows(args.baseline), rows(args.candidate)
    split = json.loads((HERE/'SPLIT.json').read_text())
    groups = dict(all=comparison(a, b))
    if any(i in a or i in b for i in split['development']):
        groups.update(dev6=comparison(a, b, split['development']),
                      held22=comparison(a, b, split['held']),
                      primary28=comparison(a, b, split['development']+split['held']))
    completion = HERE.parents[1]/'c5/leader/completion.py'
    spec = importlib.util.spec_from_file_location('c6_completion', completion)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = dict(paired=groups, completion={k: mod.table(v) for k, v in [('baseline', a), ('candidate', b)]})
    Path(args.out).write_text(json.dumps(result, indent=2)+'\n')
    print(mod.markdown(result['completion']))
    for name, data in groups.items():
        print(name, json.dumps({k: v for k, v in data.items() if k not in ('by_map', 'loadavg_start', 'parts')}))


if __name__ == '__main__':
    main()
