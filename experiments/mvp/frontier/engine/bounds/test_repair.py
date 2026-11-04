# Copied from experiments/mvp/frontier/lab/c2/c2-hatch/repair_tests.py; engine integration changes are local.
"""Real-replay regression for the restow-free CP repair candidate."""
from pathlib import Path
import json
import random

from alns import Problem, _mask_ok
from stowage.actions.space import Place
from stowage.io import load_instance
from stowage.moves import apply_plan

from .repair import _geometric, _install, neighbors

HERE = Path(__file__).resolve().parent
INSTANCE = 'experiments/mvp/corpus/val/small/feeder_800/simulated_yard/seed-040100025.json'


def _candidate():
    row = json.loads((HERE / 'fixtures/repair-sim.small.40100025.json').read_text())
    return tuple(Place(**a) for a in row['candidates'][0]['actions'])


def test_restow_free_candidate_replays_and_mask_cleans_up():
    _install()
    instance = load_instance(INSTANCE)
    p = Problem(instance)
    actions = _candidate()
    scored = p.score(actions)
    assert scored is not None
    count = apply_plan(instance, scored.plan).count
    assert scored.objective <= 10
    assert count.restow_lifts == 0
    assert sum(x.restow_lifts for x in count.future) == 0
    assert _mask_ok(p, actions)
    assert not p.engine.is_complete()
    # Repeating the same mask gate verifies its push/pop cleanup.
    assert _mask_ok(p, actions)


def test_geometry_rejects_overlapping_box_footprints():
    p = Problem(load_instance(INSTANCE))
    assignment = {a.box: a.slot for a in _candidate()}
    assert _geometric(p, assignment)
    boxes = list(assignment)
    assignment[boxes[0]] = assignment[boxes[1]]
    assert not _geometric(p, assignment)


def test_neighborhood_keeps_every_box_once_and_frees_slots():
    p = Problem(load_instance(INSTANCE))
    assignment = {a.box: a.slot for a in _candidate()}
    occupied = set(assignment.values())
    freed = False
    for candidate in neighbors(p, assignment, random.Random(0)):
        assert set(candidate) == set(assignment)
        freed |= bool(set(candidate.values()) - occupied)
    assert freed
