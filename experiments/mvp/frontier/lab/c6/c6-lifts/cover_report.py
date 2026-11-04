"""Read-only analysis of paired cold rows; never imported by a solver."""
import importlib.util
import json
from pathlib import Path
from statistics import mean

from compare import comparison, rows

HERE = Path(__file__).resolve().parent
PARTS = ('relocations', 'hatch_now', 'hatch_later', 'restow_now', 'restow_later')
ENGINE_SHARED = {
    'D192.medium-feeder_500-f60-high-s192200000': 71,
    'D192.medium-feeder_500-f60-high-s192200002': 70,
    'D192.medium-feeder_500-f60-low-s192200000': 63,
    'D192.medium-feeder_800-f60-low-s192200101': 104,
    'D192.medium-feeder_800-f60-low-s192200102': 100,
}


def stages(candidates):
    out = {}
    for name in ('polish', 'wide_polish', 'cover_lns'):
        data = [r.get('method_metadata', {}).get(name) for r in candidates.values()]
        data = [r for r in data if r]
        out[name] = dict(maps=len(data), accepted=sum(r.get('accepted', 0) for r in data),
                         mean_wall_s=mean(r.get('wall_s', 0) for r in data) if data else None,
                         errors=[r for r in data if r.get('error') or r.get('adapter_error')])
    paired = [r for r in candidates.values() if r.get('passed') and r['method_metadata'].get('pre_cover', {}).get('passed')]
    out['cover_increment'] = dict(maps=len(paired),
        wins=sum(r['lifts'] < r['method_metadata']['pre_cover']['lifts'] for r in paired),
        saved=sum(r['method_metadata']['pre_cover']['lifts']-r['lifts'] for r in paired),
        parts_saved={k: sum(r['method_metadata']['pre_cover']['parts'][k]-r['parts'][k] for r in paired) for k in PARTS},
        by_map=[dict(instance_id=r['instance_id'], before=r['method_metadata']['pre_cover']['lifts'],
                     after=r['lifts']) for r in paired])
    return out


def main():
    spec = importlib.util.spec_from_file_location('cover_completion', HERE.parents[1]/'c5/leader/completion.py')
    completion = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(completion)
    result = dict(pairing='cold same-process fresh unchanged c5, checked before any improvement',
                  paired={}, completion={}, stages={})
    markdown = []
    sets = {}
    for name in ('all48', 'quick_tiny', 'quick_small', 'shared5'):
        directory = HERE/'results'/f'cover-{name}'
        a, b = rows(directory/'baseline'), rows(directory/'candidate')
        if not a and not b:
            continue
        sets[name] = (a, b)
        result['paired'][name] = comparison(a, b)
        result['stages'][name] = stages(b)
        for arm, data in [('baseline', a), ('candidate', b)]:
            result['completion'][f'{name} {arm}'] = completion.table(data)
        data = result['paired'][name]
        markdown.append(f"{name}: {data['baseline_passes']}/{data['maps']} → {data['candidate_passes']}/{data['maps']} passes; "
                        f"{data['baseline_mean_lifts']} → {data['candidate_mean_lifts']} lifts; "
                        f"{data['wins']}/{data['ties']}/{data['losses']} wins/ties/losses; "
                        f"wall {data['baseline_mean_wall_s']:.2f} → {data['candidate_mean_wall_s']:.2f} s/map.")
    if 'all48' in sets:
        a, b = sets['all48']
        certificates = rows(HERE/'results/cover-all48/bounds')
        gap_rows = []
        for i in sorted(set(b) & set(certificates)):
            if b[i]['passed']:
                lb, upper = certificates[i]['lower_bound'], b[i]['lifts']
                if lb > upper+1e-8:
                    raise ValueError(f'Certificate exceeds checked upper bound: {i}')
                gap_rows.append(dict(instance_id=i, upper=upper, lower=lb, gap=upper-lb,
                                     wall_s=certificates[i].get('wall_s')))
        result['cold_gap'] = dict(maps=len(gap_rows), by_map=gap_rows)
        for key in ('upper', 'lower', 'gap', 'wall_s'):
            result['cold_gap'][f'mean_{key}'] = mean(r[key] for r in gap_rows) if gap_rows else None
        # Retrospective interval accounting only: no archived witness enters runtime.
        old = json.loads((HERE/'RESULTS.json').read_text())['gaps']['by_map']
        decomposition = []
        for row in old:
            i = row['instance_id']
            old_u, old_l = row['checked_lifts'], row['lower_bound']
            new_u = min(old_u, b[i]['lifts']) if i in b and b[i]['passed'] else old_u
            new_l = max(old_l, certificates[i]['lower_bound']) if i in certificates else old_l
            if new_l > new_u+1e-8:
                raise ValueError(f'New certificate excludes an archived checked witness: {i}')
            decomposition.append(dict(instance_id=i, old_upper=old_u, old_lower=old_l,
                                      upper=new_u, lower=new_l, old_gap=old_u-old_l,
                                      measured_primal_reduction=old_u-new_u,
                                      measured_bound_increase=new_l-old_l,
                                      unassigned_remainder=new_u-new_l,
                                      new_run_passed=bool(i in b and b[i]['passed'])))
        result['old_gap_decomposition'] = dict(maps=len(decomposition), by_map=decomposition,
            qualification='report-only max of certified scalars/min of checked witnesses; archived witnesses never enter search')
        for key in ('old_gap', 'measured_primal_reduction', 'measured_bound_increase', 'unassigned_remainder'):
            result['old_gap_decomposition'][f'mean_{key}'] = mean(r[key] for r in decomposition)
        markdown.append('Old 27-map gap accounting: '+json.dumps({k: v for k, v in result['old_gap_decomposition'].items() if k.startswith('mean_')}))
    if 'shared5' in sets:
        a, b = sets['shared5']
        result['engine_shared5'] = dict(comparator='leader-provided Engine 0.12.0 archived scalars, not fresh paired evidence',
            by_map=[dict(instance_id=i, engine_012=ENGINE_SHARED[i], cold_c5=a.get(i, {}).get('lifts'),
                         cold_cover=b.get(i, {}).get('lifts'), passed=b.get(i, {}).get('passed')) for i in ENGINE_SHARED])
    result['zero_pass_losses'] = all(not r['passes_lost'] for r in result['paired'].values())
    (HERE/'COVER_RESULTS.json').write_text(json.dumps(result, indent=2)+'\n')
    table = completion.markdown(result['completion'])
    (HERE/'COVER_COMPLETION.md').write_text(table+'\n')
    (HERE/'COVER_RESULTS.md').write_text('# Cover continuation\n\n'+'\n\n'.join(markdown)+'\n\n'+table+'\n')
    print('\n'.join(markdown), flush=True)


if __name__ == '__main__':
    main()
