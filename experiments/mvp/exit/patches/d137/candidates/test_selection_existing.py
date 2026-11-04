from types import SimpleNamespace

import pytest

from llm.exit import selection as s
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance


@pytest.fixture
def case():
    instance = build_instance()
    return instance, MovePlan(id='exit.selection', instance_id=instance.id, call_index=instance.call_index)


def test_lexicographic_validity_precedes_moves_and_stops_lazily(case):
    instance, plan = case
    candidates = [s.Candidate(plan, source, source) for source in ['gold', 'model-low', 'model-high']]
    scores = {'gold': 9, 'model-low': 1, 'model-high': 5}
    seen = []
    def verify(instance, candidate):
        seen.append(candidate.source)
        valid = candidate.source != 'model-low'
        return s.Verification(valid, plan if valid else None, scores[candidate.source] if valid else None)
    result = s.select_winner(instance, candidates, polish_top=0,
        scorer=lambda i,c: scores[c.source], verifier=verify)
    assert seen == ['model-low', 'model-high']
    assert result.winner.source == 'model-high'
    assert result.weighted_moves == 5


def test_polished_model_can_replace_gold(case):
    instance, plan = case
    scores = {'model': 12, 'gold': 8, 'polished': 6}
    parents = []
    def polish(i,c):
        parents.append(c.source)
        return s.Candidate(plan, 'polished', 'lns')
    result = s.select_winner(instance,[s.Candidate(plan,'model'),s.Candidate(plan,'gold')],
        scorer=lambda i,c:scores[c.source],polisher=polish,
        verifier=lambda i,c:s.Verification(True,plan,scores[c.source]))
    assert parents == ['gold']
    assert result.winner.source == 'polished'


def test_bad_move_replay_never_reaches_expensive_verifier(case):
    instance, plan = case
    result = s.select_winner(instance,[s.Candidate(plan,'model')],polish_top=0,
        scorer=lambda *a: (_ for _ in ()).throw(ValueError('illegal')),
        verifier=lambda *a: pytest.fail('illegal plan reached checker'))
    assert result.winner is None
    assert result.verifications[0]['stage'] == 'move_score'


def test_ties_are_stable_and_every_saved_gold_is_reverified(case):
    instance,plan = case
    seen = []
    def verify(i,c):
        seen.append(c.source)
        return s.Verification(True,plan,3)
    result = s.select_winner(instance,[s.Candidate(plan,'gold'),s.Candidate(plan,'model')],
        polish_top=0,scorer=lambda *a:3,verifier=verify)
    assert result.winner.source == 'gold' and seen == ['gold']


def test_ranked_objective_cannot_change_during_verification(case):
    instance,plan = case
    with pytest.raises(ValueError,match='changed the ranked'):
        s.select_winner(instance,[s.Candidate(plan,'model')],polish_top=0,
            scorer=lambda *a:3, verifier=lambda *a:s.Verification(True,plan,4))


def test_polisher_failure_does_not_erase_checked_candidates(case):
    instance,plan = case
    result = s.select_winner(instance,[s.Candidate(plan,'model')],scorer=lambda *a:3,
        polisher=lambda *a: (_ for _ in ()).throw(RuntimeError('search failed')),
        verifier=lambda *a:s.Verification(True,plan,3))
    assert result.winner is not None
    assert [row['stage'] for row in result.verifications] == ['verify','polish']


def test_test_and_showcase_gold_forbidden(tmp_path):
    with pytest.raises(ValueError,match='forbidden'):
        s.load_gold_candidate(tmp_path/'corpus/test/tiny/x.json',tmp_path/'corpus',tmp_path/'gold')
    with pytest.raises(ValueError,match='forbidden'):
        s.load_gold_candidate(tmp_path/'corpus/showcase/x.json',tmp_path/'corpus',tmp_path/'gold')


def test_gold_missing_is_optional(tmp_path):
    assert s.load_gold_candidate(tmp_path/'corpus/train/tiny/x.json',tmp_path/'corpus',tmp_path/'gold') is None


def test_verifier_rejects_rule_freeze_before_any_compute(case, monkeypatch):
    import stowage.rules.catalogue as rules
    monkeypatch.setattr(rules,'rule_freeze_v1_enabled',lambda:True)
    instance,plan=case
    result=s.verify_candidate(instance,s.Candidate(plan,'gold'))
    assert not result.passed and 'switch OFF' in result.details['error']


def test_scoped_v4_restores_environment_when_finalize_raises(case,monkeypatch):
    import os
    import stowage.planner as planner
    monkeypatch.setenv('STOWAGE_BALLAST_V4','0')
    def fail(*a,**kw):
        assert os.environ['STOWAGE_BALLAST_V4']=='1'
        raise RuntimeError('synthetic finisher failure')
    monkeypatch.setattr(planner,'finalize',fail)
    instance,plan=case
    result=s.verify_candidate(instance,s.Candidate(plan,'model'))
    assert not result.passed
    assert 'synthetic finisher failure' in result.details['error']
    assert os.environ['STOWAGE_BALLAST_V4']=='0'


def test_v4_cannot_leak_into_checker_and_mask(case, monkeypatch):
    monkeypatch.setenv('STOWAGE_BALLAST_V4', '1')
    instance, plan = case
    result = s.verify_candidate(instance, s.Candidate(plan, 'model'))
    assert not result.passed and 'must be unset' in result.details['error']


def test_decoder_uses_both_production_layer_factories(monkeypatch):
    import llm.lookahead
    import llm.securing
    captured={}
    def factory(*,engine_wrapper):
        captured['wrapper']=engine_wrapper
        def decoder(*args): return []
        decoder.name = 'test-production-securing'
        return decoder
    monkeypatch.setattr(llm.securing,'securing_decoder_factory',factory)
    wrapped = s.decoder_factory()
    assert callable(wrapped) and wrapped.name == 'test-production-securing'
    assert captured['wrapper'] is llm.lookahead.wrap_engine


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1])
def test_unmeasured_scores_do_not_train(case,bad):
    instance,plan=case
    result=s.select_winner(instance,[s.Candidate(plan,'model')],polish_top=0,
                           scorer=lambda *a:bad,verifier=lambda *a:pytest.fail('invalid score'))
    assert result.winner is None



def test_polish_slot_goes_to_best_verified_candidate_after_lazy_failure(case):
    instance,plan=case
    events=[]
    costs={'invalid':0,'valid':4,'expensive':8,'polished':2}
    def verify(i,c):
        events.append(('verify',c.source))
        return s.Verification(c.source!='invalid',plan,costs[c.source])
    def polish(i,c):
        events.append(('polish',c.source))
        assert c.metadata['exit_verified'] is True
        return s.Candidate(plan,'polished','polished-valid',{'parent_source':c.source})
    result=s.select_winner(instance,[s.Candidate(plan,name,name) for name in ['expensive','invalid','valid']],
        scorer=lambda i,c:costs[c.source],verifier=verify,polisher=polish)
    assert events==[('verify','invalid'),('verify','valid'),('polish','valid'),('verify','polished')]
    assert result.winner.source=='polished' and result.weighted_moves==2
    selected=[row for row in result.verifications if row.get('selected_winner')]
    assert len(selected)==1 and selected[0]['candidate_id']=='polished-valid'


@pytest.mark.parametrize('polished_score',[4,5])
def test_equal_or_worse_polish_never_replaces_or_runs_expensive_gates(case,polished_score):
    instance,plan=case
    seen=[]
    result=s.select_winner(instance,[s.Candidate(plan,'model','model')],
        scorer=lambda i,c:4 if c.source=='model' else polished_score,
        verifier=lambda i,c:(seen.append(c.source) or s.Verification(True,plan,4)),
        polisher=lambda *a:s.Candidate(plan,'polished','polished-model'))
    assert seen==['model'] and result.winner.source=='model'
    assert result.verifications[-1]['reason']=='not_strictly_better'


def test_invalid_cheaper_polish_retains_checked_incumbent(case):
    instance,plan=case
    def verify(i,c):
        return s.Verification(c.source=='model',plan if c.source=='model' else None,
                              4 if c.source=='model' else None)
    result=s.select_winner(instance,[s.Candidate(plan,'model','model')],
        scorer=lambda i,c:4 if c.source=='model' else 1,verifier=verify,
        polisher=lambda *a:s.Candidate(plan,'polished','polished-model'))
    assert result.winner.source=='model' and result.weighted_moves==4
    assert [row['selected_winner'] for row in result.verifications if row['stage']=='verify']==[True,False]


def test_no_verified_incumbent_means_no_polish(case):
    instance,plan=case
    result=s.select_winner(instance,[s.Candidate(plan,'model')],scorer=lambda *a:0,
        verifier=lambda *a:s.Verification(False),polisher=lambda *a:pytest.fail('invalid polish input'))
    assert result.winner is None


def test_polisher_receives_finalized_winner_plan_not_unfinished_proposal(case):
    instance,plan=case
    finalized=plan.model_copy(update={'id':'finalized'})
    def polish(i,c):
        assert c.plan is finalized and c.metadata['exit_verified'] is True
    result=s.select_winner(instance,[s.Candidate(plan,'model')],scorer=lambda *a:3,
        verifier=lambda *a:s.Verification(True,finalized,3),polisher=polish)
    assert result.winner.plan is finalized


def test_polish_does_not_finalize_already_checked_incumbent_again(case,monkeypatch):
    import stowage.planner
    import stowage.solver.gold_warmstart
    instance,plan=case
    monkeypatch.setattr(stowage.planner,'finalize',lambda *a,**kw:pytest.fail('duplicate finisher'))
    calls=[]
    def improve(i,result,**kw):
        calls.append((result.plan,kw))
        return result
    monkeypatch.setattr(stowage.solver.gold_warmstart,'improve_checked',improve)
    checked=s.Candidate(plan,'model','model',{'exit_verified':True})
    result=s.polish_candidate(instance,checked,time_limit_s=15)
    assert result.source=='polished' and calls==[(plan,{'time_limit_s':15,'seed':instance.seed})]
    with pytest.raises(ValueError,match='verified'):
        s.polish_candidate(instance,s.Candidate(plan,'model'))


def test_two_step_baseline_is_bounded_labelled_unchecked_and_source_bound(case,monkeypatch):
    import stowage.solver.twostep
    from stowage.model import PlannerResult
    instance,plan=case
    calls=[]
    class FakePlanner:
        version='4.4.0'
        def __init__(self,*,feasibility_config):
            assert feasibility_config.extra['global_screen']==0
        def plan(self,i,**kw):
            calls.append(kw)
            return PlannerResult(status='ok',plan=plan,planner='baseline.twostep',planner_version=self.version,
                                 seed=kw['seed'],provenance=plan.provenance)
    monkeypatch.setattr(stowage.solver.twostep,'TwoStepPlanner',FakePlanner)
    candidate=s.baseline_candidate(instance)
    assert calls==[{'seed':instance.seed,'time_limit_s':120.}]
    assert candidate.source=='baseline_twostep'
    assert candidate.candidate_id=='baseline-twostep-v4.4'
    assert candidate.metadata['requires_current_verification'] is True
    assert candidate.metadata['model'] is None and candidate.metadata['model_inference'] is False
    assert candidate.metadata['planner_version']=='4.4.0'
    assert len(candidate.metadata['source_sha256'])==4
    assert all(len(digest)==64 for digest in candidate.metadata['source_sha256'].values())
    assert candidate.metadata['provenance']==candidate.plan.provenance.model_dump(mode='json')
    verified=[]
    result=s.select_winner(instance,[candidate],polish_top=0,scorer=lambda *a:3,
        verifier=lambda i,c:(verified.append(c.source) or s.Verification(False)))
    assert result.winner is None and verified==['baseline_twostep']


def test_baseline_solver_failure_is_optional_and_other_versions_refused(case,monkeypatch):
    import stowage.solver.twostep
    from stowage.model import PlannerResult
    instance,plan=case
    class FakePlanner:
        version='4.4.0'
        def __init__(self,**kwargs):pass
        def plan(self,*a,**kw):
            return PlannerResult(status='timeout',planner='baseline.twostep',planner_version=self.version,
                                 provenance=plan.provenance)
    monkeypatch.setattr(stowage.solver.twostep,'TwoStepPlanner',FakePlanner)
    assert s.baseline_candidate(instance) is None
    FakePlanner.version='4.3.0'
    with pytest.raises(ValueError,match='4.4.0'):
        s.baseline_candidate(instance)


@pytest.mark.parametrize('budget',[0,-1,float('nan'),float('inf')])
def test_baseline_budget_checked_before_solver(case,budget):
    instance,_=case
    with pytest.raises(ValueError,match='budget'):
        s.baseline_candidate(instance,time_limit_s=budget)


def test_baseline_source_drift_during_planning_refused(case,monkeypatch):
    import stowage.solver.twostep
    from stowage.model import PlannerResult
    instance,plan=case
    changed=[False]
    class FakePlanner:
        version='4.4.0'
        def __init__(self,**kwargs):pass
        def plan(self,*a,**kw):
            changed[0]=True
            return PlannerResult(status='ok',plan=plan,planner='baseline.twostep',planner_version=self.version,
                                 provenance=plan.provenance)
    original=s.Path.read_bytes
    def contents(path):
        return b'changed during planning' if changed[0] and path.stem.startswith('twostep') else original(path)
    monkeypatch.setattr(stowage.solver.twostep,'TwoStepPlanner',FakePlanner)
    monkeypatch.setattr(s.Path,'read_bytes',contents)
    with pytest.raises(ValueError,match='source changed'):
        s.baseline_candidate(instance)
