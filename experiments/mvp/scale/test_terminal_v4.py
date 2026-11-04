"""Exact AST delta and mocked native protocol, with no map/dataset inputs."""
import ast
from contextlib import ExitStack
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import terminal_v4


class TerminalTests(unittest.TestCase):
    def test_exactly_one_ast_condition_changes(self):
        from stowage.solver import twostep_terminal as module
        source=inspect.getsource(module._fast_terminal)
        original=ast.parse(source)
        modified=terminal_v4.rewrite(source)
        old=ast.dump(ast.parse(terminal_v4._OLD,mode='eval').body,include_attributes=False)
        new=ast.dump(ast.parse(terminal_v4._NEW,mode='eval').body,include_attributes=False)
        found=0
        for node in ast.walk(modified):
            if isinstance(node,ast.If) and ast.dump(node.test,include_attributes=False)==new:
                node.test=ast.parse(terminal_v4._OLD,mode='eval').body
                found+=1
        self.assertEqual(found,1)
        self.assertEqual(ast.dump(original,include_attributes=False),
                         ast.dump(modified,include_attributes=False))
        self.assertEqual(sum(isinstance(node,ast.If) and
            ast.dump(node.test,include_attributes=False)==old for node in ast.walk(original)),1)
        with self.assertRaises(ValueError):
            terminal_v4.rewrite('def incompatible():\n    return None\n')

    def run_native_protocol(self,failed=()):
        from stowage.actions.space import Place
        from stowage.solver import twostep_terminal as module
        import stowage.moves as moves
        counters=dict(legal=0,geometry=0,complete=0,state=0,replay=0,engines=0)
        category=('same-booking',)
        instance=NS()
        prestow=NS(slots=[NS(slot='slot0',category=category,representative_box='B'),
                          NS(slot='slot1',category=category,representative_box='A')],
                   actions=[Place('B','slot0'),Place('A','slot1')])
        class Expander:
            def __init__(self,instance):
                self.yard={}
            def location(self,cid):
                return None
            def step(self,action):
                pass
        class Engine:
            def __init__(self,index):
                self.index=index
                self.actions=[]
            def is_legal(self,action):
                counters['legal']+=1
                return self.index not in failed
            def push(self,action):
                self.actions.append(action)
            def is_complete(self):
                counters['complete']+=1
                return len(self.actions)==2
        def factory(*args,**kwargs):
            engine=Engine(counters['engines'])
            counters['engines']+=1
            return engine
        def geometry(*args,**kwargs):
            counters['geometry']+=1
            return None
        def state(*args):
            counters['state']+=1
            return []
        def replay(instance,plan):
            counters['replay']+=1
            # The exact carrier replay costs more than yard substitution here.
            cost=9 if plan.actions[0].box=='B' else 1
            return NS(legal=True,count=NS(weighted_nonproductive=cost))
        space=NS(expand=lambda instance,actions:NS(actions=list(actions)))
        metrics=dict(terminal_expansions=0,terminal_candidates=0)
        with ExitStack() as stack:
            for name,value in dict(get_action_space=lambda kind:space,Expander=Expander,
                _precedences=lambda *args:set(),cells_of_slot=lambda slot:[slot],
                _slot_adjustments=lambda *args:[],securing_geometry_violation=geometry,
                securing_state_violations=state).items():
                stack.enter_context(patch.object(module,name,value))
            stack.enter_context(patch.object(moves,'apply_plan',replay))
            original=module._fast_terminal
            handle=terminal_v4.install()
            try:
                result=module._fast_terminal(instance,prestow,{category:['A','B']},
                    deadline=float('inf'),engine_factory=factory,
                    feasibility_config=None,metrics=metrics)
            finally:
                handle.restore()
            self.assertIs(module._fast_terminal,original)
        return result,metrics,counters

    def test_successful_representatives_win_despite_higher_true_cost(self):
        result,metrics,counters=self.run_native_protocol()
        self.assertEqual([action.box for action in result.actions],['B','A'])
        self.assertEqual(metrics['objective'],9)
        self.assertEqual(metrics['terminal_fast_selected_policy'],'carrier_representatives')
        self.assertEqual(metrics['terminal_category_substitutions'],0)
        self.assertEqual(metrics['terminal_fast_attempts'],2)
        self.assertEqual(counters,dict(legal=4,geometry=4,complete=2,state=2,replay=2,engines=2))

    def test_representative_failure_keeps_successful_yard_fallback(self):
        result,metrics,counters=self.run_native_protocol(failed=(1,))
        self.assertEqual([action.box for action in result.actions],['A','B'])
        self.assertEqual(metrics['objective'],1)
        self.assertEqual(metrics['terminal_fast_selected_policy'],'yard_first')
        self.assertEqual(metrics['terminal_fast_failures'],1)
        self.assertEqual(counters['engines'],2)

    def test_both_failed_remains_none(self):
        result,metrics,counters=self.run_native_protocol(failed=(0,1))
        self.assertIsNone(result)
        self.assertEqual(metrics['terminal_fast_attempts'],2)
        self.assertEqual(metrics['terminal_fast_failures'],2)
        self.assertEqual(counters['replay'],0)


if __name__=='__main__':
    unittest.main()
