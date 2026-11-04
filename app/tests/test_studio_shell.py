"""StowLM Studio shell routes (experiments/fleet/orders/studio/CONTRACT.md): /studio, /studio*.js|css, /studio-data/*.json.

Everything is served from app/static only, by bare allow-listed names; nothing else on disk is reachable."""
from __future__ import annotations

import json

import pytest

from app import server
from app.backend import build_replay
from app.server import make_handler, static_name
from app.tests.test_server import MemorySocket
from http.client import HTTPResponse


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    """A throwaway app/static: the routes must find files there and nowhere else."""
    root = tmp_path / "static"
    (root / "studio-data").mkdir(parents=True)
    (root / "studio.html").write_text("<!doctype html><title>StowLM Studio</title>")
    (root / "studio.js").write_text("window.Studio = {};")
    (root / "studio.css").write_text(":root { --st-bg: #0a111d; }")
    (root / "studio-voyage.js").write_text("// voyage")
    (root / "studio-intake.css").write_text("/* intake */")
    (root / "studio-data" / "evidence.json").write_text(json.dumps({"rows": [1, 2]}))
    (root / "stage.js").write_text("// stage")
    (tmp_path / "secret.json").write_text('{"secret": true}')
    (tmp_path / "studio-outside.js").write_text("// outside app/static")
    monkeypatch.setattr(server, "STATIC", root)
    return root


@pytest.fixture
def get(replay_case, static_dir):
    instance, plan = replay_case
    handler = make_handler(build_replay(instance, plan))

    def fetch(path):
        transport = MemorySocket(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        handler(transport, ("127.0.0.1", 12345), object())
        response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
        response.begin()
        return response

    return fetch


@pytest.mark.parametrize("path", ["/studio", "/studio.html", "/studio?step=intake&demo=1", "/studio?autoplay=1&speed=2"])
def test_studio_page_is_served(get, path):
    response = get(path)
    assert response.status == 200
    assert response.headers.get_content_type() == "text/html"
    assert response.headers["Cache-Control"] == "no-cache, no-store"
    assert "StowLM Studio" in response.read().decode()


@pytest.mark.parametrize("path, mime", [("/studio.js", "text/javascript"), ("/studio.css", "text/css"),
                                        ("/studio-voyage.js", "text/javascript"), ("/studio-intake.css", "text/css")])
def test_studio_assets_are_served(get, path, mime):
    response = get(path)
    assert response.status == 200
    assert response.headers.get_content_type() == mime


def test_studio_data_json_is_served(get):
    response = get("/studio-data/evidence.json")
    assert response.status == 200
    assert response.headers.get_content_type() == "application/json"
    assert json.load(response) == {"rows": [1, 2]}


def test_missing_studio_module_is_a_clean_404(get):
    for path in ("/studio-copilot.js", "/studio-data/impact.json"):
        response = get(path)
        assert response.status == 404
        assert response.headers.get_content_type() == "application/json"


@pytest.mark.parametrize("path", [
    "/studio-data/../secret.json", "/studio-data/%2e%2e/secret.json", "/studio-data/..%2fsecret.json",
    "/studio-data/evidence.json/", "/studio-data/sub/evidence.json", "/studio-data/.hidden.json",
    "/studio-data/evidence.js", "/studio-data/Evidence.json", "/studio-data/",
    "/../studio-outside.js", "/%2e%2e/studio-outside.js", "/studio-outside.js/..", "/studio/../secret.json",
    "/studio.min.js", "/studio-a.b.js", "/studio-.js", "/studio_x.js", "/studio-voyage.js.map", "/studio.json",
    "/studio-data", "/studio-x.html", "/studio-data/evidence.json%00.js",
])
def test_nothing_outside_the_allow_list_is_served(get, path):
    response = get(path)
    assert response.status in {400, 404}
    body = response.read().decode()
    assert "secret" not in body and "outside app/static" not in body


def test_static_name_never_leaves_static(static_dir):
    assert static_name("/studio") == "studio.html"
    assert static_name("/studio.html") == "studio.html"
    assert static_name("/studio-voyage.js") == "studio-voyage.js"
    assert static_name("/studio-data/evidence.json") == "studio-data/evidence.json"
    assert static_name("/stage.js") == "stage.js"  # the stage assets keep their own rule
    for route in ("/studio-data/../secret.json", "/studio-outside.js", "/studio-data/missing.json", "/studio-impact.js"):
        assert static_name(route) is None
    for name in ("studio.html", "studio-voyage.js", "studio-data/evidence.json"):
        resolved = (static_dir / static_name("/" + name if name != "studio.html" else "/studio")).resolve()
        assert resolved.is_relative_to(static_dir.resolve())


def test_pacing_generator_script_is_not_served(get):
    for path in ("/make_studio_pacing.py", "/studio-pacing.py"):
        assert get(path).status == 404
