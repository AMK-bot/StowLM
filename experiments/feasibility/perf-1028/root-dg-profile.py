import cProfile
import pstats
from pathlib import Path
from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig
from candidate import make_engine
from stowage.io import load_instance
root=Path(__file__).resolve().parent
inst=load_instance(root.parent/'fix-t4-measure/full-current.json')
eng=make_engine(inst, {'global_screen':0}, optimized=True)
p=cProfile.Profile()
p.runcall(eng.legal_index)
p.dump_stats(root/'root-dg.prof')
with (root/'root-dg-profile.txt').open('w') as f:
    pstats.Stats(p,stream=f).strip_dirs().sort_stats('cumulative').print_stats(40)
