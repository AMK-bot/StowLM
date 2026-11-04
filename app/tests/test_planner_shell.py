"""StowLM Planner routes (experiments/fleet/orders/studio/PLANNER.md): /planner, /planner*.js|css, /planner-data/*.json.

Served from app/static only, by bare allow-listed names; nothing else on disk is reachable."""
from __future__ import annotations

import json
from http.client import HTTPResponse

import pytest

from app import server
from app.backend import build_replay
from app.server import make_handler, static_name
from app.tests.test_server import MemorySocket


@pytest.fixture
def static_dir(tmp_path, monkeypatch):
    root = tmp_path / "static"
    (root / "planner-data").mkdir(parents=True)
    (root / "planner.html").write_text("<!doctype html><title>StowLM Planner</title>")
    (root / "planner.js").write_text("window.Planner = {};")
    (root / "planner.css").write_text(":root { --pl-accent: #1665d8; }")
    (root / "planner-bayplan.js").write_text("window.BayPlan = {};")
    (root / "planner-intake.css").write_text("/* intake */")
    (root / "planner-data" / "ship.json").write_text(json.dumps({"bays": [1, 3]}))
    (root / "make_planner_data.py").write_text("print('builder')")
    (tmp_path / "secret.json").write_text('{"secret": true}')
    (tmp_path / "planner-outside.js").write_text("// outside app/static")
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


@pytest.mark.parametrize("path", ["/planner", "/planner.html", "/planner?step=plan&demo=1", "/planner?demo=1&autoplay=1&paced=1"])
def test_planner_page_is_served(get, path):
    response = get(path)
    assert response.status == 200
    assert response.headers.get_content_type() == "text/html"
    assert response.headers["Cache-Control"] == "no-cache, no-store"
    assert "StowLM Planner" in response.read().decode()


@pytest.mark.parametrize("path, mime", [("/planner.js", "text/javascript"), ("/planner.css", "text/css"),
                                        ("/planner-bayplan.js", "text/javascript"), ("/planner-intake.css", "text/css")])
def test_planner_assets_are_served(get, path, mime):
    response = get(path)
    assert response.status == 200
    assert response.headers.get_content_type() == mime


def test_planner_data_json_is_served(get):
    response = get("/planner-data/ship.json")
    assert response.status == 200
    assert response.headers.get_content_type() == "application/json"
    assert json.load(response) == {"bays": [1, 3]}


def test_missing_planner_files_are_clean_404s(get):
    for path in ("/planner-copilot.js", "/planner-data/moves.json"):
        response = get(path)
        assert response.status == 404
        assert response.headers.get_content_type() == "application/json"


@pytest.mark.parametrize("path", [
    "/planner-data/../secret.json", "/planner-data/%2e%2e/secret.json", "/planner-data/..%2fsecret.json",
    "/planner-data/ship.json/", "/planner-data/sub/ship.json", "/planner-data/.hidden.json", "/planner-data/ship.js",
    "/planner-data/Ship.json", "/planner-data/", "/planner-data", "/../planner-outside.js", "/%2e%2e/planner-outside.js",
    "/planner/../secret.json", "/planner.min.js", "/planner-a.b.js", "/planner-.js", "/planner_x.js", "/planner.json",
    "/planner-x.html", "/make_planner_data.py", "/planner-data/ship.json%00.js",
])
def test_nothing_outside_the_allow_list_is_served(get, path):
    response = get(path)
    assert response.status in {400, 404}
    body = response.read().decode()
    assert "secret" not in body and "outside app/static" not in body and "builder" not in body


def test_static_name_never_leaves_static(static_dir):
    assert static_name("/planner") == "planner.html"
    assert static_name("/planner-bayplan.js") == "planner-bayplan.js"
    assert static_name("/planner-data/ship.json") == "planner-data/ship.json"
    for route in ("/planner-data/../secret.json", "/planner-outside.js", "/planner-data/missing.json", "/planner-impact.js"):
        assert static_name(route) is None


def test_demo_app_label_and_functional_link_without_moving_the_header():
    """The planner is the Demo app: a visible label and a link to /functional, placed out of the header's flow."""
    real = server.Path(__file__).resolve().parents[1] / "static"
    page = (real / "planner.html").read_text()
    css = (real / "planner.css").read_text()
    assert '<span class="pl-demo-badge"' in page and ">Demo app</span>" in page
    assert "Replays a stored Engine 0.13.0 plan, computed on 2 Nov 2026 (recheck status shown with the plan), and recorded AI answers" in page
    assert '<a class="pl-demo-link" href="/functional">Open the Functional app</a>' in page
    # Existing header controls keep their ids and order.
    order = [page.index(f'id="{name}"') for name in ("pl-header", "pl-call-title", "pl-call-sub", "pl-demo-tag", "pl-nav", "pl-bell")]
    assert order == sorted(order)
    assert "position: absolute" in css.split(".pl-demo-tag {", 1)[1].split("}", 1)[0]
