"""Cargo repair must leave native tank rebalancing to its ballast caller."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

spec = importlib.util.spec_from_file_location('_c4_repair_tests', Path(__file__).with_name('repair.py'))
repair = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = repair
spec.loader.exec_module(repair)


def test_descent_transverse_change_does_not_exclude_cp_incumbent_under_old_tanks(monkeypatch):
    class Plan:
        def __init__(self, name, cargo_tcg_tm, ballast=None):
            self.name, self.cargo_tcg_tm, self.ballast = name, cargo_tcg_tm, ballast

        def model_copy(self, *, update):
            return Plan(self.name, self.cargo_tcg_tm, update['ballast'])

        def model_dump_json(self):
            return json.dumps([self.name, self.cargo_tcg_tm,
                               self.ballast.tank_masses_kg if self.ballast else None])

    original = Plan('original', 0.)
    descended = Plan('descended', -15.)
    exchanged = Plan('exchanged', -14.)
    calls = []

    def optimize(instance, plan, reference, **kwargs):
        calls.append(kwargs)
        assert plan.name == 'descended' and plan.cargo_tcg_tm == -15.
        assert reference == dict(tank_masses_kg={'T': 123.})
        assert kwargs['context']['balance_reference_tcg'] is False
        assert kwargs['relax_rank'] and kwargs['relax_heavy'] and kwargs['relax_category']
        assert kwargs['context']['reference_limit'] == .995
        return NS(plan=exchanged), dict(status='candidate')

    evaluator = NS(ship=NS(model_copy=lambda **k: NS(**k['update'])),
                   fixed_masses_kg={'fixed': 50.}, tank_ids=['T'], initial_masses_kg=[1.])
    modules = {
        'permutation.py': NS(optimize_assignment=optimize),
        'physics.py': NS(PhysicsEvaluator=lambda *a: evaluator),
        'lash_oracle.py': NS(Fast=lambda *a: object()),
    }
    tools = NS(LAB=Path('/unused'), _load=lambda name, path: modules[path.name],
               _runtime=lambda: (object(), NS(Problem=lambda i: object()), object()),
               refine=lambda *a, **k: (NS(plan=descended), dict(status='improved')),
               _model_class=lambda *a: lambda *a, **k: NS(),
               _seed=lambda *a: None)
    monkeypatch.setitem(sys.modules, 'page_tools', tools)
    monkeypatch.setattr(repair, 'monotonic', lambda: 0.)
    candidates, metadata = repair.refine_current(object(), original,
        dict(tank_masses_kg={'T': 123.}), budget_s=60.)
    assert len(calls) == 1
    assert [plan.cargo_tcg_tm for plan in candidates] == [-15., -14.]
    assert all(plan.ballast.tank_masses_kg == {'T': 123.} for plan in candidates)
    assert metadata['physical_certificate'] is False
    assert metadata['transverse_balance_policy'] == 'native ballast-caller balance after cargo change'
