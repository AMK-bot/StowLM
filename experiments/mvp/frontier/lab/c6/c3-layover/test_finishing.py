"""Unknown local physics must retain its cause and a checked failed candidate."""
from types import SimpleNamespace
import json
import numpy as np


def test_unknown_physics_does_not_mask_error_with_infinite_json(tmp_path, monkeypatch):
    import ballast_opt
    class Candidate:
        def model_dump(self, **kwargs):
            return {'moves': [], 'ballast': None}
    candidate = Candidate()
    class Unknown:
        def __init__(self, instance, plan):
            self.capacity_kg = np.array([1000.])
            self.initial_fractions = np.array([.5])
            self.tank_ids = ('DB-M-P',)
            self.evaluations = self.cache_hits = 0
            self.ship = SimpleNamespace(placements=[])
        def evaluate_fractions(self, values, **kwargs):
            raise ValueError('deliberately unavailable native separation')
        def candidate_plan(self, values):
            return candidate
    class Report:
        passed = False
        violations = []
        def model_dump(self, **kwargs):
            return {'passed': False, 'violations': []}
    monkeypatch.setattr(ballast_opt, 'PhysicsEvaluator', Unknown)
    monkeypatch.setattr(ballast_opt, 'check', lambda instance, plan: Report())
    returned, meta = ballast_opt.optimize(SimpleNamespace(id='unit', load_list=[]), candidate,
        out=tmp_path, budget_s=1., max_evaluations=1, max_iterations=1)
    assert returned is candidate
    assert not meta['full_checker_passed']
    assert 'deliberately unavailable' in meta['error']
    summary = json.loads((tmp_path/'summary.json').read_text())
    assert summary['best_merit'] is None
    assert summary['error'] == meta['error']


def test_area_failure_does_not_lower_kg_floor_with_actual_gm():
    from method import _learn
    state = dict(gm=1.9, kg_gm_floor=1.9, limit=.95, shift=0., sight_margin=.15,
                 vertical_scale=1.)
    report = SimpleNamespace(violations=[SimpleNamespace(severity='hard',
        rule_id='iscode.b2_3.containership_second_check', measured=.2)])
    _learn(state, report, 1.2)
    assert state['gm'] == 1.25  # securing uses achieved motion
    assert state['kg_gm_floor'] > 1.9  # area guidance never loosens because GM missed
    assert state['vertical_scale'] < 1.


def test_predeclared_distant_area_gate_retains_checked_complete_candidate():
    import method
    plan = SimpleNamespace(moves=[])
    report = SimpleNamespace(passed=False, violations=[SimpleNamespace(severity='hard',
        rule_id='iscode.b2_3.containership_second_check', measured=.15,limit=.30,unit='m*rad')])
    context = {'_recipes':{():plan},'_recipe_checks':{():report},
               'finishing_config':{'max_area_deficit_mrad':.05}}
    returned, meta = method.finish(SimpleNamespace(),plan,seed=0,finalize_s=1.,label='test',context=context)
    assert returned is plan and not meta['passed']
    assert meta['local_skipped'] == 'distant second-check area deficit'
