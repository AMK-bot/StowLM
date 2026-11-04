"""Read-only verification of frozen paired measurements and bound intervals."""
import hashlib
import json
from pathlib import Path
import sys

from compare import rows

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
LAB = HERE.parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    frozen = json.loads((HERE/'FROZEN.json').read_text())
    own_changed = [name for name, value in frozen['sha256'].items() if digest(HERE/name) != value]
    external_changed = [name for name, value in frozen['external_sha256'].items() if digest(ROOT/name) != value]
    assert not own_changed, own_changed
    assert not external_changed, external_changed
    assert (HERE/'baseline.py').read_bytes() == (LAB/'c5/c5-securing/method.py').read_bytes()
    results = {}
    shared_digests = set()
    for name, list_path in [('all48', HERE/'all48.txt'), ('quick_tiny', LAB/'sets/quick_tiny.txt'),
                            ('quick_small', LAB/'sets/quick_small.txt')]:
        expected = {line.strip() for line in list_path.read_text().splitlines() if line.strip() and not line.startswith('#')}
        a, b = rows(HERE/'results'/f'baseline-{name}'), rows(HERE/'results'/f'candidate-{name}')
        for label, data, module in [('baseline', a, 'baseline.py'), ('candidate', b, 'method.py')]:
            assert len(data) == len(expected), (name, label, len(data), len(expected))
            assert {row['input'] for row in data.values()} == expected, (name, label, 'input set differs')
            for identity, row in data.items():
                assert row['complete'], (identity, 'incomplete')
                assert row['input_sha256'] == digest(ROOT/row['input']), identity
                assert row['config']['method']['sha256'] == frozen['sha256'][module], identity
                assert row['config']['budget_s'] == 240 and row['config']['finalize_s'] == 120, identity
                assert row['config']['seed'] == 0 and row['config']['tries'] == 2, identity
                shared_digests.add(row['config']['source_sha256'])
                assert isinstance(row['method_metadata'], dict), identity
                if row['passed']:
                    assert row['plan'] and row['assignment'] and row['parts'], identity
                    assert len(row['assignment']) == row['n_boxes'], (identity, 'partial')
        assert set(a) == set(b), (name, 'different IDs')
        lost = [i for i in a if a[i]['passed'] and not b[i]['passed']]
        results[name] = dict(maps=len(expected), baseline_passes=sum(r['passed'] for r in a.values()),
                             candidate_passes=sum(r['passed'] for r in b.values()), passes_lost=lost)
    primary = set((HERE/'primary28.txt').read_text().splitlines())
    certificates = rows(HERE/'results/bounds-primary28')
    assert len(certificates) == 28
    assert {r['input'] for r in certificates.values()} == primary
    candidate = rows(HERE/'results/candidate-all48')
    for identity, row in certificates.items():
        assert row['cold'] is True and row['input_sha256'] == digest(ROOT/row['input']), identity
        if candidate[identity]['passed']:
            assert row['lower_bound'] <= candidate[identity]['lifts']+1e-6, identity
    sys.path.insert(0, str(LAB))
    from score import source_sha256
    assert shared_digests == {source_sha256()}, shared_digests
    out = dict(source_freeze_unchanged=True, original_baseline_unchanged=True,
               scorer_checker_sources_unchanged=True,
               sets=results, primary_certificates=28,
               zero_pass_losses=all(not r['passes_lost'] for r in results.values()))
    (HERE/'AUDIT.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
