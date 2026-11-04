"""Run the plug-in on one map without the scorecard, printing the ladder as it goes."""
from pathlib import Path
import sys
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[5]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(HERE)]
from alns import Problem
from stowage.io import load_instance
import method


class Show(list):
    def append(self, row):
        print({k: v for k, v in row.items() if k not in ('mixes', 'lp_proved', 'heel_tm', 'layout', 'columns')}, flush=True)
        list.append(self, row)


class Meta(dict):
    def __setitem__(self, k, v):
        dict.__setitem__(self, k, Show() if k == 'ladder' else v)


i = load_instance(ROOT/sys.argv[1]); p = Problem(i)
ctx = dict(problem=p, method_metadata=Meta())
out = method.propose(i, seed=0, budget_s=float(sys.argv[2]), context=ctx)
print('candidates', len(out), [p.score(a).objective for a in out])
