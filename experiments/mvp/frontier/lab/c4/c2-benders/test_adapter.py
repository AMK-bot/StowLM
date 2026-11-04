"""Cheap fake-provider tests; no numerical library, map solve or checker call."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('_test_c4_adapter', HERE/'method.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
audit_spec = importlib.util.spec_from_file_location('_test_c4_audit', HERE/'audit.py')
audit = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(audit)


class Plan:
    def __init__(self, label):
        self.label = label

    def model_dump(self, **kwargs):
        return {'label': self.label}


def record(number, kind='physics_low_kg'):
    scored = SimpleNamespace(plan=Plan(str(number)), objective=100.+number,
        actions=(SimpleNamespace(box='a', slot=f'a{number}'), SimpleNamespace(box='b', slot=f'b{number}')))
    return dict(scored=scored, candidate_id=f'cold-{number}',
                metadata=dict(complete_replay_legal=True, kind=kind, cargo_kg_m=10.+number))


class AdapterTests(unittest.TestCase):
    def setUp(self):
        directory = HERE/'results/mock-tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.clock = 0.
        self.instance = SimpleNamespace(id='fake', load_list=['a', 'b'])

    def tools(self, records, finisher):
        def cold(instance, seed, budget_s, context):
            self.assertEqual(set(context), {'problem'})
            self.clock += budget_s
            return records, {'complete_stows': len(records)}
        modules = {'cold': SimpleNamespace(cold_candidates=cold),
                   'ballast': SimpleNamespace(finish_candidate=finisher)}
        stack = ExitStack()
        stack.enter_context(patch.object(adapter, '_load', side_effect=modules.__getitem__))
        stack.enter_context(patch.object(adapter, '_sources', return_value={}))
        stack.enter_context(patch.object(adapter, '_screen', return_value={'proof': None}))
        stack.enter_context(patch.object(adapter, '_detail_directory', return_value=self.directory))
        stack.enter_context(patch.object(adapter, 'monotonic', side_effect=lambda: self.clock))
        return stack

    def test_cold_context_and_focused_shared_budget(self):
        supplied = [record(0, 'plain'), record(1), record(2, 'port_cover')]
        budgets = []

        def finish(instance, plan, seed, budget_s, out):
            budgets.append((plan.label, budget_s))
            self.clock += budget_s
            return plan, {'full_checker_passed': False,
                          'binding_margins': {'arrival/area': -.1}, 'evaluations': 7}

        context = dict(problem=object(), stored={'forbidden': True},
                       stored_plan={'ballast': 'forbidden'}, baseline={'forbidden': True})
        with self.tools(supplied, finish):
            actions = adapter.propose(self.instance, seed=3, budget_s=120., context=context)
            candidate, metadata = adapter.finish(self.instance, supplied[0]['scored'].plan,
                                                 finalize_s=1080., context=context)
        self.assertEqual(len(actions), 3)
        self.assertEqual([name for name, _ in budgets], ['1', '2'])
        self.assertGreaterEqual(budgets[0][1], 600.)
        self.assertLessEqual(self.clock, adapter.MAP_BUDGET_S-adapter.CHECK_RESERVE_S)
        self.assertEqual(metadata['status'], 'unknown')
        self.assertEqual(context['method_metadata']['cold_pipeline']['finish_attempts'][0]['binding_rules'],
                         {'arrival/area': -.1})
        self.assertEqual(candidate.label, '2')

    def test_securing_proof_skips_ballast_without_claiming_pass(self):
        called = []
        def finish(*args, **kwargs):
            called.append(True)
            raise AssertionError('proved stow should not enter numerical ballast search')
        context = {'problem': object()}
        with self.tools([record(0)], finish), patch.object(adapter, '_screen',
                return_value={'proof': 'fixed-stow securing contradiction'}):
            adapter.propose(self.instance, seed=0, budget_s=1., context=context)
            candidate, meta = adapter.finish(self.instance, Plan('0'), context=context)
        self.assertIsNone(candidate)
        self.assertFalse(called)
        self.assertEqual(meta['cold_pipeline']['certified_fixed_stow_rejections'], 1)
        self.assertEqual(meta['cold_pipeline']['full_checker_passes_claimed_by_adapter'], 0)

    def test_finisher_requires_current_cold_context(self):
        with self.assertRaisesRegex(ValueError, 'fresh propose context'):
            adapter.finish(self.instance, Plan('stored'), context={'stored_plan': {}})

    def test_legacy_and_compact_stages_share_one_constructor_budget(self):
        self.instance.onboard = SimpleNamespace(placements=[])
        legacy = record(0, 'legacy_native_physics')
        legacy['candidate_id'] = 'legacy-000'
        later = record(1, 'mixed_physics')
        def old(instance, seed, budget_s, context):
            self.assertEqual(set(context), {'problem'})
            self.assertEqual(budget_s, 210.)
            self.clock += 50.
            return [legacy], {'variants': [], 'complete_stows': 1}
        def compact(instance, seed, budget_s, context):
            self.assertEqual(budget_s, 250.)
            self.assertIs(context['cold_live_scored'], legacy['scored'])
            self.assertTrue(context['cold_compact_after_legacy'])
            self.assertNotIn('stored', context)
            self.clock += budget_s
            return [later], {'variants': [], 'complete_stows': 1}
        modules = {'legacy_cold': SimpleNamespace(legacy_candidates=old),
                   'cold': SimpleNamespace(cold_candidates=compact)}
        context = {'problem': object(), 'stored': {'forbidden': True}}
        with self.tools([], None), patch.object(adapter, '_load', side_effect=modules.__getitem__):
            actions = adapter.propose(self.instance, seed=0, budget_s=300., context=context)
        self.assertEqual(len(actions), 2)
        self.assertEqual(self.clock, 300.)
        self.assertEqual(context['method_metadata']['cold_pipeline']['distinct_complete_assignments'], 2)

    def test_finisher_success_still_requires_runner_check(self):
        def finish(instance, plan, seed, budget_s, out):
            return plan, {'full_checker_passed': True}
        context = {'problem': object()}
        with self.tools([record(0)], finish):
            adapter.propose(self.instance, seed=0, budget_s=1., context=context)
            candidate, meta = adapter.finish(self.instance, Plan('0'), context=context)
        self.assertIsNotNone(candidate)
        self.assertTrue(meta['independent_runner_check_required'])
        self.assertEqual(meta['cold_pipeline']['full_checker_passes_claimed_by_adapter'], 1)

    def test_audit_rejects_wrong_input_and_never_imputes_failed_lifts(self):
        expected = {'instance_id': 'fake', 'input': 'approved', 'input_sha256': 'right'}
        row = dict(input='approved', input_sha256='wrong', complete=True, passed=True,
                   plan={'label': 'x'}, lifts=12., status='passed',
                   method_metadata={'cold_pipeline': dict(cold=True, stored_context_used=False,
                       runtime_historical_artifacts_read=False, complete_replay_legal_stows=1)})
        result = audit.inspect_row(row, expected)
        self.assertTrue(result['raw_runner_pass'])
        self.assertFalse(result['full_checker_pass'])
        self.assertIsNone(result['lifts'])
        self.assertIn('input_hash_mismatch', result['issues'])
        row.update(passed=False, input_sha256='right')
        self.assertIsNone(audit.inspect_row(row, expected)['lifts'])


if __name__ == '__main__':
    unittest.main()
