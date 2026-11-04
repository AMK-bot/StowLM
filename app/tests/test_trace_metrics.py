"""Decode evidence is replayed; aggregate mask statistics must cover all samples."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.tests.test_counter_totals import seven_moves
from app.tests.test_renderer import render_page


PIPELINE = Path('experiments/sft-pilot/pipeline-test/c1-loop')


def test_real_pipeline_trace_accepts_structured_actions_and_terminal_counts():
    from app.model_runs import read_trace
    trace = read_trace(PIPELINE / 'instances/v3.random_yard.tiny.110280000/trace.jsonl')
    assert trace[0]['chosen']['action_id']['op'] == 'P'
    assert trace[-1]['chosen']['action_id'] is None
    assert trace[-1]['n_actions'] is None


def test_aggregate_override_uses_all_sample_metrics_even_after_repair():
    from app.model_runs import mask_override_summary
    stats = {'mask_override_rate_decision': .25, 'mask_override_rate_all_tokens': .125,
             'mask_override_decision_positions': 40, 'mask_override_token_positions': 160,
             'mask_override_scope': 'all samples, batched sampling pass; first token of box/bay/row/tier'}
    record = {'metrics': {'repair_original_metrics': stats}}
    result = mask_override_summary({'decoding': {'samples_per_instance': 8}}, record, [])
    assert result['available'] is True and result['decision_rate'] == .25
    assert result['decision_positions'] == 40 and result['token_positions'] == 160
    assert result['samples'] == 8 and result['source'] == 'result.metrics.repair_original_metrics'


@pytest.mark.parametrize('stats', [
    {'mask_override_rate_decision': .9, 'mask_override_scope': 'selected sample'},
    {'mask_override_rate_decision': 2., 'mask_override_scope': 'all samples'},
    {},
])
def test_selected_or_invalid_statistics_never_become_all_sample_rate(stats):
    from app.model_runs import mask_override_summary
    assert mask_override_summary({}, {'metrics': stats}, [])['available'] is False


def test_manifest_override_statistics_can_supply_the_all_sample_rate():
    from app.model_runs import mask_override_summary
    stats = {'mask_override_rate_decision': .5, 'mask_override_decision_positions': 8,
             'mask_override_scope': 'all samples; box/bay/row/tier'}
    assert mask_override_summary({'mask_override': stats}, {}, [])['decision_rate'] == .5


def test_current_c1_c2_on_and_in_thread_pool_manifest_imports():
    from app.model_runs import validate_manifest
    manifest = json.loads(Path('app/tests/fixtures/model_run/manifest.json').read_text())
    manifest['decoding'].update(c2='on', pool_size=0)
    validated = validate_manifest(manifest)
    assert validated['decoding']['c2'] is True
    assert validated['decoding']['pool_size'] == 0


def test_trace_caption_physical_denominator_and_all_sample_rate_render():
    replay = deepcopy(seven_moves())
    replay['metadata']['model_run'] = {'verified': True,
        'label': 'StowLM (diagnostic adapter, not evidence)', 'adapter_clean': False,
        'mask_override': {'available': True, 'decision_rate': .25, 'token_rate': .125,
                          'decision_positions': 40, 'token_positions': 160, 'samples': 8,
                          'decision_scope': 'box/bay/row/tier'},
        'trace': [{'step': 0, 'n_legal': 2, 'n_actions': 6,
                   'n_actions_scope': 'physically possible Place(box, slot)',
                   'chosen': {'action_id': {'op': 'P'}, 'text': 'P000 0010202'},
                   'top_k': [{'text': '', 'legal': False, 'logprob': -1.}]}]}
    page = render_page(replay)
    assert 'selected sample, replayed' in page['decode-trace-caption']['text']
    assert '2 / 6 legal / physically possible actions' in page['decode-trace']['html']
    assert 'all samples' in page['mask-override']['text']
    assert '25%' in page['mask-override']['text'] and '40' in page['mask-override']['text']
    assert '12.5%' in page['mask-override']['text'] and '160' in page['mask-override']['text']


def test_historical_run_without_aggregate_rate_says_unavailable():
    replay = seven_moves()
    replay['metadata']['model_run'] = {'verified': True, 'label': 'StowLM (diagnostic adapter, not evidence)',
                                      'trace': []}
    page = render_page(replay)
    assert 'all samples' in page['mask-override']['text']
    assert 'unavailable' in page['mask-override']['text'].lower()


def test_real_failed_pipeline_run_renders_diagnostic_without_placeholder():
    from app.inputs import load_inputs
    from app.flow import DemoService
    loaded = load_inputs(run_folder=PIPELINE, entry='instances/v3.random_yard.tiny.110280000/result.json')
    service = DemoService(loaded.instance, loaded.plan, loaded.metadata, discover=False)
    payload = service.replay(service.active_plan_id)
    assert payload['plan'] is None and payload['frames'] == []
    page = render_page(payload)
    assert page['model-run-state']['text'] == 'StowLM (diagnostic adapter, not evidence)'
    assert page['no-plan-state']['hidden'] is False
    assert 'error' in page['no-plan-state']['text'].lower()
    assert page['operations-view']['hidden'] is True
    assert page['metrics']['hidden'] is True
    assert page['playback-panel']['hidden'] is True
    assert 'P000 0010202' in page['decode-trace']['html']
