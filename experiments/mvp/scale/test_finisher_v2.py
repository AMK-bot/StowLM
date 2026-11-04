"""V2 hook/telemetry tests use mocks and temporary paths, never map inputs."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finisher_v2


class FinisherV2Tests(unittest.TestCase):
    def test_order_preserves_all_original_callbacks_and_checker_acceptance(self):
        from stowage import checker
        from stowage.solver import ballast_v4_evaluator as module
        original = module.checkers()
        original_check = checker.check
        original_evaluate = module.Evaluator.evaluate
        with finisher_v2.search_hooks():
            reordered = module.checkers()
            self.assertEqual(tuple(reordered), finisher_v2.ORDER)
            self.assertEqual(reordered, original)
            self.assertIs(checker.check, original_check)
            self.assertIs(module.Evaluator.evaluate, original_evaluate)
        self.assertEqual(module.checkers(), original)

    def test_hooks_restore_on_base_exception(self):
        from stowage.solver import ballast_v4_evaluator as module
        before = module.checkers, module.Evaluator._part, module.Evaluator.checkpoint
        with self.assertRaises(KeyboardInterrupt):
            with finisher_v2.search_hooks():
                raise KeyboardInterrupt('analytic interruption')
        self.assertEqual((module.checkers, module.Evaluator._part,
                          module.Evaluator.checkpoint), before)

    def test_atomic_bounded_progress_and_periodic_checkpoint(self):
        from stowage.solver import ballast_v4_evaluator as module
        evaluator = NS(diagnostics={'candidate_count': 4, 'evaluations': 9,
                                    'seed_outcomes': ['large']*10000})
        def part(evaluator, *args):
            evaluator.diagnostics['physics_part_calls'] = 1
            return 'original result'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'worker.json'
            with patch.object(module.Evaluator, '_part', part), \
                    patch.object(module.Evaluator, 'checkpoint', lambda evaluator: None):
                with finisher_v2.search_hooks(path):
                    module.Evaluator.checkpoint(evaluator)
                    first = json.loads(path.read_text())
                    self.assertEqual(first['event'], 'checkpoint')
                    module.Evaluator.checkpoint(evaluator)
                    self.assertEqual(json.loads(path.read_text())['serial'], first['serial'])
                    result = module.Evaluator._part(evaluator, b'one-candidate',
                                                    'departure', None, None, 'visibility')
            self.assertEqual(result, 'original result')
            payload = json.loads(path.read_text())
            self.assertEqual(payload['event'], 'part_done')
            self.assertEqual(payload['diagnostics']['candidate_count'], 4)
            self.assertEqual(payload['stage'], 'physics:departure/visibility')
            self.assertNotIn('seed_outcomes', payload['diagnostics'])
            self.assertLess(path.stat().st_size, 8192)
            self.assertEqual(list(Path(folder).glob('*.tmp-*')), [])

    def test_error_telemetry_reraises_original_exception(self):
        from stowage.solver import ballast_v4_evaluator as module
        error = ValueError('original numerical failure')
        def fail(*args):
            raise error
        evaluator = NS(diagnostics={})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'worker.json'
            with patch.object(module.Evaluator, '_part', fail):
                with finisher_v2.search_hooks(path):
                    with self.assertRaises(ValueError) as result:
                        module.Evaluator._part(evaluator,b'x','arrival',None,None,'lashing')
            self.assertIs(result.exception, error)
            self.assertEqual(json.loads(path.read_text())['event'], 'part_error')

    def test_dispatch_installs_and_restores_owned_worker_only(self):
        from stowage.solver import ballast_v4_dispatch
        original = ballast_v4_dispatch._worker
        original_finish = ballast_v4_dispatch.finish
        restore = finisher_v2.install()
        try:
            self.assertIs(ballast_v4_dispatch._worker, finisher_v2.worker)
            self.assertIs(ballast_v4_dispatch.finish, original_finish)
        finally:
            restore()
        restore()
        self.assertIs(ballast_v4_dispatch._worker, original)


if __name__ == '__main__':
    unittest.main()
