"""CPU-only contract tests for D137 staged candidates; no live source edits."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
from llm.exit import selection as S, preferences as P

def candidate(cid, moves, source='model'):
    return S.Candidate({'moves': moves}, source, cid)

def verify(instance, c):
    cost = c.plan['moves']
    return S.Verification(True, c.plan, cost,
        {'report': {'passed': True}, 'mask': {'passed': True}})

def test_teachers_both_run_even_with_gold():
    calls = []
    def baseline(i, *, time_limit_s, diagnostics):
        calls.append(time_limit_s)
        diagnostics.update(status='ok', planning_budget_s=time_limit_s)
        return candidate('baseline', 3, 'baseline_twostep')
    teachers, cache, rows = S.prepare_teacher_candidates(object(), candidate('gold', 5, 'gold'),
        verifier=verify, baseline_planner=baseline)
    assert calls == [120.]
    assert [c.source for c in teachers] == ['gold', 'baseline_twostep']
    assert len(cache) == 2 and all(row['passed'] for row in rows)
    assert rows[1]['planning']['planning_budget_s'] == 120


def test_teachers_record_missing_gold_and_no_proposal():
    def baseline(i, *, time_limit_s, diagnostics):
        diagnostics.update(status='not_found', message='search exhausted')
    teachers, cache, rows = S.prepare_teacher_candidates(object(), baseline_planner=baseline)
    assert not teachers and not cache
    assert rows[0]['reason'] == 'no_stored_gold'
    assert rows[1]['status'] == 'not_found'
    assert rows[1]['passed'] is False


def test_teacher_exception_is_visible_not_valid():
    def baseline(*args, **kwargs):
        raise RuntimeError('oracle failure')
    _, _, rows = S.prepare_teacher_candidates(object(), baseline_planner=baseline)
    assert 'oracle failure' in rows[1]['error']
    assert rows[1]['passed'] is False


def test_verified_teacher_can_beat_model_and_polish_starts_there():
    seen = []
    polished_from = []
    def checked(i, c):
        seen.append(c.candidate_id)
        return verify(i, c)
    def polish(i, c):
        assert c.metadata['exit_verified'] is True
        polished_from.append(c.source)
        return candidate('polished', 2, 'polished')
    result = S.select_winner(object(), [candidate('model', 9), candidate('gold', 5, 'gold'),
        candidate('baseline', 3, 'baseline_twostep')], scorer=lambda i,c:c.plan['moves'],
        verifier=checked, polisher=polish, verify_all=True)
    assert result.winner.source == 'polished' and result.weighted_moves == 2
    assert seen == ['baseline', 'gold', 'model', 'polished']
    assert polished_from == ['baseline_twostep']
    assert sum(r.get('selected_winner', False) for r in result.verifications) == 1
    assert all(c.metadata.get('exit_verified') for c in result.candidates[:3])


def test_failed_cheapest_teacher_never_wins():
    def checked(i,c):
        return S.Verification(False, details={'unavailable':True}) if c.source == 'gold' else verify(i,c)
    result = S.select_winner(object(), [candidate('gold', 1, 'gold'), candidate('model', 7)],
        scorer=lambda i,c:c.plan['moves'], verifier=checked, polish_top=0, verify_all=True)
    assert result.winner.source == 'model'
    assert len(result.verifications) == 2


def test_legacy_lazy_default_retained():
    seen=[]
    result=S.select_winner(object(), [candidate('a',1),candidate('b',2)],
        scorer=lambda i,c:c.plan['moves'], verifier=lambda i,c:(seen.append(c.candidate_id) or verify(i,c)),
        polish_top=0)
    assert seen == ['a']


def selection(tmp_path):
    winner = tmp_path / 'winner.json'
    instance = str(tmp_path / 'map.json')
    winner.write_text(json.dumps({'instance_path': instance, 'plan': {'id':'win'},
                                  'report': {'passed': True}}))
    return {'passed':True,'winner_record':str(winner),'instance_path':instance,'weighted_moves':3,
        'verifications':[{'candidate_id':'gold-winner','stage':'verify','passed':True,
                          'selected_winner':True,'mask':{'passed':True}}], 'candidates':[]}


def row(cid, cost=6, source='model', status='complete'):
    return {'candidate_id':cid,'source':source,'plan':{'id':cid},
            'metadata':{'weighted_moves':cost,'status':status,'token_ids':[1,2]}}


def check(cid, cost=6, passed=True):
    return {'candidate_id':cid,'stage':'verify','passed':passed,'weighted_moves':cost,
            'mask':{'passed':passed},'report':{'passed':passed}}


def test_dpo_valid_costlier_own_model_first_then_dead_end(tmp_path):
    data=selection(tmp_path)
    data['candidates']=[row('dead',status='dead_end'), row('more'), row('teacher',source='gold'),
                        row('unchecked'),row('tie',3)]
    data['verifications'] += [check('more'),check('teacher'),check('tie',3)]
    pairs=P.build_preference_pairs(data,prefer_model_costlier_only=True,layers={'fixture':True})
    assert [p['rejected']['candidate_id'] for p in pairs] == ['more','dead']
    assert pairs[0]['evidence']['fully_checked'] is True


@pytest.mark.parametrize('damage', ['unavailable','failed_mask','failed_report','failed_check'])
def test_dpo_never_admits_uncertain_or_invalid_costlier(tmp_path, damage):
    data=selection(tmp_path); data['candidates']=[row('more')]
    proof=check('more')
    if damage=='unavailable': proof['unavailable']=True
    elif damage=='failed_mask': proof['mask']['passed']=False
    elif damage=='failed_report': proof['report']['passed']=False
    else: proof['passed']=False
    data['verifications'].append(proof)
    assert P.build_preference_pairs(data,prefer_model_costlier_only=True,layers={'fixture':True}) == []


def test_dpo_scores_exact_checked_plan_not_unbound_token_metadata(tmp_path,monkeypatch):
    from stowage.io import load_instance
    from stowage.model import CheckReport, MovePlan
    from stowage.checker.full import _input_bindings
    from stowage.versions import CHECKER_VERSION
    from llm.exit import selection as live_selection
    root=ROOT
    spec=importlib.util.spec_from_file_location('_d137_preferences_fixture',
        root/'tests/llm/exit/test_preferences.py')
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    data=fixture.admitted_selection.__wrapped__(tmp_path)
    instance=load_instance(data['instance_path'])
    c=data['candidates'][0]
    plan=MovePlan.model_validate(json.loads(Path(c['plan_path']).read_text()))
    report=CheckReport(passed=True,count={'weighted_nonproductive':8},
        provenance={'versions':{'checker':CHECKER_VERSION},
                    'parents':list(_input_bindings(instance,plan))})
    data['verifications'].append({'candidate_id':c['candidate_id'],'stage':'verify','passed':True,
        'weighted_moves':8,'mask':{'passed':True},'report':report.model_dump(mode='json')})
    c['metadata']['token_ids']=[999]
    monkeypatch.setattr(live_selection,'score_candidate',
        lambda i,c:0 if c.plan.id=='chosen' else 8)
    calls=[]
    def trace(instance, *, plan=None, tokens=None, require_complete=False):
        calls.append((plan.id if plan else None,tokens,require_complete))
        value=1 if plan.id=='chosen' else 2
        return {'prompt':[9],'token_ids':[value],'allowed_sets':[[1,2]],
                'action_mask':[True],'status':'complete'}
    monkeypatch.setattr(P,'prepare_masked_sequence',trace)
    monkeypatch.setattr(P,'layer_identity',lambda:{'fixture':True})
    result=P.prepare_training_pairs(data,prefer_model_costlier_only=True,
        corpus_root=tmp_path/'empty-corpus')
    assert result['refusals']==[] and len(result['pairs'])==1
    assert calls==[('chosen',None,True),('rejected',None,True)]
    pair=result['pairs'][0]
    assert pair['rejected']['tokens']==[2]
    assert 'token_ids' not in pair['rejected_witness']
