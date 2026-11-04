"""Frozen exact-relaxation evaluation; validation outcomes are never tuned on."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from time import perf_counter

from catalog import ROOT, load_set
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_uppers(paths):
    """Read only approved input identities and their explicitly named evidence."""
    target = HERE / 'references/upper_index.json'
    index = json.loads(target.read_text()) if target.exists() else {}
    for rel in paths:
        instance = load_instance(ROOT / rel)
        candidates = []
        previous = index.get(instance.id)
        if previous:
            if previous['input_sha256'] != digest(ROOT / rel):
                raise AssertionError(f'previous upper/input mismatch: {instance.id}')
            candidates.append(previous)
        sources = [HERE / 'references/engine-quick-0.10.0' / f'{instance.id}.json',
                   HERE / 'results/pilot25b' / f'{instance.id}.json',
                   HERE.parents[1] / 'c2/c2-hatch/results/quick' / f'{instance.id}.json',
                   ROOT / 'experiments/mvp/frontier/runs' /
                   ('tiny48-0.9' if instance.size_class == 'tiny' else 'val-0.9') /
                   f'{instance.id}.json']
        # Only this documented validation-only benchmark folder is eligible;
        # never scan arbitrary engine result trees or sealed milestones.
        benchmark = ROOT / 'experiments/mvp/frontier/engine/results/bench-small'
        if benchmark.exists():
            sources += sorted(benchmark.glob(f'shard-*/{instance.id}.json'))
        input_hash = digest(ROOT / rel)
        for source in sources:
            if not source.exists():
                continue
            raw = source.read_bytes()
            row = json.loads(raw)
            if not row.get('passed') or not row.get('plan'):
                continue
            if row.get('input') != rel or row.get('input_sha256') != input_hash:
                raise AssertionError(f'upper-bound evidence/input mismatch: {source}')
            lifts = row.get('lifts', row.get('weighted'))
            if lifts is None:
                continue
            evidence = source
            if source.is_relative_to(benchmark):
                stamp = hashlib.sha256(raw).hexdigest()[:12]
                evidence = HERE / 'references/engine-validation' / f'{instance.id}.{stamp}.json'
                evidence.parent.mkdir(parents=True, exist_ok=True)
                evidence.write_bytes(raw)
            candidates.append(dict(instance_id=instance.id, input=rel,
                                   input_sha256=input_hash, passed=True, lifts=lifts,
                                   plan=row['plan'], evidence=str(evidence.relative_to(ROOT)),
                                   evidence_sha256=hashlib.sha256(raw).hexdigest()))
        if candidates:
            index[instance.id] = min(candidates, key=lambda r: r['lifts'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(index, indent=2)+'\n')
    return index


def main():
    evaluation_started = perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', required=True)
    parser.add_argument('--budget-s', type=float, default=60)
    parser.add_argument('--out', required=True)
    parser.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args()
    folder = Path(args.out).resolve()
    if not folder.is_relative_to(HERE):
        raise ValueError('all output must stay in the experiment folder')
    paths = load_set(args.set)
    uppers = snapshot_uppers(paths)
    if args.snapshot_only:
        print(f'snapshotted checked upper evidence for {sum(load_instance(ROOT/p).id in uppers for p in paths)}/{len(paths)} maps')
        return
    from certified import lower_bound
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    folder.mkdir(parents=True, exist_ok=True)
    config = dict(selection=args.set, budget_s=args.budget_s, seed=0,
                  bound_sha256=digest(HERE / 'certified.py'),
                  bound_dependencies={p.name: digest(p) for p in HERE.glob('*.py')
                                      if p.name not in {'evaluate.py', 'method.py', 'report.py'}
                                      and 'test' not in p.name},
                  leader_benchmark_running=(HERE.parents[1] / 'c1/leader/BENCH-RUNNING').exists(),
                  rule_freeze_v1=rule_freeze_v1_enabled(), numerical_threads=1)
    rows = []
    for rel in paths:
        instance = load_instance(ROOT / rel)
        destination = folder / f'{instance.id}.json'
        input_hash = digest(ROOT / rel)
        if destination.exists():
            row = json.loads(destination.read_text())
            if row['config'] != config or row['input_sha256'] != input_hash:
                raise ValueError(f'frozen evaluation cannot resume changed inputs/code/settings: {instance.id}')
            rows.append(row)
            continue
        started = perf_counter()
        upper = uppers.get(instance.id)
        result = asdict(lower_bound(instance, budget_s=args.budget_s, seed=0,
                                    upper_bound=upper['lifts'] if upper else None))
        lower = result['lower_bound']
        if upper and result['status'] == 'infeasible':
            raise AssertionError(f'full relaxation infeasible despite checked upper: {instance.id}')
        if upper and lower > upper['lifts'] + 1e-7:
            raise AssertionError(f'lower bound exceeds checked upper: {instance.id}: {lower}>{upper["lifts"]}')
        row = dict(instance_id=instance.id, input=rel, input_sha256=input_hash,
                   result=result, config=config, wall_s=perf_counter()-started,
                   checked_upper=upper['lifts'] if upper else None,
                   upper_evidence=upper['evidence'] if upper else None,
                   upper_evidence_sha256=upper['evidence_sha256'] if upper else None,
                   proved=upper is not None and abs(lower-upper['lifts'])<1e-7,
                   gap=upper['lifts']-lower if upper else None)
        destination.write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        print(instance.id, 'UB', row['checked_upper'], 'LB', lower,
              result['status'], 'proved', row['proved'], f'{row["wall_s"]:.2f}s', flush=True)
    paired = [r for r in rows if r['gap'] is not None]
    summary = dict(config=config, maps=len(rows), checked_uppers=len(paired),
                   proved=sum(r['proved'] for r in rows),
                   seconds=sum(r['wall_s'] for r in rows),
                   evaluation_elapsed_s=perf_counter()-evaluation_started,
                   mean_gap=sum(r['gap'] for r in paired)/len(paired) if paired else None,
                   optimal_relaxations=sum(r['result']['status']=='optimal' for r in rows),
                   cutoff_certificates=sum(r['result']['status'] in
                                           {'threshold_infeasible', 'cutoff_infeasible',
                                            'initial_bound_above_cutoff'} for r in rows),
                   note='Proof equality uses recorded full-checker upper evidence; fresh admission is separately recorded by score.py.')
    (folder / 'SUMMARY.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
