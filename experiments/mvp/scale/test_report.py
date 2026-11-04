"""Synthetic saved-artifact checks only; no map, solver or checker imports."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(report, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.folder = self.root / 'out/maps/synthetic'
        self.folder.mkdir(parents=True)
        self.job = dict(map_id='synthetic', input_sha256='input-hash', sha256='input-hash',
                        split='train', input='synthetic/maps/train/map.json')
        self.run = dict(split='train', config=dict(no_repair=True, rule_freeze_v1=False))
        self.finished = dict(status='ok', plan={})
        self.checked = dict(passed=True, violations=[], count=dict(weighted_nonproductive=27.))
        self.save(self.folder / 'finished.json', self.finished)
        self.save(self.folder / 'report.json', self.checked)
        self.row = dict(job=self.job, complete=True, passed=True,
                        solver_status='ok', finalize_status='ok', checker_passed=True,
                        report_sha256=report.sha(self.folder / 'report.json'),
                        finished_sha256=report.sha(self.folder / 'finished.json'),
                        moves=27., timings=dict(planner_seconds=12., finisher_seconds=8., checker_seconds=3.),
                        metrics=dict(preplan_time_s=7., terminal_time_s=4.))
        self.save(self.folder / 'summary.json', self.row)

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def outcome(self):
        return report.new_outcome(self.folder, self.job, self.run)

    def test_valid_certificate_and_native_stage_times(self):
        outcome = self.outcome()
        self.assertTrue(outcome['passed'])
        self.assertTrue(outcome['resolved'])
        self.assertEqual(outcome['moves'], 27.)
        self.assertEqual(outcome['seconds'], dict(planner=12., carrier=7., terminal=4., finisher=8., checker=3.))

    def test_report_hash_tamper_cannot_display_pass(self):
        self.checked['count']['weighted_nonproductive'] = 9.
        self.save(self.folder / 'report.json', self.checked)
        outcome = self.outcome()
        self.assertFalse(outcome['passed'])
        self.assertFalse(outcome['resolved'])
        self.assertEqual(outcome['stage'], 'certificate_invalid')
        self.assertIn('hash differs', outcome['error'])

    def test_finished_hash_tamper_cannot_display_pass(self):
        self.finished['plan'] = {'changed': True}
        self.save(self.folder / 'finished.json', self.finished)
        self.assertFalse(self.outcome()['passed'])
        self.assertIn('hash differs', self.outcome()['error'])

    def test_finalizer_failure_cannot_display_pass_even_if_flag_says_pass(self):
        self.row['finalize_status'] = 'error'
        self.save(self.folder / 'summary.json', self.row)
        outcome = self.outcome()
        self.assertFalse(outcome['passed'])
        self.assertFalse(outcome['resolved'])
        self.assertIn('unsuccessful', outcome['error'])

    def test_missing_train_authority_cannot_display_pass(self):
        for run in ({}, dict(self.run, split='test'), dict(self.run, split=None)):
            with self.subTest(run=run):
                outcome = report.new_outcome(self.folder, self.job, run)
                self.assertFalse(outcome['passed'])
                self.assertFalse(outcome['resolved'])
                self.assertIn('TRAIN', outcome['error'])
        for job in ({k:v for k,v in self.job.items() if k != 'split'},
                    dict(self.job, split='test'), dict(self.job, input='maps/test/map.json')):
            with self.subTest(job=job):
                outcome = report.new_outcome(self.folder, job, self.run)
                self.assertFalse(outcome['passed'])
                self.assertFalse(outcome['resolved'])
                self.assertIn('TRAIN', outcome['error'])

    def test_input_binding_and_no_repair_guard_are_required(self):
        altered_job = dict(self.job, input_sha256='different')
        self.assertFalse(report.new_outcome(self.folder, altered_job, self.run)['passed'])
        altered_run = deepcopy(self.run)
        altered_run['config']['no_repair'] = False
        self.assertFalse(report.new_outcome(self.folder, self.job, altered_run)['passed'])

    def test_rejecting_full_report_and_unchecked_moves_cannot_pass(self):
        for changed in (dict(self.checked, passed=False),
                        dict(self.checked, violations=[dict(rule_id='RULE')]),
                        dict(self.checked, count=dict(weighted_nonproductive=99.))):
            with self.subTest(changed=changed):
                self.save(self.folder / 'report.json', changed)
                self.row['report_sha256'] = report.sha(self.folder / 'report.json')
                self.save(self.folder / 'summary.json', self.row)
                self.assertFalse(self.outcome()['passed'])

    def baseline(self):
        ladder, d185 = [], []
        for fill in (60, 70, 80):
            for ship in ('feeder_500', 'feeder_800'):
                for mass in ('high', 'low'):
                    for seed in range(3):
                        name = f'{ship}-{fill}-{mass}-{seed}'
                        ladder.append(dict(self.job, map_id=name, fill_target=fill,
                                           ship=ship, mass_level=mass, seed=seed))
        for ship in ('feeder_500', 'feeder_800', 'feeder_1700'):
            for seed in range(4):
                d185.append(dict(self.job, map_id=f'{ship}-D185-{seed}', ship=ship, seed=seed))
        rows = [dict(j, resolved=False) for j in ladder]
        return dict(format='D199-scale-barrier-census-v1', rows=rows, ladder_jobs=ladder,
                    d185_jobs=d185, ranking=[], updated_at='synthetic', resolved_maps=0,
                    passing_maps=0, failed_maps=0)

    def test_report_before_run_exists_counts_pending_without_failures(self):
        baseline_path = self.root / 'baseline.json'
        self.save(baseline_path, self.baseline())
        text = report.render(self.root / 'absent-run', baseline_path)
        self.assertIn('**0/0** certified passes', text)
        self.assertIn('**48 pending/unresolved** of 48 planned', text)
        self.assertIn('Run has not started', text)
        self.assertIn('| f60 | 0/12 | 0/12 | 0 | 0 | 0 | 0 / 0 | 12/12 | 0/12 | pending |', text)
        self.assertIn('| f60 | feeder_500 | high | 0/0 | 0/0 | 3 | 3 | 3 |', text)

    def test_incomplete_summary_is_pending_even_with_true_pass_flag(self):
        self.row['complete'] = False
        self.row['stage'] = 'full_checker'
        self.save(self.folder / 'summary.json', self.row)
        outcome = self.outcome()
        self.assertFalse(outcome['passed'])
        self.assertFalse(outcome['resolved'])
        self.assertIsNone(outcome['moves'])

    def test_planned_cohort_worst_case_bound(self):
        bound = report.planned_bounds(1, 8, 6, 12)
        self.assertEqual(bound, dict(baseline_upper=5, new_lower=6, superiority=True))
        self.assertFalse(report.planned_bounds(1, 8, 5, 12)['superiority'])
        self.assertFalse(report.planned_bounds(0, 0, 12, 12)['superiority'])
        with self.assertRaises(ValueError):
            report.planned_bounds(9, 8, 6, 12)

    def test_gate_zero_complete_new_cannot_win_with_pending_baseline(self):
        self.assertEqual(report.gate_decision(0, 0, 0, 12, 12), 'not passed')
        self.assertEqual(report.gate_decision(0, 6, 0, 12, 12), 'not passed')

    def test_gate_new_incomplete_upper_bound_equal_baseline_lower_cannot_win(self):
        # One new pass plus two unresolved maps can only tie three old passes.
        self.assertEqual(report.gate_decision(3, 4, 1, 10, 12), 'not passed')
        self.assertEqual(report.gate_decision(3, 4, 1, 10, 12,
                                             paired_improvement=True), 'not passed')

    def test_gate_reversible_comparison_remains_pending(self):
        # This is the final V6 f60 bound: five new passes may only tie the
        # baseline's five possible passes, so strict superiority is unresolved.
        self.assertEqual(report.gate_decision(1, 8, 5, 12, 12), 'pending')
        self.assertEqual(report.gate_decision(1, 8, 5, 12, 12,
                                             paired_improvement=True),
                         'pending; paired improvement')
        self.assertEqual(report.gate_decision(0, 0, 0, 0, 12), 'pending')

    def test_gate_positive_certified_lower_bound_strictly_exceeds_baseline_upper(self):
        self.assertEqual(report.gate_decision(1, 8, 6, 6, 12),
                         'PASS (worst-case baseline bound)')
        self.assertEqual(report.gate_decision(1, 12, 2, 12, 12),
                         'PASS (worst-case baseline bound)')
        self.assertEqual(report.gate_decision(2, 12, 2, 12, 12), 'not passed')
        with self.assertRaises(ValueError):
            report.gate_decision(1, 8, 6, 5, 12)

    def test_render_completed_zero_pass_rungs_are_not_pending(self):
        baseline = self.baseline()
        baseline_path = self.root / 'baseline.json'
        self.save(baseline_path, baseline)
        out = self.root / 'synthetic-run'
        for job in baseline['ladder_jobs']:
            if job['fill_target'] in (70, 80):
                self.save(out / 'maps' / job['map_id'] / 'summary.json',
                          dict(complete=True, passed=False, solver_status='dead_end'))
        text = report.render(out, baseline_path)
        for rung in ('f70', 'f80'):
            self.assertIn(f'| {rung} | 0/12 | 12/12 | 0 | 0 | 0 | 0 / 0 | 12/12 | 0/12 | not passed |', text)
        self.assertIn('| f60 | 0/12 | 0/12 | 0 | 0 | 0 | 0 / 0 | 12/12 | 0/12 | pending |', text)

    def test_render_historical_timing_caveat_preserves_legality_and_moves(self):
        baseline = self.baseline()
        baseline_path = self.root / 'baseline.json'
        self.save(baseline_path, baseline)
        out = self.root / 'synthetic-old-run'
        self.save(out / 'run.json', dict(split='train', config=dict(packing_module='carrier_v6'),
                                       cohort=dict(maps=baseline['ladder_jobs']+baseline['d185_jobs'])))
        text = report.render(out, baseline_path)
        self.assertIn('adaptive TRAIN observations', text)
        self.assertIn('Historical V6 lacks that evidence', text)
        self.assertIn('not a D206-admitted timing or failure-rate curve', text)
        self.assertIn('does not invalidate unchanged full-checker legality or measured moves', text)
        self.assertIn('requires a repeat under the declared admission policy', text)

    def test_retiring_prototype_cannot_overwrite_primary_report(self):
        original = self.root / 'experiments/mvp/scale/runs/main'
        primary = self.root / 'experiments/mvp/scale/runs/v3'
        self.assertEqual(report.report_filename(original, primary_exists=False), 'SCALE-SOLVER.md')
        self.assertEqual(report.report_filename(original, primary_exists=True), 'PROTOTYPE.md')
        self.assertEqual(report.report_filename(primary, primary_exists=True), 'SCALE-SOLVER.md')
        self.assertEqual(report.report_filename(primary, primary_exists=False), 'SCALE-SOLVER.md')
        self.assertEqual(report.report_filename(primary, primary_exists=True, primary_name='v4'), 'V3.md')
        self.assertEqual(report.report_filename(self.root / 'experiments/mvp/scale/runs/v4',
                                               primary_exists=True, primary_name='v4'), 'SCALE-SOLVER.md')
        self.assertEqual(report.report_filename(self.root / 'experiments/mvp/scale/runs/v4',
                                               primary_exists=True, primary_name='v5'), 'V4.md')
        self.assertEqual(report.report_filename(self.root / 'experiments/mvp/scale/runs/v5',
                                               primary_exists=True, primary_name='v6'), 'V5.md')


if __name__ == '__main__':
    unittest.main()
