"""Analytic numerical/cache tests; reads no map or dataset input."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import finisher


class Snapshot:
    def __init__(self, **values):
        self.values = values

    def model_dump_json(self, *, exclude=None, **kwargs):
        return json.dumps({k: v for k, v in self.values.items()
                           if exclude is None or k not in exclude}, sort_keys=True)


class CacheTests(unittest.TestCase):
    def test_exact_keys_failure_not_cached_and_bound(self):
        stats = {}
        cache = finisher._CandidateCache(stats)
        calls = []
        def compute():
            calls.append(1)
            return object()
        self.assertIs(cache.get('x', 1., compute), cache.get('x', 1., compute))
        cache.get('x', 1.0000000000000002, compute)
        self.assertEqual(len(calls), 2)
        def fail():
            raise ValueError('not a result')
        with self.assertRaises(ValueError):
            cache.get('x', 3., fail)
        self.assertNotIn(3., cache.values['x'])
        for index in range(5):
            cache.get('bounded', index, compute, limit=2)
        self.assertEqual(len(cache.values['bounded']), 2)

    def test_full_content_keys_detect_mutation(self):
        instance = Snapshot(rule=1, cargo=[1])
        state = Snapshot(placements=[1], tank_masses_kg={'t': 3.})
        old_loading = finisher._state_key(instance, state)
        old_cargo = finisher._state_key(instance, state, cargo_only=True)
        state.values['tank_masses_kg']['t'] = 4.
        self.assertNotEqual(old_loading, finisher._state_key(instance, state))
        self.assertEqual(old_cargo, finisher._state_key(instance, state, cargo_only=True))
        instance.values['cargo'].append(2)
        self.assertNotEqual(old_cargo, finisher._state_key(instance, state, cargo_only=True))

    def test_part_only_reuse_original_full_checker_path_and_restoration(self):
        from stowage.checker import visibility
        from stowage.solver.ballast_v4_evaluator import Evaluator
        from stowage.vessel.tanks import TankGeometry
        instance = Snapshot(vessel='analytic')
        state = Snapshot(placements=[], tank_masses_kg={})
        evaluator = Snapshot()
        evaluator.diagnostics = {}
        calls = []
        def loading(instance, state):
            calls.append('loading')
            return ('unchanged', len(calls))
        def probe(evaluator, identity, phase, instance, state, name):
            first = visibility._loading_condition(instance, state)
            second = visibility._loading_condition(instance, state)
            self.assertEqual(first, second)
            return first
        original_from = TankGeometry.__dict__['from_params']
        with patch.object(visibility, '_loading_condition', loading), \
                patch.object(Evaluator, '_part', probe):
            with finisher.exact_part_reuse():
                Evaluator._part(evaluator, b'a', 'departure', instance, state, 'visibility')
                self.assertEqual(calls, ['loading'])
                # A full checker invokes helpers outside _part: both recompute.
                visibility._loading_condition(instance, state)
                visibility._loading_condition(instance, state)
                self.assertEqual(len(calls), 3)
                state.values['tank_masses_kg']['changed'] = 1.
                Evaluator._part(evaluator, b'a', 'departure', instance, state, 'lashing')
                self.assertEqual(len(calls), 4)
                # Different candidates cannot share even identical snapshots.
                Evaluator._part(evaluator, b'b', 'departure', instance, state, 'visibility')
                self.assertEqual(len(calls), 5)
            self.assertIs(visibility._loading_condition, loading)
            self.assertIs(Evaluator._part, probe)
        self.assertIs(TankGeometry.__dict__['from_params'], original_from)
        self.assertIsNone(finisher._ACTIVE.get())

    def test_real_analytic_geometry_and_liquid_exact_equality(self):
        from stowage.vessel.tanks import TankGeometry
        from stowage.solver.ballast_v4_evaluator import Evaluator
        raw = dict(x_min_m=0., x_max_m=4., y_min_m=-2., y_max_m=2.,
                   z_min_m=0., z_max_m=3.)
        reference = TankGeometry.from_params(raw)
        reference_liquid = reference.liquid(20., .01, -.005)
        geometries = []
        def probe(evaluator, *args):
            geometries.extend([TankGeometry.from_params(raw), TankGeometry.from_params(raw)])
        evaluator = Snapshot()
        evaluator.diagnostics = {}
        with patch.object(Evaluator, '_part', probe):
            with finisher.exact_part_reuse():
                Evaluator._part(evaluator, b'a', None, None, None, None)
                self.assertIs(geometries[0], geometries[1])
                # Ordinary full-checker path reconstructs, bypassing the cache.
                self.assertIsNot(TankGeometry.from_params(raw), geometries[0])
        self.assertEqual(reference, geometries[0])
        self.assertEqual(reference_liquid, geometries[0].liquid(20., .01, -.005))


if __name__ == '__main__':
    unittest.main()
