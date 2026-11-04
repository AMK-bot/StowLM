"""Check that incomplete/missing measurements cannot masquerade as regression passes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('summary_under_test', Path(__file__).with_name('summarize_benchmark.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def row(n=121, uncovered=0., objective=0., complete=True):
    return dict(instance_id='synthetic', n_boxes=n, lp=dict(uncovered=uncovered, objective=objective),
                lp_s=10., phase3_dive_s=20., complete=complete,
                pattern_audit=dict(passed=True), complete_stow=True, replay_legal=True)


class SummaryTests(unittest.TestCase):
    def test_improved_relaxation_is_no_regression(self):
        old, new = row(uncovered=5., objective=5000.), row()
        result = MODULE.summarize({'map': old}, {'map': new})
        self.assertTrue(result['small_medium_regression_passed'])

    def test_one_percent_tolerance(self):
        old = row(uncovered=10., objective=10000.)
        new = row(uncovered=10.11, objective=10110.)
        self.assertFalse(MODULE.summarize({'map': old}, {'map': new})['small_medium_regression_passed'])

    def test_missing_row_fails_regression(self):
        result = MODULE.summarize({'map': row()}, {})
        self.assertFalse(result['small_medium_regression_passed'])
        self.assertEqual(result['optimized_complete_replay_legal'], 0)

    def test_zero_baseline_uses_numerical_tolerance(self):
        result = MODULE.summarize({'map': row()}, {'map': row(uncovered=.001, objective=1.)})
        self.assertFalse(result['small_medium_regression_passed'])

    def test_428_gate_is_separate(self):
        result = MODULE.summarize({'map': row(n=428)}, {'map': row(n=428)})
        self.assertEqual(result['optimized_428_complete_replay_legal'], 1)
        self.assertFalse(result['small_medium_regression_passed'])


if __name__ == '__main__':
    unittest.main()
