import json
from pathlib import Path

import pytest

from llm.exit import preferences as p


def hard_failure_report():
    violation = dict(rule_id='objective.all_loaded', severity='hard', call_index=0,
                     message='Load-list container not loaded.')
    parts = {name: {'name': name, 'ran': True} for name in
             ('moves', 'rules', 'voyage', 'stability', 'strength', 'visibility', 'lashing', 'constraints')}
    return dict(passed=False, parts=parts, violations=[violation])


@pytest.fixture
def selection(tmp_path):
    record = tmp_path / 'winner.record.json'
    record.write_text(json.dumps({'instance_path': str(tmp_path / 'map.json'),
                                  'plan': {'id': 'chosen'}, 'report': {'passed': True}}))
    candidates = []
    for cid, cost in [('winner', 3), ('tie', 3), ('higher', 5), ('unknown-low', 1), ('failed-low', 0)]:
        plan = tmp_path / (cid + '.json')
        plan.write_text(json.dumps({'id': cid}))
        candidates.append({'candidate_id': cid, 'source': 'model', 'plan_path': str(plan),
                           'metadata': {'weighted_moves': cost}})
    return {'passed': True, 'weighted_moves': 3, 'winner_record': str(record),
        'instance_path': str(tmp_path / 'map.json'), 'candidates': candidates,
        'verifications': [
            {'candidate_id': 'failed-low', 'stage': 'verify', 'passed': False,
             'report': hard_failure_report()},
            {'candidate_id': 'winner', 'stage': 'verify', 'passed': True,
             'mask': {'passed': True}}]}


def pairs(selection, **kw):
    return p.build_preference_pairs(selection, layers={'fixture': True}, **kw)


def test_only_proven_inferior_candidates(selection):
    result = pairs(selection)
    assert [r['rejected']['candidate_id'] for r in result] == ['higher', 'failed-low']
    assert result[0]['evidence']['rejected_moves'] == 5
    assert result[1]['evidence']['kind'] == 'checker_failure'


def test_timeouts_and_polish_failure_do_not_establish_invalidity(selection):
    selection['verifications'].insert(0, {'candidate_id': 'unknown-low',
        'stage': 'verify', 'passed': False, 'error': 'ballast timeout'})
    selection['verifications'].insert(0, {'candidate_id': 'tie',
        'stage': 'polish', 'passed': False, 'error': 'timeout'})
    assert len(pairs(selection)) == 2


@pytest.mark.parametrize('failure', ['timeout', 'internal_error', 'incomplete', 'soft_only', 'mixed'])
def test_unknown_checker_failure_does_not_become_preference(selection, failure):
    report = hard_failure_report()
    if failure in ('timeout', 'internal_error', 'mixed'):
        report['parts']['stability'].update(ran=False, error='TimeoutError: calculation exhausted')
        internal = dict(rule_id='checker.internal_error', severity='hard', call_index=0,
                        message='TimeoutError: calculation exhausted')
        report['violations'] = [internal] if failure != 'mixed' else [*report['violations'], internal]
    elif failure == 'incomplete':
        del report['parts']['strength']
    else:
        report['violations'][0]['severity'] = 'soft'
    selection['verifications'][0]['report'] = report
    assert [row['rejected']['candidate_id'] for row in pairs(selection)] == ['higher']


def test_checker_namespace_is_not_physical_evidence_even_when_part_ran(selection):
    report = hard_failure_report()
    report['violations'][0]['rule_id'] = 'checker.missing_vessel_data'
    selection['verifications'][0]['report'] = report
    assert [row['rejected']['candidate_id'] for row in pairs(selection)] == ['higher']


def test_failed_score_cannot_use_stale_raw_move_metadata(selection):
    selection['verifications'].append({'candidate_id': 'higher',
        'stage': 'move_score', 'passed': False, 'error': 'illegal move replay'})
    assert [r['rejected']['candidate_id'] for r in pairs(selection)] == ['failed-low']


def test_dead_end_partial_tokens_are_preserved_but_empty_prefix_excluded(selection):
    rows = [{'candidate_id': 'dead', 'status': 'dead_end', 'token_ids': [2, 3]},
            {'candidate_id': 'empty', 'status': 'dead_end', 'token_ids': []},
            {'candidate_id': 'truncated', 'status': 'max_tokens', 'token_ids': [4]}]
    result = pairs(selection, samples=rows, max_pairs=10)
    assert [row['evidence']['kind'] for row in result] == ['strictly_more_moves', 'checker_failure', 'dead_end']
    assert result[-1]['rejected']['token_ids'] == [2, 3]


@pytest.mark.parametrize('nested',[False,True])
def test_pruned_prefix_is_never_dpo_negative_even_with_cost_or_checker_evidence(selection,nested):
    candidate={'candidate_id':'pruned','token_ids':[2,3],'weighted_moves':100,
               'status':'pruned_worse_than_incumbent'}
    if nested:
        candidate={'candidate_id':'pruned','metadata':candidate}
    selection['verifications'].append({'candidate_id':'pruned','stage':'verify',
                                       'passed':False,'report':{'passed':False}})
    result=pairs(selection,samples=[candidate],max_pairs=10)
    assert all(row['rejected']['candidate_id']!='pruned' for row in result)


def test_complete_rejected_keeps_plan_even_with_generation_tokens(selection):
    selection['candidates'][2]['metadata']['token_ids'] = [8]
    reject = pairs(selection)[0]['rejected']
    assert reject['token_ids'] == [8] and reject['plan'] == {'id': 'higher'}


def test_no_winner_no_pairs_and_masks_are_mandatory(selection):
    selection['passed'] = False
    assert pairs(selection) == []
    selection['passed'] = True
    selection['verifications'][-1]['mask']['passed'] = False
    with pytest.raises(ValueError, match='layered mask'):
        pairs(selection)


def test_chosen_instance_cannot_be_swapped(selection):
    selection['instance_path'] += '.other'
    with pytest.raises(ValueError, match='instance mismatch'):
        pairs(selection)


class Decoder:
    def __init__(self, end='complete'):
        self.position = 0
        self.status = 'running'
        self.end = end

    def allowed_token_ids(self):
        if self.position == 2:
            self.status = self.end
            return []
        return [[1, 2], [3]][self.position]

    def advance(self, token):
        self.position += 1


def test_complete_trace_excludes_end_token_from_action_mask():
    trace = p.replay_tokens(Decoder(), [2, 3], end_plan_tokens=[3], require_complete=True)
    assert trace['action_mask'] == [True, False]
    assert trace['decision_tokens'] == 1


@pytest.mark.parametrize('length,ends,start', [(8, (2, 5, 8), 5), (7, (2, 5), 5), (3, (3,), 0)])
def test_deadend_only_blames_tokens_after_last_good_action(length, ends, start):
    class ActionsDecoder:
        def __init__(self):
            self.position = 0
            self.status = 'running'
        def allowed_token_ids(self):
            if self.position == length:
                self.status = 'dead_end'
                return []
            return [1, 2]
        def advance(self, token):
            self.position += 1
            return object() if self.position in ends else None
    trace = p.replay_tokens(ActionsDecoder(), [1]*length)
    assert trace['dead_end_loss_start'] == start
    assert trace['action_mask'] == [False]*start+[True]*(length-start)


def test_final_polished_winner_identity_drives_preferences(selection):
    selection['verifications'].insert(0, dict(candidate_id='older-pass', stage='verify',
        passed=True, selected_winner=False, mask={'passed': True}))
    selection['verifications'][-1]['selected_winner'] = True
    assert all(row['chosen']['candidate_id'] == 'winner' for row in pairs(selection))


def test_deadend_scores_emitted_tokens_without_invented_terminal():
    trace = p.replay_tokens(Decoder('dead_end'), [2, 3])
    assert trace['status'] == 'dead_end' and all(trace['action_mask'])
    with pytest.raises(ValueError, match='support'):
        p.replay_tokens(Decoder('dead_end'), [2, 3, 4])


def test_unsupported_and_empty_action_sequences_refused():
    with pytest.raises(ValueError, match='support'):
        p.replay_tokens(Decoder(), [9])
    with pytest.raises(ValueError, match='no action'):
        p.replay_tokens(Decoder(), [])
    with pytest.raises(ValueError, match='not complete'):
        p.replay_tokens(Decoder('dead_end'), [1, 3], require_complete=True)


def test_pair_writer_is_jsonl_and_rejects_infinite_values(tmp_path):
    path = tmp_path / 'pairs.jsonl'
    p.write_pairs(path, [{'id': 1}, {'id': 2}])
    assert [json.loads(s) for s in path.read_text().splitlines()] == [{'id': 1}, {'id': 2}]
    with pytest.raises(ValueError):
        p.write_pairs(path, [{'score': float('inf')}])


def test_training_admission_rejects_heldout_map_before_loading_winner(tmp_path):
    from stowage.model.fixtures import build_instance
    corpus = tmp_path / 'corpus'
    (corpus / 'val').mkdir(parents=True)
    instance = build_instance()
    path = corpus / 'val' / 'case.json'
    path.write_text(instance.model_dump_json())
    # Same semantic map at a new TRAIN-looking path is still held out.
    clone = tmp_path / 'train.json'
    clone.write_text(json.dumps(instance.model_dump(mode='json'), indent=3))
    result = p.prepare_training_pairs({'instance_path': str(clone), 'passed': True,
                                      'winner_record': 'does-not-exist'}, corpus_root=corpus)
    assert not result['pairs'] and 'held-out val' in result['refusals'][0]['reason']


def test_training_admission_refuses_unbound_checker_pass(tmp_path):
    from stowage.model import MovePlan, CheckReport
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    ipath = tmp_path / 'instance.json'
    ipath.write_text(instance.model_dump_json())
    plan = MovePlan(id='p', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True)
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'plan': plan.model_dump(mode='json'),
                                 'report': report.model_dump(mode='json')}))
    result = p.prepare_training_pairs({'instance_path': str(ipath), 'passed': True,
        'winner_record': str(record)}, corpus_root=tmp_path / 'empty-corpus')
    assert not result['pairs'] and 'content binding' in result['refusals'][0]['reason']


@pytest.fixture
def admitted_selection(tmp_path):
    from stowage.model import MovePlan, CheckReport
    from stowage.model.fixtures import build_instance
    from stowage.checker.full import _input_bindings
    from stowage.versions import CHECKER_VERSION
    instance = build_instance()
    ipath = tmp_path / 'instance.json'
    ipath.write_text(instance.model_dump_json())
    plan = MovePlan(id='chosen', instance_id=instance.id, call_index=instance.call_index)
    report = CheckReport(passed=True, provenance={'versions': {'checker': CHECKER_VERSION},
        'parents': list(_input_bindings(instance, plan))})
    record = tmp_path / 'winner.record.json'
    record.write_text(json.dumps({'instance_path': str(ipath), 'plan': plan.model_dump(mode='json'),
                                 'report': report.model_dump(mode='json')}))
    reject = plan.model_copy(update={'id': 'rejected'})
    rpath = tmp_path / 'rejected.json'
    rpath.write_text(reject.model_dump_json())
    return {'instance_path': str(ipath), 'passed': True, 'weighted_moves': 0,
        'winner_record': str(record), 'candidates': [
            {'candidate_id': 'reject', 'source': 'model', 'plan_path': str(rpath),
             'metadata': {'weighted_moves': 8}}],
        'verifications': [{'candidate_id': 'chosen', 'stage': 'verify', 'passed': True,
                           'mask': {'passed': True}}]}


def test_more_moves_is_remeasured_not_trusted(admitted_selection, tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'prepare_masked_sequence', lambda *a, **kw:
        {'prompt': [10], 'token_ids': [1, 2], 'allowed_sets': [[1], [2]],
         'action_mask': [True, True], 'status': 'complete'})
    result = p.prepare_training_pairs(admitted_selection, corpus_root=tmp_path / 'empty-corpus')
    assert not result['pairs']
    assert 'not strictly more moves' in result['refusals'][0]['reason']


def test_bound_timeout_report_is_not_admitted_as_checker_rejection(admitted_selection, tmp_path, monkeypatch):
    from stowage.io import load_instance
    from stowage.model import CheckReport, MovePlan
    from stowage.checker.full import _failed, _input_bindings
    from stowage.versions import CHECKER_VERSION
    instance = load_instance(admitted_selection['instance_path'])
    candidate = admitted_selection['candidates'][0]
    candidate['metadata']['weighted_moves'] = 0
    rejected = MovePlan.model_validate(json.loads(Path(candidate['plan_path']).read_text()))
    timeout = _failed(instance, 'stability', TimeoutError('stability budget exhausted'))
    report = CheckReport(passed=False, violations=timeout.violations, parts={'stability': timeout},
        provenance={'versions': {'checker': CHECKER_VERSION},
                    'parents': list(_input_bindings(instance, rejected))})
    admitted_selection['verifications'].append(dict(candidate_id='reject', stage='verify',
        passed=False, report=report.model_dump(mode='json')))
    monkeypatch.setattr(p, 'prepare_masked_sequence', lambda *a, **kw:
        {'prompt': [10], 'token_ids': [1], 'allowed_sets': [[1, 2]],
         'action_mask': [True], 'status': 'complete'})
    result = p.prepare_training_pairs(admitted_selection, corpus_root=tmp_path / 'empty-corpus')
    assert not result['pairs'] and not result['refusals']


def test_complete_ready_pair_and_deadend_status_reproduced(admitted_selection, tmp_path, monkeypatch):
    admitted_selection['candidates'] = []
    def prepare(instance, tokens=None, **kw):
        return {'prompt': [10], 'token_ids': tokens or [1, 2],
                'allowed_sets': [[1, 3], [2]][:len(tokens or [1, 2])],
                'action_mask': [True] * len(tokens or [1, 2]),
                'status': 'dead_end' if tokens else 'complete'}
    monkeypatch.setattr(p, 'prepare_masked_sequence', prepare)
    samples = [{'candidate_id': 'dead', 'status': 'dead_end', 'token_ids': [3]}]
    result = p.prepare_training_pairs(admitted_selection, samples=samples, max_pairs=1,
                                     corpus_root=tmp_path / 'empty-corpus')
    assert not result['refusals'] and len(result['pairs']) == 1
    pair = result['pairs'][0]
    assert pair['chosen_verified'] is True and pair['pair_id']
    assert pair['chosen']['tokens'] == [1, 2] and pair['rejected']['tokens'] == [3]
    assert 'partial-sequence' in pair['approximation']
    assert pair['chosen_witness']['report']['passed'] is True
    assert pair['rejected_witness']['token_ids'] == [3]
    assert pair['rejected_witness']['candidate_id'] == 'dead'


def test_fake_deadend_status_refused(admitted_selection, tmp_path, monkeypatch):
    admitted_selection['candidates'] = []
    monkeypatch.setattr(p, 'prepare_masked_sequence', lambda *a, **kw:
        {'prompt': [10], 'token_ids': [1, 2], 'allowed_sets': [[1], [2]],
         'action_mask': [True, True], 'status': 'complete'})
    samples = [{'candidate_id': 'dead', 'status': 'dead_end', 'token_ids': [1]}]
    result = p.prepare_training_pairs(admitted_selection, samples=samples,
                                     corpus_root=tmp_path / 'empty-corpus')
    assert not result['pairs'] and 'actual dead end' in result['refusals'][0]['reason']
