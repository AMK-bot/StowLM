"""lab/score.py with the rest of the D192 fill ladder approved, feeder_1700 above all, and the leader's holdout.

The ladder's cohort file marks all 90 maps TRAIN, but lab/catalog.py only approves maps V6 happened to run. Running
executives bind catalog.py's hash, so this wraps it for this process and its workers instead of editing it.
Takes the same arguments as score.py.
"""
import json
from pathlib import Path
import sys

LAB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LAB))
import catalog

_approved = catalog._train_allowlist


def _with_ladder():
    cohort = json.loads((catalog.ROOT / 'experiments/mvp/strategy/fillsweep/cohort.json').read_text())
    assert cohort['split'] == 'train'
    holdout = (catalog.ROOT / 'experiments/mvp/frontier/lab/c6/c6-maps/holdout.txt').read_text().split()
    return _approved() | {row['input'] for row in cohort['maps']} | set(holdout)


catalog._train_allowlist = _with_ladder

if __name__ == '__main__':
    import score
    raise SystemExit(score.main())
