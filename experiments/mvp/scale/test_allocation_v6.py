"""Native plan protocol and exact AST delta; no map/dataset inputs."""
import ast
from contextlib import ExitStack
import inspect
import math
from pathlib import Path
import sys
import textwrap
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import allocation_v6


class AllocationTests(unittest.TestCase):
    def test_only_target_deadline_constant_changes(self):
        from stowage.solver.twostep import TwoStepPlanner
        source=inspect.getsource(inspect.unwrap(TwoStepPlanner.plan))
        original=ast.parse(textwrap.dedent(source))
        modified=allocation_v6.rewrite(source)
        count=0
        for node in ast.walk(modified):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='_preplan':
                for keyword in node.keywords:
                    if keyword.arg=='deadline':
                        value=keyword.value.right.right
                        self.assertEqual(value.value,.75)
                        value.value=.5
                        count+=1
        self.assertEqual(count,1)
        self.assertEqual(ast.dump(original,include_attributes=False),
                         ast.dump(modified,include_attributes=False))
        with self.assertRaises(ValueError):
            allocation_v6.rewrite('def changed():\n    return _preplan(deadline=1)\n')

    def native(self,*,limit=300.,prestow_status='ok',error=None):
        from stowage.solver import twostep,twostep_terminal
        calls=[]
        provenance=NS()
        provenance.merge=lambda value:provenance
        instance=NS(provenance=provenance)
        prestow=NS(status=prestow_status,message='native outcome',slots=(),search_metrics={})
        def preplan(instance,**kwargs):
            calls.append(('carrier',kwargs))
            if error:
                raise error
            return prestow
        def terminal(instance,prestow,**kwargs):
            calls.append(('terminal',kwargs))
            return None,{'native_terminal':True},'ok',None
        with ExitStack() as stack:
            # Live module globals are intentionally patched AFTER cloning.
            original=twostep.TwoStepPlanner.plan
            handle=allocation_v6.install()
            try:
                for name,value in dict(monotonic=lambda:100.,perf_counter=lambda:200.,
                    _use_fast_prestow=lambda instance:False,_preplan=preplan,
                    current_provenance=lambda **kwargs:provenance,
                    PlannerResult=lambda **kwargs:NS(**kwargs)).items():
                    stack.enter_context(patch.object(twostep,name,value))
                stack.enter_context(patch.object(twostep_terminal,'terminal_plan',terminal))
                planner=twostep.TwoStepPlanner()
                result=planner.plan(instance,seed=7,time_limit_s=limit)
            finally:
                handle.restore()
            self.assertIs(twostep.TwoStepPlanner.plan,original)
        return result,calls

    def test_300_total_keeps_terminal_deadline_and_225_carrier_slice(self):
        result,calls=self.native()
        self.assertEqual([name for name,_ in calls],['carrier','terminal'])
        self.assertEqual(calls[0][1]['deadline'],325.)
        self.assertEqual(calls[1][1]['deadline'],400.)
        self.assertEqual(calls[0][1]['seed'],7)
        self.assertEqual(result.metrics['time_budget_s'],300.)
        self.assertEqual(result.metrics['budget_overrun_s'],0.)
        self.assertTrue(result.metrics['native_terminal'])

    def test_default_and_zero_budgets_keep_native_branches(self):
        _,calls=self.native(limit=None)
        self.assertEqual(calls[0][1]['deadline'],145.)
        self.assertEqual(calls[1][1]['deadline'],160.)
        _,calls=self.native(limit=0.)
        self.assertEqual(calls[0][1]['deadline'],100.)
        self.assertEqual(calls[1][1]['deadline'],100.)

    def test_invalid_budgets_skip_both_native_stages(self):
        for limit in (-1.,math.nan,math.inf):
            with self.subTest(limit=limit):
                result,calls=self.native(limit=limit)
                self.assertEqual(calls,[])
                self.assertEqual(result.status,'error')
                self.assertEqual(result.message,'time_limit_s must be finite and nonnegative')

    def test_failed_prestow_and_native_error_paths_unchanged(self):
        result,calls=self.native(prestow_status='dead_end')
        self.assertEqual([name for name,_ in calls],['carrier'])
        self.assertEqual(result.status,'dead_end')
        self.assertEqual(result.message,'native outcome')
        result,calls=self.native(error=NotImplementedError('missing oracle'))
        self.assertEqual(result.status,'error')
        self.assertEqual(result.message,'Shared feasibility checks unavailable: missing oracle')
        result,_=self.native(error=ValueError('native failure'))
        self.assertEqual(result.message,'ValueError: native failure')

    def test_live_globals_metadata_idempotence_and_ownership_restore(self):
        from stowage.solver.twostep import TwoStepPlanner
        original=TwoStepPlanner.plan
        handle=allocation_v6.install()
        replacement=TwoStepPlanner.plan
        nested=allocation_v6.install()
        try:
            self.assertIs(replacement.__globals__,original.__globals__)
            self.assertEqual(replacement.__name__,original.__name__)
            self.assertEqual(replacement.__qualname__,original.__qualname__)
            self.assertEqual(replacement.__defaults__,original.__defaults__)
            self.assertEqual(replacement.__kwdefaults__,original.__kwdefaults__)
            nested.restore()
            self.assertIs(TwoStepPlanner.plan,replacement)
            # A restore handle may only undo the replacement it owns.
            other=lambda *args,**kwargs:None
            TwoStepPlanner.plan=other
            handle.restore()
            self.assertIs(TwoStepPlanner.plan,other)
        finally:
            TwoStepPlanner.plan=replacement
            handle.restore()
        handle.restore()
        self.assertIs(TwoStepPlanner.plan,original)


if __name__=='__main__':
    unittest.main()
