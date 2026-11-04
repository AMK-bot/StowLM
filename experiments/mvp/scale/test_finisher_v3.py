"""Analytic/mocked V3 cache tests; no map/dataset inputs."""
from pathlib import Path
import json
import struct
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import finisher_v3


class LiquidCacheTests(unittest.TestCase):
    def solid(self, maximum=4.):
        from stowage.vessel.tanks import TankGeometry
        return TankGeometry(0.,maximum,-2.,2.,0.,3.)

    def test_geometry_identity_includes_actual_section_contours(self):
        from stowage.vessel.tanks import TankGeometry
        def shaped(left, right):
            polygon=[[left,0.],[right,0.],[right,3.],[left,3.]]
            return TankGeometry.from_params({'geometry':dict(
                shape='hull_sections',x_min_m=0.,x_max_m=4.,
                y_min_m=-2.,y_max_m=2.,z_min_m=0.,z_max_m=3.,
                x_m=[2.-2./np.sqrt(3),2.+2./np.sqrt(3)],
                integration_weights_m=[2.,2.],sections_yz_m=[[polygon],[polygon]])})
        first,second=shaped(-2.,0.),shaped(0.,2.)
        self.assertEqual(first.capacity_m3,second.capacity_m3)
        self.assertNotEqual(finisher_v3._geometry_identity(first),
                            finisher_v3._geometry_identity(second))

    def test_exact_analytic_roots_and_defensive_copies(self):
        from finisher import _ACTIVE
        from stowage.checker import stability_tank_transfer as module
        solid=self.solid()
        original=module._direct_liquid
        queries=[(20.,0.,0.,True),(20.,.02,-.01,False)]
        reference=[original(solid,v,h,t,polynomial=p) for v,h,t,p in queries]
        active=NS(diagnostics={})
        with finisher_v3.exact_liquid_reuse():
            token=_ACTIVE.set(active)
            try:
                for query,(expected,level) in zip(queries,reference):
                    v,h,t,p=query
                    result=module._direct_liquid(solid,v,h,t,polynomial=p)
                    np.testing.assert_array_equal(result[0],expected)
                    self.assertEqual(result[1],level)
                    result[0][:]=999.
                    again=module._direct_liquid(solid,v,h,t,polynomial=p)
                    np.testing.assert_array_equal(again[0],expected)
            finally:
                _ACTIVE.reset(token)
        self.assertIs(module._direct_liquid,original)

    def test_actual_section_roots_bit_exact_at_two_angles_and_adjacent_volumes(self):
        from finisher import _ACTIVE
        from stowage.checker import stability_tank_transfer as module
        from stowage.vessel.hull_tanks import SectionTankGeometry
        from stowage.vessel.tanks import TankGeometry
        # Longitudinally changing sections exercise the actual shaped-tank
        # polynomial and direct-integration paths rather than a box adapter.
        first=[[-2.,0.],[2.,0.],[2.,3.],[-2.,3.]]
        second=[[-2.,0.],[2.,0.],[1.5,3.],[-1.5,3.]]
        raw={'geometry':dict(shape='hull_sections',x_min_m=0.,x_max_m=4.,
            y_min_m=-2.,y_max_m=2.,z_min_m=0.,z_max_m=3.,
            x_m=[2.-2./np.sqrt(3),2.+2./np.sqrt(3)],
            integration_weights_m=[2.,2.],sections_yz_m=[[first],[second]])}
        solid=TankGeometry.from_params(raw)
        self.assertIs(type(solid),SectionTankGeometry)
        self.assertFalse(solid._prismatic)
        volume=.37*solid.capacity_m3
        queries=[(v,h,t,p) for h,t in ((.03,-.01),(-.02,.015))
                 for p in (True,False)
                 for v in (volume,float(np.nextafter(volume,np.inf)))]
        original=module._direct_liquid
        reference=[original(solid,v,h,t,polynomial=p) for v,h,t,p in queries]
        active=NS(diagnostics={})
        calls=[]
        def counted(*args,**kwargs):
            calls.append((args,kwargs))
            return original(*args,**kwargs)
        with patch.object(module,'_direct_liquid',counted):
            with finisher_v3.exact_liquid_reuse():
                token=_ACTIVE.set(active)
                try:
                    for (v,h,t,p),(expected,level) in zip(queries,reference):
                        value=module._direct_liquid(solid,v,h,t,polynomial=p)
                        self.assertEqual(value[0].dtype,expected.dtype)
                        self.assertEqual(value[0].tobytes(),expected.tobytes())
                        self.assertEqual(struct.pack('!d',value[1]),struct.pack('!d',level))
                        value[0][:]=999.
                        again=module._direct_liquid(solid,v,h,t,polynomial=p)
                        self.assertEqual(again[0].tobytes(),expected.tobytes())
                        self.assertEqual(struct.pack('!d',again[1]),struct.pack('!d',level))
                    self.assertEqual(len(calls),len(queries))
                    self.assertEqual(active.diagnostics['scale_exact_reuse']['liquid_tilted_hits'],
                                     len(queries))
                finally:
                    _ACTIVE.reset(token)
            self.assertIs(module._direct_liquid,counted)

    def test_unknown_provider_bypasses_cache(self):
        from finisher import _ACTIVE
        from stowage.checker import stability_tank_transfer as module
        provider=NS(capacity_m3=100.)
        calls=[]
        def fake(*args,**kwargs):
            calls.append(1)
            return np.array([1.,2.,3.]),4.
        active=NS(diagnostics={})
        with patch.object(module,'_direct_liquid',fake):
            with finisher_v3.exact_liquid_reuse():
                token=_ACTIVE.set(active)
                try:
                    for _ in range(2):
                        result=module._direct_liquid(provider,20.,.01,.02)
                        np.testing.assert_array_equal(result[0],[1.,2.,3.])
                    self.assertEqual(len(calls),2)
                    self.assertNotIn('scale_exact_reuse',active.diagnostics)
                finally:
                    _ACTIVE.reset(token)

    def test_helper_restores_on_base_exception(self):
        from stowage.checker import stability_tank_transfer as module
        original=module._direct_liquid
        with self.assertRaises(KeyboardInterrupt):
            with finisher_v3.exact_liquid_reuse():
                raise KeyboardInterrupt('analytic interruption')
        self.assertIs(module._direct_liquid,original)

    def test_complete_checker_bypasses_cache_and_all_inputs_participate(self):
        from finisher import _ACTIVE
        from stowage.checker import stability_tank_transfer as module
        calls=[]
        def fake(solid,v,h,t,*,polynomial=True):
            calls.append((solid,v,h,t,polynomial))
            return np.array([v,h,t]),float(polynomial)
        solid=self.solid()
        active=NS(diagnostics={})
        with patch.object(module,'_direct_liquid',fake):
            with finisher_v3.exact_liquid_reuse():
                # Full checking is outside search's ACTIVE scope: recompute.
                module._direct_liquid(solid,20.,0.,0.)
                module._direct_liquid(solid,20.,0.,0.)
                self.assertEqual(len(calls),2)
                token=_ACTIVE.set(active)
                try:
                    module._direct_liquid(solid,20.,0.,0.)
                    module._direct_liquid(solid,20.,0.,0.)
                    self.assertEqual(len(calls),3)
                    for args in [(solid,20.000000000000004,0.,0.),
                                 (solid,20.,1e-18,0.),(solid,20.,0.,1e-18),
                                 (self.solid(5.),20.,0.,0.)]:
                        module._direct_liquid(*args)
                    module._direct_liquid(solid,20.,0.,0.,polynomial=False)
                    self.assertEqual(len(calls),8)
                finally:
                    _ACTIVE.reset(token)
            self.assertIs(module._direct_liquid,fake)

    def test_failed_results_uncached_banks_bounded_and_exact_crosscandidate_reuse(self):
        from finisher import _ACTIVE
        from stowage.checker import stability_tank_transfer as module
        calls=[]
        def fake(solid,v,h,t,*,polynomial=True):
            calls.append(v)
            if v==9.:
                raise ValueError('analytic failure')
            return np.array([v,h,t]),v
        solid=self.solid()
        active=NS(diagnostics={})
        with patch.object(module,'_direct_liquid',fake), \
                patch.object(finisher_v3,'ENTRIES_PER_BANK',2):
            with finisher_v3.exact_liquid_reuse():
                token=_ACTIVE.set(active)
                try:
                    for _ in range(2):
                        with self.assertRaises(ValueError):
                            module._direct_liquid(solid,9.,0.,0.)
                    self.assertEqual(calls.count(9.),2)
                    for value in (1.,2.,3.,1.):
                        module._direct_liquid(solid,value,0.,0.)
                    self.assertEqual(calls.count(1.),2)
                    other=_ACTIVE.set(NS(diagnostics={}))
                    try:
                        module._direct_liquid(solid,1.,0.,0.)
                        self.assertEqual(calls.count(1.),2)
                        # New candidate identity changes no kernel inputs;
                        # even an adjacent float volume must still miss.
                        adjacent=float(np.nextafter(1.,np.inf))
                        module._direct_liquid(solid,adjacent,0.,0.)
                        self.assertEqual(calls.count(adjacent),1)
                    finally:
                        _ACTIVE.reset(other)
                    self.assertEqual(calls.count(1.),2)
                finally:
                    _ACTIVE.reset(token)

    def test_no_acceptance_overrides_and_restoration(self):
        from stowage import checker
        from stowage.solver import ballast_v4_dispatch,ballast_v4_evaluator
        original_check=checker.check
        original_evaluate=ballast_v4_evaluator.Evaluator.evaluate
        original_worker=ballast_v4_dispatch._worker
        with finisher_v3.exact_liquid_reuse():
            self.assertIs(checker.check,original_check)
            self.assertIs(ballast_v4_evaluator.Evaluator.evaluate,original_evaluate)
        restore=finisher_v3.install()
        self.assertIs(ballast_v4_dispatch._worker,finisher_v3.worker)
        restore();restore()
        self.assertIs(ballast_v4_dispatch._worker,original_worker)


class _Snapshot:
    def __init__(self, **values):
        self.values=values

    def model_dump_json(self, **kwargs):
        return json.dumps(self.values,sort_keys=True)


class LoadingCacheTests(unittest.TestCase):
    def test_reuses_across_active_workspaces_with_exact_keys_and_copies(self):
        from finisher import _ACTIVE
        from stowage.checker import visibility
        instance=_Snapshot(geometry={'beam':10.})
        ship=_Snapshot(tank_masses_kg={'t':1.})
        geometry=object()
        reference=visibility.LoadingCondition(1.,2.,3.,4.,5.,6.,7.,8.,geometry)
        calls=[]
        def compute(instance,ship):
            calls.append(1)
            return reference
        with patch.object(visibility,'_loading_condition',compute):
            with finisher_v3.exact_loading_reuse():
                first_active=NS(diagnostics={})
                token=_ACTIVE.set(first_active)
                try:
                    first=visibility._loading_condition(instance,ship)
                    object.__setattr__(first,'mass_kg',999.)
                finally:
                    _ACTIVE.reset(token)
                next_active=NS(diagnostics={})
                token=_ACTIVE.set(next_active)
                try:
                    second=visibility._loading_condition(instance,ship)
                    self.assertEqual(second.mass_kg,1.)
                    self.assertIs(second.geometry,geometry)
                    self.assertIsNot(second,reference)
                    self.assertEqual(len(calls),1)
                    self.assertEqual(next_active.diagnostics['scale_exact_reuse']
                                     ['visibility_loading_worker_hits'],1)
                    ship.values['tank_masses_kg']['t']=np.nextafter(1.,2.).item()
                    visibility._loading_condition(instance,ship)
                    instance.values['geometry']['beam']=11.
                    visibility._loading_condition(instance,ship)
                    self.assertEqual(len(calls),3)
                finally:
                    _ACTIVE.reset(token)
                # Independent complete-checker calls always bypass this cache.
                visibility._loading_condition(instance,ship)
                visibility._loading_condition(instance,ship)
                self.assertEqual(len(calls),5)
            self.assertIs(visibility._loading_condition,compute)

    def test_outer_cache_survives_inner_three_workspace_eviction(self):
        from finisher import exact_part_reuse
        from stowage.checker import visibility
        from stowage.solver.ballast_v4_evaluator import Evaluator
        instance=_Snapshot(geometry='analytic')
        ship=_Snapshot(tank_masses_kg={'t':1.})
        evaluator=NS(diagnostics={})
        reference=visibility.LoadingCondition(1.,2.,3.,4.,5.,6.,7.,8.,object())
        calls=[]
        def compute(instance,ship):
            calls.append(1)
            return reference
        def part(evaluator,identity,phase,instance,ship,name):
            return visibility._loading_condition(instance,ship)
        with patch.object(visibility,'_loading_condition',compute), \
                patch.object(Evaluator,'_part',part):
            with finisher_v3.exact_loading_reuse():
                with exact_part_reuse():
                    for identity in (b'centre',b't1',b't2',b't3',b't4',b'centre'):
                        result=Evaluator._part(evaluator,identity,'departure',instance,ship,'lashing')
                        self.assertEqual(result,reference)
            self.assertEqual(len(calls),1)
            self.assertEqual(evaluator.diagnostics['scale_exact_reuse']
                             ['visibility_loading_worker_hits'],5)

    def test_eviction_failed_calls_and_base_exception_restoration(self):
        from finisher import _ACTIVE
        from stowage.checker import visibility
        original=visibility._loading_condition
        instance=_Snapshot(geometry='analytic')
        ship=_Snapshot(tank_masses_kg={'t':1.})
        reference=visibility.LoadingCondition(1.,2.,3.,4.,5.,6.,7.,8.,object())
        calls=[]
        def compute(instance,ship):
            calls.append(ship.values['tank_masses_kg']['t'])
            if calls[-1]==9.:
                raise ValueError('analytic failure')
            return reference
        with patch.object(visibility,'_loading_condition',compute), \
                patch.object(finisher_v3,'LOADING_LIMIT',2):
            with self.assertRaises(KeyboardInterrupt):
                with finisher_v3.exact_loading_reuse():
                    token=_ACTIVE.set(NS(diagnostics={}))
                    try:
                        for value in (1.,2.,3.,1.):
                            ship.values['tank_masses_kg']['t']=value
                            visibility._loading_condition(instance,ship)
                        self.assertEqual(calls.count(1.),2)
                        ship.values['tank_masses_kg']['t']=9.
                        for _ in range(2):
                            with self.assertRaises(ValueError):
                                visibility._loading_condition(instance,ship)
                        self.assertEqual(calls.count(9.),2)
                        raise KeyboardInterrupt('analytic interruption')
                    finally:
                        _ACTIVE.reset(token)
            self.assertIs(visibility._loading_condition,compute)
        self.assertIs(visibility._loading_condition,original)


if __name__=='__main__':
    unittest.main()
