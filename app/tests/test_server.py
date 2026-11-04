"""Exercise real HTTP parsing/serialization without sandbox-blocked socket binds."""

import io
import json
from http.client import HTTPResponse

import pytest

from app.backend import build_replay
from app.server import create_server, make_handler


def test_unbounded_checker_diagnostics_use_valid_json():
    from app.server import json_bytes
    raw = json_bytes({'passed': False, 'margins': {'unbounded': float('inf'),
                                                'undefined': float('nan')}})
    assert json.loads(raw) == {'passed': False, 'margins': {'unbounded': 'Infinity', 'undefined': 'NaN'}}


class MemorySocket:
    """Minimal socket transport retaining the standard-library HTTP machinery."""

    def __init__(self, request=b""):
        self.incoming = io.BytesIO(request)
        self.outgoing = bytearray()

    def makefile(self, mode, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.extend(data)


@pytest.fixture
def http_request(replay_case):
    instance, plan = replay_case
    payload = build_replay(instance, plan)
    handler = make_handler(payload)

    def get(path):
        transport = MemorySocket(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        handler(transport, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
        response.begin()
        return response

    return get, payload


def test_api_payload_is_complete_engine_replay(http_request):
    get, payload = http_request
    response = get("/api/demo")
    assert response.status == 200
    assert response.headers.get_content_type() == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    raw = response.read()
    assert len(raw) == int(response.headers["Content-Length"])
    assert json.loads(raw) == payload


def test_health_and_static_entrypoint(http_request):
    get, _ = http_request
    response = get("/api/health")
    assert response.status == 200
    assert json.load(response) == {"status": "ok"}
    response = get("/")
    assert response.status == 200
    assert response.headers.get_content_type() == "text/html"
    assert "html" in response.read().decode().lower()


@pytest.mark.parametrize('path', ['/', '/index.html', '/app.js', '/style.css', '/app.js?demo=recording'])
def test_static_ui_always_requires_cache_revalidation(http_request, path):
    get, _ = http_request
    response = get(path)
    assert response.status == 200
    directives = {part.strip() for part in response.headers['Cache-Control'].split(',')}
    assert 'no-cache' in directives
    assert 'no-store' in directives
    assert response.read()


@pytest.mark.parametrize("path", ["/missing", "/../README.md", "/%2e%2e/README.md", "/api/not-found"])
def test_unknown_and_nonstatic_paths_return_json_404(http_request, path):
    get, _ = http_request
    response = get(path)
    assert response.status == 404
    assert json.load(response) == {"error": "not found"}


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.1", "example.com"])
def test_server_refuses_nonlocal_bind(replay_case, host):
    instance, plan = replay_case
    with pytest.raises(ValueError, match="localhost"):
        create_server(instance, plan, host=host, port=0)


def test_server_binds_ipv4_loopback(replay_case, monkeypatch):
    instance, plan = replay_case
    recorded = []

    def server_factory(address, handler):
        recorded.append((address, handler))
        return "server"

    monkeypatch.setattr("app.server.HTTPServer", server_factory)
    assert create_server(instance, plan, host="localhost", port=8123) == "server"
    assert recorded[0][0] == ("127.0.0.1", 8123)


def test_source_labels_cannot_override_fresh_checker_verdict(replay_case, monkeypatch):
    instance, plan = replay_case
    recorded = []
    monkeypatch.setattr("app.server.make_handler", lambda payload: recorded.append(payload))
    monkeypatch.setattr("app.server.HTTPServer", lambda *_: "server")
    create_server(instance, plan, source_metadata={
        "planner": "imported.custom", "planner_version": "2",
        "checker_status": "passed", "departure_approved": True,
        "physical_replay_legal": False, "notice": "Stale report claims approval",
    })
    metadata = recorded[0]["metadata"]
    assert metadata["planner"] == "imported.custom"
    assert metadata["planner_version"] == "2"
    assert metadata["checker_status"] == "NOT VALIDATED"
    assert metadata["departure_approved"] is False
    assert metadata["physical_replay_legal"] is True
    assert metadata["notice"] != "Stale report claims approval"


def test_server_preserves_source_planner_result_for_fresh_shipping_check(replay_case, monkeypatch):
    from stowage.model import PlannerResult

    instance, plan = replay_case
    result = PlannerResult(status="timeout", plan=plan, planner="imported.custom",
                           planner_version="2", later_plans=[plan])
    payload = build_replay(instance, plan)
    calls = []

    def replay(*args, **kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setattr("app.server.build_replay", replay)
    monkeypatch.setattr("app.server.make_handler", lambda payload: None)
    monkeypatch.setattr("app.server.HTTPServer", lambda *_: "server")
    create_server(instance, plan, source_metadata={"planner_result": result.model_dump(mode="json")})
    assert calls[0].get("planner_result") == result


def test_server_forwards_explicit_catalog_isolation(replay_case, monkeypatch):
    from types import SimpleNamespace

    instance, plan = replay_case
    calls = []
    monkeypatch.setattr("app.server.make_handler", lambda *_: SimpleNamespace())
    monkeypatch.setattr("app.server.DemoService", lambda *args, **kwargs: calls.append(kwargs) or object())
    monkeypatch.setattr("app.server.HTTPServer", lambda *_: "server")
    assert create_server(instance, plan, discover=False) == "server"
    assert calls[0]["discover"] is False


@pytest.fixture
def flow_http(replay_case, tmp_path):
    from app.flow import DemoService

    instance, plan = replay_case
    payload = build_replay(instance, plan)
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    handler = make_handler(payload, service=service)

    def request(method, path, raw=b"", headers=None):
        headers = {"Host": "localhost", "Content-Type": "application/json", **(headers or {})}
        if raw:
            headers["Content-Length"] = str(len(raw))
        lines = [f"{method} {path} HTTP/1.0", *(f"{k}: {v}" for k, v in headers.items())]
        transport = MemorySocket(("\r\n".join(lines) + "\r\n\r\n").encode() + raw)
        handler(transport, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
        response.begin()
        return response

    return request, service


def test_flow_api_catalog_replay_comparison_and_unknown_ids(flow_http):
    request, service = flow_http
    response = request("GET", "/api/catalog")
    assert response.status == 200
    catalog = json.load(response)
    identity = catalog["active_plan_id"]
    assert any(p["id"] == "solver.scalable" for p in catalog["planners"])
    response = request("GET", f"/api/replay?plan_id={identity}")
    assert response.status == 200
    assert json.load(response) == service.replay(identity)
    response = request("GET", f"/api/comparison?left={identity}&right={identity}")
    assert response.status == 200
    assert json.load(response)["saved_objective"] == 0
    for path in ["/api/replay?plan_id=unknown", "/api/jobs/unknown", "/api/comparison?left=unknown"]:
        response = request("GET", path)
        assert response.status == 404
        assert "error" in json.load(response)


@pytest.mark.parametrize("raw", [b"{", b"[]", b"{}", b'{"instance_id":"x","path":"/tmp/a"}'])
def test_solve_api_rejects_malformed_or_unknown_fields(flow_http, raw):
    request, _ = flow_http
    response = request("POST", "/api/solve", raw)
    assert response.status == 400
    assert "error" in json.load(response)


def test_solve_api_accepted_and_conflict_statuses(flow_http, monkeypatch):
    from app.flow import ConflictError

    request, service = flow_http
    recorded = []

    def start(**kwargs):
        recorded.append(kwargs)
        if len(recorded) > 1:
            raise ConflictError("A live solve is already running")
        return {"id": "mock-job", "status": "queued"}

    monkeypatch.setattr(service, "start_solve", start)
    raw = json.dumps({"instance_id": "selected", "planner": "baseline.twostep", "compare": True}).encode()
    response = request("POST", "/api/solve", raw)
    assert response.status == 202
    assert json.load(response)["id"] == "mock-job"
    assert recorded[0]["compare"] is True
    response = request("POST", "/api/solve", raw)
    assert response.status == 409
