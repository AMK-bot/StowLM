"""Synthetic supervisor checks; never read or launch live corpus trials."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import d213_finish as finish
import matched


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.patch_value(finish, 'P', self.output)
        self.patch_value(matched, 'OUT', self.output)
        self.complete = self.patch_value(matched, 'completed_map', return_value=True)
        self.command = self.patch_value(finish.subprocess, 'run')
        self.sleep = self.patch_value(finish.time, 'sleep')
        self.man = {'maps': [{'instance_id': f'map-{index:02}'} for index in range(55)]}
        matched.write(self.output/'manifest.json', self.man)
        self.plan = dict(decision='D213', map_limit=20,
                         instance_ids=[job['instance_id'] for job in self.man['maps'][:20]],
                         manifest_sha256=matched.sha(self.output/'manifest.json'))
        matched.write(self.output/'stop-plan.json', self.plan)
        matched.write(self.output/'status-D206.json', {'status': 'running'})
        for iid in self.plan['instance_ids']:
            for arm in matched.ARMS:
                # No outcomes exist: the supervisor uses presence until lock release.
                matched.write(self.output/'rows'/iid/f'{arm}.json', {})
        for job in self.man['maps'][20:]:
            for directory in ('batches-D206', 'batches'):
                matched.write(self.output/directory/job['instance_id']/'fence.json', {'decision': 'D213'})
        # run() is a standalone-process entry point; retain and explicitly close
        # its lock handles when exercising several invocations in one test process.
        self.lock_handles = []
        original_open = Path.open

        def tracked_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            if path.suffix == '.lock':
                self.lock_handles.append(stream)
            return stream

        self.patch_value(Path, 'open', new=tracked_open)
        self.addCleanup(lambda: [stream.close() for stream in self.lock_handles])

    def patch_value(self, owner, name, *args, **kwargs):
        change = patch.object(owner, name, *args, **kwargs)
        value = change.start()
        self.addCleanup(change.stop)
        return value

    def run_supervisor(self):
        with redirect_stdout(StringIO()):
            finish.run()

    def read(self, name):
        return json.loads((self.output/name).read_text())

    def test_waits_for_controller_lock_then_seals_twenty_maps(self):
        # First call locks the supervisor; controller is busy once, then releases.
        locks = self.patch_value(finish.fcntl, 'flock', side_effect=[None, BlockingIOError(), None])
        self.run_supervisor()
        self.assertEqual(locks.call_count, 3)
        self.sleep.assert_called_once_with(20)
        self.assertEqual(self.complete.call_count, 20)
        self.assertEqual([call.args[1] for call in self.complete.call_args_list], self.man['maps'][:20])
        status = self.read('status-D213.json')
        self.assertEqual((status['status'], status['decision'], status['maps'], status['trials']),
                         ('complete', 'D213', 20, 60))
        self.assertEqual(status['total_maps'], 55)
        self.assertTrue(status['planned_stop'])
        self.assertEqual(self.read('status.json'), status)
        active = self.read('active-controller.json')
        self.assertEqual(Path(active['status_path']), self.output/'status-D213.json')
        self.assertEqual(active['decision'], 'D213')
        verification = self.read('D213-final-verification.json')
        self.assertTrue(verification['all_complete_source_bound'])
        self.assertTrue(verification['no_out_of_plan_releases'])
        self.assertEqual(verification['planned_trials'], 60)
        self.assertEqual(self.read('D213-progress.json')['status'], 'complete')
        self.command.assert_called_once()
        args, kwargs = self.command.call_args
        self.assertTrue(args[0][-1].endswith('/matched_report.py'))
        self.assertTrue(kwargs['check'])

    def test_early_controller_exit_rejected(self):
        (self.output/'rows'/self.plan['instance_ids'][0]/'8.2.json').unlink()
        self.patch_value(finish.fcntl, 'flock')
        with self.assertRaisesRegex(RuntimeError, 'exited without60'):
            self.run_supervisor()
        self.command.assert_not_called()
        self.complete.assert_not_called()
        self.assertFalse((self.output/'status-D213.json').exists())

    def test_failed_controller_before_sixty_rows_rejected(self):
        (self.output/'rows'/self.plan['instance_ids'][0]/'8.2.json').unlink()
        matched.write(self.output/'status-D206.json', {'status': 'failed'})
        self.patch_value(finish.fcntl, 'flock')
        with self.assertRaisesRegex(RuntimeError, 'failed before fixed20'):
            self.run_supervisor()
        self.command.assert_not_called()

    def test_out_of_plan_release_in_either_generation_rejected(self):
        self.patch_value(finish.fcntl, 'flock')
        for directory in ('batches-D206', 'batches'):
            with self.subTest(directory=directory):
                start = self.output/directory/self.man['maps'][20]['instance_id']/'start.json'
                matched.write(start, {})
                with self.assertRaisesRegex(RuntimeError, 'Out-of-plan cohort released'):
                    self.run_supervisor()
                start.unlink()
        self.command.assert_not_called()
        self.complete.assert_not_called()

    def test_source_validation_false_rejected(self):
        self.patch_value(finish.fcntl, 'flock')
        self.complete.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Missing final cohort'):
            self.run_supervisor()
        self.command.assert_not_called()
        self.assertFalse((self.output/'status-D213.json').exists())


if __name__ == '__main__':
    unittest.main()
