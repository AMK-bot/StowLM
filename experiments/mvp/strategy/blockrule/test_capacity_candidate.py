"""Synthetic capacity regressions for the optional, inactive v2 proposal."""
from unittest.mock import patch
import unittest

from experiments.mvp.strategy.blockrule import capacity_candidate as candidate
from experiments.mvp.strategy.blockrule.test_rule import instance


class CapacityCandidateTests(unittest.TestCase):
    def test_current_train_shortfall_shapes_share_only_boundary_station(self):
        cases = [([68, 96, 76], [116, 83], [(0, 1), (2,)], [(0, 1), (1, 2)]),
                 ([92, 124, 128, 108], [220, 159], [(0, 1), (2, 3)], [(0, 1, 2), (2, 3)]),
                 ([92, 124, 128, 108], [241, 128], [(0, 1, 2), (3,)], [(0, 1, 2), (2, 3)]),
                 ([92, 124, 128, 108], [251, 137], [(0, 1, 2), (3,)], [(0, 1, 2), (2, 3)])]
        for capacities, demands, original, expected in cases:
            with self.subTest(demands=demands):
                runs, flow = candidate.boundary_relaxation(capacities, demands, original)
                self.assertEqual(runs, tuple(expected))
                self.assertTrue(flow["feasible"])
                allocation = flow["station_allocation_teu"]
                self.assertEqual([sum(row) for row in allocation], demands)
                for station, capacity in enumerate(capacities):
                    self.assertLessEqual(sum(row[station] for row in allocation), capacity)
                self.assertEqual(len(set(runs[0]) & set(runs[1])), 1)

    def test_pure_feasible_layout_remains_unchanged(self):
        original = ((0, 1), (2,))
        runs, _ = candidate.boundary_relaxation([68, 96, 76], [123, 70], original)
        self.assertEqual(runs, original)
        inst = instance()
        with patch("experiments.mvp.strategy.blockrule.rule.booking_category", lambda box, _: (box.pod, "H")):
            base = candidate.port_blocking_layout(inst)
            with patch.object(candidate, "port_blocking_layout", return_value=base):
                self.assertIs(candidate.capacity_relaxed_layout(inst), base)

    def test_joint_capacity_cannot_be_double_counted(self):
        flow = candidate.teu_flow([5, 5], [6, 6], [(0, 1), (0, 1)])
        self.assertFalse(flow["feasible"])
        self.assertEqual(flow["assigned_teu"], 10)
        self.assertIsNone(candidate.boundary_relaxation([5, 5], [6, 6], [(0,), (1,)]))

    def test_no_permission_to_nonadjacent_station_is_invented(self):
        capacities = [4, 5, 6, 5]
        demands = [5, 5, 5]
        result = candidate.boundary_relaxation(capacities, demands, [(0,), (1,), (2, 3)])
        self.assertIsNotNone(result)
        runs, _ = result
        self.assertTrue(set(runs[0]).isdisjoint(runs[2]))
        self.assertEqual(runs[0], (0, 1))
        self.assertNotIn(3, runs[0])


if __name__ == "__main__":
    unittest.main()
