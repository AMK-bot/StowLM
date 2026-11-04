"""AST and isolated native ranked-generator tests; no map/dataset inputs."""
import ast
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
import greedy_v5


class GreedyScoreTests(unittest.TestCase):
    def source(self):
        from stowage.solver import twostep
        return inspect.getsource(inspect.unwrap(twostep._fast_prestow))

    def test_exactly_one_return_tuple_changes(self):
        original=ast.parse(self.source())
        modified=greedy_v5.rewrite(self.source())
        changed=0
        for node in ast.walk(modified):
            if (isinstance(node,ast.Return) and isinstance(node.value,ast.Tuple)
                    and len(node.value.elts)>=4
                    and tuple(value.id if isinstance(value,ast.Name) else None
                              for value in node.value.elts[:4])==greedy_v5._NEW):
                values=node.value.elts
                node.value.elts=[values[0],values[3],values[1],values[2],*values[4:]]
                changed+=1
        self.assertEqual(changed,1)
        self.assertEqual(ast.dump(original,include_attributes=False),
                         ast.dump(modified,include_attributes=False))
        with self.assertRaises(ValueError):
            greedy_v5.rewrite('def incompatible():\n    return None\n')

    def ranked(self,modified=False,limit=8):
        from stowage.solver import twostep
        tree=greedy_v5.rewrite(self.source()) if modified else ast.parse(self.source())
        nodes=[node for node in ast.walk(tree)
               if isinstance(node,ast.FunctionDef) and node.name=='ranked']
        self.assertEqual(len(nodes),1)
        tree=ast.Module(body=[nodes[0]],type_ignores=[])
        environment=dict(twostep.__dict__)
        # The tall column avoids soft overstows; the lower column incurs them.
        shapes={'tall':(('late',),('late',),0.,0.,10.,True),
                'low':(('early',),('early',),0.,0.,2.,True),
                'restow':(('late',),('late',),0.,0.,0.,True),
                'blocked':(('late',),('late',),0.,0.,1.,True)}
        probes=[]
        def geometry(instance,ex,action):
            probes.append(action.slot)
            return 'native geometry rejection' if action.slot=='blocked' else None
        environment.update(boxes={'box':NS(gross_mass_kg=10.)},shape=shapes.__getitem__,
            pods={'box':2},stack_pod={'early':1,'late':3},hatch_pod={'early':1,'late':3},
            engine=NS(forces_restow=lambda action:action.slot=='restow'),
            my=0.,mx=0.,mass=0.,deadline=float('inf'),monotonic=lambda:0.,
            securing_geometry_violation=geometry,canonical=NS(),expander=NS(),
            reasons=set(),slot_limit=limit)
        exec(compile(ast.fix_missing_locations(tree),'<isolated_native_ranked>','exec'),environment)
        return environment['ranked'],probes,environment['reasons']

    def test_lower_roof_precedes_soft_overstow_with_same_native_actions(self):
        native,old_probes,old_reasons=self.ranked()
        changed,new_probes,new_reasons=self.ranked(modified=True)
        slots=['restow','tall','blocked','low']
        old=list(native('box',slots))
        new=list(changed('box',slots))
        self.assertEqual([action.slot for action in old],['tall','low','restow'])
        self.assertEqual([action.slot for action in new],['low','tall','restow'])
        self.assertEqual(set(old),set(new))
        self.assertEqual(set(old_probes),set(new_probes))
        self.assertEqual(old_reasons,{'native geometry rejection'})
        self.assertEqual(new_reasons,old_reasons)

    def test_native_slot_cap_and_deadline_code_retained(self):
        changed,_,_=self.ranked(modified=True,limit=1)
        self.assertEqual(len(list(changed('box',['tall','low','restow']))),1)
        # The complete native AST equality test above preserves every existing
        # deadline/restow/domain/E6 statement as well as the cap implementation.
        body=greedy_v5.rewrite(self.source())
        self.assertTrue(any(isinstance(node,ast.Name) and node.id=='deadline'
                            for node in ast.walk(body)))
        self.assertTrue(any(isinstance(node,ast.Name) and node.id=='e6_completion_possible'
                            for node in ast.walk(body)))

    def test_live_native_globals_and_restoration(self):
        from stowage.solver import twostep
        original=twostep._fast_prestow
        handle=greedy_v5.install()
        try:
            self.assertIs(twostep._fast_prestow.__globals__,original.__globals__)
            self.assertEqual(twostep._fast_prestow._scale_greedy_score,greedy_v5._NEW)
        finally:
            handle.restore()
        handle.restore()
        self.assertIs(twostep._fast_prestow,original)


if __name__=='__main__':
    unittest.main()
