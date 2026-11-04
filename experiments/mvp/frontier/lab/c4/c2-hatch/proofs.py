"""Frozen proof-count evaluation of the thirteen C3 outstanding identities."""
from dataclasses import asdict
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from catalog import ROOT, load_set
from stowage.io import load_instance

HERE = Path(__file__).resolve().parent
SCOPE = ('fewest lifts among plans the checker passes, counting later ports by '
         'our D15 projection (fixed stow, no later loads)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--continuation', action='store_true')
    args = parser.parse_args()
    freeze = json.loads((HERE/'PROOF_FREEZE.json').read_text())
    pinned = json.loads((HERE/'PROOF_INPUTS.json').read_text())
    for name, digest in pinned['sha256'].items():
        assert sha256((HERE/name).read_bytes()).hexdigest() == digest
    for name, digest in freeze['global_bound_modules'].items():
        assert sha256((HERE/name).read_bytes()).hexdigest() == digest
    uppers = json.loads((HERE/'references/upper_index.json').read_text())
    improved = HERE/'results/accepted_upper_index.json'
    if args.continuation and improved.exists():
        for identity, row in json.loads(improved.read_text()).items():
            if row.get('passed') and row['lifts'] < uppers.get(identity, {'lifts': float('inf')})['lifts']:
                uppers[identity] = row
    previous = {r['instance_id']: r for r in json.loads(
        (HERE/'references/c3_certificates.json').read_text())['validation_small']['rows']}
    budget = freeze['continuation_budget_s' if args.continuation else 'proof_budget_s']
    folder = HERE/'results'/('proofs-300' if args.continuation else 'proofs-120')
    folder.mkdir(parents=True, exist_ok=True)
    from certified import lower_bound
    rows = []
    started = perf_counter()
    for relative in load_set(str(HERE/'remaining13.txt')):
        instance = load_instance(ROOT/relative)
        out = folder/f'{instance.id}.json'
        input_hash = sha256((ROOT/relative).read_bytes()).hexdigest()
        upper = uppers[instance.id]
        assert upper['input_sha256'] == input_hash
        prior = previous[instance.id]['lower_bound']
        prior_evidence = previous[instance.id]['bound_evidence']
        initial_path = HERE/'results/proofs-120'/f'{instance.id}.json'
        if args.continuation and initial_path.exists():
            initial = json.loads(initial_path.read_text())
            prior = max(prior, initial['lower_bound'])
            prior_evidence = str(initial_path.relative_to(HERE))
        if out.exists():
            row = json.loads(out.read_text())
            assert row['input_sha256'] == input_hash and row['budget_s'] == budget
            assert row['seed'] == 0 and row['source_modules'] == freeze['global_bound_modules']
            assert row['upper'] == upper['lifts'] and row['upper_evidence'] == upper['evidence']
            rows.append(row)
            continue
        tick = perf_counter()
        result = None
        if prior < upper['lifts']-1e-7:
            result = asdict(lower_bound(instance, budget_s=budget, seed=0,
                                       upper_bound=upper['lifts']))
            prior = max(prior, result['lower_bound'])
        assert prior <= upper['lifts']+1e-7
        row = dict(instance_id=instance.id, input=relative, input_sha256=input_hash,
                   lower_bound=prior, upper=upper['lifts'], gap=upper['lifts']-prior,
                   proved=abs(upper['lifts']-prior)<1e-7, scope=SCOPE,
                   budget_s=budget, seed=0, result=result,
                   prior_evidence=prior_evidence, upper_evidence=upper['evidence'],
                   wall_s=perf_counter()-tick, load=os.getloadavg(),
                   source_modules=freeze['global_bound_modules'])
        out.write_text(json.dumps(row, indent=2)+'\n')
        rows.append(row)
        print(instance.id, 'upper', row['upper'], 'lower', prior,
              'proved', row['proved'], f'{row["wall_s"]:.3f}s', flush=True)
    summary = dict(maps=len(rows), proved=sum(r['proved'] for r in rows),
                   seconds=sum(r['wall_s'] for r in rows), elapsed_s=perf_counter()-started,
                   scope=SCOPE, budget_s=budget, validation_tuning=False)
    (folder/'SUMMARY.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
