"""Alternating fresh exact-construction / 0.9.0+E9 pairs on fixed quick_small."""
from hashlib import sha256
import json
import os
from pathlib import Path
from time import perf_counter

from catalog import load_baseline, load_set
from score import one, plugin_info, source_sha256, write_json
from summary import summarize

HERE = Path(__file__).resolve().parent


def main():
    freeze = json.loads((HERE/'CONSTRUCTOR_FREEZE.json').read_text())
    for name, digest in freeze['modules'].items():
        assert sha256((HERE/name).read_bytes()).hexdigest() == digest
    source = source_sha256()
    controls = dict(cold=dict(method='method.py', finish=None, budget_s=30., finalize_s=20., tries=1),
                    fresh09=dict(method='baseline_method.py', finish='baseline_method.py',
                                 budget_s=120., finalize_s=300., tries=3))
    baseline = load_baseline()
    rows = {arm: [] for arm in controls}
    records = []
    start = perf_counter()
    for i, relative in enumerate(load_set('quick_small')):
        arms = ('cold', 'fresh09') if i % 2 == 0 else ('fresh09', 'cold')
        for arm in arms:
            settings = controls[arm]
            folder = HERE/'results'/f'paired-{arm}'
            folder.mkdir(parents=True, exist_ok=True)
            config = dict(method=plugin_info(str(HERE/settings['method'])+':propose'),
                          finish=plugin_info(str(HERE/settings['finish'])+':finish') if settings['finish'] else None,
                          order=None, seed=0, source_sha256=source,
                          **{k: settings[k] for k in ('budget_s', 'finalize_s', 'tries')})
            load = os.getloadavg()
            row, cached = one((relative, str(folder), baseline, config))
            rows[arm].append(row)
            records.append(dict(instance_id=row['instance_id'], arm=arm,
                                load_before=load, load_after=os.getloadavg(), cached=cached,
                                wall_s=row['wall_s'], passed=row['passed'], lifts=row['lifts']))
            write_json(HERE/'results/paired-progress.json', records)
            print(arm, row['instance_id'], 'pass', row['passed'], 'lifts', row['lifts'],
                  f'{row["wall_s"]:.2f}s', 'cached' if cached else '', flush=True)
    for arm, values in rows.items():
        write_json(HERE/'results'/f'paired-{arm}'/'summary.json', summarize(values, baseline, seed=0))
    exact = {r['instance_id']: r for r in rows['cold']}
    reference = {r['instance_id']: r for r in rows['fresh09']}
    mutual = [i for i in exact if exact[i]['passed'] and reference[i]['passed']]
    summary = dict(maps=len(exact), exact_passes=sum(r['passed'] for r in exact.values()),
                   fresh09_passes=sum(r['passed'] for r in reference.values()),
                   exact_replay=sum(bool(r['tries']) for r in exact.values()),
                   mutual=len(mutual),
                   exact_paired_mean=sum(exact[i]['lifts'] for i in mutual)/len(mutual) if mutual else None,
                   fresh09_paired_mean=sum(reference[i]['lifts'] for i in mutual)/len(mutual) if mutual else None,
                   exact_mean_wall_s=sum(r['wall_s'] for r in exact.values())/len(exact),
                   fresh09_mean_wall_s=sum(r['wall_s'] for r in reference.values())/len(reference),
                   elapsed_s=perf_counter()-start, source_sha256=source,
                   alternating_order=True, one_numerical_process=True,
                   caveat='Fixed wall budgets and recorded shared load; alternating adjacent arms, not identical machine states.')
    write_json(HERE/'results/PAIRED.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
