"""Admission checks only; no corpus trials, finisher, or subprocess workers."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import admission
import matched


class AdmissionTest(unittest.TestCase):
    def result(self, payload, now_s=1000):
        with patch.object(Path, 'read_text', return_value=json.dumps(payload)):
            return admission.probe(now_s=now_s)

    def test_fresh_inclusive_thresholds(self):
        row = self.result(dict(mask=975, load1=45, t=999))
        self.assertTrue(row['admitted'])
        self.assertEqual(row['age_s'], 1)
        self.assertEqual(row['read_at'], 1000)
        self.assertEqual(row['source_payload'], dict(mask=975, load1=45, t=999))
        self.assertEqual(row['core_count'], 8)
        self.assertEqual(row['policy'], 'D206')

    def test_freshness_strict_boundary_and_future(self):
        self.assertTrue(self.result(dict(mask=4095, load1=1, t=880.001))['admitted'])
        self.assertFalse(self.result(dict(mask=4095, load1=1, t=880))['admitted'])
        self.assertFalse(self.result(dict(mask=4095, load1=1, t=879))['admitted'])
        self.assertFalse(self.result(dict(mask=4095, load1=1, t=1000.001))['admitted'])

    def test_high_load_low_mask(self):
        self.assertFalse(self.result(dict(mask=262143, load1=45.01, t=999))['admitted'])
        self.assertFalse(self.result(dict(mask=127, load1=1, t=999))['admitted'])
        self.assertFalse(self.result(dict(mask=1 << 17, load1=1, t=999))['admitted'])

    def test_missing_and_malformed(self):
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError('missing')):
            self.assertFalse(admission.probe(now_s=1000)['admitted'])
        with patch.object(Path, 'read_text', return_value='{'):
            self.assertFalse(admission.probe(now_s=1000)['admitted'])
        for value in (None, [], {}, {'mask': 4095, 'load1': 1}):
            self.assertFalse(self.result(value)['admitted'])

    def test_strict_finite_numbers(self):
        for key in ('mask', 'load1', 't'):
            for value in ('4095', True, None, float('nan'), float('inf'), 10**1000):
                payload = dict(mask=4095, load1=1, t=999)
                payload[key] = value
                self.assertFalse(self.result(payload)['admitted'], (key, value))

    def test_worker_rechecks_release_age(self):
        snapshot = self.result(dict(mask=4095, load1=1, t=999))
        self.assertTrue(admission.validate_snapshot(snapshot, now_s=1118.999)['admitted'])
        self.assertFalse(admission.validate_snapshot(snapshot, now_s=1119)['admitted'])

    def test_matched_uses_shared_probe(self):
        expected = {'admitted': False, 'errors': ['stale']}
        with patch.object(matched, 'state_probe', return_value=expected):
            self.assertIs(matched.probe(), expected)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.out_patch = patch.object(matched, 'OUT', self.output)
        self.out_patch.start()
        self.addCleanup(self.out_patch.stop)
        self.binding_patch = patch.object(matched, 'assert_binding')
        self.binding = self.binding_patch.start()
        self.addCleanup(self.binding_patch.stop)
        self.man = {'code': {'judge.py': 'judge-hash'}}
        self.job = dict(instance_id='map-0', input_sha256='map-hash',
                        samples={arm: f'sample-{arm}' for arm in matched.ARMS})
        self.identity = {'outcome_epoch': 'epoch-hash', 'version': 'identity-version'}
        matched.write(self.output/'cohort-identity.json', self.identity)
        self.paths = {}
        for arm in matched.ARMS:
            plan = self.output/'plans'/'map-0'/f'{arm}.json'
            report = plan.with_name(f'{arm}.report.json')
            matched.write(plan, {'plan': arm})
            matched.write(report, {'report': arm})
            row = dict(arm=arm, instance_id='map-0', sample=0, complete=True,
                       passed=True, eligible=True, timed_out=False, moves=10, error=None,
                       binding=dict(input_sha256='map-hash', sample_sha256=f'sample-{arm}',
                                    code=self.man['code'], finisher_s=600, **self.identity),
                       winner=dict(plan_sha256=matched.sha(plan), report_sha256=matched.sha(report)),
                       admission=dict(admitted=True, policy='D206', core_count=8,
                                      core_mask=975, load1=45, t=999, read_at=1000, age_s=1,
                                      source_payload=dict(mask=975, load1=45, t=999)))
            path = self.output/'rows'/'map-0'/f'{arm}.json'
            matched.write(path, row)
            self.paths[arm] = path

    def change_row(self, selected_arm='8.2', **changes):
        row = json.loads(self.paths[selected_arm].read_text())
        row.update(changes)
        matched.write(self.paths[selected_arm], row)

    def test_complete_source_bound_cohort_preserved(self):
        before = {arm: path.read_bytes() for arm, path in self.paths.items()}
        self.assertTrue(matched.completed_map(self.man, self.job))
        self.assertEqual(self.binding.call_count, 3)
        self.assertEqual(before, {arm: path.read_bytes() for arm, path in self.paths.items()})

    def test_absent_cohort_not_completed_partial_rejected(self):
        self.paths['8.2'].unlink()
        with self.assertRaisesRegex(RuntimeError, 'partial cohort'):
            matched.completed_map(self.man, self.job)
        for path in self.paths.values():
            path.unlink(missing_ok=True)
        self.assertFalse(matched.completed_map(self.man, self.job))

    def test_row_binding_and_identity_drift_rejected(self):
        original = json.loads(self.paths['8.2'].read_text())
        for field, value in [('binding', {}), ('arm', 'K1r'), ('sample', 1),
                             ('instance_id', 'other-map'), ('complete', False), ('error', 'worker failed')]:
            self.change_row(**{field: value})
            with self.assertRaisesRegex(RuntimeError, 'invalid historical cohort row'):
                matched.completed_map(self.man, self.job)
            matched.write(self.paths['8.2'], original)
        matched.write(self.output/'cohort-identity.json', {'outcome_epoch': 'different'})
        with self.assertRaisesRegex(RuntimeError, 'invalid historical cohort row'):
            matched.completed_map(self.man, self.job)

    def test_live_source_binding_drift_rejected(self):
        self.binding.side_effect = RuntimeError('stored sample changed')
        with self.assertRaisesRegex(RuntimeError, 'stored sample changed'):
            matched.completed_map(self.man, self.job)

    def test_winner_plan_and_report_drift_rejected(self):
        for suffix in ('.json', '.report.json'):
            path = self.output/'plans'/'map-0'/f'8.2{suffix}'
            original = path.read_bytes()
            path.write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'historical winner artifact changed'):
                matched.completed_map(self.man, self.job)
            path.write_bytes(original)

    def test_invalid_historical_admission_rejected(self):
        row = json.loads(self.paths['8.2'].read_text())
        row['admission']['core_count'] = 7
        matched.write(self.paths['8.2'], row)
        with self.assertRaisesRegex(RuntimeError, 'invalid historical cohort row'):
            matched.completed_map(self.man, self.job)


class PlannedLimitTest(unittest.TestCase):
    def test_default_and_sealed_prefix_limit(self):
        with TemporaryDirectory() as directory, patch.object(matched, 'OUT', Path(directory)):
            output = Path(directory)
            man = {'maps': [{'instance_id': f'map-{index:02}'} for index in range(55)]}
            matched.write(output/'manifest.json', man)
            self.assertEqual(matched.planned_map_limit(man), 55)
            plan = dict(decision='D213', map_limit=20,
                        instance_ids=[job['instance_id'] for job in man['maps'][:20]],
                        manifest_sha256=matched.sha(output/'manifest.json'))
            matched.write(output/'stop-plan.json', plan)
            self.assertEqual(matched.planned_map_limit(man), 20)
            for wrong in [dict(plan, map_limit=21), dict(plan, decision='unknown'),
                          dict(plan, instance_ids=list(reversed(plan['instance_ids']))),
                          dict(plan, instance_ids=plan['instance_ids'][:-1]),
                          dict(plan, manifest_sha256='wrong-hash')]:
                matched.write(output/'stop-plan.json', wrong)
                with self.assertRaisesRegex(RuntimeError, 'invalid fixed-prefix stop plan'):
                    matched.planned_map_limit(man)
            matched.write(output/'stop-plan.json', plan)
            manifest = output/'manifest.json'
            manifest.write_text(manifest.read_text() + '\n')
            with self.assertRaisesRegex(RuntimeError, 'invalid fixed-prefix stop plan'):
                matched.planned_map_limit(man)


if __name__ == '__main__':
    unittest.main()
