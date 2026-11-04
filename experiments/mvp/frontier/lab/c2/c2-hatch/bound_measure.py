"""Measure admissible lift bounds against the fixed lab's stored 0.9.0 rows.

This reads only catalog-approved validation/TRAIN inputs. A relaxed optimum is
a lower bound, never an operational plan. Full checker verdicts belong to score.py.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import perf_counter

from catalog import load_baseline, load_set, ROOT
from stowage.bounds import lower_bound as counting_bound
from stowage.io import load_instance

from bound import lower_bound


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', default='quick_small')
    parser.add_argument('--budget-s', type=float, default=5.)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    folder = Path(args.out)
    own = Path(__file__).resolve().parent
    if not folder.resolve().is_relative_to(own):
        raise ValueError('Results must stay inside c2-hatch')
    folder.mkdir(parents=True, exist_ok=True)
    baseline = load_baseline()
    rows = []
    for rel in load_set(args.set):
        started = perf_counter()
        instance = load_instance(ROOT / rel)
        stored = baseline.get(instance.id, {})
        old = counting_bound(instance)
        result = asdict(lower_bound(instance, budget_s=args.budget_s))
        upper = stored.get('lifts') if stored.get('passed') else None
        bound = result['lower_bound']
        row = dict(instance_id=instance.id, input=rel,
                   input_sha256=hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(),
                   n_boxes=len(instance.load_list), baseline_passed=stored.get('passed'),
                   baseline_lifts=upper, baseline_parts=stored.get('parts'),
                   counting_bound=old.model_dump(), result=result,
                   seconds=perf_counter() - started)
        if upper is not None:
            if bound > upper + 1e-6:
                raise AssertionError(f'Bound exceeds stored upper bound: {instance.id}: {bound} > {upper}')
            row.update(gap_lifts=upper-bound,
                       gap_fraction_of_stored=(upper-bound)/upper if upper else 0.,
                       suboptimality_ratio_upper=upper/bound-1 if bound else None,
                       equality_certificate=abs(upper-bound) <= 1e-6)
        (folder / f'{instance.id}.json').write_text(json.dumps(row, indent=2) + '\n')
        rows.append(row)
        print(instance.id, 'UB', upper, 'LB', bound, result['status'],
              f'{row["seconds"]:.2f}s', flush=True)
    paired = [row for row in rows if row['baseline_lifts'] is not None]
    summary = dict(map_set=args.set, maps=len(rows), paired=len(paired),
                   baseline_passes=len(paired),
                   exact_equalities=sum(row.get('equality_certificate', False) for row in rows),
                   seconds=sum(row['seconds'] for row in rows),
                   mean_lower_bound=sum(row['result']['lower_bound'] for row in paired)/len(paired) if paired else None,
                   mean_stored_lifts=sum(row['baseline_lifts'] for row in paired)/len(paired) if paired else None,
                   within_ten_percent=sum(row.get('suboptimality_ratio_upper') is not None
                                          and row['suboptimality_ratio_upper'] <= .1 + 1e-9 for row in paired),
                   bound_source_sha256=hashlib.sha256((own / 'bound.py').read_bytes()).hexdigest(),
                   note='Upper bounds use stored full-checker passing 0.9.0 rows, not fresh checker runs. Lower bounds optimize an explicit relaxation.')
    (folder / 'SUMMARY.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
