"""Runner integrity tests; never execute a planner or a corpus draw."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('fillsweep_runner', Path(__file__).with_name('run.py'))
run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run)


class RunnerIntegrity(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.here = self.root / 'experiments/mvp/strategy/fillsweep'
        self.here.mkdir(parents=True)
        self.patches = [patch.object(run, 'ROOT', self.root), patch.object(run, 'HERE', self.here)]
        for value in self.patches:
            value.start()

    def tearDown(self):
        for value in reversed(self.patches):
            value.stop()
        self.directory.cleanup()

    def manifest(self, source=None, status='accepted', split='train'):
        source = source or self.here / 'maps/train/a.json'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('{}')
        row = dict(map_id='a', input=str(source.relative_to(self.root)),
                   sha256=run.digest(source), generation_status=status, fill_target=40)
        target = self.here / 'cohort.json'
        target.write_text(json.dumps(dict(split=split, maps=[row])))
        return target, row

    def test_manifest_preserves_cell_design(self):
        path, _ = self.manifest()
        _, jobs = run.manifest_jobs(path)
        self.assertEqual(jobs[0]['fill_target'], 40)
        self.assertEqual(jobs[0]['input_sha256'], run.digest(self.root / jobs[0]['input']))

    def test_rejects_evaluation_paths_and_split(self):
        for partition in ('val', 'test', 'showcase'):
            with self.subTest(partition=partition):
                path, _ = self.manifest(self.here / f'maps/train/{partition}/a.json')
                with self.assertRaises(ValueError):
                    run.manifest_jobs(path)
        path, _ = self.manifest(split='val')
        with self.assertRaises(ValueError):
            run.manifest_jobs(path)

    def test_rejects_digest_changes(self):
        path, row = self.manifest()
        (self.root / row['input']).write_text('{"changed": true}')
        with self.assertRaises(ValueError):
            run.manifest_jobs(path)

    def test_generation_failure_has_no_solver_job(self):
        path, row = self.manifest()
        data = run.read_json(path)
        data['maps'].append(dict(map_id='failed', generation_status='failed'))
        path.write_text(json.dumps(data))
        metadata, jobs = run.manifest_jobs(path)
        self.assertEqual(len(metadata['maps']), 2)
        self.assertEqual(len(jobs), 1)

    def test_immutable_runtime_survives_working_tree_change(self):
        source = self.root / 'src/stowage/fixture.py'
        source.parent.mkdir(parents=True)
        source.write_text('frozen = True\n')
        sources = {'src/stowage/fixture.py': run.digest(source)}
        asset = self.root / 'data/raw/kcs/kcs.fix'
        asset.parent.mkdir(parents=True)
        asset.write_text('external geometry')
        assets = {'data/raw/kcs/kcs.fix': run.digest(asset)}
        with patch.object(run, 'runtime_sources', return_value=sources), patch.object(run, 'runtime_data_sources', return_value=assets):
            runtime, original = run.freeze_runtime(self.here / 'runs/main')
            source.write_text('frozen = False\n')
            _, resumed = run.freeze_runtime(self.here / 'runs/main')
            self.assertEqual(original, resumed)
            (runtime / 'src/stowage/fixture.py').write_text('corrupted\n')
            with self.assertRaises(RuntimeError):
                run.freeze_runtime(self.here / 'runs/main')

    def test_missing_external_hull_data_fails_before_admission(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing required hull dependency'):
            run.runtime_data_sources()

    def test_external_data_drift_rejected(self):
        runtime = self.here / 'runs/main/runtime'
        runtime.mkdir(parents=True)
        run.atomic_json(runtime / 'snapshot.json', {'original': True})
        asset = self.root / 'data/raw/kcs/kcs.fix'
        asset.parent.mkdir(parents=True)
        asset.write_text('geometry')
        with patch.object(run, 'runtime_data_sources', return_value={'data/raw/kcs/kcs.fix': run.digest(asset)}):
            run.freeze_data(runtime, {'runner_sha256': run.digest(run.__file__)})
            (runtime / 'data/raw/kcs/kcs.fix').write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'Frozen external dependency changed'):
                run.freeze_data(runtime, {'runner_sha256': run.digest(run.__file__)})

    def test_live_admission_pause_and_load(self):
        control = self.here / 'control.json'
        with patch.object(run.os, 'getloadavg', return_value=(0, 0, 0)):
            self.assertEqual(run.admission_limit(control, 3, 10), 3)
            control.write_text('{"active_map_limit": 0}')
            self.assertEqual(run.admission_limit(control, 3, 10), 0)
        control.write_text('{"active_map_limit": 3}')
        with patch.object(run.os, 'getloadavg', return_value=(20, 0, 0)):
            self.assertEqual(run.admission_limit(control, 3, 10), 0)

    def test_breadth_schedule_preserves_all_grid_cells(self):
        rows = [dict(map_id=f'{ship}-{fill}-{mass}-{rep}', ship=ship,
                     fill_target=fill, mass_level=mass, seed=192200000 + 100*index + rep)
                for index, ship in enumerate(run.SHIP_ORDER) for rep in range(3)
                for fill in (40, 50, 60, 70, 80) for mass in run.MASS_ORDER]
        ordered = sorted(reversed(rows), key=run.scheduling_key)
        self.assertEqual(len(ordered), 90)
        self.assertEqual({r['map_id'] for r in ordered}, {r['map_id'] for r in rows})
        self.assertEqual([r['ship'] for r in ordered[:3]], list(run.SHIP_ORDER))
        self.assertTrue(all(r['fill_target'] == 40 and r['mass_level'] == 'low' for r in ordered[:3]))
        self.assertTrue(all(r['fill_target'] == 60 for r in ordered[6:12]))
        out = self.here / 'runs/main'
        first = run.freeze_scheduling(out, rows)
        self.assertEqual(first, run.freeze_scheduling(out, list(reversed(rows))))
        rows.pop()
        with self.assertRaises(RuntimeError):
            run.freeze_scheduling(out, rows)

    def test_only_harness_exceptions_are_campaign_errors(self):
        scientific = dict(default=dict(status='timeout', raw=dict(passed=False, finalize_status='not_reached')),
                          rule=dict(status='not_found', compact=dict(passed=False, finalize_status='timeout')))
        self.assertEqual(run.harness_errors(scientific), [])
        scientific['default']['status'] = 'exception'
        scientific['default']['message'] = 'ValueError: infrastructure'
        scientific['rule']['compact']['error'] = 'RuntimeError: checker infrastructure'
        errors = run.harness_errors(scientific)
        self.assertEqual(len(errors), 2)
        self.assertEqual({row['stage'] for row in errors}, {'planner', 'certification'})

    def test_completion_error_is_durable_before_nonzero_exit(self):
        out = self.here / 'runs/main'
        errors = [dict(map_id='a', error='harness exception')]
        with self.assertRaisesRegex(RuntimeError, 'harness failure'):
            run.commit_completion(out, 1, errors)
        self.assertEqual(run.read_json(out / 'completion.json')['errors'], errors)


if __name__ == '__main__':
    unittest.main()
