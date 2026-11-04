"""Local-only, dependency-free HTTP transport for engine replay and demo flow."""
from __future__ import annotations

import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from stowage.model import Instance, MovePlan, PlannerResult
from app.backend import build_replay, demo_instance, make_demo_plan
from app.flow import ConflictError, DemoService, SOURCE_KEYS
from app.v6_fixtures import Withheld

STATIC = Path(__file__).with_name("static")
# Stage-page assets the frontend may split into several files: stage.js, stage-scene.js, stage-yard.css, ...
# Only a bare file name matching this pattern AND existing directly in app/static is served (no separators, no dots
# beyond the extension, so no traversal).
STAGE_ASSET = re.compile(r"stage(-[a-z0-9]+)?\.(js|css)")
FIXED_STATIC = {"/", "/index.html", "/style.css", "/app.js", "/styles.css", "/flow.js", "/stage", "/stage.html", "/studio", "/studio.html", "/planner", "/planner.html"}
# StowLM Studio (experiments/fleet/orders/studio/CONTRACT.md): studio.js, studio-intake.css, ... and the generated
# /studio-data/<name>.json files. Same rule as STAGE_ASSET: a bare name, directly in its folder, or nothing.
STUDIO_ASSET = re.compile(r"studio(-[a-z0-9]+)?\.(js|css)")
STUDIO_DATA = re.compile(r"/studio-data/([a-z0-9][a-z0-9_-]*\.json)")
# StowLM Planner (experiments/fleet/orders/studio/PLANNER.md): planner.js, planner-bayplan.js, ... and /planner-data/<name>.json.
PLANNER_ASSET = re.compile(r"planner(-[a-z0-9]+){0,3}\.(js|css)")
PLANNER_DATA = re.compile(r"/planner-data/([a-z0-9][a-z0-9_-]*\.json)")


class HTTPServer(ThreadingHTTPServer):
    """One daemon thread per request: a full check on /api/stage must not stall the page's other fetches.

    Shared state stays behind the services' own locks (DemoService, StageService, BriefService). DemoService builds one
    plan's replay once however many requests ask; StageService still runs its slow work on one background worker.
    """
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):  # a page that went away isn't an error
            super().handle_error(request, client_address)


def static_name(route: str) -> str | None:
    """Map a request route to a file name in ``app/static`` or None (never a path outside it)."""
    if route in FIXED_STATIC:
        return {"/": "index.html", "/stage": "stage.html", "/studio": "studio.html", "/planner": "planner.html"}.get(route, route[1:])
    for pattern, folder in ((STUDIO_DATA, "studio-data"), (PLANNER_DATA, "planner-data")):
        data = pattern.fullmatch(route)
        if data:
            path = STATIC / folder / data.group(1)
            return f"{folder}/{data.group(1)}" if path.parent == STATIC / folder and path.is_file() else None
    if route.startswith("/") and (STAGE_ASSET.fullmatch(route[1:]) or STUDIO_ASSET.fullmatch(route[1:]) or PLANNER_ASSET.fullmatch(route[1:])):
        name = route[1:]
        path = STATIC / name
        if path.parent == STATIC and path.is_file():
            return name
    return None


def json_bytes(value) -> bytes:
    """Preserve unbounded checker diagnostics as strings, like eval records.

    Infinity is meaningful for some unbounded margins but is not a JSON number.
    Never replace it with zero or turn a serialization failure into approval.
    """
    def safe(item):
        if isinstance(item, float) and not math.isfinite(item):
            return 'NaN' if math.isnan(item) else 'Infinity' if item > 0 else '-Infinity'
        if isinstance(item, dict):
            return {key: safe(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [safe(val) for val in item]
        return item
    return json.dumps(safe(value), ensure_ascii=False, allow_nan=False).encode('utf-8')


def make_handler(payload: dict, service: DemoService | None = None, *,
                 test_authority: str | None = None,
                 stage_options: dict | None = None,
                 functional=None) -> type[BaseHTTPRequestHandler]:
    """Bind playback and optional service; accept registry ids, never file paths.

    ``test_authority`` is an explicit in-process transport seam, never a CLI or
    request setting. Listening servers accept only loopback and their own port.
    """
    encoded = json_bytes(payload)
    stage_options = dict(stage_options or {})
    stage_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        demo_service = service
        expected_port = 80
        _stage = None
        functional_app = functional

        def parse_request(self):
            if not super().parse_request():
                return False
            hosts = self.headers.get_all('Host', [])
            valid = False
            if len(hosts) == 1:
                authority = hosts[0]
                if test_authority is not None and authority == test_authority:
                    valid = True
                else:
                    try:
                        parsed = urlsplit('http://' + authority)
                        # Exact authority reconstruction also rejects userinfo,
                        # empty ports, whitespace, paths, queries and fragments.
                        hostname = parsed.hostname
                        spelling = f'[{hostname}]' if hostname == '::1' else hostname
                        port = parsed.port if parsed.port is not None else 80
                        expected = spelling + (f':{parsed.port}' if parsed.port is not None else '') if spelling else ''
                        valid = (hostname in {'localhost', '127.0.0.1', '::1'} and
                                 authority.lower() == expected and port == self.expected_port)
                    except ValueError:
                        pass
            if not valid:
                self.json_response(403, {'error': 'Host must be the configured loopback authority'})
                return False
            return True

        @classmethod
        def stage_service(cls):
            """Lazily wrap the demo service for the stage page (one per handler class, however many threads ask)."""
            if cls.demo_service is None:
                return None
            with stage_lock:
                if getattr(cls, '_stage', None) is None or cls._stage.demo is not cls.demo_service:
                    from app.stage_service import StageService
                    cls._stage = StageService(cls.demo_service, **stage_options)
                return cls._stage

        def json_response(self, status, value):
            self.respond(status, json_bytes(value),
                         "application/json; charset=utf-8")

        def functional_request(self, method, route):
            app = self.functional_app
            if app is None:
                self.json_response(404, {"error": "The Functional app is off in this launch; start it with --functional"})
                return
            try:
                from app.functional import canonical_functional_path
                route = canonical_functional_path(route, decoded=True)
                if route == "/api/functional/key" and urlsplit("http://" + self.headers.get("Host", "")).hostname not in {"localhost", "127.0.0.1"}:
                    self.json_response(403, {"error": "Key setup requires a localhost or 127.0.0.1 Host"})
                    return
                body = None
                if method in {"POST", "DELETE"}:
                    if (self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"} or
                            self.headers.get("Origin") not in {None, f"http://{self.headers.get('Host')}"}):
                        self.json_response(403, {"error": "Cross-origin requests are not accepted"})
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    # Base64 adds a third to a 10 MiB upload; allow JSON metadata too.
                    if length > 14 * 1024 * 1024:
                        self.json_response(413, {"error": "Uploads must be no larger than 10 MB"})
                        return
                    if length < 0:
                        raise ValueError("Invalid request body length")
                    if length:
                        if self.headers.get_content_type() != "application/json":
                            raise ValueError("Content-Type must be application/json")
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict):
                            raise ValueError("Expected a JSON object")
                    else:
                        body = {}
                status, result = app.dispatch(method, route, body)
                if isinstance(result, Path):
                    import mimetypes
                    mime = mimetypes.guess_type(result.name)[0] or "application/octet-stream"
                    self.respond(status, result.read_bytes(), mime)
                else:
                    self.json_response(status, result)
            except (ValueError, TypeError, UnicodeError):
                self.json_response(400, {"error": "The Functional app request is invalid"})
            except OSError:
                self.json_response(404, {"error": "The requested Functional app file is unavailable"})

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            route = unquote(parsed.path)
            query = parse_qs(parsed.query)
            try:
                if route in {"/functional", "/functional.html"} or (route == "/" and self.functional_app is not None):
                    self.send_response(302)
                    self.send_header("Location", "/planner?live=1")
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                elif route.startswith("/api/functional/"):
                    self.functional_request("GET", route)
                elif route == "/api/demo":
                    if self.demo_service is not None:
                        self.json_response(200, self.demo_service.replay(self.demo_service.active_plan_id))
                    else:
                        self.respond(200, encoded, "application/json; charset=utf-8")
                elif route == "/api/health":
                    self.respond(200, b'{"status":"ok"}', "application/json")
                elif self.demo_service is not None and route == "/api/catalog":
                    self.json_response(200, self.demo_service.catalog())
                elif self.demo_service is not None and route == "/api/replay":
                    self.json_response(200, self.demo_service.replay(query["plan_id"][0]))
                elif self.demo_service is not None and route == "/api/comparison":
                    self.json_response(200, self.demo_service.comparison(query["left"][0], query["right"][0]))
                elif route == "/api/stage/showcase":  # video director: state of the three showcase calls (D158)
                    from app.stage_showcase import showcase_status
                    self.json_response(200, showcase_status())
                elif route in {"/api/stage", "/api/stage/physics", "/api/stage/explain"}:
                    stage = self.stage_service()
                    if stage is None:
                        self.json_response(404, {"error": "not found"})
                        return
                    plan_id = query.get("plan_id", [None])[0]
                    if route == "/api/stage":
                        self.json_response(200, stage.bundle(plan_id))
                    elif route == "/api/stage/physics":
                        self.json_response(200, stage.physics_status(plan_id or self.demo_service.active_plan_id))
                    else:
                        live = query.get("live", ["0"])[0] == "1"
                        self.json_response(200, stage.explain(plan_id, query.get("box", [None])[0], live=live))
                elif route.startswith("/api/instruct"):  # instruction box (app/instruct.py)
                    from app.instruct import handle_get
                    handle_get(self, route, query)
                elif route.startswith("/api/brief"):  # brief flow (app/brief.py)
                    from app.brief import handle_get as brief_get
                    brief_get(self, route, query)
                elif route.startswith("/api/v6/"):  # demo v6 flow (app/v6.py)
                    from app.v6 import handle_get as v6_get
                    v6_get(self, route, query)
                elif self.demo_service is not None and route.startswith("/api/jobs/"):
                    self.json_response(200, self.demo_service.job(route.removeprefix("/api/jobs/")))
                elif static_name(route) is not None:
                    path = STATIC / static_name(route)
                    if not path.is_file():
                        self.json_response(404, {"error": "not found"})
                        return
                    mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".json": "application/json"}[path.suffix]
                    self.respond(200, path.read_bytes(), mime + "; charset=utf-8",
                                 cache_control="no-cache, no-store")
                else:
                    self.json_response(404, {"error": "not found"})
            except Withheld as error:
                self.json_response(403, {"error": str(error), "withheld": True})
            except KeyError:
                self.json_response(404, {"error": "Unknown registry id or missing query parameter"})
            except (ValueError, OSError) as error:
                self.json_response(400, {"error": str(error)})

        def do_POST(self):
            route = unquote(urlsplit(self.path).path)
            if route.startswith("/api/functional/"):
                self.functional_request("POST", route)
                return
            if urlsplit(self.path).path.startswith("/api/brief/"):  # brief flow (app/brief.py)
                from app.brief import handle_post as brief_post
                brief_post(self)
                return
            if urlsplit(self.path).path.startswith("/api/v6/"):  # demo v6 flow (app/v6.py)
                from app.v6 import handle_post as v6_post
                v6_post(self)
                return
            if urlsplit(self.path).path != "/api/solve" or self.demo_service is None:
                self.json_response(404, {"error": "not found"})
                return
            if (getattr(self, "brief_options", None) or {}).get("v6"):  # v6 solves only through /api/v6/replan: its confirmation, its runner gates
                self.json_response(403, {"error": "Solving is disabled in the v6 demo. A live replan needs a confirmed reading."})
                return
            # Same-origin local UI only. Binding localhost alone does not stop a
            # hostile website from submitting a form to a local background job.
            if self.headers.get("Sec-Fetch-Site") not in {None, "same-origin", "none"}:
                self.json_response(403, {"error": "Cross-origin requests are not accepted"})
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.json_response(403, {"error": "Cross-origin requests are not accepted"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("JSON request body must be between 1 and 4096 bytes")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                request = json.loads(self.rfile.read(length))
                allowed = {"instance_id", "planner", "compare", "seed", "time_limit_s"}
                if not isinstance(request, dict) or set(request) - allowed or "instance_id" not in request:
                    raise ValueError("Expected instance_id and optional planner, compare, seed, time_limit_s")
                self.json_response(202, self.demo_service.start_solve(**request))
            except ConflictError as error:
                self.json_response(409, {"error": str(error)})
            except KeyError:
                self.json_response(404, {"error": "Unknown instance id"})
            except (ValueError, TypeError, UnicodeError) as error:
                self.json_response(400, {"error": str(error)})

        def do_DELETE(self):
            route = unquote(urlsplit(self.path).path)
            if route.startswith("/api/functional/"):
                self.functional_request("DELETE", route)
            else:
                self.json_response(404, {"error": "not found"})

        def respond(self, status: int, body: bytes, mime: str, *, cache_control: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:
            pass

    return Handler


def create_server(instance: Instance | None = None, plan: MovePlan | None = None,
                  *, host: str = "127.0.0.1", port: int = 8000,
                  source_metadata: dict | None = None, comparison_inputs=None,
                  discover: bool = True, stage_options: dict | None = None,
                  lite_replay: bool | None = None) -> HTTPServer:
    """Bind only IPv4 localhost. Port zero is useful for isolated API tests."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Demo server must bind localhost (127.0.0.1)")
    if instance is None:
        from app.inputs import load_inputs
        default = load_inputs()  # current-parameter default world (+ its saved plan when one exists)
        instance, plan = default.instance, plan if plan is not None else default.plan
        source_metadata = source_metadata or default.metadata
    failed_model = bool((source_metadata or {}).get('model_run') and
                        (source_metadata or {}).get('planner_result', {}).get('plan') is None)
    plan = plan if plan is not None or failed_model else make_demo_plan(instance)
    source_result = (source_metadata or {}).get("planner_result")
    result = PlannerResult.model_validate(source_result) if source_result is not None else None
    from app.stage_fast import LITE_MIN_LIFTS, build_lite_replay
    if lite_replay is None:
        lite_replay = plan is not None and len(plan.moves) > LITE_MIN_LIFTS
    if lite_replay:
        # Very large plans (full-ship hero): per-frame states are skipped; the stage page needs only moves and
        # counts. The full checker still runs once (measured 21 s on the 1 486-lift full ship). The old dashboard
        # (index.html) expects per-frame states and is not supported for such plans; use /stage.
        payload = build_lite_replay(instance, plan, planner_result=result, run_checker=True)
    else:
        payload = build_replay(instance, plan, planner_result=result)
    if source_metadata:
        payload["metadata"].update({key: value for key, value in source_metadata.items() if key in SOURCE_KEYS})
    handler = make_handler(payload) if stage_options is None else make_handler(payload, stage_options=stage_options)
    if handler is not None:
        handler.expected_port = port
        handler.demo_service = DemoService(instance, plan, source_metadata, comparison_inputs,
                                           initial_replay=payload, discover=discover)
    server = HTTPServer(("127.0.0.1", port), handler)
    if handler is not None and port == 0:
        handler.expected_port = server.server_port
    return server
