# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-fvs/test_pipeline.py.
"""Real replay tests for order search and joint identity reassignment."""
from itertools import permutations
import unittest

from alns import Problem
from .assignment import joint_candidates
from .exact import search_replay, solve_relaxation
from stowage.actions.space import Place
from stowage.model import YardStack
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan


def case(stacked=False):
    instance = build_instance()
    source = instance.containers[1]
    boxes = [source.model_copy(update={'id':ident, 'dg':None, 'special_stowage':[],
                                      'oog':None, 'reefer':False, 'pod':'THSGZ'})
             for ident in (instance.containers[1].id, instance.containers[2].id)]
    instance.containers = boxes
    instance.load_list = [b.id for b in boxes]
    instance.yard.stacks = [YardStack(key='A-001-01', length_ft=20, boxes=instance.load_list)]
    assignment = {boxes[0].id:'0010102', boxes[1].id:'0010104' if stacked else '0010202'}
    return Problem(instance), assignment


class PipelineTests(unittest.TestCase):
    def test_exact_real_search_removes_one_unnecessary_relocation(self):
        p, assignment = case()
        initial = tuple(Place(box, slot) for box, slot in assignment.items())
        self.assertEqual(apply_plan(p.instance, p.score(initial).plan).count.relocations, 1)
        result = search_replay(p, assignment, incumbent_actions=initial,
                               time_limit_s=2., lower_bound=0)
        self.assertTrue(result.optimal)
        self.assertEqual(result.relocations, 0)
        self.assertEqual(tuple(a.box for a in result.actions), tuple(reversed(assignment)))

    def test_fixed_stow_bound_and_real_exhaustion_agree(self):
        p, assignment = case(stacked=True)
        bound = solve_relaxation(p, assignment, time_limit_s=1.)
        self.assertTrue(bound.optimal)
        self.assertEqual(bound.lower_bound, 1)
        result = search_replay(p, assignment, time_limit_s=2., lower_bound=0)
        self.assertTrue(result.optimal)
        self.assertTrue(result.exhausted)
        self.assertEqual(result.relocations, 1)

    def test_joint_assignment_removes_fixed_stow_cycle(self):
        p, assignment = case(stacked=True)
        candidates, meta = joint_candidates(p, assignment, time_limit_s=2., exact=True)
        self.assertEqual(meta['status'], 'OPTIMAL')
        self.assertEqual(meta['relaxed_relocations'], 0)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate, {box:slot for box,slot in zip(assignment, reversed(list(assignment.values())))})
        values = []
        for boxes in permutations(candidate):
            scored = p.score(tuple(Place(box,candidate[box]) for box in boxes))
            if scored is not None:
                replay = apply_plan(p.instance,scored.plan)
                if not replay.count.restow_lifts:
                    values.append(replay.count.relocations)
        self.assertEqual(min(values), 0)


if __name__ == '__main__':
    unittest.main()
