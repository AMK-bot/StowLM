"""Harness scope, epoch and complete-result reuse tests; no dataset input."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

import scale_solver as solver


class ScopeTests(unittest.TestCase):
    def test_input_scope_rejects_heldout_and_nontraining(self):
        for value in ('experiments/mvp/corpus/test-v3/x.json',
                      'experiments/mvp/strategy/fillsweep/maps/val/x.json',
                      'experiments/mvp/strategy/fillsweep/maps/train/test-v3/x.json',
                      'experiments/mvp/strategy/blockrule/maps/train/../../val/x.json',
                      '/private/tmp/x.json'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                solver.train_path(value)

    def test_symlink_cannot_escape_training_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            train = root / 'experiments/mvp/strategy/fillsweep/maps/train'
            train.mkdir(parents=True)
            heldout = root / 'heldout'
            heldout.mkdir()
            (train / 'link').symlink_to(heldout, target_is_directory=True)
            with patch.object(solver, 'ROOT', root), self.assertRaises(ValueError):
                solver.train_path('experiments/mvp/strategy/fillsweep/maps/train/link/x.json')

    def test_frozen_code_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'plugin.py'
            path.write_text('original')
            with patch.object(solver, 'HERE', root):
                expected = solver.code_hashes()
                solver.check_code(expected)
                path.write_text('changed')
                with self.assertRaises(RuntimeError):
                    solver.check_code(expected)

    def test_commits_are_parseable_and_no_partial_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested/result.json'
            solver.write(path, {'complete': True, 'passed': False})
            self.assertEqual(solver.read(path), {'complete': True, 'passed': False})
            self.assertEqual(list(path.parent.glob('*.tmp-*')), [])


class AdmissionTests(unittest.TestCase):
    """Pure mocked dispatch/stage tests: no runtime or dataset is imported."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'experiments/fleet/core-state.json'
        self.path.parent.mkdir(parents=True)
        self.out = self.root / 'runs/new-revision'
        self.control = self.root / 'control.json'
        self.job = dict(map_id='analytic-map')
        self.config = dict(max_map_workers=2)
        self.clock = dict(wall=1000., mono=0.)
        self.root_patch = patch.object(solver, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.wall_patch = patch.object(solver.time, 'time', side_effect=lambda:self.clock['wall'])
        self.wall_patch.start()
        self.addCleanup(self.wall_patch.stop)
        self.mono_patch = patch.object(solver.time, 'monotonic', side_effect=lambda:self.clock['mono'])
        self.mono_patch.start()
        self.addCleanup(self.mono_patch.stop)
        self.publish()

    def publish(self, **changes):
        state = dict(mask=975, load1=45, t=self.clock['wall'], leader_note='retain exact evidence')
        state.update(changes)
        self.path.write_text(json.dumps(state) + '\n')
        return state

    def advance(self, seconds):
        self.clock['wall'] += seconds
        self.clock['mono'] += seconds

    def reservation(self):
        pool = FakePool()
        future, _ = solver.submit_admitted_map(pool, self.job, self.out, 'unused-runtime',
            self.config, {}, active_maps=0, control=self.control)
        self.assertIsNotNone(future)
        return solver.worker_admission(self.job, self.out, pool.calls[0][-1])

    def test_thresholds_popcount_and_freshness_boundaries(self):
        cases = [(dict(t=1000), True), (dict(t=880.001), True),
                 (dict(t=880), False), (dict(t=1000.001), False),
                 (dict(mask=255, load1=45), True), (dict(mask=127), False),
                 (dict(mask=1 << 40), False), (dict(mask=255 << 32), True),
                 (dict(load1=45.001), False), (dict(load1=0), True)]
        for changes, allowed in cases:
            with self.subTest(changes=changes):
                self.publish(**changes)
                self.assertIs(solver.core_admission()['allowed'], allowed)

    def test_missing_invalid_nonfinite_duplicate_and_wrong_types_fail_closed(self):
        self.path.unlink()
        self.assertFalse(solver.core_admission()['allowed'])
        texts = ['not JSON', 'null', '[]', '{}',
                 '{"mask":255,"mask":975,"load1":20,"t":1000}',
                 '{"mask":975,"load1":20,"t":1000,"extra":NaN}']
        for field, values in dict(mask=[True, -1, 255., '975', None],
                                  load1=[True, -1, float('inf'), '45', None],
                                  t=[True, -1, float('nan'), '1000', None]).items():
            for value in values:
                state=dict(mask=975, load1=20, t=1000);state[field]=value
                texts.append(json.dumps(state))
        for text in texts:
            with self.subTest(text=text):
                self.path.write_text(text)
                row=solver.core_admission()
                self.assertFalse(row['allowed'])
                self.assertTrue(row['errors'])

    def test_exact_bytes_extra_fields_and_source_sha_are_retained(self):
        raw=b'{ "t": 999, "mask": 975, "load1": 45, "note": "leader" }\n'
        self.path.write_bytes(raw)
        row=solver.core_admission()
        self.assertTrue(row['allowed'])
        self.assertEqual(row['source_text'].encode(),raw)
        self.assertEqual(row['source_sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual(row['raw']['note'],'leader')
        self.assertEqual(row['age_s'],1)

    def test_worker_caps_reserve_owned_children_and_diagnostics(self):
        cases=[(0,2,[],2,True),(1,2,[],2,True),(2,2,[],2,False),
               (0,1,[],1,True),(1,1,[],1,False),
               (0,2,[10,11],2,True),(1,2,[10],2,False),
               (0,2,[10,11,12],2,False),(0,2,[],0,False)]
        for active,cap,pids,limit,allowed in cases:
            with self.subTest(active=active,cap=cap,pids=pids,limit=limit):
                self.control.write_text(json.dumps(dict(owned_diagnostic_pids=pids,active_map_limit=limit)))
                result=solver.map_admission(active_maps=active,max_map_workers=cap,control=self.control)
                self.assertIs(result['allowed'],allowed)
        for changes in (dict(active_map_limit=True),dict(owned_diagnostic_pids='10'),
                        dict(owned_diagnostic_pids=[True]),dict(stop_after_active=True)):
            self.control.write_text(json.dumps(changes))
            self.assertFalse(solver.map_admission(active_maps=0,max_map_workers=2,control=self.control)['allowed'])

    def test_new_file_read_per_submit_and_persistence_before_pool_submit(self):
        def before_start(args):
            evidence=solver.worker_admission(self.job,self.out,args[-1])
            self.assertEqual(evidence['evidence']['raw']['leader_note'],'retain exact evidence')
        pool=FakePool(before_start)
        with patch.object(solver,'cpu_sample',side_effect=AssertionError('hardware sample authorized admission')):
            first,_=solver.submit_admitted_map(pool,self.job,self.out,'runtime',self.config,{},active_maps=0)
            self.assertIsNotNone(first)
            self.publish(load1=46)
            second,row=solver.submit_admitted_map(pool,dict(map_id='next-map'),self.out,'runtime',self.config,{},active_maps=1)
        self.assertIsNone(second)
        self.assertFalse(row['allowed'])
        self.assertEqual(len(pool.calls),1)
        self.assertFalse((self.out/'maps/next-map').exists())

    def test_admission_artifact_tamper_and_missing_binding_rejected_before_native_imports(self):
        pool=FakePool()
        solver.submit_admitted_map(pool,self.job,self.out,'runtime',self.config,{},active_maps=0)
        binding=pool.calls[0][-1]
        (self.out/binding['path']).write_text('{}')
        with self.assertRaises(ValueError):solver.worker_admission(self.job,self.out,binding)
        with patch.object(solver,'load_base',side_effect=AssertionError('native imported before admission')):
            with self.assertRaises(ValueError):solver.run_map(self.job,self.out,'runtime',self.config,{},None)

    def test_long_native_setup_cannot_reuse_dispatch_snapshot(self):
        reservation=self.reservation()
        self.advance(200)
        called=[]
        def sleep(seconds):
            self.assertFalse(called)
            self.advance(seconds)
            self.publish()
        def call():
            called.append(True);self.advance(3);return 'planned'
        with patch.object(solver.time,'sleep',side_effect=sleep):
            result,seconds,evidence=solver.run_admitted_stage(self.job,self.out,'planner',reservation,call,poll_s=10)
        self.assertEqual((result,seconds),('planned',3))
        self.assertEqual(evidence['attempts'],2)
        self.assertGreaterEqual(evidence['wait_seconds'],10)
        self.assertEqual(evidence['evidence']['raw']['t'],1210)

    def test_finisher_rechecks_after_planner_and_denied_wait_does_not_consume_budget(self):
        reservation=self.reservation();calls=[];recorded=[]
        def plan():
            calls.append('plan');self.advance(225);self.publish(load1=46);return 'cargo'
        cargo,plan_seconds,_=solver.run_admitted_stage(self.job,self.out,'planner',reservation,plan)
        self.assertEqual((cargo,plan_seconds),('cargo',225))
        def sleep(seconds):
            self.assertEqual(calls,['plan'])
            self.advance(seconds)
            if self.clock['wall']>=1245:self.publish()
        def finish():
            calls.append('finish');self.advance(7);return 'finished'
        def record(evidence):
            path=self.out/evidence['artifact']['path']
            self.assertEqual(solver.sha(path),evidence['artifact']['sha256'])
            recorded.append(evidence)
        with patch.object(solver.time,'sleep',side_effect=sleep):
            result,seconds,evidence=solver.run_admitted_stage(self.job,self.out,'finisher',reservation,finish,record=record,poll_s=10)
        self.assertEqual((result,seconds),('finished',7))
        self.assertEqual(calls,['plan','finish'])
        self.assertEqual(evidence['attempts'],3)
        self.assertEqual(evidence['wait_seconds'],20)
        self.assertEqual(len(recorded),1)
        self.assertEqual(self.clock['mono'],252)  # 225 work + 20 wait + 7 work.

    def test_expiry_during_persistence_rechecks_before_timed_callback(self):
        reservation=self.reservation();self.publish(t=880.001);records=[];calls=[]
        def record(evidence):
            records.append(evidence)
            if len(records)==1:self.advance(.01)
        def sleep(seconds):self.advance(seconds);self.publish()
        with patch.object(solver.time,'sleep',side_effect=sleep):
            _,_,evidence=solver.run_admitted_stage(self.job,self.out,'planner',reservation,
                lambda:calls.append('only-fresh'),record=record,poll_s=10)
        self.assertEqual(calls,['only-fresh'])
        self.assertEqual(evidence['attempts'],2)

    def test_completed_historical_map_reuse_does_not_rewrite_admission(self):
        summary=self.out/'maps'/self.job['map_id']/'summary.json'
        solver.write(summary,dict(complete=True,passed=False,original='V6'))
        original=summary.read_bytes()
        with patch.object(solver,'core_admission',side_effect=AssertionError('historical map admitted again')):
            result=solver.run_map(self.job,self.out,'runtime',self.config,{},None)
        self.assertTrue(result['complete'])
        self.assertEqual(summary.read_bytes(),original)


class FakePool:
    def __init__(self,before_start=None):
        self.calls=[];self.before_start=before_start

    def submit(self,function,*args):
        self.calls.append(args)
        if self.before_start:self.before_start(args)
        return object()


if __name__ == '__main__':
    unittest.main()
