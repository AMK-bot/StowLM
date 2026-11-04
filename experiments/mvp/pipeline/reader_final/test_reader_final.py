import json
import os
from pathlib import Path
import subprocess

import pytest

from experiments.mvp.pipeline.reader_final import hero_candidates, plan_cases, read_all, reader as module, report
from experiments.mvp.pipeline.reader_final.reader import AdoptedReader, StoredReadings, reader_label, reading_key
from experiments.mvp.reader.pipeline import ParserOutputError, ParserUnavailableError

KNOWN = {'name': 'adapter-v2', 'adapter_path': 'unused', 'adapter_sha256': 'a' * 64, 'decoder_sha256': 'd' * 64,
         'domain_words_sha256': 'e' * 64, 'decoding_policy': 'policy'}
CONTEXT = {'bays': [1, 3], 'max_tiers': {'hold': 3, 'deck': 4}, 'has_accommodation': True, 'hatch_covers': []}
GOOD = '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":3}}'


def fake(text=GOOD):
    return lambda sentence, context: (text, 'b' * 64)


def test_every_reading_carries_all_three_hashes():
    answer, evidence = AdoptedReader(fake(), KNOWN).parse('Close bay 3.', CONTEXT)
    assert answer.bay == 3
    assert (evidence['adapter_sha256'], evidence['decoder_sha256'], evidence['prompt_sha256']) == ('a' * 64, 'd' * 64, 'b' * 64)
    assert evidence['masked'] is True and evidence['raw'] == GOOD and evidence['wall_s'] >= 0
    assert reader_label(evidence, KNOWN) == module.LABEL


def test_bad_output_raises_with_evidence_and_no_fallback():
    with pytest.raises(ParserOutputError) as error:
        AdoptedReader(fake('{"bay":'), KNOWN).parse('Close bay 3.', CONTEXT)
    assert error.value.evidence['prompt_sha256'] == 'b' * 64 and error.value.evidence['raw'] == '{"bay":'


@pytest.mark.parametrize('field, value', [('adapter_sha256', 'c' * 64), ('adapter_sha256', None), ('decoder_sha256', 'c' * 64),
                                          ('prompt_sha256', None), ('masked', False), ('parser', 'SparkParser'),
                                          ('model', 'muse-spark-1.3'), ('decoding_policy', 'other')])
def test_label_needs_every_field_true(field, value):
    _, evidence = AdoptedReader(fake(), KNOWN).parse('Close bay 3.', CONTEXT)
    assert reader_label({**evidence, field: value}, KNOWN) == module.UNKNOWN
    assert reader_label(None, KNOWN) == module.UNKNOWN


def test_identity_refuses_moved_weights_or_decoder(tmp_path):
    folder = tmp_path / 'adapter'
    folder.mkdir()
    (folder / 'adapters.safetensors').write_bytes(b'weights')
    v3 = tmp_path / 'v3'
    v3.mkdir()
    (v3 / 'decoder.py').write_text('decoder')
    (v3 / 'domain_words.py').write_text('words')
    sha = module.sha256_file
    adoption = tmp_path / 'adoption.json'
    adoption.write_text(json.dumps({'adopted': {'name': 'x', 'path': str(folder), 'adapter_sha256': sha(folder / 'adapters.safetensors')}}))
    freeze = tmp_path / 'freeze.json'
    prefix = 'experiments/mvp/reader/v3/'
    freeze.write_text(json.dumps({'files_sha256': {prefix + 'decoder.py': sha(v3 / 'decoder.py'),
                                                   prefix + 'domain_words.py': sha(v3 / 'domain_words.py')}}))
    assert module.identity(adoption, freeze, v3)['name'] == 'x'
    (v3 / 'decoder.py').write_text('changed')
    with pytest.raises(ValueError):
        module.identity(adoption, freeze, v3)
    (v3 / 'decoder.py').write_text('decoder')
    (folder / 'adapters.safetensors').write_bytes(b'other')
    with pytest.raises(ValueError):
        module.identity(adoption, freeze, v3)


def test_real_adoption_identity_is_swa_last_5():
    known = module.identity()
    assert known['name'] == 'swa-last-5' and known['adapter_sha256'].startswith('eff712e1')


def stored_row(sentence=' Close bay 3.', context=CONTEXT, status='read', text=GOOD):
    return {'key': reading_key(sentence, context), 'sentence': sentence, 'status': status,
            'evidence': {'parser': 'GlimmerParser', 'adapter_sha256': 'a' * 64, 'raw': text}}


def test_stored_readings_replay_exact_sentence_and_ship_only():
    stored = StoredReadings([stored_row()])
    answer, evidence = stored.parse(' Close bay 3.', CONTEXT)
    assert answer.bay == 3 and evidence['recorded'] is True
    with pytest.raises(ParserUnavailableError):
        stored.parse('Close bay 3.', CONTEXT)
    with pytest.raises(ParserUnavailableError):
        stored.parse(' Close bay 3.', {**CONTEXT, 'bays': [1]})
    with pytest.raises(ParserOutputError):
        StoredReadings([stored_row(status='malformed')]).parse(' Close bay 3.', CONTEXT)


def test_stored_reading_passes_the_real_brief_and_confirmation_gates():
    from experiments.mvp.pipeline.binding import confirm_brief
    from experiments.mvp.reader.public_brief import VoyageBrief, read_brief
    from experiments.mvp.reader.schema import context_from_instance
    from stowage.model import Instance
    case = read_all.d211_cases()[0]
    instance = Instance.model_validate_json((module.ROOT / case['input']).read_text())
    context = context_from_instance(instance)
    hatch = '{"schema_version":"stowlm.reader/2","constraint":{"type":"one_port_per_hatch"}}'
    text = json.dumps(case['expected'])
    readings = [stored_row(case['sentence'], context, text=text), stored_row(read_all.STANDING, context, text=hatch)]
    receipt = read_brief(instance, VoyageBrief(standing=read_all.STANDING, voyage=(case['sentence'],)),
                         parser=StoredReadings(readings))
    rows = receipt.evidence()['sentences']
    assert [row['status'] for row in rows] == ['preference_enabled', 'accepted'] and rows[1]['parser']['recorded'] is True
    assert confirm_brief(receipt, plan_cases.SOURCE).source == plan_cases.SOURCE


def test_queue_resumes_and_keeps_a_refused_or_malformed_row(tmp_path):
    path = tmp_path / 'readings.jsonl'
    queue = [dict(sentence=' Close bay 3.', context=CONTEXT), dict(sentence='two', context=CONTEXT)]
    path.write_text(json.dumps(stored_row()) + '\n{"key": "torn')
    reader = AdoptedReader(fake('nonsense'), KNOWN)
    rows = read_all.read_all(queue, reader, path)
    assert len(rows) == 2 and rows[reading_key('two', CONTEXT)]['status'] == 'malformed'
    assert len(read_all.load_rows(path)) == 2
    calls = []
    read_all.read_all(queue, AdoptedReader(lambda *a: calls.append(a), KNOWN), path)
    assert not calls


def test_rules_table_is_split_into_sentences():
    table = '| Instruction | Status |\n|---|---|\n| Close bay 1.; Close bay 3. | checked |\n| base | checked |\n| Close bay 1. | x |\n'
    assert read_all.rules_sentences(table) == ['Close bay 1.', 'Close bay 3.']


def test_wrong_reading_is_never_confirmed(tmp_path):
    context = CONTEXT
    expected = json.loads(GOOD)
    assert plan_cases.same_meaning(expected, expected, context)
    other = {'schema_version': 'stowlm.reader/2', 'constraint': {'type': 'close_bay', 'bay': 1}}
    assert not plan_cases.same_meaning(other, expected, context)
    assert not plan_cases.same_meaning(None, expected, context)


def test_twelve_jobs_span_all_four_rule_types():
    jobs = plan_cases.jobs()
    assert len(jobs) == 12 and len({job['expected'].get('constraint', {}).get('type') for job in jobs}) == 4
    assert plan_cases.WORKERS == 4 and plan_cases.WALL_S == 1800


def test_stored_baseline_has_a_plan_for_every_selected_case():
    for case in read_all.d211_cases():
        assert report.stored_baseline(case)['plan']


def test_judged_marks_wrong_correct_and_unread():
    queue = [dict(group='voice-clean', case='a', sentence=' Close bay 3.', context=CONTEXT, expected=json.loads(GOOD), demo=True),
             dict(group='refusal-card', case='b', sentence='x', context=CONTEXT, expected=None, demo=True)]
    _, evidence = AdoptedReader(fake(), KNOWN).parse(' Close bay 3.', CONTEXT)
    rows = {queue[0] and reading_key(' Close bay 3.', CONTEXT): {'status': 'read', 'evidence': evidence}}
    judged = report.judged(queue, rows, KNOWN)
    assert judged[0]['correct'] is True and judged[0]['label'] == module.LABEL
    assert judged[1]['status'] == 'not_read' and report.tally(judged)['correct'] == 1


def test_chain_order_and_a_failed_gpu_step_does_not_stop_it(tmp_path):
    fake_run = tmp_path / 'fake'
    fake_run.write_text('#!/bin/sh\necho "$1" >> "$READER_FINAL_DIR/steps"\ncase "$1" in *base_arm) exit 3;; esac\n')
    fake_run.chmod(0o755)
    env = {**os.environ, 'READER_FINAL_FOREGROUND': '1', 'READER_FINAL_DIR': str(tmp_path), 'READER_FINAL_RUN': str(fake_run),
           'READER_FINAL_WAIT_PID': '999999', 'READER_FINAL_POLL_S': '0', 'READER_FINAL_SKIP_INTAKE': '1'}
    done = subprocess.run(['sh', str(Path(read_all.HERE) / 'chain.sh')], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    steps = [line.rsplit('.', 1)[1] for line in (tmp_path / 'steps').read_text().split()]
    assert steps[:2] == ['read_all', 'report'] and set(steps) >= {'plan_cases', 'retrace', 'hero_candidates', 'offline_timing', 'base_arm'}
    assert steps[-2:] == ['report', 'hero_candidates']
    gpu = [step for step in steps if step != 'plan_cases']
    assert gpu.index('hero_candidates') < gpu.index('offline_timing') < gpu.index('base_arm')


def test_borrowed_rule_must_fit_this_ship():
    rule = json.loads(GOOD)
    assert read_all.fits(rule, CONTEXT) == rule
    assert read_all.fits(rule, {**CONTEXT, 'bays': [1]}) is None
    assert read_all.fits(None, CONTEXT) is None


def candidate_file(tmp_path, rows):
    path = tmp_path / 'hero-candidates.json'
    path.write_text(json.dumps({'nested': {'candidates': rows}}))
    return path


def test_hero_candidates_keep_only_exact_bindings(monkeypatch, tmp_path):
    other = '{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":1}}'
    rule = json.loads(GOOD)
    path = candidate_file(tmp_path, [{'sentence': 'Shut bay three.', 'canonical_rules': [rule]},
                                     {'sentence': 'Shut the third.', 'canonical_rules': [rule]},
                                     {'wording': 'Seal bay three.', 'rule': rule}, {'note': 'no sentence'}])
    found = hero_candidates.find(path)
    assert [item['sentence'] for item in found] == ['Shut bay three.', 'Shut the third.', 'Seal bay three.']
    answers = {'Shut bay three.': GOOD, 'Shut the third.': other, 'Seal bay three.': '{"bad"'}

    class Reader:
        known = KNOWN

        def parse(self, sentence, context):
            return AdoptedReader(fake(answers[sentence]), KNOWN).parse(sentence, context)

    rows = hero_candidates.judge(found, Reader(), CONTEXT)
    assert [row['binds'] for row in rows] == [True, False, False]
    assert rows[1]['verdict'] == 'misread' and rows[2]['verdict'] == 'malformed'


def test_process_network_block_is_real_when_run_sandboxed():
    from experiments.mvp.pipeline.reader_final import offline_timing
    assert 'deny network' in offline_timing.SANDBOX


def test_successor_adoption_keeps_history_and_both_numbers():
    data = json.loads(module.ADOPTION.read_text())
    assert data['outcome'] == 'swa-last-5' and data['supersedes']['file'].endswith('final-adoption.json')
    assert data['sealed_128']['swa-last-5']['exact_match_count'] == 118 and data['sealed_128']['adapter-v2']['exact_match_count'] == 120
    assert data['out_of_scope_84']['adapter-v2']['made_a_rule'] == 27 and data['out_of_scope_84']['swa-last-5']['made_a_rule'] == 4


def test_an_unread_sentence_with_an_answer_counts_as_missed():
    queue = [dict(group='voice-clean', case='a', sentence='never read', context=CONTEXT, expected=json.loads(GOOD), demo=True),
             dict(group='refusal-card', case='b', sentence='no answer', context=CONTEXT, expected=None, demo=True)]
    counts = report.tally(report.judged(queue, {}, KNOWN))
    assert counts == {'sentences': 2, 'scored': 1, 'correct': 0, 'unread': 1, 'no_expected_answer': 1}
