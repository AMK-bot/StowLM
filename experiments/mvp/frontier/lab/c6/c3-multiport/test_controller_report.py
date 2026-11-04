"""Stdlib mocks only: scheduling, cleanup evidence, checkpoint and report gates."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('_test_c6_controller',HERE/'controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)
report = controller.local_report()


class Clock:
    def __init__(self): self.value = 0.
    def now(self): return self.value
    def sleep(self,seconds): self.value += seconds


class ControllerTests(unittest.TestCase):
    def audit(self, absent=False, errors=None):
        return dict(registration_errors=errors or [],all_registered_descendants_absent=absent,
                    probes=[dict(pid=123,state='absent' if absent else 'alive')])

    def test_transient_native_cleanup_preserves_initial_evidence(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller,'monotonic',clock.now), \
             patch.object(controller,'sleep',clock.sleep), patch.object(controller,'_descendant_snapshot',
             side_effect=[self.audit(),self.audit(absent=True)]):
            callback = Mock()
            result = controller.check_registered_descendants(directory,worker_pid=7,launch_epoch=10,
                job_argv=['run.py','--worker'],on_wait=callback)
            self.assertEqual(result['cleanup_outcome'],'absent')
            self.assertEqual(result['grace_elapsed_s'],1)
            self.assertEqual(len(result['iterations']),2)
            self.assertEqual(result['initial_evidence']['probes'][0]['state'],'alive')
            callback.assert_called_once()
            with patch.object(controller,'_descendant_snapshot',return_value=self.audit(absent=True)):
                controller.check_registered_descendants(directory,worker_pid=7,launch_epoch=10,
                    job_argv=['run.py','--worker'],grace_s=0)
            history = list((Path(directory)/'cleanup-audits/history').glob('*.json'))
            self.assertEqual(len(history),1)
            self.assertEqual(len(json.loads(history[0].read_text())['iterations']),2)

    def test_persistent_native_cleanup_fails_closed_at360(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory, patch.object(controller,'monotonic',clock.now), \
             patch.object(controller,'sleep',clock.sleep), patch.object(controller,'_descendant_snapshot',return_value=self.audit()):
            with self.assertRaises(controller.CleanupBlocked):
                controller.check_registered_descendants(directory,worker_pid=7,launch_epoch=10,job_argv=['run.py'])
            audit = json.loads((Path(directory)/'cleanup-audit.json').read_text())
            self.assertEqual(audit['grace_s'],360.)
            self.assertEqual(audit['grace_elapsed_s'],360.)
            self.assertEqual(audit['cleanup_outcome'],'grace_expired')
            self.assertEqual(len(audit['iterations']),361)

    def test_malformed_registration_fails_immediately(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(controller,'sleep') as sleep, \
             patch.object(controller,'_descendant_snapshot',return_value=self.audit(errors=['bad'])):
            with self.assertRaises(controller.CleanupBlocked):
                controller.check_registered_descendants(directory,worker_pid=7,launch_epoch=10,job_argv=[])
            sleep.assert_not_called()

    def test_deadline_and_sentinel_during_drain_preserve_timeout_reason(self):
        active = {1:dict(child=Mock(poll=lambda:None),reason=None,began=0),
                  2:dict(child=Mock(poll=lambda:None),reason=None,began=100)}
        signals, records = [], []
        with patch.object(controller,'monotonic',return_value=601):
            controller.service_limits(active,record=lambda job,**kw:records.append(kw),
                signal_child=lambda job:signals.append(job),sentinel_exists=lambda:True,draining=True)
        self.assertEqual(active[1]['reason'],'timeout')
        self.assertEqual(active[2]['reason'],'sentinel')
        self.assertEqual(len(signals),2)
        self.assertTrue(records[1]['draining_slot_reserved'])

    def test_timeout_still_reserves_slot_when_sentinel_appears(self):
        active = {1:dict(child=Mock(poll=lambda:None),reason=None,began=100),
                  2:dict(child=Mock(poll=lambda:None),reason='timeout',began=0)}
        with patch.object(controller,'monotonic',return_value=601):
            controller.service_limits(active,record=Mock(),signal_child=Mock(),sentinel_exists=lambda:True)
        self.assertEqual(active[1]['reason'],'sentinel')
        self.assertEqual(active[2]['reason'],'timeout')

    def test_descendant_probes_only_correlated_recorded_pids(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out/'pids').mkdir()
            for pid,parent,started,argv in [(20,10,100,['run.py']), (21,20,101,['run.py']),
                                            (30,999,101,['other.py']), (40,10,50,['run.py'])]:
                (out/'pids'/f'native-{pid}.json').write_text(json.dumps(dict(pid=pid,parent_pid=parent,started=started,argv=argv)))
            with patch.object(controller.os,'kill',side_effect=ProcessLookupError) as probe:
                audit = controller._descendant_snapshot(out,worker_pid=10,launch_epoch=99,job_argv=['run.py'])
            self.assertEqual([call.args for call in probe.call_args_list],[(20,0),(21,0)])
            self.assertTrue(audit['all_registered_descendants_absent'])

    def test_previous_owned_worker_blocks_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            controller._atomic_json(output/'pids/worker-77.json',dict(pid=77,started=1.,argv=['run.py']))
            with patch.object(controller.os,'kill',return_value=None) as probe:
                with self.assertRaises(controller.CleanupBlocked):
                    controller.confirm_previous_output(output)
            probe.assert_called_once_with(77,0)


class RowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        (self.root/'map.json').write_text(json.dumps(dict(load_list=['a','b'])))
        self.entry = dict(instance_id='map',path='map.json',sha256='ab',size='medium',ports=3,
                          vessel='feeder_500',fill_pct=60,fill=.59,development=True,n_boxes=2)
        self.config = dict(marker='one')
        self.root_patch = patch.object(report,'ROOT',self.root)
        self.root_patch.start()
        self.row = self.make_row()

    def tearDown(self):
        self.root_patch.stop()
        self.directory.cleanup()

    def make_row(self, *, passed=True):
        from frozen_reporting.tiers import analyze_report
        native = dict(passed=passed,violations=[],count=dict(weighted_nonproductive=10,
                      relocations=2,hatch_cover_lifts=4,restow_lifts=0,
                      future=[dict(hatch_cover_lifts=4,restow_lifts=0)]),
                      parts=dict(replay=dict(ran=True)),shut_out=[])
        analysis = analyze_report(native)
        return dict(instance_id='map',input='map.json',input_sha256='ab',size='medium',ports=3,
            vessel='feeder_500',fill_pct=60,actual_fill=.59,n_boxes=2,development=True,arm='engine12',
            cold=True,config=self.config,run_key=report.kernel().run_key(self.config,'ab'),complete=True,
            passed=True,wall_s=20.,plan=dict(moves=['concrete']),report=native,assignment={'a':'s1','b':'s2'},
            lifts=10.,parts=dict(relocations=2,hatch_now=4,hatch_later=4,restow_now=0,restow_later=0),
            completed=dict(level=0,column='T5',checked_s=10.,checked_epoch=1010.,analysis=analysis))

    def test_wrong_key_and_partial_cannot_be_completed(self):
        report.validate_row(self.row,self.entry,'engine12',self.config)
        for altered in (dict(self.row,run_key='bad'),dict(self.row,assignment={'a':'s1'}),dict(self.row,passed=False)):
            with self.assertRaises(ValueError):
                report.validate_row(altered,self.entry,'engine12',self.config)

    def test_timeout_retains_pre_cap_concrete_checked_pass(self):
        output = self.root/'results'
        checkpoint = output/'checkpoints/engine12/map.json'
        controller._atomic_json(checkpoint,dict(self.row,complete=False))
        with patch.object(controller,'local_report',return_value=report):
            row = controller.finalize_timeout(output,self.entry,'engine12',self.config,worker_pid=55,
                launched=1000.,cleanup=dict(grace_elapsed_s=3))
        self.assertTrue(row['passed'])
        self.assertEqual(row['wall_s'],600.)
        self.assertEqual(row['plan'],self.row['plan'])
        self.assertEqual(row['report'],self.row['report'])
        self.assertTrue(row['timeout']['concrete_checkpoint_retained'])
        self.assertFalse(row['timeout']['checker_reexecuted'])

    def test_timeout_rejects_stale_or_late_checkpoint(self):
        for altered in (dict(self.row,run_key='old'),dict(self.row,completed=dict(self.row['completed'],checked_epoch=1601.))):
            output = self.root/str(len(list(self.root.iterdir())))
            controller._atomic_json(output/'checkpoints/engine12/map.json',altered)
            with patch.object(controller,'local_report',return_value=report):
                row = controller.finalize_timeout(output,self.entry,'engine12',self.config,worker_pid=55,
                    launched=1000.,cleanup={})
            self.assertFalse(row['passed'])
            self.assertIsNone(row['plan'])
            self.assertIsNone(row['report'])
            self.assertEqual(row['timeout']['replay_result'],'unknown; no concrete saved result')

    def test_timeout_keeps_breach_priority_before_lifts_within_level(self):
        import copy
        from frozen_reporting.tiers import analyze_report
        output = self.root/'rank'
        for path,breach,lifts in ((output/'engine12/map.json',99,1),
                                  (output/'checkpoints/engine12/map.json',1,100)):
            row = copy.deepcopy(self.row)
            row.update(passed=False,lifts=lifts)
            row['report'].update(passed=False,violations=[dict(rule_id='expert.e6.heavy_under_deck',
                severity='hard',measured=1+breach,limit=1,unit='kg')])
            row['report']['count']['weighted_nonproductive'] = lifts
            row['completed'].update(level=1,column='T4',analysis=analyze_report(row['report']))
            controller._atomic_json(path,row)
        with patch.object(controller,'local_report',return_value=report):
            selected = controller.finalize_timeout(output,self.entry,'engine12',self.config,
                worker_pid=55,launched=1000.,cleanup={})
        self.assertEqual(selected['completed']['analysis']['total_breach'],1)
        self.assertEqual(selected['lifts'],100)

    def test_checkpoint_is_not_counted_as_second_final_row(self):
        output = self.root/'results'
        controller._atomic_json(output/'engine12/map.json',self.row)
        controller._atomic_json(output/'checkpoints/engine12/map.json',self.row)
        result = report.summarize(output,entries=[self.entry],arms=['engine12'],configs={('map','engine12'):self.config})
        self.assertEqual(result['completed_jobs'],1)
        self.assertEqual(result['groups']['development']['by_arm']['engine12']['counts']['T5'],1)
        self.assertEqual(len((output/'per-map.csv').read_text().splitlines()),2)

    def test_external_clock_is_separate_from_capture_clock(self):
        output = self.root/'timing'
        ledger = dict(pid=55,instance_id='map',arm='engine12',run_key=self.row['run_key'],
                      started=1000.,reaped=1025.,returncode=0,reason=None)
        controller._atomic_json(output/'pids/worker-55.json',ledger)
        timed = report._timed_row(output,self.row)
        self.assertEqual(timed['wall_s'],25.)
        self.assertEqual(timed['capture_wall_s'],20.)
        self.assertEqual(timed['timing_source'],'registered worker launch to observed reap')
        timeout = report._timed_row(output,dict(self.row,outer_timeout=True,wall_s=999))
        self.assertEqual(timeout['wall_s'],600.)
        self.assertEqual(timeout['capture_wall_s'],999)

    def test_ineligible_diagnostics_do_not_become_completion_or_lift_metrics(self):
        import csv
        from frozen_reporting.tiers import analyze_report
        native = dict(self.row['report'],passed=False,violations=[dict(rule_id='expert.e9.over_width_length',
            severity='hard',measured=2,limit=1,unit='m')])
        analysis = analyze_report(native)
        row = dict(self.row,completed=None,passed=False,plan=None,report=None,assignment=None,lifts=None,parts=None,
            best_ineligible=dict(analysis=analysis,report=native,plan={'moves':['diagnostic']},lifts=10.))
        output = self.root/'diagnostics'
        controller._atomic_json(output/'engine12/map.json',row)
        result = report.summarize(output,entries=[self.entry],arms=['engine12'],configs={('map','engine12'):self.config})
        counts = result['groups']['development']['by_arm']['engine12']
        self.assertEqual(counts['not_completed'],1)
        self.assertTrue(all(value == 0 for value in counts['counts'].values()))
        with (output/'per-map.csv').open() as stream:
            csv_row = next(csv.DictReader(stream))
        self.assertEqual(csv_row['evidence_kind'],'ineligible_candidate_diagnostic_only')
        self.assertEqual(json.loads(csv_row['hard_rules']),['expert.e9.over_width_length'])
        self.assertEqual(csv_row['lifts'],'')
        self.assertEqual(csv_row['tier'],'')
        self.assertEqual(csv_row['restow_later'],'')
        self.assertIn('diagnostic only',(output/'report.md').read_text())

    def test_paired_means_and_tier_counts_stay_within_stratum(self):
        import copy
        from frozen_reporting.tiers import analyze_report
        output = self.root/'paired'
        other = dict(self.entry,instance_id='map2',path='map2.json')
        third = dict(self.entry,instance_id='map3',path='map3.json',vessel='feeder_800',development=False)
        for e in (other,third):
            (self.root/e['path']).write_text(json.dumps(dict(load_list=['a','b'])))
        configs = {}
        for entry,engine,port in ((self.entry,10,8),(other,100,1),(third,1000,900)):
            for arm,lifts in (('engine12',engine),('port_lp',port)):
                row = copy.deepcopy(self.row)
                row.update({k:entry[k] for k in ('instance_id','vessel','development')})
                row.update(input=entry['path'],arm=arm,lifts=lifts)
                row['report']['count']['weighted_nonproductive'] = lifts
                if entry is other and arm == 'port_lp':
                    row['report'].update(passed=False,violations=[dict(rule_id='expert.e6.heavy_under_deck',
                        severity='hard',measured=2,limit=1,unit='fraction')])
                    row['passed'] = False
                analysis = analyze_report(row['report'])
                row['completed'].update(analysis=analysis,level=analysis['level'],
                    column=('T5','T4','T3','T2','T1','T0 only')[analysis['level']])
                config = dict(self.config,input=entry['path'],arm=arm)
                row.update(config=config,run_key=report.kernel().run_key(config,entry['sha256']))
                configs[entry['instance_id'],arm] = config
                controller._atomic_json(output/arm/f"{entry['instance_id']}.json",row)
        result = report.summarize(output,entries=[self.entry,other,third],arms=['engine12','port_lp'],configs=configs)
        dev = result['groups']['development']
        self.assertEqual(dev['by_arm']['port_lp']['counts']['T4'],1)
        cell = 'feeder_500/p3/f60'
        self.assertEqual(dev['by_vessel_ports_fill'][cell]['engine12']['own_T5_pass_means']['mean_lifts'],55)
        self.assertEqual(dev['paired_T5_within_vessel_ports_fill'][cell]['paired_T5_maps'],1)
        self.assertEqual(dev['paired_T5_within_vessel_ports_fill'][cell]['arms']['engine12']['mean_lifts'],10)
        self.assertEqual(result['groups']['nondevelopment']['by_arm']['engine12']['maps'],1)
        self.assertIn('expert.e6.heavy_under_deck',(output/'per-map.csv').read_text())


if __name__ == '__main__':
    unittest.main()
