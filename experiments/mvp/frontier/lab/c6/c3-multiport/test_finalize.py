"""Finalizer gates and stage ledgers, using only temporary files and mocks."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('_test_multiport_finalize',HERE/'finalize.py')
finalize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalize)


class FinalizerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.output = Path(self.directory.name).resolve()
        self.controller = dict(pid=77,started=1000.,policy=dict(phase='frozen'),
            selection=[dict(instance_id=f'map{i}',arm=a) for i in range(32) for a in ('engine12','port_lp')])
        finalize.write(self.output/'controller-process.json',self.controller)

    def tearDown(self):
        self.directory.cleanup()

    def successful_state(self):
        finalize.write(self.output/'controller-state.json',dict(pid=77,finished=1100.,interrupted=False,
            error=None,cleanup_errors=[],completed_jobs=64,pending_jobs=0))
        finalize.write(self.output/'controller-complete.json',dict(finished=1101.,jobs=64,maps=32,phase='frozen'))

    def test_live_controller_gate_never_reads_results_or_launches(self):
        with patch.object(finalize,'pid_absent',return_value=False),patch.object(finalize.subprocess,'Popen') as launch:
            ready,row = finalize.completion_gate(self.output,77,1000.)
        self.assertFalse(ready)
        self.assertEqual(row,self.controller)
        launch.assert_not_called()

    def test_exact_pid_start_phase_and_successful_cleanup_required(self):
        with patch.object(finalize,'pid_absent',return_value=True):
            for pid,started in ((78,1000.),(77,1001.)):
                with self.assertRaises(ValueError):
                    finalize.completion_gate(self.output,pid,started)
            self.successful_state()
            self.assertTrue(finalize.completion_gate(self.output,77,1000.)[0])
            state = json.loads((self.output/'controller-state.json').read_text())
            state['cleanup_errors'] = ['registered native PID still alive']
            finalize.write(self.output/'controller-state.json',state)
            with self.assertRaises(ValueError):
                finalize.completion_gate(self.output,77,1000.)

    def test_pid_probe_only_exact_expected_pid_and_unknown_fails_closed(self):
        with patch.object(finalize.os,'kill',side_effect=ProcessLookupError) as probe:
            self.assertTrue(finalize.pid_absent(77))
        probe.assert_called_once_with(77,0)
        with patch.object(finalize.os,'kill',side_effect=PermissionError):
            with self.assertRaises(RuntimeError):
                finalize.pid_absent(77)

    def test_stage_records_actual_command_environment_pid_and_exit(self):
        child = Mock(pid=888)
        child.wait.return_value = 0
        state = dict(stages=[])
        with patch.object(finalize,'HERE',self.output),patch.object(finalize,'ROOT',self.output), \
             patch.object(finalize.subprocess,'Popen',return_value=child) as launch, \
             patch.dict(finalize.os.environ,{'C6_WORKER_DEADLINE':'old','C6_WORKER_STARTED':'old'}):
            finalize.run_stage(self.output,'mock',[sys.executable,'dummy.py'],state)
        self.assertEqual(launch.call_args.args[0],['/usr/bin/nice','-n','5',sys.executable,'dummy.py'])
        self.assertNotIn('C6_WORKER_DEADLINE',launch.call_args.kwargs['env'])
        row = json.loads((self.output/'final-mock-process.json').read_text())
        self.assertEqual(row['pid'],888)
        self.assertEqual(row['returncode'],0)
        self.assertEqual(row['command'],launch.call_args.args[0])
        self.assertTrue(all(v == '1' for v in row['environment'].values()))
        self.assertLessEqual(row['started'],row['registered'])
        self.assertLessEqual(row['registered'],row['finished'])

    def test_failed_audit_never_updates_notes(self):
        output = self.output/'results/main'
        output.mkdir(parents=True)
        entries = [dict(instance_id=f'map{i}') for i in range(32)]
        argv = ['finalize.py','--controller-pid','77','--controller-started','1000.']
        with patch.object(finalize,'HERE',self.output),patch.object(finalize,'ROOT',self.output), \
             patch.object(finalize,'source_record',return_value={'fixed':'sha'}), \
             patch.object(finalize,'completion_gate',return_value=(True,self.controller)), \
             patch.object(finalize,'bound_rows',return_value=({'bindings':'fixed'},entries,{})), \
             patch.object(finalize,'run_stage',side_effect=RuntimeError('audit failed')), \
             patch.object(finalize,'update_notes') as update,patch.object(sys,'argv',argv):
            with self.assertRaisesRegex(RuntimeError,'audit failed'):
                finalize.main()
        update.assert_not_called()
        result = json.loads((output/'FINALIZE.json').read_text())
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'],'failed')

    def test_marked_notes_replacement_preserves_other_prose(self):
        notes = self.output/'NOTES.md'
        notes.write_text('Original prose\n\n'+finalize.BEGIN+'\nstale\n'+finalize.END+'\nTail\n')
        block = finalize.BEGIN+'\nverified\n'+finalize.END
        with patch.object(finalize,'HERE',self.output):
            finalize.update_notes(block)
        text = notes.read_text()
        self.assertIn('Original prose',text)
        self.assertIn('Tail',text)
        self.assertIn('verified',text)
        self.assertNotIn('stale',text)
        self.assertEqual(text.count(finalize.BEGIN),1)


if __name__ == '__main__':
    unittest.main()
