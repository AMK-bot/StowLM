# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-vlsn/test_lazy_fusion.py.
"""Deterministic lazy-admission checks; no actual-map or physics claim."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import lazy_fusion as module
from .test_recombine import FakeProblem, FakeSequencer


class Clock:
    now = 0.

    def __call__(self):
        return self.now


class CountingProblem(FakeProblem):
    def __init__(self, costs, left, right):
        super().__init__(costs, left, right)
        self.real_calls = 0

    def score(self, actions):
        self.real_calls += 1
        key = tuple(sorted((a.box, a.slot) for a in actions))
        cost = self.costs[key]
        return None if cost is None else SimpleNamespace(objective=cost, actions=tuple(actions))


class FakeFast:
    def __init__(self, problem):
        self.problem = problem
        self.calls = 0

    def score(self, actions):
        self.calls += 1
        key = tuple(sorted((a.box, a.slot) for a in actions))
        return self.problem.costs[key]


class LazyFusionTests(unittest.TestCase):
    def setUp(self):
        self.left = {'a': '0010102', 'b': '0030102'}
        self.right = {'a': '0050102', 'b': '0070102'}
        self.costs = {
            (('a', '0010102'), ('b', '0030102')): 10.,
            (('a', '0050102'), ('b', '0070102')): 11.,
            (('a', '0050102'), ('b', '0030102')): 1.,
            (('a', '0010102'), ('b', '0070102')): 4.,
        }
        self.problem = CountingProblem(self.costs, self.left, self.right)
        self.clock = Clock()

    def run_fusion(self, *, fast=FakeFast, legal=lambda p, a: True, deadline=10.):
        with patch.object(module, 'monotonic', self.clock), \
                patch.object(module, 'Sequencer', FakeSequencer), \
                patch.object(module, 'FastScorer', fast), \
                patch.object(module, '_mask_ok', side_effect=legal):
            return module.recombine(self.problem, [self.left, self.right], deadline=deadline)

    def test_best_feasible_after_complete_scoring(self):
        checked = []

        def legal(problem, actions):
            key = tuple(sorted((a.box, a.slot) for a in actions))
            cost = problem.costs[key]
            checked.append(cost)
            return cost != 1.

        out = self.run_fusion(legal=legal)
        self.assertEqual(self.problem.score(out[0]).objective, 4.)
        self.assertEqual(checked, [1., 4.])
        stats = module.recombine.last_stats
        self.assertEqual(stats['family_size_sum'], 4)
        self.assertEqual(stats['fast'], 4)
        self.assertEqual(stats['masks_checked'], 2)
        self.assertEqual(stats['real_verifications'], 1)
        self.assertTrue(stats['enumeration_complete'])
        self.assertTrue(stats['best_proven'])
        self.assertTrue(stats['complete'])

    def test_fast_none_always_falls_back(self):
        class Unsupported(FakeFast):
            def score(self, actions):
                return None

        out = self.run_fusion(fast=Unsupported)
        self.assertEqual(self.problem.real_calls, 4)
        self.assertEqual(module.recombine.last_stats['fallback'], 4)
        self.assertEqual(self.problem.score(out[0]).objective, 1.)
        self.assertTrue(module.recombine.last_stats['best_proven'])

    def test_real_none_excludes_child_without_mask(self):
        self.costs[(('a', '0050102'), ('b', '0030102'))] = None
        out = self.run_fusion()
        self.assertEqual(self.problem.score(out[0]).objective, 4.)
        stats = module.recombine.last_stats
        self.assertEqual(stats['fallback'], 1)
        self.assertEqual(stats['replay_illegal'], 1)
        self.assertEqual(stats['masks_checked'], 1)
        self.assertTrue(stats['best_proven'])

    def test_incomplete_scoring_withdraws_certificate(self):
        clock = self.clock

        class Expiring(FakeFast):
            def score(self, actions):
                result = super().score(actions)
                if self.calls == 2:
                    clock.now = 11.
                return result

        self.assertEqual(self.run_fusion(fast=Expiring), [])
        stats = module.recombine.last_stats
        self.assertFalse(stats['enumeration_complete'])
        self.assertFalse(stats['best_proven'])
        self.assertFalse(stats['complete'])
        self.assertEqual(stats['masks_checked'], 0)

    def test_mask_deadline_withdraws_certificate(self):
        def expiring_mask(problem, actions):
            self.clock.now = 11.
            return True

        self.assertEqual(self.run_fusion(legal=expiring_mask), [])
        stats = module.recombine.last_stats
        self.assertTrue(stats['enumeration_complete'])
        self.assertFalse(stats['best_proven'])
        self.assertFalse(stats['complete'])
        self.assertEqual(stats['real_verifications'], 0)

    def test_real_replay_mismatch_fails_loudly(self):
        class Incorrect(FakeFast):
            def score(self, actions):
                return super().score(actions)-1.

        with self.assertRaisesRegex(AssertionError, 'disagrees'):
            self.run_fusion(fast=Incorrect)
        self.assertFalse(module.recombine.last_stats['complete'])
        self.assertIn('score_mismatch', module.recombine.last_stats)


if __name__ == '__main__':
    unittest.main()
