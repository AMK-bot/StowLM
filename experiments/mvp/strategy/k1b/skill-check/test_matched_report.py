"""Small standard-library checks; run directly, without the repository test suite."""
import unittest
import json
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

import matched_report as report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.ids = [f'map-{i:02}' for i in range(55)]
        self.rows = [dict(arm=arm, instance_id=iid, complete=True, passed=True,
                          moves=10, timed_out=False, eligible=True, error=None,
                          admission=dict(admitted=True, load1=30, core_mask=4095,
                                         t=999, read_at=1000, age_s=1,
                                         source_payload=dict(mask=4095, load1=30, t=999)))
                     for iid in self.ids for arm in report.ARMS]

    def test_sign(self):
        self.assertEqual(report.sign_p(0, 0), 1)
        self.assertEqual(report.sign_p(1, 1), 1)
        self.assertEqual(report.sign_p(5, 0), .0625)
        self.assertEqual(report.sign_p(0, 5), .0625)

    def test_completeness(self):
        self.assertTrue(report.analyze(self.rows, self.ids)['definitive'])
        self.assertFalse(report.analyze(self.rows[:-1], self.ids)['definitive'])
        self.assertFalse(report.analyze(self.rows + [self.rows[0]], self.ids)['definitive'])

    def test_error_and_admission_fail_closed(self):
        self.rows[0]['error'] = 'worker failed'
        self.assertFalse(report.analyze(self.rows, self.ids)['definitive'])
        self.rows[0]['error'] = None
        self.rows[0]['admission']['core_mask'] = None
        self.assertFalse(report.analyze(self.rows, self.ids)['definitive'])

    def test_missing_plan_needs_no_admission(self):
        self.rows[0].update(eligible=False, passed=False, moves=None, admission={})
        self.assertTrue(report.analyze(self.rows, self.ids)['definitive'])

    def test_d206_popcount_and_load_boundaries(self):
        admission = self.rows[0]['admission']
        # Eight discontiguous bits: capacity is counted, not compared numerically.
        mask = sum(1 << i for i in range(0, 16, 2))
        admission.update(policy='D206', core_count=8, core_mask=mask, load1=45)
        admission['source_payload'].update(mask=mask, load1=45)
        self.assertTrue(report.valid_admission(self.rows[0]))
        self.assertTrue(report.analyze(self.rows, self.ids)['definitive'])
        for key, value in [('core_count', 9), ('core_count', True), ('core_count', None),
                           ('load1', 45.01), ('policy', 'D999')]:
            original = admission[key]
            admission[key] = value
            self.assertFalse(report.valid_admission(self.rows[0]), (key, value))
            admission[key] = original
        admission.update(core_mask=1 << 20, core_count=1)
        admission['source_payload']['mask'] = 1 << 20
        self.assertFalse(report.valid_admission(self.rows[0]))

    def test_d204_history_is_not_reinterpreted(self):
        admission = self.rows[0]['admission']
        self.assertTrue(report.valid_admission(self.rows[0]))
        admission['policy'] = 'D204'
        self.assertTrue(report.valid_admission(self.rows[0]))
        admission['load1'] = admission['source_payload']['load1'] = 31
        self.assertFalse(report.valid_admission(self.rows[0]))
        admission['load1'] = admission['source_payload']['load1'] = 30
        # D204's historical numeric comparison admitted a sparse high mask.
        admission['core_mask'] = admission['source_payload']['mask'] = 1 << 20
        self.assertTrue(report.valid_admission(self.rows[0]))

    def test_policy_history_and_strata_are_visible(self):
        self.rows[0]['admission'].update(policy='D206', core_count=12)
        event = {'policy': 'D206', 'declared_at': '2026-10-31T22:00:00Z'}
        summary = report.analyze(self.rows, self.ids)
        self.assertEqual(summary['admission_strata']['D206']['8.2']['rows'], 1)
        self.assertEqual(summary['admission_strata']['D204']['8.2']['rows'], 54)
        text = report.render(self.rows, self.ids, policy_history=[event])
        self.assertIn(event['declared_at'], text)
        self.assertIn('| D206 | 8.2 | 1 | 1 | 0 |', text)
        self.assertIn('normal priority', text)

    def test_d206_stale_or_changed_source_rejected(self):
        admission = self.rows[0]['admission']
        admission.update(policy='D206', core_count=12)
        admission.update(read_at=1119, age_s=120)
        self.assertFalse(report.valid_admission(self.rows[0]))
        admission.update(read_at=1000, age_s=1)
        admission['source_payload']['mask'] = 255
        self.assertFalse(report.valid_admission(self.rows[0]))

    def test_stale_and_inconsistent_evidence_is_invalid(self):
        admission = self.rows[0]['admission']
        admission.update(read_at=1119, age_s=120)
        self.assertFalse(report.analyze(self.rows, self.ids)['definitive'])
        admission.update(read_at=1000, age_s=1)
        self.assertTrue(report.valid_admission(self.rows[0]))
        admission['age_s'] = 2
        self.assertFalse(report.valid_admission(self.rows[0]))
        admission['age_s'] = 1
        admission['source_payload']['load1'] = 31
        self.assertFalse(report.valid_admission(self.rows[0]))

    def test_missing_nonfinite_and_future_evidence_is_invalid(self):
        admission = self.rows[0]['admission']
        for key in ('t', 'read_at', 'age_s'):
            original = admission[key]
            for value in (None, True, '1000', float('nan'), float('inf')):
                admission[key] = value
                self.assertFalse(report.valid_admission(self.rows[0]), (key, value))
            admission[key] = original
        admission.update(age_s=-1, read_at=998)
        self.assertFalse(report.valid_admission(self.rows[0]))

    def test_partial_is_not_conclusion(self):
        text = report.render([], self.ids, {'state': 'blocked', 'reason': 'probe denied'})
        self.assertIn('not settled', text)
        self.assertIn('probe denied', text)

    def test_unobserved_row_is_not_paired_failure(self):
        summary = report.analyze(self.rows[:-1], self.ids)
        comparison = summary['comparisons']['8.2 vs K1r']
        self.assertEqual(comparison['compared_maps'], 54)
        self.assertEqual(comparison['only_left'], [])

    def test_twenty_map_complete_and_partial_scope(self):
        ids = self.ids[:20]
        rows = [row for row in self.rows if row['instance_id'] in ids]
        summary = report.analyze(rows, ids)
        self.assertTrue(summary['definitive'])
        self.assertEqual(summary['rows'], 60)
        self.assertEqual(summary['comparisons']['8.2 vs null']['compared_maps'], 20)
        self.assertFalse(report.analyze(rows[:-1], ids)['definitive'])
        self.assertIn('59/60', ' '.join(report.analyze(rows[:-1], ids)['reasons']))
        plan = {'decision': 'D213', 'map_limit': 20, 'instance_ids': ids}
        text = report.render(rows, ids, stop_plan=plan)
        self.assertIn('first 20 of 55 maps in fixed manifest order', text)
        self.assertIn('Coverage: 60/60', text)
        self.assertIn('Passed / 20', text)
        self.assertIn('20/20', text)
        self.assertIn('does not settle the full 55-map advantage', text)
        self.assertNotIn('advantage is gone', text)

    def test_subset_lead_not_compared_to_original_absolute_gap(self):
        ids = self.ids[:20]
        rows = [row.copy() for row in self.rows if row['instance_id'] in ids]
        for row in rows:
            if row['arm'] != '8.2':
                row.update(passed=False, moves=None)
        summary = report.analyze(rows, ids)
        self.assertTrue(summary['definitive'])
        self.assertEqual(summary['comparisons']['8.2 vs null']['passes_p'], report.sign_p(20, 0))
        text = report.conclusion(summary)
        self.assertIn('planned 20-map cohort', text)
        self.assertIn('Both paired exact tests establish a lead', text)
        self.assertNotIn('smaller under', text)
        self.assertNotIn('originally 22', text)

    def test_stop_plan_precedes_outcome_reads_and_excludes_unplanned_rows(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            matched = root/'matched'
            matched.mkdir()
            self.assertEqual(report.planned_ids(self.ids, root), (self.ids, None))
            plan = {'decision': 'D213', 'map_limit': 20, 'instance_ids': self.ids[:20]}
            stop = matched/'stop-plan.json'
            stop.write_text(json.dumps(plan))
            ids, selected = report.planned_ids(self.ids, root)
            self.assertEqual(ids, self.ids[:20])
            self.assertEqual(selected, plan)
            inside = matched/'rows'/self.ids[0]
            inside.mkdir(parents=True)
            (inside/'8.2.json').write_text(json.dumps({'instance_id': self.ids[0]}))
            outside = matched/'rows'/self.ids[20]
            outside.mkdir(parents=True)
            # Parsing this would fail: proves outcomes outside the cap are untouched.
            (outside/'8.2.json').write_text('UNREAD INVALID JSON')
            self.assertEqual(report.read_rows(ids, root), [{'instance_id': self.ids[0]}])
            for wrong in [dict(plan, map_limit=21), dict(plan, instance_ids=self.ids[1:21]),
                          dict(plan, decision='unknown')]:
                stop.write_text(json.dumps(wrong))
                with self.assertRaises(ValueError):
                    report.planned_ids(self.ids, root)

    def test_expected_ids_must_be_nonempty_unique(self):
        self.assertFalse(report.analyze([], [])['definitive'])
        self.assertFalse(report.analyze([], ['same', 'same'])['definitive'])

    def test_exported_subset_compatible_with_cached_full_cohort_publisher(self):
        ids = self.ids[:20]
        rows = [row.copy() for row in self.rows if row['instance_id'] in ids]
        for row in rows:
            if row['arm'] != '8.2':
                row.update(passed=False, moves=None)
        summary = report.analyze(rows, ids)
        exported = report.exported_summary(summary)
        self.assertTrue(summary['definitive'])
        self.assertFalse(exported['definitive'])
        self.assertTrue(exported['planned_cohort_definitive'])
        self.assertIn('full 55-map cohort', exported['definitive_scope_note'])
        self.assertIn('planned 20-map cohort', report.conclusion(exported))
        self.assertIn('does not settle the full 55-map advantage', report.conclusion(exported))
        # The cached implementation checks this flag before comparing absolute gaps.
        self.assertFalse(exported['definitive'])
        full = report.analyze(self.rows, self.ids)
        self.assertEqual(report.exported_summary(full), full)
        partial = report.exported_summary(report.analyze(rows[:-1], ids))
        self.assertFalse(partial['planned_cohort_definitive'])
        self.assertIn('not settled', report.conclusion(partial))

    def test_stop_plan_manifest_hash_when_present(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            matched = root/'matched'
            matched.mkdir()
            manifest = matched/'manifest.json'
            manifest.write_text(json.dumps(self.ids))
            plan = {'decision': 'D213', 'map_limit': 20, 'instance_ids': self.ids[:20],
                    'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
            stop = matched/'stop-plan.json'
            stop.write_text(json.dumps(plan))
            self.assertEqual(report.planned_ids(self.ids, root)[0], self.ids[:20])
            manifest.write_text(manifest.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'manifest_sha256'):
                report.planned_ids(self.ids, root)

    def test_current_generation_status_and_safe_fallback(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            matched = root/'matched'
            matched.mkdir()
            (matched/'status.json').write_text(json.dumps({'generation': 'old'}))
            self.assertEqual(report.read_status(root), {'generation': 'old'})
            generation = matched/'generations'/'d206'
            generation.mkdir(parents=True)
            current = generation/'status.json'
            current.write_text(json.dumps({'generation': 'D206'}))
            active = matched/'active-controller.json'
            for pointer in (str(current), str(current.relative_to(root))):
                active.write_text(json.dumps({'status_path': pointer}))
                self.assertEqual(report.read_status(root), {'generation': 'D206'})
            active.write_text(json.dumps({'status_path': str(root/'outside.json')}))
            with self.assertRaises(ValueError):
                report.read_status(root)
            active.write_text(json.dumps({'status_path': 'matched/../outside.json'}))
            with self.assertRaises(ValueError):
                report.read_status(root)
            active.write_text(json.dumps({}))
            self.assertEqual(report.read_status(root), {'generation': 'old'})


if __name__ == '__main__':
    unittest.main()
