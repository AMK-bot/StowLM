"""Small independent enumeration checks for the exact repair decision tree."""
from importlib.util import module_from_spec, spec_from_file_location
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from stowage.actions.space import Place

spec = spec_from_file_location('exact_repair_method', Path(__file__).with_name('method.py'))
method = module_from_spec(spec)
spec.loader.exec_module(method)


class Engine:
    def __init__(self, n):
        self.actions, self.n = [], n

    def is_legal(self, action):
        return action.box not in {a.box for a in self.actions}

    def push(self, action):
        self.actions.append(action)

    def pop(self):
        return self.actions.pop()

    def is_complete(self):
        return len(self.actions) == self.n


class State:
    def __init__(self, costs):
        self.costs = costs

    def copy(self):
        return State(self.costs)

    def step(self, action):
        return [SimpleNamespace(kind='relocate')] * self.costs.get((action.box, action.slot), 0)


def problem():
    costs = {('a', '1'): 3, ('b', '2'): 2, ('c', '1'): 1}
    p = SimpleNamespace(instance=None, engine=Engine(3),
                        objective=SimpleNamespace(relocate=1., restow=1., hatch_cover=1.,
                                                  future_restow=1., future_hatch_cover=1.),
                        boxes={box: SimpleNamespace(length_ft=20) for box in 'abc'}, yard_depth={})
    p.space = SimpleNamespace(expander=lambda instance: State(costs))
    p.score = lambda actions: SimpleNamespace(actions=tuple(actions),
                            objective=float(sum(costs.get((a.box, a.slot), 0) for a in actions)))
    return p


class TestExactRepair(unittest.TestCase):
    def test_exhaustive_matches_independent_permutations(self):
        p = problem()
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        expected = min(p.score(tuple(Place(box, str(i + 1)) for i, box in enumerate(order))).objective
                       for order in permutations('abc'))
        best, stats = method.solve_neighborhood(p, actions, set('abc'), deadline=float('inf'))
        self.assertEqual(best[0].objective, expected)
        self.assertEqual(stats['lower_bound'], expected)
        self.assertTrue(stats['optimal_in_neighborhood'])
        self.assertEqual(p.engine.actions, [])

    def test_nonselected_position_is_frozen(self):
        p = problem()
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        best, stats = method.solve_neighborhood(p, actions, set('ac'), deadline=float('inf'))
        self.assertTrue(stats['exhaustive'])
        self.assertTrue(all(candidate.actions[1] == actions[1] for candidate in best))
        self.assertTrue(all(tuple(a.slot for a in candidate.actions) == ('1', '2', '3') for candidate in best))

    def test_timeout_bound_and_engine_cleanup(self):
        p = problem()
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        ticks = iter(i * .01 for i in range(10000))
        with patch.object(method, 'monotonic', side_effect=lambda: next(ticks)):
            best, stats = method.solve_neighborhood(p, actions, set('abc'), deadline=.105)
        self.assertFalse(stats['exhaustive'])
        self.assertLessEqual(stats['lower_bound'], 0.)
        self.assertGreaterEqual(best[0].objective, 0.)
        self.assertEqual(p.engine.actions, [])

    def test_incompatible_lengths_never_swap(self):
        p = problem()
        p.boxes['a'].length_ft = 40
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        best, stats = method.solve_neighborhood(p, actions, set('ab'), deadline=float('inf'))
        self.assertTrue(stats['exhaustive'])
        self.assertTrue(all(candidate.actions[0] == actions[0] for candidate in best))

    def test_original_baseline_retained_after_four_improvements(self):
        p = problem()
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        repairs = [SimpleNamespace(actions=(Place('new' + str(i), 'slot'),), objective=float(i))
                   for i in range(4)]
        context = {'problem': p, 'stored': actions}
        with patch.object(method, '_groups', return_value=[('yard:X', set('abc'))]), \
             patch.object(method, 'exact_neighborhood', return_value=(repairs, {})):
            output = method.propose(None, seed=0, budget_s=1., context=context)
        self.assertEqual(len(output), 5)
        self.assertEqual(output[-1], actions)
        self.assertIs(context['method_metadata']['exact_repair_stats'], context['exact_repair_stats'])

    def test_tiny_fractional_objective_is_not_pruned_as_zero(self):
        p = problem()
        scale = 1e-10
        base_score = p.score
        p.score = lambda actions: SimpleNamespace(actions=tuple(actions),
                                                   objective=base_score(actions).objective * scale)
        p.objective.relocate = scale
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        best, stats = method.solve_neighborhood(p, actions, set('abc'), deadline=float('inf'))
        self.assertEqual(best[0].objective, 0.)
        self.assertEqual(stats['lower_bound'], 0.)
        self.assertTrue(stats['exhaustive'])

    def test_tiny_positive_gap_does_not_match_supplied_global_bound(self):
        p = problem()
        p.score = lambda actions: SimpleNamespace(actions=tuple(actions), objective=5e-10)
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        context = {'problem': p, 'stored': actions, 'exact_lower_bound': 0.}
        with patch.object(method, '_groups', return_value=[]):
            method.propose(None, seed=0, budget_s=1., context=context)
        self.assertEqual(context['exact_repair_stats'], [])
        self.assertEqual(len(context['method_metadata']['actual_method_sha256']), 64)

    def test_fractional_repeated_accumulation_disables_prefix_certificates(self):
        p = problem()
        scale = .1
        base_score = p.score
        p.score = lambda actions: SimpleNamespace(actions=tuple(actions),
                                                   objective=base_score(actions).objective * scale)
        p.objective.relocate = scale
        # Ordinary float repeated addition differs from count*weight.
        accumulated = 0.
        for _ in range(30):
            accumulated += scale
        self.assertNotEqual(accumulated, 30 * scale)
        actions = tuple(Place(box, str(i + 1)) for i, box in enumerate('abc'))
        best, stats = method.solve_neighborhood(p, actions, set('abc'), deadline=float('inf'))
        self.assertEqual(best[0].objective, 0.)
        self.assertEqual(stats['pruned'], 0)
        self.assertEqual(stats['prefix_bound_arithmetic'], 'disabled')
        ticks = iter(i * .01 for i in range(10000))
        with patch.object(method, 'monotonic', side_effect=lambda: next(ticks)):
            _, timed = method.solve_neighborhood(p, actions, set('abc'), deadline=.105)
        self.assertFalse(timed['exhaustive'])
        self.assertEqual(timed['lower_bound'], 0.)


if __name__ == '__main__':
    unittest.main()
