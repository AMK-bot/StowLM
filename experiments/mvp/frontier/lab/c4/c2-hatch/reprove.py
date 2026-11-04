"""One unchanged300s cutoff test after any genuinely improved checked upper."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

from catalog import ROOT, load_set
from stowage.io import load_instance
from certified import lower_bound

HERE = Path(__file__).resolve().parent


def main():
    freeze = json.loads((HERE/'PROOF_FREEZE.json').read_text())
    for name, digest in freeze['global_bound_modules'].items():
        assert sha256((HERE/name).read_bytes()).hexdigest() == digest
    report = json.loads((HERE/'results/CERTIFICATES.json').read_text())
    accepted = json.loads((HERE/'results/accepted_upper_index.json').read_text())
    allowed = set(load_set(str(HERE/'remaining13.txt')))
    folder = HERE/'results/proofs-refined'
    folder.mkdir(parents=True, exist_ok=True)
    output = []
    for previous in report['validation_small']['rows']:
        identity = previous['instance_id']
        if previous['proved'] or previous['input'] not in allowed:
            continue
        old_path = HERE/'results/proofs-300'/f'{identity}.json'
        old = json.loads(old_path.read_text())
        upper = accepted[identity]
        if upper['lifts'] >= old['upper']-1e-7:
            continue
        destination = folder/f'{identity}.json'
        if destination.exists():
            row = json.loads(destination.read_text())
            assert row['upper'] == upper['lifts'] and row['source_modules'] == freeze['global_bound_modules']
            output.append(row)
            continue
        instance = load_instance(ROOT/previous['input'])
        tick = perf_counter()
        result = asdict(lower_bound(instance, budget_s=300., seed=0, upper_bound=upper['lifts']))
        bound = max(previous['lower_bound'], result['lower_bound'])
        assert bound <= upper['lifts']+1e-7
        row = dict(instance_id=identity, input=previous['input'], input_sha256=previous['input_sha256'],
            result=result, lower_bound=bound, upper=upper['lifts'], gap=upper['lifts']-bound,
            proved=abs(upper['lifts']-bound)<1e-7, budget_s=300., seed=0,
            source_modules=freeze['global_bound_modules'], scope=previous['scope'],
            upper_evidence=upper['evidence'], prior_evidence=previous['bound_evidence'],
            wall_s=perf_counter()-tick)
        destination.write_text(json.dumps(row, indent=2)+'\n')
        output.append(row)
        print(identity, 'upper',upper['lifts'],'lower',bound,'proved',row['proved'],f'{row["wall_s"]:.3f}s',flush=True)
    summary = dict(maps=len(output), proved=sum(r['proved'] for r in output),
                   seconds=sum(r['wall_s'] for r in output), budget_s=300.,
                   policy='One uniform cutoff test only for new checked upper improvements with positive gap; unchanged model.')
    (folder/'SUMMARY.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
