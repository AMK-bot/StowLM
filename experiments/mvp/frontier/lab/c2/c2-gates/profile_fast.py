"""Profile the exact accelerated search path on its slow 22-box map."""
import cProfile
import hashlib
import io
import json
from pathlib import Path
import pstats
import sys

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
for p in (LAB/'c1/fx-vlsn', LAB/'c1/leader', HERE):
    sys.path.insert(0, str(p))

from alns import Problem
from catalog import load_baseline, ensure_safe
from e9fix import install
from mask import PrefixMask
from search_kernel import run
from stowage.actions.space import Place
from stowage.io import load_instance


def main():
    install()
    identity = 'random.small.41100002.0.2'
    row = load_baseline()[identity]
    instance = load_instance(ensure_safe(row['input']))
    problem = Problem(instance)
    actions = tuple(Place(a['box'], a['slot']) for a in row['ordered_actions'])
    oracle = PrefixMask(problem)
    profile = cProfile.Profile()
    original = oracle.check

    def observed(actions):
        return profile.runcall(original, actions)

    oracle.check = observed
    candidates, stats = run(problem, [actions], budget_s=600, seed=0,
                            mask=oracle, max_iterations=200)
    payload = [[[a.box, a.slot] for a in actions] for actions in candidates]
    digest = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    reference = json.loads((HERE/'results/search-fast'/f'{identity}.json').read_text())
    assert digest == reference['method_metadata']['candidate_sha256']
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(50)
    profile.dump_stats(str(HERE/'results/mask_fast_profile.pstats'))
    result = dict(instance=identity, profile=stream.getvalue(), stats=stats,
                  candidate_sha256=digest, matches_unprofiled_search=True,
                  mask_sha256=hashlib.sha256((HERE/'mask.py').read_bytes()).hexdigest())
    (HERE/'results/mask_fast_profile.json').write_text(json.dumps(result, indent=2)+'\n')
    (HERE/'results/mask_fast_profile.txt').write_text(stream.getvalue())
    print(stream.getvalue())


if __name__ == '__main__':
    main()
