"""Saved real model diagnostics through import, HTTP JSON and production renderer.

No inference is executed. trace-smoke-b4 predates decision-token traces, so the
new evidence comes from decisions-smoke. mask-diag-smoke has a genuine dead end
but its rule_ids are empty: screen IDs must not become invented attribution.
The optional run artifacts stay untouched and are never promoted to approvals.
"""
import html
from http.client import HTTPResponse
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest

from app.flow import DemoService
from app.inputs import load_inputs
from app.server import make_handler
from app.tests.test_renderer import NODE, render_page
from app.tests.test_server import MemorySocket


pytestmark = pytest.mark.skipif(NODE is None, reason='Production renderer requires local Node')
RUNS = Path('experiments/eval-c1/runs')


def real_replay(name, instance_id):
    """Read an existing run without synthesizing manifest/result/trace contents."""
    folder = RUNS / name
    entry = f'instances/{instance_id}/result.json'
    if not (folder / entry).is_file():
        pytest.skip(f'Optional real recorded run {name} is not checked in')
    loaded = load_inputs(run_folder=folder, entry=entry)
    service = DemoService(loaded.instance, loaded.plan, loaded.metadata, discover=False)
    handler = make_handler(service.replay(service.active_plan_id), service=service)
    query = urlencode({'plan_id': service.active_plan_id})
    transport = MemorySocket(f'GET /api/replay?{query} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
    handler(transport, ('127.0.0.1', 12345), object())
    response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
    response.begin()
    assert response.status == 200
    assert response.headers.get_content_type() == 'application/json'
    assert response.headers['Cache-Control'] == 'no-store'
    replay = json.load(response)
    trace = [json.loads(line) for line in (folder / entry).with_name('trace.jsonl').read_text().splitlines() if line]
    # Round-trip every recorded decision and dead-state diagnostic through the
    # same JSON endpoint fetched by the browser, not a hand-built UI payload.
    assert replay['metadata']['model_run']['trace'] == trace
    return folder, replay, trace, render_page(replay)


def test_real_decision_trace_flow_preserves_box_slot_preferences_and_run_rates():
    folder, replay, trace, page = real_replay('decisions-smoke', 'v3.random_yard.tiny.110280001')
    model = replay['metadata']['model_run']
    markup = page['decode-trace']['html']
    action = next(row for row in trace if row.get('decisions'))
    assert [decision['field'] for decision in action['decisions']] == ['box', 'bay', 'row', 'tier']
    for decision in action['decisions']:
        assert f"{decision['field']} token · position {decision['position']}" in markup
        assert len(decision['top_k']) == 5
        for token in decision['top_k']:
            if token['legal'] is False:
                escaped = html.escape(token['text'] or '[empty token]', quote=True).replace('&#x27;', '&#39;')
                assert f'<s>{escaped}</s>' in markup
    summary = json.loads((folder / 'summary.json').read_text())
    assert model['run_rates']['fallback_rate'] == summary['fallback_rate']
    assert model['run_rates']['repair_rate'] == summary['fallback_rate']
    assert model['run_rates']['instances'] == summary['n']
    assert '100%' in page['run-reliability']['text']
    assert 'box/slot positions' in page['mask-override']['text']
    assert model['mask_override']['decision_positions'] == 84
    assert model['mask_override']['format_positions'] == 21
    assert '84 positions' in page['mask-override']['text']
    assert '21 positions' in page['format-override']['text']
    assert 'format_override' in markup
    assert replay['plan'] is None and replay['frames'] == []
    assert page['verification']['text'].startswith('NOT VALIDATED')
    assert page['no-plan-state']['hidden'] is False


def test_real_dead_end_flow_preserves_unattributed_state_and_separate_screen_rules():
    _, replay, trace, page = real_replay('mask-diag-smoke', 'v3.simulated_yard.tiny.120280002')
    dead = next(row for row in trace if row.get('event') == 'dead_end')
    markup = page['decode-trace']['html']
    assert dead['dead_end']['rule_ids'] == []
    assert dead['dead_ends_by_rule'] == {}
    assert f"dead end · depth {dead['depth']}" in markup
    assert 'rules active at the dead state: not recorded · unattributed: 1' in markup
    # This real row exposes a screen probe, not an attributed cause. Keep its
    # rule ID inside the diagnostics and preserve the unattributed count.
    for rule in dead['screens']['rejected']:
        assert rule in markup
    assert 'Screen diagnostics (recorded probes, not rejected-action counts)' in markup
    assert 'Unattributed: 4' in page['dead-end-summary']['text']
    assert replay['metadata']['model_run']['dead_ends']['unattributed'] == 4
    assert 'not recorded' in page['mask-override']['text']
    assert 'not recorded' in page['format-override']['text']


def test_real_b4_legacy_flow_labels_missing_new_fields_without_fabricated_evidence():
    _, replay, trace, page = real_replay('trace-smoke-b4', 'v3.random_yard.tiny.110280002')
    assert not any(row.get('decisions') for row in trace)
    assert not any(row.get('event') == 'dead_end' for row in trace)
    markup = page['decode-trace']['html']
    assert 'Decision tokens: not recorded' in markup
    assert 'legacy token position: not recorded' in markup
    assert 'format_override: not recorded' in markup
    for identity in ('mask-override', 'format-override', 'model-outcome', 'dead-end-summary'):
        assert 'not recorded' in page[identity]['text']
    assert '100%' in page['run-reliability']['text']
    assert 'undefined' not in markup
    assert replay['plan'] is None and page['verification']['text'].startswith('NOT VALIDATED')
