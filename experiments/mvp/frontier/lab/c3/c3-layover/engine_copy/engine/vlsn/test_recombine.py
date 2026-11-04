# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-vlsn/test_recombine.py.
"""Small algorithm checks; no map search, finisher, or checker claims."""
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from stowage.actions.space import Place
from . import recombine as module


class FakeProblem:
    def __init__(self, costs, left, right):
        self.costs = costs
        self.boxes = {box: object() for box in left}
        self.cells = {cell: SimpleNamespace(stack_id='stack')
                      for slot in list(left.values())+list(right.values())
                      for cell in module.cells_of_slot(slot)}
        self.stacks = {'stack': SimpleNamespace(hatch_cover_id='cover')}
        self.yard_stack = {box: 'yard' for box in left}
        self.instance = SimpleNamespace(load_list=list(left),
                                        onboard=SimpleNamespace(placements=[]),
                                        discharge_list=[])

    def score(self, actions):
        assignment = tuple(sorted((a.box, a.slot) for a in actions))
        return SimpleNamespace(objective=self.costs[assignment], actions=tuple(actions))

    def mixes_hold_stack(self, assignment):
        return False


class FakeSequencer:
    def __init__(self, problem):
        pass

    def order(self, assignment):
        return tuple(Place(box, slot) for box, slot in sorted(assignment.items()))


class RecombinationTests(unittest.TestCase):
    def setUp(self):
        self.left = {'a': '0010102', 'b': '0030102'}
        self.right = {'a': '0050102', 'b': '0070102'}
        self.costs = {}
        for a in (self.left['a'], self.right['a']):
            for b in (self.left['b'], self.right['b']):
                self.costs[(('a', a), ('b', b))] = 10
        # This coupled optimum cannot be inferred from independently scored
        # parent components; the whole child's cost must be consulted.
        self.costs[(('a', self.right['a']), ('b', self.left['b']))] = 1
        self.problem = FakeProblem(self.costs, self.left, self.right)

    def test_global_cost_and_family_coverage(self):
        with patch.object(module, 'Sequencer', FakeSequencer), \
                patch.object(module, '_mask_ok', return_value=True) as mask:
            out = module.recombine(self.problem, [self.left, self.right],
                                   deadline=monotonic()+1)
        self.assertEqual(dict((a.box, a.slot) for a in out[0]),
                         {'a': self.right['a'], 'b': self.left['b']})
        self.assertEqual(mask.call_count, 4)
        stats = module.recombine.last_stats
        self.assertTrue(stats['complete'])
        self.assertEqual(stats['pair_stats'][0]['family_size'], 4)
        self.assertEqual(stats['pair_stats'][0]['conservative_components'], 1)

    def test_global_mask_rejects_cheapest_child(self):
        def legal(problem, actions):
            return problem.score(actions).objective != 1
        with patch.object(module, 'Sequencer', FakeSequencer), \
                patch.object(module, '_mask_ok', side_effect=legal):
            out = module.recombine(self.problem, [self.left, self.right],
                                   deadline=monotonic()+1)
        self.assertEqual(len(out), 3)
        self.assertTrue(all(self.problem.score(actions).objective == 10 for actions in out))

    def test_both_cells_of_long_slot_join_components(self):
        left = {'a': '0020102', 'b': '0050102'}
        right = {'a': '0060102', 'b': '0030102'}
        problem = FakeProblem({}, left, right)
        self.assertEqual(module.components(problem, left, right), [('a', 'b')])

    def test_expired_deadline_is_not_complete(self):
        self.assertEqual(module.recombine(self.problem, [self.left, self.right],
                                         deadline=monotonic()-1), [])
        self.assertFalse(module.recombine.last_stats['complete'])


if __name__ == '__main__':
    unittest.main()
