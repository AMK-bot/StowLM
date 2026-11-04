"""CPU-only scripted logits: these tests make no model or network calls."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.mvp.pipeline.contract.schema import Mode
from experiments.mvp.reader.v3.decoder import ReaderDecoder, ReaderLogitsProcessor
from .fixture import CONTEXT, PREFIX, SCRIPT, SENTENCE, STOP, TOKENS, ScriptedTokenizer, make_fixture, scripted_logits
from .recorder import RecordingLogitsProcessor
from .trace import load_trace, seal_trace, validate_trace


def make_processor():
    return ReaderLogitsProcessor(ReaderDecoder(ScriptedTokenizer(), CONTEXT), 2, STOP)


@pytest.mark.parametrize("batch", [False, True])
def test_recording_preserves_exact_logits_and_greedy_output(batch):
    plain = make_processor()
    wrapped = RecordingLogitsProcessor(make_processor(), sentence=SENTENCE, top_k=2, fake=True)
    history = [42, 43]
    for index in range(len(SCRIPT)):
        raw = scripted_logits(index, batch=batch)
        untouched = raw.copy()
        expected = plain(np.array(history), raw)
        actual = wrapped(np.array(history), raw)
        np.testing.assert_array_equal(raw, untouched)
        np.testing.assert_array_equal(actual, expected)
        assert actual.dtype == raw.dtype and actual.shape == raw.shape
        # A repeated callback cannot commit a token or change its mask.
        np.testing.assert_array_equal(wrapped(np.array(history), raw), expected)
        chosen = int(np.argmax(actual))
        assert chosen == SCRIPT[index]
        history.append(chosen)
    # Last EOS never reached another processor callback: supply the actual IDs.
    output = ''.join(TOKENS[t] for t in SCRIPT if t != STOP)
    trace = wrapped.finish(output, final_token_ids=history[2:])
    assert [step["chosen"]["token_id"] for step in trace["steps"]] == SCRIPT
    assert trace["output"] == output == plain.validate_output(output)
    assert trace["complete"] is True
    assert trace["stage_record"]["mode"] == Mode.generated.value
    # Top-k=2 excludes chosen bay 7; its full-vocabulary probability is retained.
    bay = trace["steps"][4]
    assert bay["chosen"]["token_id"] not in [c["token_id"] for c in bay["top_k"]]
    assert sum(c["probability"] for c in bay["top_k"]) < 1.
    scores = scripted_logits(4).astype(np.float64)
    probability = np.exp(scores - scores.max())
    probability /= probability.sum()
    assert bay["chosen"]["probability"] == pytest.approx(probability[4])


@pytest.mark.parametrize("limit", [3, len(SCRIPT)])
def test_mlx_lookahead_records_only_returned_sequence(limit):
    recorder = RecordingLogitsProcessor(make_processor(), sentence=SENTENCE, fake=True)
    history = [42, 43]
    # MLX prepares next logits before yielding the preceding sampled token.
    for index in range(limit):
        logits = recorder(np.array(history), scripted_logits(index))
        history.append(int(np.argmax(logits)))
    # Callback consumes final sample then computes an extra, unselected snapshot.
    recorder(np.array(history), scripted_logits(min(limit, len(SCRIPT) - 1)))
    selected = history[2:]
    output = ''.join(TOKENS[t] for t in selected if t != STOP)
    trace = recorder.finish(output, final_token_ids=selected, require_complete=False)
    assert len(trace["steps"]) == limit
    assert trace["complete"] is (limit == len(SCRIPT))
    assert [s["chosen"]["token_id"] for s in trace["steps"]] == selected


def test_last_sample_is_never_guessed_and_bad_output_is_not_repaired():
    recorder = RecordingLogitsProcessor(make_processor(), sentence=SENTENCE, fake=True)
    recorder(np.array([42, 43]), scripted_logits(0))
    with pytest.raises(ValueError, match="final sampled token is missing"):
        recorder.finish("")
    with pytest.raises(ValueError, match="blocked"):
        recorder.observe_token(12)
    recorder.observe_token(0)
    with pytest.raises(ValueError, match="output differs"):
        recorder.finish(PREFIX + " ", require_complete=False)
    trace = recorder.finish(PREFIX, require_complete=False)
    assert trace["complete"] is False
    assert len(trace["steps"]) == 1


class VocabularyTokenizer:
    def __init__(self, tokens):
        self.tokens = tokens

    def to_str(self):
        return json.dumps({"model": {"type": "BPE", "vocab": {t: i for i, t in enumerate(self.tokens)}},
                           "decoder": {"type": "ByteLevel"},
                           "added_tokens": [{"id": len(self.tokens) - 1, "content": "<eos>"}]})


def reasons_after(prefix, candidates, *, context=None):
    tokens = [prefix, *candidates, "<eos>"]
    tokenizer = VocabularyTokenizer(tokens)
    processor = ReaderLogitsProcessor(ReaderDecoder(tokenizer, context or CONTEXT), 0, len(tokens) - 1)
    recorder = RecordingLogitsProcessor(processor, sentence="Synthetic test.", fake=True, top_k=len(tokens))
    logits = np.arange(len(tokens), dtype=np.float32)
    recorder(np.array([], dtype=np.int64), logits)
    recorder.observe_token(0)
    recorder(np.array([0]), logits)
    return {c["token"]: c["reason"] for c in recorder._pending["top_k"]}


def test_entire_bpe_tokens_classified_by_counterfactual_grammar():
    close = PREFIX + '{"type":"close_bay","bay":'
    reasons = reasons_after(close, ['99}}', '7}}', '99x', '0}}', '9999}}'])
    assert reasons['99}}'] == "bay_not_on_ship"
    assert reasons['7}}'] is None
    assert reasons['99x'] == reasons['0}}'] == reasons['9999}}'] == "not_in_grammar"
    tier = PREFIX + '{"type":"max_tiers","bay":7,"zone":"deck","max_tiers":'
    reasons = reasons_after(tier, ['4}}', '12}}', '13}}', '3}}', '4x'])
    assert reasons['4}}'] == reasons['12}}'] == "tier_over_cap"
    assert reasons['3}}'] is None
    assert reasons['13}}'] == reasons['4x'] == "not_in_grammar"


def test_tier_reason_uses_bay_specific_cap_and_compound_failure_is_not_overclaimed():
    context = deepcopy(CONTEXT)
    context['max_tiers_by_bay'] = {'7': {'deck': 1}}
    tier = PREFIX + '{"type":"max_tiers","bay":7,"zone":"deck","max_tiers":'
    assert reasons_after(tier, ['2}}', '1}}'], context=context)['2}}'] == "tier_over_cap"
    before_bay = PREFIX + '{"type":"max_tiers","bay":'
    candidates = ['99,"zone":"deck","max_tiers":9}}', '99,"zone":"deck","max_tiers":2}}',
                  '7,"zone":"deck","max_tiers":2}}']
    reasons = reasons_after(before_bay, candidates)
    assert reasons[candidates[0]] == "not_in_grammar"
    assert reasons[candidates[1]] == "bay_not_on_ship"


def test_fixture_is_fake_serializable_and_has_illegal_bay_wish():
    trace = make_fixture()
    assert validate_trace(trace) == trace
    assert trace['source']['kind'] == 'fake' and 'FAKE' in trace['source']['label']
    assert trace['stage_record']['model'] == 'FAKE-scripted-logits'
    wish = trace['steps'][4]['top_k'][0]
    assert wish['token'] == '99}}' and wish['reason'] == 'bay_not_on_ship' and wish['blocked']
    assert trace['steps'][4]['chosen']['token'] == '7'
    assert json.loads(trace['output'])['constraint'] == {'type': 'close_bay', 'bay': 7}
    fixture = Path(__file__).parent / 'fixtures/fake-illegal-bay.json'
    assert load_trace(fixture)['output'] == trace['output']
    javascript = fixture.with_suffix('.js').read_text()
    assert javascript.startswith('window.XRAY_FIXTURE = ') and 'FAKE' in javascript
    assert json.loads(javascript.removeprefix('window.XRAY_FIXTURE = ').rstrip(';\n')) == load_trace(fixture)


def test_trace_validation_detects_edits_and_malformed_steps():
    trace = make_fixture()
    trace['sentence'] = 'Edited.'
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_trace(trace)
    trace = make_fixture()
    trace['steps'][0]['json_so_far'] += 'x'
    with pytest.raises(ValueError, match='prefixes'):
        validate_trace(trace)
    trace = make_fixture()
    trace['source']['label'] = 'Recorded answer'
    with pytest.raises(ValueError, match='FAKE'):
        validate_trace(trace)


def test_wrapper_rejects_reuse_and_wrong_final_ids():
    recorder = RecordingLogitsProcessor(make_processor(), sentence=SENTENCE, fake=True)
    recorder(np.array([42, 43]), scripted_logits(0))
    with pytest.raises(ValueError, match='blocked'):
        recorder.finish(PREFIX, final_token_ids=[12], require_complete=False)
    recorder.observe_token(0)
    with pytest.raises(ValueError, match='differs'):
        recorder.finish(PREFIX, final_token_ids=[1], require_complete=False)
    recorder.finish(PREFIX, final_token_ids=[0], require_complete=False)
    with pytest.raises(ValueError, match='finished'):
        recorder(np.array([42, 43]), scripted_logits(0))


def test_rehashed_invalid_complete_trace_is_rejected():
    trace = make_fixture()
    trace['complete'] = False
    trace = seal_trace(trace)
    with pytest.raises(ValueError, match='completion flag'):
        validate_trace(trace)
    trace = make_fixture()
    trace['steps'][0]['chosen']['probability'] /= 2
    with pytest.raises(ValueError, match='ranked candidate'):
        validate_trace(trace)


def test_relabelled_fake_and_stage_provenance_are_rejected():
    trace = make_fixture()
    trace['source'] = {'kind': 'recorded', 'label': 'Recorded answer', 'synthetic': True}
    with pytest.raises(ValueError, match='stage mode'):
        validate_trace(trace)
    trace['stage_record']['mode'] = 'recorded'
    with pytest.raises(ValueError, match='model provenance'):
        validate_trace(seal_trace(trace))
    trace = make_fixture()
    trace['stage_record']['wall_s'] += 1
    with pytest.raises(ValueError, match='hash mismatch'):
        validate_trace(trace)


def test_canonical_payload_preserves_python_number_and_unicode_bytes():
    trace = make_fixture()
    trace['numeric_probe'] = [0.0, 1e-9, 1e20]
    trace['unicode_probe'] = 'ท่าเรือ 🚢'
    trace = seal_trace(trace)
    assert '[0.0,1e-09,1e+20]' in trace['payload_json']
    assert 'ท่าเรือ 🚢' in trace['payload_json']
    assert validate_trace(trace) == trace
