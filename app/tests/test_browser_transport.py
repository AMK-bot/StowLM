"""The no-listening-socket browser adapter uses the production HTTP handler."""
import json

from app.backend import build_replay
from app.browser_verify import InProcessHTTP
from app.flow import DemoService


def test_inprocess_browser_transport_serves_actual_page_and_bound_payload(replay_case):
    instance, plan = replay_case
    payload = build_replay(instance, plan)
    service = DemoService(instance, plan, initial_replay=payload, discover=False)
    transport = InProcessHTTP(payload, service)
    status, headers, body = transport.request('GET', '/')
    assert status == 200 and b'id="plan-compare"' in body
    assert headers['Content-Type'].startswith('text/html')
    status, _, body = transport.request('GET', '/api/demo')
    returned = json.loads(body)
    assert status == 200 and returned['checker']['bound'] is True
    assert returned['instance']['id'] == instance.id
    status, _, body = transport.request('GET', '/api/catalog')
    assert status == 200 and any(p['id'] == 'solver.exact' for p in json.loads(body)['planners'])


def test_inprocess_browser_transport_keeps_production_origin_checks(replay_case):
    instance, plan = replay_case
    payload = build_replay(instance, plan)
    transport = InProcessHTTP(payload, DemoService(instance, plan, discover=False))
    status, _, body = transport.request('POST', '/api/solve',
        b'{"instance_id":"fixture_empty_ship"}',
        {'Content-Type': 'application/json', 'Origin': 'https://unrelated.invalid'})
    assert status == 403
    assert 'Cross-origin' in json.loads(body)['error']


def test_inprocess_browser_transport_preserves_browser_lowercase_host_once(replay_case):
    instance, plan = replay_case
    transport = InProcessHTTP({}, DemoService(instance, plan, discover=False))
    status, _, _ = transport.request('GET', '/api/health', headers={'host': 'stowlm.invalid'})
    assert status == 200


def test_initial_demo_endpoint_revalidates_registered_artifact(replay_case, tmp_path):
    from app.inputs import load_inputs
    instance, plan = replay_case
    world, artifact = tmp_path / 'instance.json', tmp_path / 'plan.json'
    world.write_text(instance.model_dump_json())
    artifact.write_text(plan.model_dump_json())
    inputs = load_inputs(instance_path=world, plan_path=artifact)
    payload = build_replay(inputs.instance, inputs.plan)
    service = DemoService(inputs.instance, inputs.plan, inputs.metadata, initial_replay=payload, discover=False)
    transport = InProcessHTTP(payload, service)
    assert transport.request('GET', '/api/demo')[0] == 200
    artifact.write_text(plan.model_copy(update={'moves': []}).model_dump_json())
    assert transport.request('GET', '/api/demo')[0] == 400
