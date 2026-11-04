"""Reporting gates must not turn metadata or an unknown reference into a pass."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('score_summary_under_test', HERE/'summarize_score.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ScoreSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=HERE, prefix='.summary-test-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)

    def save(self, **updates):
        row = dict(instance_id='synthetic', complete=True, n_boxes=121, passed=False,
                   baseline_passed=None, baseline_lifts=None, lifts=None,
                   config=dict(budget_s=60.), method_metadata={}, propose_s=4., wall_s=5.)
        row.update(updates)
        (self.folder/'synthetic.json').write_text(json.dumps(row))

    def test_metadata_claim_without_audit_is_not_a_pass(self):
        self.save(method_metadata=dict(replay_complete=True))
        result = MODULE.summarize('rmp60', self.folder, expected=1)
        self.assertEqual(result['metadata_replay_complete'], 1)
        self.assertIsNone(result['fresh_replay_legal'])
        self.assertEqual(result['full_checker_passes'], 0)
        self.assertEqual(result['baseline_known_maps'], 0)
        self.assertIsNone(result['paired_mean_lift_change'])

    def test_fresh_audit_can_refute_metadata_claim(self):
        self.save(method_metadata=dict(replay_complete=True))
        audit = self.folder/'audit.json'
        audit.write_text(json.dumps(dict(rows=[dict(instance_id='synthetic', replay_legal=False,
                                                    full_checker_pass=False)])))
        result = MODULE.summarize('rmp60', self.folder, audit, expected=1)
        self.assertEqual(result['metadata_fresh_replay_mismatches'], ['synthetic'])
        self.assertEqual(result['fresh_replay_legal'], 0)
        self.assertEqual(result['full_checker_passes'], 0)

    def test_paired_lifts_require_mutual_known_full_pass(self):
        self.save(passed=True, lifts=8., baseline_passed=True, baseline_lifts=10.)
        result = MODULE.summarize('rmp60', self.folder, expected=1)
        self.assertEqual(result['mutual_passes'], 1)
        self.assertEqual(result['paired_mean_lift_change'], -2.)
        self.save(passed=True, lifts=8., baseline_passed=None, baseline_lifts=10.)
        result = MODULE.summarize('rmp60', self.folder, expected=1)
        self.assertEqual(result['mutual_passes'], 0)
        self.assertIsNone(result['paired_mean_lift_change'])

    def test_failure_rule_maps_are_deduplicated_across_rungs(self):
        rung = dict(full_check=False, rules=['securing', 'securing'], replay_complete=True, lifts=8.)
        self.save(method_metadata=dict(replay_complete=True, ladder=[rung, rung]))
        result = MODULE.summarize('rmp60', self.folder, expected=1)
        self.assertEqual(result['failure_rule_map_histogram'], {'securing': 1})
        self.assertEqual(result['failure_rule_rung_histogram'], {'securing': 2})


if __name__ == '__main__':
    unittest.main()
