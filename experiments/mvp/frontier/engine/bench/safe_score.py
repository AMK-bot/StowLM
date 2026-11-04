"""Run unchanged lab/score.py using frozen public input provenance only."""
from pathlib import Path
import sys

FRONTIER = Path(__file__).resolve().parents[2]
ROOT = FRONTIER.parents[2]
for path in (ROOT/'src', FRONTIER, FRONTIER/'lab'):
    sys.path.insert(0, str(path))
from engine.safe_catalog import install
install()
# Module-level import also initializes spawned scorecard workers identically.
import score as shared_score
from engine.bench.admission import install as install_admission
install_admission(shared_score)

if __name__ == '__main__':
    raise SystemExit(shared_score.main())
