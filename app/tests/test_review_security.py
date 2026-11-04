"""Negative controls for retained imported-artifact and local-HTTP boundaries."""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from app.flow import DemoService
from app.identity import record_instance_digest
from app.server import make_handler
from app.tests.test_server import MemorySocket
from http.client import HTTPResponse
from stowage.model import PlannerResult


@pytest.mark.parametrize('change', ['status', 'world'])
@pytest.mark.parametrize('operation', ['replay', 'comparison'])
def test_cached_artifact_revalidates_source_on_every_read(replay_case, tmp_path, monkeypatch, change, operation):
    instance, plan = replay_case
    source = tmp_path / 'run' / 'result.json'
    source.parent.mkdir()
    record = {'instance': instance.model_dump(mode='json'),
              'result': PlannerResult(status='ok', plan=plan, planner='solver.scalable', planner_version='test').model_dump(mode='json')}
    source.write_text(json.dumps(record))
    # Cached evidence only isolates source invalidation; this is not a physics qualification.
    monkeypatch.setattr('app.flow.build_replay', lambda *a, **k: {'metadata': {'departure_approved': True}})
    monkeypatch.setattr('app.comparison.build_comparison', lambda *a: {'left': {}, 'right': {}})
    service = DemoService(instance, plan, instance_root=tmp_path/'absent', run_root=tmp_path)
    identity = next(p['id'] for p in service.catalog()['plans'] if p['id'].startswith('artifact-'))
    assert service.replay(identity)['metadata']['departure_approved']
    if change == 'status':
        record['result']['status'] = 'error'
        record['result']['plan']['moves'] = []
    else:
        record['instance']['provenance']['created_by'] = 'changed world'
    source.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='source changed'):
        service.replay(identity) if operation == 'replay' else service.comparison(identity, identity)


def test_cached_registered_replay_revalidates_instance_files(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    source = tmp_path / 'world.json'
    source.write_text(instance.model_dump_json())
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path/'absent',
                          initial_replay={'metadata': {'departure_approved': True}})
    assert service.replay(service.active_plan_id)['metadata']['departure_approved']
    source.write_text(source.read_text() + ' ')
    with pytest.raises(ValueError, match='source changed'):
        service.replay(service.active_plan_id)


def test_capture_serializes_mixed_case_html_script_terminators_safely(replay_case, tmp_path, monkeypatch):
    from app import capture
    instance, plan = replay_case
    marker = 'globalThis.__stowage_import_executed=true'
    instance.provenance.created_by = '</ScRiPt><SCRIPT>' + marker + '</SCRIPT>'
    source = tmp_path / 'instance.json'
    planfile = tmp_path / 'plan.json'
    source.write_text(instance.model_dump_json())
    planfile.write_text(plan.model_dump_json())
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(); self.active = False; self.bodies = []
        def handle_starttag(self, tag, attrs):
            if tag == 'script': self.active = True; self.bodies.append('')
        def handle_endtag(self, tag):
            if tag == 'script': self.active = False
        def handle_data(self, text):
            if self.active: self.bodies[-1] += text
    class StopBeforeBrowser(Exception): pass
    def inspect(argv, **kwargs):
        html = Path(unquote(urlsplit(argv[-1]).path)).read_text()
        parser = Scripts(); parser.feed(html)
        assert marker not in parser.bodies
        assert r'\u003c/ScRiPt>' in html
        raise StopBeforeBrowser
    monkeypatch.setattr(sys, 'argv', ['capture', '--chrome', '/not-launched', '--instance', str(source), '--plan', str(planfile), '--output', str(tmp_path/'out')])
    monkeypatch.setattr(capture.subprocess, 'run', inspect)
    with pytest.raises(StopBeforeBrowser): capture.main()


def request(handler, method, host, origin=None):
    headers = [f'Host: {host}', 'Content-Type: application/json']
    body = b'{"instance_id":"selected"}' if method == 'POST' else b''
    if origin is not None: headers.append(f'Origin: {origin}')
    if body: headers.append(f'Content-Length: {len(body)}')
    target = '/api/solve' if method == 'POST' else '/api/demo'
    socket = MemorySocket(('\r\n'.join([f'{method} {target} HTTP/1.0', *headers])+'\r\n\r\n').encode()+body)
    handler(socket, ('127.0.0.1', 1), object())
    response = HTTPResponse(MemorySocket(bytes(socket.outgoing))); response.begin()
    return response.status


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('host', ['evil.example', 'localhost:8001', '127.0.0.1.evil.example:8000', 'localhost@evil.example:8000'])
def test_host_validation_rejects_hostile_or_wrong_port_authorities(method, host):
    class Service:
        def start_solve(self, **kwargs): raise AssertionError('must not run')
    handler = make_handler({'private': True}, Service())
    handler.expected_port = 8000
    assert request(handler, method, host, f'http://{host}') == 403


@pytest.mark.parametrize('host', ['localhost:8000', '127.0.0.1:8000', '[::1]:8000'])
def test_host_validation_accepts_only_the_configured_loopback_port(host):
    handler = make_handler({'ok': True}); handler.expected_port = 8000
    assert request(handler, 'GET', host) == 200


@pytest.mark.parametrize('provenance', ['bad', [], {'parents': None}, {'parents': 'bad'}, {'parents': {}}, {'parents': [42]}])
def test_malformed_report_binding_metadata_is_rejected_as_value_error(provenance):
    with pytest.raises(ValueError, match='Malformed'):
        record_instance_digest({'report': {'provenance': provenance}})


@pytest.mark.parametrize('report', ['bad', []])
def test_malformed_report_object_is_rejected_as_value_error(report):
    with pytest.raises(ValueError, match='Malformed'):
        record_instance_digest({'report': report})


def test_malformed_binding_does_not_crash_discovery(replay_case, tmp_path):
    instance, plan = replay_case
    record = {'instance': instance.model_dump(mode='json'),
              'result': PlannerResult(status='ok', plan=plan, planner='imported', planner_version='test').model_dump(mode='json'),
              'report': {'provenance': 'bad'}}
    (tmp_path/'result.json').write_text(json.dumps(record))
    service = DemoService(instance, plan, instance_root=tmp_path/'absent', run_root=tmp_path)
    assert not any(p['id'].startswith('artifact-') for p in service.catalog()['plans'])
