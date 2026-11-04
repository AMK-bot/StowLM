import json
import os
import subprocess

import numpy as np
import pytest

from experiments.mvp.pipeline.xray import fixture
from experiments.mvp.pipeline.xray.recorder import RecordingLogitsProcessor
from experiments.mvp.pipeline.xray.trace import load_trace
from experiments.mvp.reader.v3 import mask_study, train
from experiments.mvp.reader.v3.decoder import ReaderDecoder, ReaderLogitsProcessor

DIGEST = 'a' * 64
ROW = {'id': 'r1', 'sentence': 'Close bay 7.', 'context': fixture.CONTEXT, 'family': 'f',
       'expected': {'schema_version': fixture.SCHEMA_VERSION, 'constraint': {'type': 'close_bay', 'bay': 7}}}


def recorded(wanted=True):
    """The real recorder and mask over scripted logits, recorded-kind only for this test."""
    processor = ReaderLogitsProcessor(ReaderDecoder(fixture.ScriptedTokenizer(), fixture.CONTEXT), 2, fixture.STOP)
    recorder = RecordingLogitsProcessor(processor, sentence=fixture.SENTENCE, top_k=6, model='muse-glimmer-30b',
                                        adapter_sha256=DIGEST, prompt_sha256=DIGEST)
    history = [42, 43]
    for index in range(len(fixture.SCRIPT)):
        logits = fixture.scripted_logits(4 if wanted and index == 4 else 0 if index == 4 else index).copy()
        if index == 4 and not wanted:
            logits[:] = -5
            logits[4] = 3
        masked = recorder(np.array(history), logits)
        chosen = int(np.argmax(masked))
        history.append(chosen)
        recorder.observe_token(chosen)
    return recorder.finish(''.join(fixture.TOKENS[t] for t in fixture.SCRIPT if t != fixture.STOP))


def test_top_change_is_counted_from_the_raw_ranking():
    blocks = mask_study.top_blocks(recorded())
    assert len(blocks) == 1 and blocks[0]['reason'] == 'bay_not_on_ship' and blocks[0]['chosen'] == '7'
    assert mask_study.top_blocks(recorded(wanted=False)) == []


def test_end_token_alias_is_not_a_block_but_stopping_early_is():
    def step(token, is_stop):
        return {'step': 1, 'is_stop': is_stop, 'chosen': {'token': '<eos>' if is_stop else '7'},
                'top_k': [{'token': token, 'blocked': True, 'reason': 'not_in_grammar', 'probability': 1.0}]}

    assert mask_study.top_blocks({'steps': [step('<|eot|>', True)]}) == []
    assert len(mask_study.top_blocks({'steps': [step('<|eot|>', False)]})) == 1
    assert len(mask_study.top_blocks({'steps': [step('99', True)]})) == 1


def test_tiny_probability_is_not_a_real_block():
    trace = recorded()
    trace['steps'][4]['top_k'][0]['probability'] = .005
    assert mask_study.top_blocks(trace) and not mask_study.real_blocks(trace)


def test_best_block_order(monkeypatch):
    def fake(reason, probability):
        return {'trace': {'complete': True, 'steps': [{'step': 1, 'is_stop': False, 'chosen': {'token': 'x'}, 'top_k': [
            {'token': 't', 'blocked': True, 'reason': reason, 'probability': probability}]}]}}
    records = {'a': fake('not_in_grammar', .99), 'b': fake('bay_not_on_ship', .4), 'c': fake('bay_not_on_ship', .4),
               'd': fake('tier_over_cap', .9), 'e': {'trace': {'complete': False, 'steps': []}}, 'f': {'error': 'x'}}
    rows = [{'id': key} for key in 'fedcba']
    block, trace = mask_study.best_block(rows, records)
    assert block['reason'] == 'bay_not_on_ship' and trace is records['b']['trace']
    assert mask_study.best_block([{'id': 'f'}], records) is None


def test_label_survives_validation_and_save(monkeypatch, tmp_path):
    monkeypatch.setattr(mask_study, 'TRACES', tmp_path)
    label = 'Glimmer before fine-tuning, selected from 200 sentences'
    path = mask_study.save_trace(mask_study.labelled(recorded(), label, 'Untrained base Glimmer, no adapter.', 200), 'x.json')
    trace = load_trace(path)
    assert trace['source']['label'] == label and trace['selection']['selected_from_sentences'] == 200
    assert trace['source']['kind'] == 'recorded'


def test_store_resumes_and_drops_a_torn_line(tmp_path):
    path = tmp_path / 'arm.jsonl'
    path.write_text(json.dumps({'id': 'a', 'trace': {}}) + '\n{"id": "b", "tr')
    seen = []

    def record(row):
        seen.append(row['id'])
        if row['id'] == 'c':
            raise ValueError('text differs')
        return {}

    done = mask_study.run_rows([{'id': 'a'}, {'id': 'b'}, {'id': 'c'}], path, record, wait=lambda: None)
    assert seen == ['b', 'c'] and 'text differs' in done['c']['error']
    assert set(mask_study.load_store(path)) == {'a', 'b', 'c'}
    assert mask_study.run_rows([{'id': 'c'}], path, record, wait=lambda: None)['c']['error']
    assert seen == ['b', 'c', 'c']  # a stored error is not done


def test_a_run_of_errors_stops_the_arm(tmp_path):
    def broken(row):
        raise ValueError('bfloat16')

    rows = [{'id': str(i)} for i in range(20)]
    with pytest.raises(SystemExit):
        mask_study.run_rows(rows, tmp_path / 'arm.jsonl', broken, wait=lambda: None)
    assert len(mask_study.load_store(tmp_path / 'arm.jsonl')) == mask_study.ERROR_RUN


def test_rows_are_the_review_counts_and_are_unedited():
    sample, x4 = mask_study.study_rows()
    valid = {row['id']: row for row in mask_study.read_jsonl(mask_study.DATA / 'valid.jsonl')}
    assert len(sample) == 200 and len({row['id'] for row in sample}) == 200
    assert all(valid[row['id']] == row for row in sample)
    assert sum(map(mask_study.is_absent_bay, x4)) == 28 and sum(map(mask_study.is_unsupported, x4)) == 84
    assert 'sealed' not in str(mask_study.SAMPLE).lower()


def test_x4_splits_mask_from_model():
    row = {'context': {'bays': [3, 7, 11]}}
    masked = recorded()
    assert mask_study.x4_outcome(row, masked) == 'mask_real_bay'
    assert mask_study.x4_outcome(row, recorded(wanted=False)) == 'model_real_bay'
    refused = {'complete': True, 'output': json.dumps({'refusal': {'code': 'unsupported'}}), 'steps': []}
    assert mask_study.x4_outcome(row, refused) == 'refused'
    other = {'complete': True, 'output': json.dumps({'constraint': {'type': 'one_port_per_hatch'}}), 'steps': []}
    assert mask_study.x4_outcome(row, other) == 'other_rule'
    assert mask_study.x4_outcome(row, {'complete': False}) == 'unfinished'
    table = mask_study.x4_table([{**row, 'id': 'a', 'sentence': 's'}, {**row, 'id': 'b', 'sentence': 's'}],
                                {'a': {'trace': masked}, 'b': {'error': 'x'}})
    assert table['outcomes'] == {'mask_real_bay': 1, 'error': 1} and len(table['mask_real_bay_examples']) == 1


def test_nested_bay_is_found():
    constraint = {'type': 'dg_keep_away', 'imdg_class': '3', 'away_from': {'kind': 'bay', 'bay': 7}}
    assert mask_study.bays_named(constraint) == [7]


def test_mask_chain_waits_then_runs_the_study(tmp_path):
    fake = tmp_path / 'fake'
    fake.write_text('#!/bin/sh\necho "$1" >> "$READER_CHAIN_DIR/steps"\n')
    fake.chmod(0o755)
    (tmp_path / 'chain.pid').write_text('999999\n')
    env = {**os.environ, 'READER_CHAIN_FOREGROUND': '1', 'READER_CHAIN_DIR': str(tmp_path), 'READER_CHAIN_RUN': str(fake)}
    done = subprocess.run(['sh', str(train.HERE / 'mask_chain.sh')], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / 'steps').read_text().split() == ['experiments.mvp.reader.v3.mask_study']
