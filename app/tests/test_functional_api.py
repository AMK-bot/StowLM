"""Functional API orchestration against synthetic module and process seams only."""
import base64
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from app.functional import FunctionalApp, KeyProvider, MAX_UPLOAD, _read, _write

_REAL_POPEN = subprocess.Popen


def wav_bytes(*, rate=16000, channels=1, width=2, silent=False):
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes((b"\0" if silent else b"\1") * width * channels * 160)
    return output.getvalue()


def wav_b64():
    return base64.b64encode(wav_bytes()).decode()


class FakeProcess:
    next_pid = 800000

    def __init__(self, command, **options):
        self.command, self.options = command, options
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.event = threading.Event()
        self.code = 0

    def wait(self):
        assert self.event.wait(5), 'test forgot to release its fake worker'
        return self.code


@pytest.fixture
def service(tmp_path, monkeypatch):
    available = {
        'spark': dict(available=True, who='Muse Spark', where='cloud', model='muse-spark-1.3'),
        'voice': dict(available=True, who='Muse Voice Transcribe', where='cloud', model='muse-voice-transcribe-1.0'),
        'reader': dict(available=True, who='StowLM Reader', where='this laptop', model='muse-glimmer-30b', loaded=False),
    }
    options = dict(ships=[dict(id='feeder_500')], size_classes=['tiny'], generators=['random_yard'], ports=['TEST'],
                   fill_pct=dict(min=10, max=100), envelope=dict(max_boxes=195),
                   defaults=dict(ship='feeder_500', size_class='tiny', fill_pct=60, generator='random_yard', port='TEST'))
    cases = SimpleNamespace(options=lambda: options, new_seed=lambda: 123456789,
                            seed_status=lambda seed: dict(sealed=seed == 9, unseen=seed != 9))
    class Unavailable(RuntimeError):
        def __init__(self, stage, reason):
            self.stage, self.reason = stage, reason
            super().__init__(reason)
    models = SimpleNamespace(availability=lambda **kwargs: {key: dict(value) for key, value in available.items()},
                             Unavailable=Unavailable,
                             transcribe=lambda path, **kwargs: dict(mode='live', transcript='Typed synthetic instruction', model='muse-voice-transcribe-1.0', wall_s=.01, cost_usd=0))
    models.merge_intake_views = lambda views: dict(views[0])
    models.spend_status = lambda **kwargs: dict(available=True, spent_usd=0., holds_usd=0., cap_usd=10. if kwargs.get('cap_usd') is None else kwargs['cap_usd'])
    models.configure_spend = models.spend_status
    models.grant_call = lambda **kwargs: SimpleNamespace(ledger_path=tmp_path / 'booking-ledger.jsonl', cap_usd=kwargs.get('cap_usd', 1.384448))
    models.release = lambda grant: None
    monkeypatch.setitem(sys.modules, 'app.functional_models', models)
    monkeypatch.setitem(sys.modules, 'app.functional_cases', cases)
    processes = []
    def spawn(command, **kwargs):
        process = FakeProcess(command, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr('app.functional.subprocess.Popen', spawn)
    app = FunctionalApp(root_dir=tmp_path / 'calls', key_provider=KeyProvider(environ={}))
    app.test_available, app.test_models, app.test_processes = available, models, processes
    yield app
    for job in list(app._jobs.values()):
        process = job.get('process')
        if process:
            process.event.set()
    wait_for(lambda: not any(job.get('active') for job in app._jobs.values()))


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.002)


def finish(app, identity, state='needs_papers', code=0):
    path = app.root_dir / identity
    app._state(path, state, 'Synthetic step completed.')
    process = app._jobs[identity]['process']
    process.code = code
    process.event.set()
    wait_for(lambda: not app._jobs[identity]['active'])
    return path


def new_call(app):
    status, view = app.dispatch('POST', '/api/functional/calls', {})
    assert status == 202
    return view['id']


def ready(app):
    identity = new_call(app)
    path = finish(app, identity, 'ready')
    _write(path / 'case.json', dict(boxes=1, teu=1, envelope=dict(in_envelope=True), papers=[]))
    _write(path / 'envelope.json', dict(in_envelope=True))
    _write(path / 'context.json', dict(id='synthetic-context'))
    _write(path / 'base-instance.json', dict(containers=[dict(id='cargo'), dict(id='background')], load_list=['cargo']))
    return identity, path


def test_status_and_options_are_lazy_and_complete(service):
    status, value = service.dispatch('GET', '/api/functional/status')
    assert status == 200
    assert value['on'] and value['label'] == 'Functional app'
    assert value['engine']['version'] == '0.13.0'
    assert len(value['engine']['manifest_sha256']) == 64
    assert value['engine']['call'] is None and not value['engine']['busy']
    assert service._reader is None
    assert service.dispatch('GET', 'options')[1]['new_seed'] == 123456789


def test_generation_returns_202_and_isolates_its_process(service):
    identity = new_call(service)
    view = service.dispatch('GET', 'calls/' + identity)[1]
    assert view['state'] == 'generating' and view['app'] == 'functional'
    expected = {'id', 'created_utc', 'label', 'app', 'state', 'message', 'request', 'case', 'envelope', 'runner_envelope', 'papers', 'load_list', 'voice', 'readings', 'run', 'outcome', 'attempts', 'stages', 'availability'}
    assert set(view) == expected
    assert view['case'] is view['envelope'] is view['voice'] is view['run'] is view['outcome'] is None
    process = service.test_processes[-1]
    assert process.command[:3] == ['nice', '-n', '5']
    assert process.command[-2:] == ['generate', str(service.root_dir / identity)]
    assert process.options['start_new_session'] is True
    for field in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        assert process.options['env'][field] == '1'
    assert (service.root_dir / identity / 'worker-generate.log').is_file()


@pytest.mark.parametrize('body', [[], {'seed': 9}, {'seed': True}, {'seed': -1}, {'fill_pct': True}, {'fill_pct': 101}, {'ship': '../../'}])
def test_call_validation(service, body):
    assert service.dispatch('POST', 'calls', body)[0] == 400
    assert not service.test_processes


def test_load_list_excludes_fixed_background(service):
    identity, _ = ready(service)
    assert service.dispatch('GET', 'calls/' + identity)[1]['load_list']['rows'] == [dict(id='cargo', origin='generator, not read by a model')]


def test_generated_choice_is_explicit_and_starts_assembly(service):
    identity, path = ready(service)
    _write(path / 'worker-control.json', dict(answers={'old': 1}, documents=[dict(name='old')], use_generated_papers=True,
                                           use_generator_records_for=['old']))
    status, view = service.dispatch('POST', f'calls/{identity}/papers', dict(use_generated_load_list=True))
    assert status == 202 and view['state'] == 'assembling'
    assert view['papers']['intake']['skipped']
    assert view['stages'][-1]['who'] == 'Generated load list'
    assert view['stages'][-1]['where'] == 'software' and view['stages'][-1]['model'] is None
    control = _read(path / 'worker-control.json')
    assert control['use_generated_load_list'] and 'answers' not in control and 'documents' not in control
    assert 'use_generator_records_for' not in control
    assert control['use_generated_papers'] is False
    assert 'not read by a model' in view['stages'][-1]['notes']
    assert not (path / 'context.json').exists()


def test_initial_document_intake_clears_answer_mode_and_sanitizes_name(service):
    identity, path = ready(service)
    _write(path / 'worker-control.json', dict(answers={'old': 1}, use_generated_papers=True))
    status, view = service.dispatch('POST', f'calls/{identity}/papers', dict(documents=[dict(name='../../cargo.csv', media_type='text/csv', text='synthetic,1')]))
    assert status == 202 and view['state'] == 'reading_papers'
    control = _read(path / 'worker-control.json')
    assert 'answers' not in control
    assert control['use_generated_papers'] is False
    document = control['documents'][0]
    assert Path(document['path']).is_relative_to(path / 'papers')
    assert document['name'] == 'cargo.csv'
    assert Path(document['path']).read_text() == 'synthetic,1'
    assert view['papers']['documents'][0]['origin'] == 'pasted'
    assert 'path' not in view['papers']['documents'][0]


@pytest.mark.parametrize('document', [dict(name='x', data_b64='!!bad!!'), dict(name='x'), dict(name='x', text='x', data_b64='eA==')])
def test_malformed_upload_has_no_partial_files(service, document):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/papers', dict(documents=[document]))[0] == 400
    assert not (path / 'papers').exists()


def test_upload_limit_is_aggregate_and_atomic(service, monkeypatch):
    identity, path = ready(service)
    monkeypatch.setattr('app.functional.MAX_UPLOAD', 10)
    documents = [dict(name='a', text='123456'), dict(name='b', data_b64=base64.b64encode(b'12345').decode())]
    assert service.dispatch('POST', f'calls/{identity}/papers', dict(documents=documents))[0] == 413
    assert not (path / 'papers').exists()


def test_unavailable_model_does_not_start_or_continue(service):
    identity, path = ready(service)
    service.test_available['spark'].update(available=False, reason='MODEL_API_KEY is not set')
    before = len(service.test_processes)
    status, error = service.dispatch('POST', f'calls/{identity}/papers', dict(documents=[dict(name='x', text='synthetic')]))
    assert status == 503 and 'Nothing was replayed' in error['error']
    assert len(service.test_processes) == before
    view = service.dispatch('GET', f'calls/{identity}')[1]
    assert view['stages'][-1]['status'] == 'unavailable'
    assert not (path / 'papers').exists()


def test_wrong_state_and_unknown_routes(service):
    identity = new_call(service)
    assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Synthetic'))[0] == 409
    assert service.dispatch('GET', 'calls/../../request.json')[0] == 404
    assert service.dispatch('GET', 'missing')[0] == 404
    assert service.dispatch('POST', f'calls/{identity}/missing', {})[0] == 404


def test_reader_runs_in_thread_once_and_confirmation_binds_hashes(service):
    identity, path = ready(service)
    started, release = threading.Event(), threading.Event()
    constructors = []
    class Reader:
        def __init__(self, **kwargs):
            constructors.append(kwargs)
        def read(self, sentence, context):
            started.set()
            assert release.wait(3)
            return dict(mode='live', status='constraint_ready', sentence=sentence,
                        answer=dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=1)), evidence={}, refusal=None, wall_s=.01)
    service.test_models.Reader = Reader
    status, _ = service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Synthetic instruction.', origin='typed'))
    assert status == 202 and started.wait(1)
    assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Other.'))[0] == 409
    release.set()
    wait_for(lambda: not service._jobs[identity]['active'])
    view = service.dispatch('GET', f'calls/{identity}')[1]
    row = view['readings'][0]
    binding = {key: row[key] for key in ('sentence_sha256', 'rule_sha256')}
    assert service.dispatch('POST', f'calls/{identity}/confirm', dict(reading=row['id'], **{**binding, 'rule_sha256': 'changed'}))[0] == 409
    status, confirmed = service.dispatch('POST', f'calls/{identity}/confirm', dict(reading=row['id'], **binding))
    assert status == 200 and confirmed['readings'][0]['confirmed']
    assert _read(path / 'confirmation.json')['source'] == 'functional_app_planner_click'
    assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Second.'))[0] == 202
    wait_for(lambda: not service._jobs[identity]['active'])
    assert len(constructors) == 1


def test_reader_failure_is_plain_and_full_trace_is_logged(service):
    identity, path = ready(service)
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, *args):
            raise RuntimeError('private diagnostic details')
    service.test_models.Reader = Reader
    assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Synthetic.'))[0] == 202
    wait_for(lambda: not service._jobs[identity]['active'])
    view = service.dispatch('GET', f'calls/{identity}')[1]
    assert 'private diagnostic' not in json.dumps(view)
    assert view['stages'][-1]['status'] == 'failed'
    assert 'private diagnostic details' in (path / 'worker-reader.log').read_text()


def test_engine_exclusivity_no_instruction_and_stop_own_group(service, monkeypatch):
    identity, path = ready(service)
    other, _ = ready(service)
    status, view = service.dispatch('POST', f'calls/{identity}/plan', dict(profile='live', instruction='none'))
    assert status == 202 and view['run']['status'] == 'running'
    assert view['run']['engine_deadline_s'] == 720
    assert view['run']['instruction'] == 'none' and view['run']['confirmed_reading'] is None
    assert any(stage['stage'] == 'reader' and stage['status'] == 'skipped' for stage in view['stages'])
    assert service.dispatch('POST', f'calls/{other}/plan', dict(profile='standard', instruction='none'))[0] == 409
    killed = []
    monkeypatch.setattr('app.functional.os.killpg', lambda pid, sig: killed.append((pid, sig)))
    process = service._jobs[identity]['process']
    assert service.dispatch('POST', f'calls/{identity}/stop', {})[0] == 200
    assert killed[0][0] == process.pid
    assert service.dispatch('POST', f'calls/{other}/plan', dict(instruction='none'))[0] == 409
    process.event.set()
    wait_for(lambda: service._engine_call is None)
    assert service.dispatch('POST', f'calls/{other}/plan', dict(profile='standard', instruction='none'))[0] == 202


def test_engine_requires_envelope_and_confirmation(service):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='confirmed'))[0] == 409
    _write(path / 'envelope.json', dict(in_envelope=False))
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))[0] == 409
    assert not (path / 'run').exists()


def test_plan_download_whitelist_and_symlink_escape(service, tmp_path):
    identity, path = ready(service)
    secret = tmp_path / 'secret.json'
    secret.write_text('synthetic private test data')
    papers = path / 'papers/generated'
    papers.mkdir(parents=True)
    (papers / 'cargo.csv').write_text('synthetic,1')
    (papers / 'escape.csv').symlink_to(secret)
    assert service.dispatch('GET', f'calls/{identity}/file/papers/generated/cargo.csv') == (200, papers / 'cargo.csv')
    for name in ('request.json', '../secret.json', 'papers/generated/../../../secret.json', 'papers/generated/escape.csv', 'run/package/unknown.json'):
        assert service.dispatch('GET', f'calls/{identity}/file/{name}')[0] == 404


def test_audio_returns_before_transcription_completes(service):
    identity, _ = ready(service)
    release = threading.Event()
    service.test_models.transcribe = lambda path, **kwargs: release.wait(3) and dict(mode='live', transcript='Synthetic transcript', wall_s=.01)
    status, view = service.dispatch('POST', f'calls/{identity}/audio', dict(name='test.wav', media_type='audio/wav', data_b64=wav_b64()))
    assert status == 202 and view['voice'] is None
    assert view['stages'][-1]['status'] == 'running'
    assert service.dispatch('POST', f'calls/{identity}/stop', {})[0] == 200
    assert service.dispatch('POST', f'calls/{identity}/audio', dict(name='test.wav', data_b64='eA=='))[0] == 409
    release.set()
    wait_for(lambda: not service._jobs[identity]['active'])
    assert service.dispatch('GET', f'calls/{identity}')[1]['voice'] is None
def test_functional_cli_bypasses_demo_loading(monkeypatch, tmp_path, capsys):
    import os
    from unittest.mock import patch
    # Importing the CLI sets native thread defaults for a new process. Preserve
    # this test process's checker environment, whose epoch is already loaded.
    with patch.dict(os.environ):
        from app import __main__ as launch
    from app import functional, server
    from types import SimpleNamespace
    import sys

    app = SimpleNamespace(close=lambda: closed.append("app"))
    closed, handlers = [], []
    monkeypatch.setattr(functional, "FunctionalApp", lambda **options: app)
    monkeypatch.setattr(launch, "_functional_adapter", lambda value: tmp_path)
    monkeypatch.setattr(launch, "load_inputs", lambda **options: pytest.fail("Functional-only launch loaded a demo"))

    class TestServer:
        server_port = 8794

        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 8794)
            assert handler.functional_app is app
            handlers.append(handler)

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            closed.append("server")

    monkeypatch.setattr(server, "HTTPServer", TestServer)
    monkeypatch.setattr(sys, "argv", ["app", "--functional", "--port", "8794", "--env-file", str(tmp_path / "missing.env")])
    launch.main()
    assert handlers[0].expected_port == 8794
    assert closed == ["app", "server"]
    assert "Functional app: http://127.0.0.1:8794/planner?live=1" in capsys.readouterr().out


def test_functional_cli_checks_adapter_before_model_load(tmp_path):
    import os
    from unittest.mock import patch
    with patch.dict(os.environ):
        from app.__main__ import _functional_adapter

    assert _functional_adapter(str(tmp_path)) == tmp_path.resolve()
    (tmp_path / "adapter_config.json").write_text("{}")
    (tmp_path / "adapters.safetensors").write_bytes(b"synthetic adapter for file validation")
    assert _functional_adapter(str(tmp_path)) == tmp_path.resolve()


def _http(handler, method, path, raw=b"", **headers):
    from http.client import HTTPResponse
    from app.tests.test_server import MemorySocket

    headers = {"Host": "localhost", "Content-Type": "application/json", "Content-Length": str(len(raw)), **headers}
    request = "\r\n".join([f"{method} {path} HTTP/1.0", *[f"{key}: {value}" for key, value in headers.items()]])
    transport = MemorySocket(request.encode() + b"\r\n\r\n" + raw)
    handler(transport, ("127.0.0.1", 12345), object())
    response = HTTPResponse(MemorySocket(bytes(transport.outgoing)))
    response.begin()
    return response


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_functional_http_off_has_launch_instructions(method):
    from app.server import make_handler

    response = _http(make_handler({}), method, "/api/functional/status")
    assert response.status == 404
    assert json.load(response) == {"error": "The Functional app is off in this launch; start it with --functional"}


def test_functional_http_dispatch_and_body_validation(service):
    from app.server import make_handler

    handler = make_handler({}, functional=service)
    response = _http(handler, "GET", "/api/functional/status")
    assert response.status == 200
    assert json.load(response)["on"] is True
    response = _http(handler, "POST", "/api/functional/calls", b"{}")
    assert response.status == 202
    assert json.load(response)["state"] == "generating"
    for raw in (b"{invalid", b"[]"):
        assert _http(handler, "POST", "/api/functional/calls", raw).status == 400
    assert _http(handler, "POST", "/api/functional/calls", **{"Content-Length": str(15 * 1024 * 1024)}).status == 413
    assert _http(handler, "POST", "/api/functional/calls", b"{}", Origin="https://elsewhere.invalid").status == 403


def test_functional_http_static_routes_are_allowlisted(tmp_path, monkeypatch):
    from app import server

    monkeypatch.setattr(server, "STATIC", tmp_path)
    (tmp_path / "functional.html").write_text("<!doctype html><title>Functional app</title>")
    (tmp_path / "functional-scene.js").write_text("window.synthetic = true;")
    (tmp_path / "functional.css").write_text("body { color: black; }")
    for name in ("planner.js", "planner-live-voyage.js", "planner-live-intake-details.css", "planner-a-b-c-d.js"):
        (tmp_path / name).write_text("/* synthetic asset */")
    handler = server.make_handler({})
    for path in ("/functional", "/functional.html", "/functional?step=cargo"):
        response = _http(handler, "GET", path)
        assert response.status == 302 and response.headers["Location"] == "/planner?live=1"
        assert response.read() == b""
    assert server.static_name("/functional") is None
    assert server.static_name("/functional.html") is None
    for path in ("/planner.js", "/planner-live-voyage.js", "/planner-live-intake-details.css"):
        response = _http(handler, "GET", path)
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-cache, no-store"
    for path in ("/functional-scene.js", "/functional.css", "/functional-../secret.js", "/functional-x.html",
                 "/functional.min.js", "/functional_missing.js", "/planner-a-b-c-d.js", "/planner-live/voyage.js",
                 "/planner-live.voyage.js", "/planner-live-voyage.min.js"):
        assert _http(handler, "GET", path).status == 404


@pytest.mark.parametrize('functional', [False, True])
@pytest.mark.parametrize('v6', [False, True])
def test_root_opens_live_planner_only_for_functional_launches(tmp_path, monkeypatch, functional, v6):
    from app import server
    monkeypatch.setattr(server, 'STATIC', tmp_path)
    original_index = b'<!doctype html><title>Synthetic original index</title>'
    (tmp_path / 'index.html').write_bytes(original_index)
    handler = server.make_handler({}, functional=object() if functional else None)
    handler.brief_options = {'v6': True} if v6 else {}
    response = _http(handler, 'GET', '/')
    if functional:
        assert response.status == 302
        assert response.headers['Location'] == '/planner?live=1'
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.read() == b''
    else:
        assert response.status == 200 and response.read() == original_index


def test_search_message_changes_only_after_checked_incumbent(service):
    identity, path = ready(service)
    assert service.dispatch("POST", f"calls/{identity}/plan", {"instruction": "none"})[0] == 202
    events = path / "run/events.jsonl"
    events.parent.mkdir(exist_ok=True)
    candidate = {"kind": "incumbent", "t_s": 1.5, "data": {"lifts": 12, "checked": False}}
    events.write_text(json.dumps(candidate) + "\n")
    assert service.view(path)["message"] == "Searching for a checked plan."
    candidate["data"].update(checked=True, plan_sha256="synthetic-hash", lane="synthetic-lane")
    events.write_text(json.dumps(candidate) + "\n")
    view = service.view(path)
    assert view["message"] == "First checked plan available. Continuing to improve."
    assert view["run"]["incumbents"] == [{"t_s": 1.5, "lifts": 12, "plan_sha256": "synthetic-hash", "lane": "synthetic-lane"}]


@pytest.mark.parametrize("role", ["booking", "shipping_instructions", "vgm", "dg_declaration", "gate_slip",
                                  "reefer_list", "oog_list", "amendment", "correspondence", "unspecified"])
def test_paper_host_metadata_is_persisted_shown_and_passed_to_intake(service, role, monkeypatch):
    from app import functional_worker

    identity, path = ready(service)
    metadata = dict(role=role, booking_ref="SYNTHETIC-BOOKING-1", complete_booking_list=True, synthetic=True)
    document = dict(name="form.txt", media_type="text/plain", text="SYNTHETIC DOCUMENT", **metadata)
    status, view = service.dispatch("POST", f"calls/{identity}/papers", {"documents": [document]})
    assert status == 202
    saved = _read(path / "papers/meta.json")["documents"]
    assert saved == _read(path / "worker-control.json")["documents"]
    assert {field: saved[0][field] for field in metadata} == metadata
    assert {field: view["papers"]["documents"][0][field] for field in metadata} == metadata
    seen = []
    monkeypatch.setattr(service.test_models, "read_papers", lambda docs, base, **kwargs: seen.append((docs, base)) or {"complete": True}, raising=False)
    monkeypatch.setattr(service.test_models, "answer_papers", None, raising=False)
    monkeypatch.setattr(functional_worker, "_state", lambda *args: None)
    functional_worker.intake(path, _read(path / "worker-control.json"))
    assert seen == [(saved, path / "base-instance.json")]


def test_missing_document_role_defaults_to_unspecified(service):
    identity, path = ready(service)
    status, view = service.dispatch("POST", f"calls/{identity}/papers", {"documents": [{"name": "form.txt", "text": "SYNTHETIC DOCUMENT"}]})
    assert status == 202
    metadata = _read(path / "papers/meta.json")["documents"][0]
    assert metadata["role"] == view["papers"]["documents"][0]["role"] == "unspecified"
    assert metadata["booking_ref"] is None
    assert metadata["complete_booking_list"] is False


@pytest.mark.parametrize("metadata", [{"role": "invented"}, {"role": None}, {"role": []},
                                      {"booking_ref": 123}, {"complete_booking_list": "true"},
                                      {"synthetic": 1}])
def test_invalid_paper_metadata_is_rejected_before_writing_files(service, metadata):
    identity, path = ready(service)
    document = dict(name="form.txt", text="SYNTHETIC DOCUMENT", **metadata)
    before = len(service.test_processes)
    status, _ = service.dispatch("POST", f"calls/{identity}/papers", {"documents": [document]})
    assert status == 400
    assert not (path / "papers").exists()
    assert len(service.test_processes) == before


def generated_manifest(path):
    items = []
    for role in ("booking", "reefer_list"):
        relative = f"papers/generated/{role}.txt"
        file = path / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("SYNTHETIC DOCUMENT\nNo real cargo.\n")
        items.append(dict(name=file.name, media_type="text/plain", path=relative, role=role,
                          booking_ref="SYNTHETIC-BOOKING-1", complete_booking_list=role == "reefer_list", synthetic=True))
    _write(path / "papers/generated/manifest.json", items)
    case = _read(path / "case.json")
    case["papers"] = items
    _write(path / "case.json", case)
    return items


def test_generated_papers_start_intake_with_roles_and_are_listed_once(service):
    identity, path = ready(service)
    generated_manifest(path)
    before = service.view(path)["papers"]["documents"]
    assert len(before) == 2
    assert {row["role"] for row in before} == {"booking", "reefer_list"}
    status, view = service.dispatch("POST", f"calls/{identity}/papers", {"use_generated_papers": True})
    assert status == 202 and view["state"] == "reading_papers"
    assert service.test_processes[-1].command[-2] == "intake"
    control = _read(path / "worker-control.json")
    assert control["use_generated_papers"] and control["use_generated_load_list"] is False
    assert "answers" not in control
    assert _read(path / "papers/meta.json")["documents"] == control["documents"]
    assert len(view["papers"]["documents"]) == 2
    for document in view["papers"]["documents"]:
        assert document["origin"] == "generated" and document["label"] == "Synthetic document"
        assert document["booking_ref"] == "SYNTHETIC-BOOKING-1"
        assert "path" not in document
    assert view["papers"]["intake"] is None


def test_generated_papers_cannot_continue_without_spark(service):
    identity, path = ready(service)
    generated_manifest(path)
    service.test_available["spark"].update(available=False, reason="MODEL_API_KEY is not set")
    before = len(service.test_processes)
    assert service.dispatch("POST", f"calls/{identity}/papers", {"use_generated_papers": True})[0] == 503
    assert len(service.test_processes) == before


@pytest.mark.parametrize("selection", [{"use_generated_papers": True, "use_generated_load_list": True},
                                      {"use_generated_papers": True, "documents": []}])
def test_paper_selection_must_be_unambiguous(service, selection):
    identity, _ = ready(service)
    assert service.dispatch("POST", f"calls/{identity}/papers", selection)[0] == 400


@pytest.mark.parametrize("origin, expected_origin, label", [
    ("browser", "browser", "Recorded in this browser"),
    ("upload", "upload", "Uploaded audio"),
    (None, "upload", "Uploaded audio"),
])
def test_audio_host_origin_is_retained_and_stages_identify_its_source(service, origin, expected_origin, label):
    identity, path = ready(service)
    started, release = threading.Event(), threading.Event()
    seen_paths = []

    def transcribe(audio_path, **kwargs):
        seen_paths.append(audio_path)
        started.set()
        assert release.wait(3)
        # Provider data cannot choose the host's account of where the audio came from.
        return dict(mode="live", origin="provider_supplied_origin", transcript="Synthetic instruction.",
                    model="muse-voice-transcribe-1.0", wall_s=.01, cost_usd=0)

    service.test_models.transcribe = transcribe
    body = dict(name="synthetic.wav", media_type="audio/wav", data_b64=wav_b64())
    if origin is not None:
        body["origin"] = origin
    status, initial_view = service.dispatch("POST", f"calls/{identity}/audio", body)
    assert status == 202 and started.wait(1)
    assert initial_view["voice"] is None
    running = [row for row in initial_view["stages"] if row["stage"] == "voice"][-1]
    assert running["status"] == "running" and running["notes"] == label
    release.set()
    wait_for(lambda: not service._jobs[identity]["active"])
    view = service.dispatch("GET", f"calls/{identity}")[1]
    assert _read(path / "voice.json")["origin"] == view["voice"]["origin"] == expected_origin
    assert view["voice"]["transcript"] == "Synthetic instruction."
    assert len(seen_paths) == 1 and seen_paths[0].read_bytes() == wav_bytes()
    done = [row for row in view["stages"] if row["stage"] == "voice"][-1]
    assert done["status"] == "done" and done["notes"] == label


@pytest.mark.parametrize("origin", ["typed", "voice", "", None, [], {}, 1, True])
def test_invalid_audio_origin_is_rejected_before_files_or_transcription(service, origin):
    identity, path = ready(service)
    seen = []
    service.test_models.transcribe = lambda audio_path: seen.append(audio_path)
    previous_job = service._jobs[identity]
    previous_processes = len(service.test_processes)
    status, payload = service.dispatch("POST", f"calls/{identity}/audio",
                                      dict(name="synthetic.wav", media_type="audio/wav", data_b64="c3ludGhldGlj", origin=origin))
    assert status == 400 and "error" in payload
    assert seen == [] and service._jobs[identity] is previous_job
    assert len(service.test_processes) == previous_processes
    assert not (path / "papers").exists() and not (path / "voice.json").exists()
    assert not any(row["stage"] == "voice" for row in service.view(path)["stages"])


@pytest.mark.parametrize("origin, label", [("browser", "Recorded in this browser"), ("upload", "Uploaded audio")])
def test_failed_audio_stage_retains_host_source_and_plain_failure_reason(service, origin, label):
    identity, path = ready(service)

    def transcribe(audio_path, **kwargs):
        raise RuntimeError("provider-only diagnostic detail")

    service.test_models.transcribe = transcribe
    assert service.dispatch("POST", f"calls/{identity}/audio",
                            dict(name="synthetic.wav", data_b64=wav_b64(), origin=origin))[0] == 202
    wait_for(lambda: not service._jobs[identity]["active"])
    view = service.view(path)
    failed = [row for row in view["stages"] if row["stage"] == "voice"][-1]
    assert failed["status"] == "failed"
    assert failed["notes"].startswith(label)
    assert "The voice stage failed." in failed["notes"]
    assert "provider-only diagnostic detail" not in json.dumps(view)
    assert "provider-only diagnostic detail" in (path / "worker-voice.log").read_text()
    assert view["voice"] is None


@pytest.mark.parametrize("name", ["ship", "boxes", "plan-before", "plan-after", "moves", "meters", "checks", "package", "state"])
def test_live_planner_payload_routes_import_adapter_lazily(service, monkeypatch, name):
    identity, path = ready(service)
    calls = []
    class NoPlan(LookupError):
        pass
    planner = SimpleNamespace(NoPlan=NoPlan, payload=lambda directory, item: calls.append((directory, item)) or {"name": item})
    monkeypatch.setitem(sys.modules, "app.functional_planner", planner)
    assert service.dispatch("GET", f"calls/{identity}/planner/{name}") == (200, {"name": name})
    assert calls == [(path, name)]


def test_live_planner_payload_unavailable_and_unknown_are_plain_errors(service, monkeypatch):
    identity, _ = ready(service)
    class NoPlan(LookupError):
        pass
    def payload(*args):
        raise NoPlan("No checked plan is available for this call.")
    monkeypatch.setitem(sys.modules, "app.functional_planner", SimpleNamespace(NoPlan=NoPlan, payload=payload))
    assert service.dispatch("GET", f"calls/{identity}/planner/plan-after") == (409, {"error": "No checked plan is available for this call."})
    monkeypatch.setitem(sys.modules, "app.functional_planner", None)
    for name in ("unknown", "ship/extra", "../case.json"):
        assert service.dispatch("GET", f"calls/{identity}/planner/{name}")[0] == 404


def test_live_planner_payload_and_recent_calls_use_http_dispatch(service, monkeypatch):
    from app.server import make_handler
    identity, _ = ready(service)
    monkeypatch.setitem(sys.modules, "app.functional_planner", SimpleNamespace(NoPlan=LookupError, payload=lambda *args: {"bays": []}))
    handler = make_handler({}, functional=service)
    response = _http(handler, "GET", f"/api/functional/calls/{identity}/planner/ship")
    assert response.status == 200 and json.load(response) == {"bays": []}
    response = _http(handler, "GET", "/api/functional/calls")
    assert response.status == 200 and json.load(response)["calls"][0]["id"] == identity


def test_recent_calls_survive_service_reload_without_loading_models(service):
    identity, path = ready(service)
    _write(path / "request.json", {"ship": "feeder_500"})
    _write(path / "case.json", {"ship": "feeder_800", "boxes": 8})
    _write(path / "intake.json", {"boxes": 7})
    reloaded = FunctionalApp(root_dir=service.root_dir)
    reloaded._availability = lambda: pytest.fail("Recent calls must not inspect models")
    status, payload = reloaded.dispatch("GET", "calls")
    assert status == 200
    row, = payload["calls"]
    assert row == {"id": identity, "created_utc": service.view(path)["created_utc"], "state": "ready", "ship": "feeder_800", "boxes": 7}
    assert reloaded._reader is None and reloaded._jobs == {}


def test_recent_calls_are_bounded_newest_first_and_skip_other_directories(service):
    for index in range(55):
        directory = service.root_dir / f"c20261103-1200{index:02d}-abcdef"
        _write(directory / "request.json", {"ship": "feeder_500"})
        _write(directory / "worker-state.json", {"state": "generating"})
    _write(service.root_dir / "not-a-call/request.json", {})
    (service.root_dir / "c20261103-130000-abcdef").symlink_to(service.root_dir / "c20261103-120054-abcdef")
    rows = service.dispatch("GET", "calls")[1]["calls"]
    assert len(rows) == 50
    assert [row["id"] for row in rows] == sorted([row["id"] for row in rows], reverse=True)
    assert rows[0]["id"] == "c20261103-120054-abcdef" and rows[-1]["id"] == "c20261103-120005-abcdef"
    assert rows[0]["boxes"] is None


def test_recent_calls_empty_without_creating_output_folder(service):
    assert service.dispatch("GET", "calls") == (200, {"calls": []})
    assert not service.root_dir.exists()


def test_stage_updates_replace_running_rows_preserving_instances_and_start_order(service):
    identity, path = ready(service)
    rows = [
        {"stage": "intake", "instance_id": "B1", "status": "running", "started_utc": "2026-11-03T12:00:00Z", "who": "Muse Spark B1"},
        {"stage": "intake", "instance_id": "B2", "status": "running", "started_utc": "2026-11-03T12:00:01Z", "who": "Muse Spark B2"},
        {"stage": "intake", "instance_id": "B2", "status": "done", "started_utc": "2026-11-03T12:00:01Z", "who": "Muse Spark B2", "cost_usd": .03},
        {"stage": "intake", "instance_id": "B1", "status": "failed", "started_utc": "2026-11-03T12:00:00Z", "who": "Muse Spark B1"},
        {"stage": "reader", "status": "running", "started_utc": "2026-11-03T12:00:02Z", "who": "StowLM Reader"},
        {"stage": "reader", "status": "done", "started_utc": "2026-11-03T12:00:02Z", "who": "StowLM Reader"},
        {"stage": "reader", "status": "running", "started_utc": "2026-11-03T12:00:03Z", "who": "StowLM Reader"},
        {"stage": "reader", "status": "refused", "started_utc": "2026-11-03T12:00:03Z", "who": "StowLM Reader"},
    ]
    (path / "stages.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    stages = service.view(path)["stages"]
    assert len(stages) == 4
    assert [row["status"] for row in stages] == ["failed", "done", "done", "refused"]
    assert [row.get("instance_id") for row in stages[:2]] == ["B1", "B2"]
    assert stages[1]["cost_usd"] == .03


def test_legacy_stage_finish_replaces_running_but_repeated_envelopes_remain_distinct(service):
    identity, path = ready(service)
    rows = [
        {"stage": "generate", "status": "running", "started_utc": "2026-11-03T12:00:00.001Z", "who": "generator"},
        {"stage": "envelope", "status": "done", "started_utc": "2026-11-03T12:00:01Z"},
        {"stage": "generate", "status": "done", "started_utc": "2026-11-03T12:00:00.002Z", "who": "generator"},
        {"stage": "envelope", "status": "done", "started_utc": "2026-11-03T12:00:02Z"},
    ]
    (path / "stages.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    stages = service.view(path)["stages"]
    assert [row["stage"] for row in stages] == ["generate", "envelope", "envelope"]
    assert stages[0]["status"] == "done" and stages[0]["started_utc"] == rows[0]["started_utc"]


@pytest.mark.parametrize("functional,v6", [(True, False), (True, True), (False, True)])
def test_cli_announces_live_and_demo_planner_links(functional, v6, capsys):
    import os
    from unittest.mock import patch
    with patch.dict(os.environ):
        from app.__main__ import _announce_apps
    _announce_apps(8794, functional=functional, v6=v6, label="Synthetic build label")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == int(functional) + int(v6)
    if functional:
        assert any(line.startswith("Functional app: http://127.0.0.1:8794/planner?live=1") for line in lines)
    if v6:
        assert any(line.startswith("Demo app: http://127.0.0.1:8794/planner?demo=1") for line in lines)


"""Synthetic credential checks for inclusion in app/tests/test_functional_api.py."""
import os
import stat

import pytest

from app.functional import APIError, KeyProvider, load_env_file


@pytest.fixture
def fake_key_ledger(monkeypatch):
    from app import functional_models
    entries = {}

    def status(*, launch_id, cap_usd=None):
        return {"available": True, "spent_usd": entries.setdefault(launch_id, 0.0), "cap_usd": 10. if cap_usd is None else cap_usd}

    monkeypatch.setattr(functional_models, "spend_status", status)
    monkeypatch.setattr(functional_models, "configure_spend", status)
    return entries


def test_functional_key_env_parser_is_inert_and_environment_stays_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "test.env"
    path.write_text('# example\nMODEL_API_KEY="synthetic-file-value"\nSTOWLM_ACCEPT_PAID_CALLS=1\nOTHER=$(not-executed)\n')
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    result = load_env_file(path)
    assert result == {"MODEL_API_KEY": "synthetic-file-value", "STOWLM_ACCEPT_PAID_CALLS": "1"}
    assert "MODEL_API_KEY" not in os.environ
    assert load_env_file(tmp_path / "missing.env") == {}


def test_functional_key_sources_follow_precedence_and_status_hides_values(tmp_path, fake_key_ledger):
    path = tmp_path / "test.env"
    path.write_text('MODEL_API_KEY=synthetic-file-value\nSTOWLM_ACCEPT_PAID_CALLS=1\n')
    provider = KeyProvider(env_file=path, environ={"MODEL_API_KEY": "synthetic-process-value"})
    provider.update({"key": "synthetic-entered-value", "accept_paid_calls": True, "cap_usd": 3})
    assert provider.get_key() == "synthetic-process-value"
    assert provider.status() == {"present": True, "source": "environment", "paid_calls_accepted": True, "cap_usd": 3.0, "spent_usd": 0.0}
    assert "synthetic-" not in str(provider.summary())
    file_provider = KeyProvider(env_file=path, environ={})
    assert file_provider.summary()["source"] == "env_file"
    assert file_provider.get_key() == "synthetic-file-value"
    empty_environment = KeyProvider(env_file=path, environ={"MODEL_API_KEY": ""})
    assert empty_environment.get_key() is None


def test_functional_key_requires_consent_and_clear_retains_redaction(fake_key_ledger):
    provider = KeyProvider(environ={})
    assert provider.status()["present"] is False
    provider.update({"key": "synthetic-first-value", "accept_paid_calls": False})
    assert "MODEL_API_KEY" not in provider.worker_env()
    provider.update({"key": "synthetic-next-value", "accept_paid_calls": True})
    worker = provider.worker_env()
    assert worker["MODEL_API_KEY"] == "synthetic-next-value"
    assert worker["STOWLM_FUNCTIONAL_LAUNCH_ID"] == provider.launch_id
    assert worker["STOWLM_FUNCTIONAL_CAP_USD"] == "10.0"
    fake_key_ledger[provider.launch_id] = 0.25
    assert provider.summary()["spent_usd"] == 0.25
    provider.clear()
    assert provider.get_key() is None
    assert provider.summary()["paid_calls_accepted"] is False
    assert "MODEL_API_KEY" not in provider.worker_env()
    assert provider.redact("synthetic-first-value synthetic-next-value") == "[redacted] [redacted]"


def test_functional_key_saves_only_by_choice_with_private_permissions(tmp_path, monkeypatch, fake_key_ledger):
    import app.functional as module
    monkeypatch.setattr(module, "ROOT", tmp_path)
    path = tmp_path / ".env"
    path.write_text("UNRELATED=keep\n")
    provider = KeyProvider(environ={})
    provider.update({"key": "synthetic-only-value", "accept_paid_calls": True})
    assert path.read_text() == "UNRELATED=keep\n"
    provider.update({"save_to_env_file": True})
    assert provider.saved_to_env_file is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_env_file(path)["MODEL_API_KEY"] == "synthetic-only-value"
    assert "UNRELATED=keep" in path.read_text()


@pytest.mark.parametrize("cap", [-1, float("nan"), float("inf"), True, "bad"])
def test_functional_key_rejects_invalid_caps(cap, fake_key_ledger):
    provider = KeyProvider(environ={})
    with pytest.raises((APIError, ValueError)):
        provider.update({"cap_usd": cap})


def test_key_http_routes_expose_safe_status_and_require_local_same_origin_host(service):
    import secrets
    from app.server import make_handler
    key = secrets.token_urlsafe(32)
    handler = make_handler({}, functional=service)
    body = json.dumps({"key": key, "accept_paid_calls": True, "cap_usd": 1.5}).encode()
    response = _http(handler, "POST", "/api/functional/key", body)
    data = response.read()
    assert response.status == 200 and key.encode() not in data
    assert json.loads(data)["key"] == dict(present=True, source="entered_in_app", paid_calls_accepted=True, cap_usd=1.5, spent_usd=0.)
    response = _http(handler, "GET", "/api/functional/status")
    data = response.read()
    assert response.status == 200 and key.encode() not in data
    assert json.loads(data)["key"]["present"] is True
    for method in ("POST", "DELETE"):
        assert _http(handler, method, "/api/functional/key", body if method == "POST" else b"", Host="[::1]").status == 403
        assert _http(handler, method, "/api/functional/key", body if method == "POST" else b"", Origin="https://elsewhere.invalid").status == 403
    response = _http(handler, "DELETE", "/api/functional/key")
    assert response.status == 200 and json.load(response)["key"]["present"] is False
    assert not service.root_dir.exists()


def test_worker_credentials_require_consent_and_never_enter_call_files_or_commands(service):
    import secrets
    key = secrets.token_urlsafe(32)
    assert service.dispatch("POST", "key", {"key": key, "accept_paid_calls": False})[0] == 200
    identity = new_call(service)
    first = service.test_processes[-1]
    assert "MODEL_API_KEY" not in first.options["env"]
    assert first.options["env"]["SPARK_CREDIT_READY"] == "0"
    path = finish(service, identity, "ready")
    assert service.dispatch("POST", "key", {"accept_paid_calls": True})[0] == 200
    service._start_worker(path, "assemble")
    process = service.test_processes[-1]
    assert process.options["env"]["MODEL_API_KEY"] == key
    assert process.options["env"]["SPARK_CREDIT_READY"] == "1"
    assert key not in " ".join(process.command)
    assert key not in json.dumps(service.view(path))
    for file in path.rglob("*"):
        if file.is_file():
            assert key.encode() not in file.read_bytes()
    assert service.dispatch("DELETE", "key")[0] == 200
    assert "MODEL_API_KEY" not in service.key_provider.worker_env()


def test_voice_key_is_in_provider_only_and_echoes_are_redacted_before_persistence(service):
    import secrets
    key = secrets.token_urlsafe(32)
    assert service.dispatch("POST", "key", {"key": key, "accept_paid_calls": True})[0] == 200
    identity, path = ready(service)
    providers = []
    def transcribe(audio_path, *, key_provider):
        providers.append(key_provider)
        return dict(mode="live", transcript="Synthetic provider echo " + key, wall_s=.1)
    service.test_models.transcribe = transcribe
    assert service.dispatch("POST", f"calls/{identity}/audio", dict(name="synthetic.wav", media_type="audio/wav", data_b64=wav_b64()))[0] == 202
    wait_for(lambda: not service._jobs[identity]["active"])
    assert providers == [service.key_provider]
    assert service.view(path)["voice"]["transcript"] == "Synthetic provider echo [redacted]"
    def failed(audio_path, **kwargs):
        raise RuntimeError("Synthetic provider diagnostic " + key)
    service.test_models.transcribe = failed
    assert service.dispatch("POST", f"calls/{identity}/audio", dict(name="synthetic.wav", data_b64=wav_b64()))[0] == 202
    wait_for(lambda: not service._jobs[identity]["active"])
    assert "[redacted]" in (path / "worker-voice.log").read_text()
    assert key not in json.dumps(service.view(path))
    for file in path.rglob("*"):
        if file.is_file():
            assert key.encode() not in file.read_bytes()


@pytest.mark.parametrize("document", [
    dict(name="synthetic.pdf", media_type="application/pdf", data_b64=base64.b64encode(b"synthetic PDF").decode()),
    dict(name="synthetic.txt", media_type="text/plain", data_b64=base64.b64encode(b"\xff\xfe").decode()),
    dict(name="synthetic.csv", media_type="text/csv", text=" \n "),
    dict(name="synthetic.png", media_type="image/png", text="synthetic non-image"),
])
def test_bad_papers_return_400_before_any_worker_or_file(service, document):
    identity, path = ready(service)
    count = len(service.test_processes)
    assert service.dispatch("POST", f"calls/{identity}/papers", {"documents": [document]})[0] == 400
    assert len(service.test_processes) == count
    assert not (path / "papers").exists()
    assert service.view(path)["state"] == "ready"


@pytest.mark.parametrize("media,data", [("audio/webm", b"synthetic webm"), ("audio/wav", b"broken WAV"),
                                        ("audio/wav", wav_bytes(rate=8000)), ("audio/wav", wav_bytes(channels=2)),
                                        ("audio/wav", wav_bytes(width=1)), ("audio/wav", wav_bytes(silent=True)),
                                        ("audio/wav", wav_bytes()[:-2])])
def test_bad_audio_returns_400_before_transcription_or_files(service, media, data):
    identity, path = ready(service)
    calls = []
    service.test_models.transcribe = lambda *args, **kwargs: calls.append(args)
    previous = service._jobs[identity]
    assert service.dispatch("POST", f"calls/{identity}/audio", dict(name="synthetic.wav", media_type=media, data_b64=base64.b64encode(data).decode()))[0] == 400
    assert calls == [] and service._jobs[identity] is previous
    assert not (path / "papers").exists()


def test_standard_profile_is_default_and_headline_ship_gate_prevents_planning(service):
    identity, path = ready(service)
    status, view = service.dispatch("POST", f"calls/{identity}/plan", {"instruction": "none"})
    assert status == 202 and view["run"]["profile"] == "standard" and view["run"]["engine_deadline_s"] == 1800
    assert _read(path / "worker-control.json")["profile"] == "standard"
    another, other_path = ready(service)
    case = _read(other_path / "case.json")
    case.update(ship="feeder_1700", headline_envelope={"in_envelope": False, "reasons": [{"test": "ship", "measured": "feeder_1700", "message": "legacy headline reason"}]})
    _write(other_path / "case.json", case)
    status, payload = service.dispatch("POST", f"calls/{another}/plan", {"instruction": "none"})
    assert status == 409 and "Ship 1,700 TEU; the envelope is 500–800 TEU feeders" in payload["error"]
    view = service.view(other_path)
    assert view["state"] == "out_of_envelope" and view["envelope"]["in_envelope"] is False


def test_reader_timing_stays_outside_sanitized_evidence_and_on_stage(service):
    identity, path = ready(service)
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, sentence, context):
            return dict(mode="live", status="refused", sentence=sentence, answer={}, evidence={},
                        refusal={"reason": "Synthetic unsupported instruction"}, wall_s=1.25, cold_load_s=2.5, decode="schema mask")
    service.test_models.Reader = Reader
    assert service.dispatch("POST", f"calls/{identity}/read", {"sentence": "Synthetic instruction"})[0] == 202
    wait_for(lambda: not service._jobs[identity]["active"])
    view = service.view(path)
    assert view["readings"][0]["wall_s"] == 1.25 and view["readings"][0]["cold_load_s"] == 2.5
    assert view["readings"][0]["evidence"] == {}
    row = next(row for row in view["stages"] if row["stage"] == "reader")
    assert row["wall_s"] == 1.25 and row["cold_load_s"] == 2.5


def test_missing_adapter_is_launchable_but_present_adopted_checksum_must_match(tmp_path, monkeypatch):
    import hashlib
    import os
    from unittest.mock import patch
    with patch.dict(os.environ):
        from app import __main__ as launch
    monkeypatch.setattr(launch, "__file__", str(tmp_path / "app/__main__.py"))
    adapter = tmp_path / "adapter"
    adoption = tmp_path / "experiments/mvp/reader/v3/results-final/final-adoption-d266.json"
    _write(adoption, {"adopted": {"path": str(adapter), "adapter_sha256": hashlib.sha256(b"synthetic correct weights").hexdigest()}})
    assert launch._functional_adapter(None) == adapter
    adapter.mkdir()
    (adapter / "adapters.safetensors").write_bytes(b"synthetic wrong weights")
    with pytest.raises(ValueError, match="checksum"):
        launch._functional_adapter(None)


# Append these tests to app/tests/test_functional_api.py.
import io as _shutdown_io
import os as _shutdown_os
import signal as _shutdown_signal
import subprocess as _shutdown_subprocess

_NATIVE_FUNCTIONAL_POPEN = _shutdown_subprocess.Popen


def test_stopped_thread_terminal_replaces_running_stage_and_close_joins_without_lock(service):
    identity, path = ready(service)
    release, started = threading.Event(), threading.Event()
    def delayed():
        started.set()
        assert release.wait(3)
        return dict(mode='live', transcript='discard this stopped result')
    service._start_thread(path, 'voice', delayed, {'state': 'ready'}, notes='Uploaded audio')
    assert started.wait(1)
    assert service.dispatch('POST', f'calls/{identity}/stop', {})[0] == 200
    assert service._jobs[identity]['active']
    view = service.view(path)
    assert len([row for row in view['stages'] if row['stage'] == 'voice']) == 1
    assert view['stages'][-1]['status'] == 'failed'
    assert view['stages'][-1]['notes'].startswith('Stopped by planner.')
    releaser = threading.Timer(.05, release.set)
    releaser.start()
    service.close()
    releaser.join()
    assert not service._jobs[identity]['thread'].is_alive()
    assert not service._jobs[identity]['active']
    view = service.view(path)
    assert view['voice'] is None
    assert len([row for row in view['stages'] if row['stage'] == 'voice']) == 1
    assert view['stages'][-1]['notes'] == 'Stopped by planner. The active stage has finished.'


def test_engine_reservation_outlives_leader_while_owned_group_is_alive(service, monkeypatch):
    identity, path = ready(service)
    status, _ = service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})
    assert status == 202
    job = service._jobs[identity]
    process = job['process']
    process.poll = lambda: 0 if process.event.is_set() else None
    group_alive, inspected = threading.Event(), threading.Event()
    group_alive.set()
    signals = []
    def kill_group(pid, sig):
        assert pid == process.pid
        signals.append(sig)
        inspected.set()
        if not group_alive.is_set():
            raise ProcessLookupError
    monkeypatch.setattr('app.functional.os.killpg', kill_group)
    process.event.set()
    assert inspected.wait(1)
    assert job['active'] and service._engine_call == identity
    group_alive.clear()
    wait_for(lambda: not job['active'])
    assert service._engine_call is None
    assert _shutdown_signal.SIGTERM in signals


def test_stop_finalizes_every_active_booking_stage_with_same_identity(service, monkeypatch):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/papers', {'documents': [{'name': 'synthetic.txt', 'text': 'SYNTHETIC'}]})[0] == 202
    for booking in ('A', 'B'):
        service._stage(path, 'intake', 'running', instance_id=f'booking-{booking}',
                       who=f'Muse Spark · booking {booking}', started_utc=f'2026-11-03T00:00:0{0 if booking == "A" else 1}Z')
    process = service._jobs[identity]['process']
    monkeypatch.setattr('app.functional.os.killpg', lambda *args: None)
    # Fake worker groups are deliberately synthetic; only the app's recorded PID is signalled.
    service._stop(path)
    process.event.set()
    wait_for(lambda: not service._jobs[identity]['active'])
    views = [row for row in service.view(path)['stages'] if row.get('instance_id', '').startswith('booking-')]
    assert {row['instance_id'] for row in views} == {'booking-A', 'booking-B'}
    assert len(views) == 2 and all(row['status'] == 'failed' for row in views)
    assert all(row['notes'].startswith('Stopped by planner.') for row in views)


def test_worker_environment_is_host_owned_and_log_redaction_keeps_launch_snapshot(service, monkeypatch):
    identity, path = ready(service)
    launch_key, inherited_key = 'SYNTHETIC-LAUNCH-SECRET', 'SYNTHETIC-INHERITED-SECRET'
    monkeypatch.setenv('MODEL_API_KEY', inherited_key)
    monkeypatch.setenv('SPARK_CREDIT_READY', 'inherited-should-disappear')
    monkeypatch.setenv('STOWLM_ACCEPT_PAID_CALLS', 'inherited-should-disappear')
    service.key_provider = SimpleNamespace(worker_env=lambda: {'MODEL_API_KEY': launch_key}, get_key=lambda: launch_key,
                                          redact=lambda value: value)
    old_spawn = __import__('app.functional', fromlist=['subprocess']).subprocess.Popen
    def spawn(command, **kwargs):
        process = old_spawn(command, **kwargs)
        process.stdout = _shutdown_io.BytesIO(f'worker diagnostic {launch_key}\n'.encode())
        return process
    monkeypatch.setattr('app.functional.subprocess.Popen', spawn)
    service._start_worker(path, 'assemble')
    job = service._jobs[identity]
    assert job['process'].options['env']['MODEL_API_KEY'] == launch_key
    assert 'SPARK_CREDIT_READY' not in job['process'].options['env']
    assert 'STOWLM_ACCEPT_PAID_CALLS' not in job['process'].options['env']
    job['process'].event.set()
    wait_for(lambda: not job['active'])
    text = (path / 'worker-assemble.log').read_text()
    assert launch_key not in text and '[redacted]' in text


def test_real_owned_term_resistant_worker_is_killed_reaped_and_redacted(service, monkeypatch):
    identity, path = ready(service)
    secret = 'SYNTHETIC-SPLIT-WORKER-SECRET'
    service.key_provider = SimpleNamespace(worker_env=lambda: {'MODEL_API_KEY': secret}, get_key=lambda: secret,
                                          redact=lambda value: value)
    ready_file = path / 'owned-worker-ready'
    code = (
        'import json,os,signal,subprocess,sys,time; from pathlib import Path; '
        'secret=os.environ["MODEL_API_KEY"]; '
        'sys.stdout.write(secret[:8]);sys.stdout.flush();time.sleep(.03); '
        'sys.stdout.write(secret[8:]+"\\n");sys.stdout.flush(); '
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"]); '
        'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
        'Path(sys.argv[1]).write_text(json.dumps({"leader":os.getpid(),"child":child.pid})); '
        '\nwhile True: child.poll(); time.sleep(.01)'
    )
    owned = []
    def spawn(command, **kwargs):
        process = _NATIVE_FUNCTIONAL_POPEN([sys.executable, '-c', code, str(ready_file)], **kwargs)
        process.event = threading.Event()
        owned.append(process)
        return process
    monkeypatch.setattr('app.functional.subprocess.Popen', spawn)
    try:
        assert service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})[0] == 202
        wait_for(ready_file.exists)
        process = owned[0]
        owned_pids = json.loads(ready_file.read_text())
        assert owned_pids['leader'] == process.pid
        _shutdown_os.kill(owned_pids['child'], 0)
        service.close()
        job = service._jobs[identity]
        assert not job['active'] and service._engine_call is None
        assert not job['thread'].is_alive() and not job['log_thread'].is_alive()
        assert process.returncode == -_shutdown_signal.SIGKILL
        with pytest.raises(ProcessLookupError):
            _shutdown_os.killpg(process.pid, 0)
        with pytest.raises(ProcessLookupError):
            _shutdown_os.kill(owned_pids['child'], 0)
        assert secret not in (path / 'worker-plan.log').read_text()
        assert '[redacted]' in (path / 'worker-plan.log').read_text()
        assert service.view(path)['stages'][-1]['notes'].startswith('Stopped by planner.')
    finally:
        for process in owned:
            if process.poll() is None:
                _shutdown_os.killpg(process.pid, _shutdown_signal.SIGKILL)
            process.wait(timeout=2)


def test_bounded_group_cleanup_failure_retains_engine_ownership(service, monkeypatch):
    identity, path = ready(service)
    old_spawn = __import__('app.functional', fromlist=['subprocess']).subprocess.Popen
    def spawn(command, **kwargs):
        process = old_spawn(command, **kwargs)
        original_wait = process.wait
        process.wait = lambda timeout=None: original_wait()
        process.poll = lambda: 0 if process.event.is_set() else None
        return process
    monkeypatch.setattr('app.functional.subprocess.Popen', spawn)
    observed = []
    monkeypatch.setattr('app.functional.os.killpg', lambda pid, sig: observed.append((pid, sig)))
    assert service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})[0] == 202
    job = service._jobs[identity]
    try:
        service._stop(path)
        job['process'].event.set()
        wait_for(lambda: job.get('cleanup_failed'))
        job['thread'].join(timeout=.5)
        job['cleanup_thread'].join(timeout=.5)
        assert not job['thread'].is_alive() and not job['cleanup_thread'].is_alive()
        assert job['active'] and service._engine_call == identity
        assert not job['group_exited'].is_set()
        assert _shutdown_signal.SIGKILL in {sig for _, sig in observed}
        assert {pid for pid, _ in observed} == {job['process'].pid}
        assert 'cleanup could not complete' in service.view(path)['message']
        assert service.view(path)['stages'][-1]['status'] == 'failed'
    finally:
        # Release this intentionally immortal fake group for the fixture's teardown.
        job['active'] = False
        service._engine_call = None


def test_call_poll_reads_compact_index_and_summary_without_booking_receipts(service, monkeypatch):
    identity, path = ready(service)
    summary = {'complete': True, 'boxes': 1, 'rows': [{'id': 'cargo', 'length_ft': 20}],
               'questions': [], 'cost_usd': .03}
    _write(path / 'intake-view.json', summary)
    _write(path / 'intake.json', {'raw': 'private frozen context'})
    booking = dict(booking_ref='B1', status='done', boxes=1, wall_s=12., cost_usd=.03,
                   file='intake/bookings/B1.json', sha256='a' * 64,
                   documents_sha256='b' * 64, base_sha256='c' * 64)
    _write(path / 'intake-bookings.json', {'schema': 'stowlm.functional-intake-index/1', 'bookings': [booking]})
    forbidden = {path / 'intake.json', path / booking['file'], path / 'base-instance.json',
                 path / 'assembled-instance.json'}
    original = Path.read_text
    def read_text(file, *args, **kwargs):
        assert file not in forbidden, 'poll opened a private worker receipt or unnecessary instance'
        return original(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    status, value = service.dispatch('GET', f'calls/{identity}')
    assert status == 200 and value['papers']['intake'] == summary
    assert value['papers']['bookings'] == [{key: booking[key] for key in
                                          ('booking_ref', 'status', 'boxes', 'wall_s', 'cost_usd', 'file', 'sha256')}]
    assert value['load_list']['origin'] == 'intake' and value['load_list']['boxes'] == 1
    assert service.dispatch('GET', 'calls')[1]['calls'][0]['boxes'] == 1


def test_poll_never_opens_large_legacy_inline_booking_index(service, monkeypatch):
    identity, path = ready(service)
    index = path / 'intake-bookings.json'
    index.write_text(' ' * (1024 * 1024 + 1))
    original = Path.read_text
    def read_text(file, *args, **kwargs):
        assert file != index
        return original(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    assert service.dispatch('GET', f'calls/{identity}')[1]['papers']['bookings'] == []


def test_legacy_intake_poll_excludes_private_context_and_reuses_public_snapshot(service, monkeypatch):
    identity, path = ready(service)
    _write(path / 'intake.json', {'boxes': 1, 'rows': [{'id': 'cargo'}], 'raw': {'private': 'large context'}})
    assert 'raw' not in service.view(path)['papers']['intake']
    original = Path.read_text
    def read_text(file, *args, **kwargs):
        assert file != path / 'intake.json'
        return original(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_text)
    assert service.view(path)['papers']['intake']['boxes'] == 1


@pytest.mark.parametrize('selection', [
    {'documents': [{'name': 'cargo.txt', 'text': 'SYNTHETIC CARGO'}]},
    {'use_generated_load_list': True},
])
def test_failed_intake_can_retry_or_choose_generated_load_list(service, selection):
    identity, path = ready(service)
    service._state(path, 'failed', 'A booking read failed.')
    _write(path / 'intake.json', {'boxes': 999, 'rows': [], 'raw': {'old': True}})
    _write(path / 'intake-view.json', {'boxes': 999, 'rows': []})
    index = {'bookings': [{'booking_ref': 'B1', 'status': 'done', 'file': 'intake/bookings/B1.json'}]}
    _write(path / 'intake-bookings.json', index)
    if selection.get('use_generated_load_list'):
        service.test_available['spark']['available'] = False
    status, value = service.dispatch('POST', f'calls/{identity}/papers', selection)
    assert status == 202
    assert _read(path / 'intake-bookings.json') == index, 'paid-read evidence must survive either choice'
    if selection.get('use_generated_load_list'):
        assert value['state'] == 'assembling'
        assert value['papers']['intake']['skipped']
        assert value['load_list']['origin'] == 'generator' and value['load_list']['boxes'] == 1
    else:
        assert value['state'] == 'reading_papers' and value['papers']['intake'] is None
        assert not (path / 'intake.json').exists()


@pytest.mark.parametrize('route', ['status', 'options', 'calls', 'call', 'planner', 'file'])
def test_get_routes_do_not_wait_for_server_global_lock(service, route):
    identity, path = ready(service)
    _write(path / 'case.json', {'ship': 'feeder_500', 'boxes': 1})
    routes = {'call': f'calls/{identity}', 'planner': f'calls/{identity}/planner/unknown',
              'file': f'calls/{identity}/file/case.json'}
    results, done = [], threading.Event()
    def request():
        results.append(service.dispatch('GET', routes.get(route, route)))
        done.set()
    with service._lock:
        thread = threading.Thread(target=request)
        thread.start()
        completed_without_lock = done.wait(.75)
    thread.join(timeout=2)
    assert completed_without_lock, f'{route} waited on the server-wide mutation lock'
    assert results[0][0] in {200, 404}


def test_slow_planner_payload_does_not_queue_poll_or_other_call_mutation(service, monkeypatch):
    identity, _ = ready(service)
    other, _ = ready(service)
    began, release = threading.Event(), threading.Event()
    def payload(*args):
        began.set()
        assert release.wait(3)
        return {'bays': []}
    monkeypatch.setitem(sys.modules, 'app.functional_planner', SimpleNamespace(NoPlan=LookupError, payload=payload))
    slow = threading.Thread(target=lambda: service.dispatch('GET', f'calls/{identity}/planner/ship'))
    slow.start()
    finished = threading.Event()
    responses = []
    def polls():
        responses.append(service.dispatch('GET', f'calls/{identity}')[0])
        responses.append(service.dispatch('POST', f'calls/{other}/papers', {'use_generated_load_list': True})[0])
        finished.set()
    try:
        assert began.wait(1)
        fast = threading.Thread(target=polls)
        fast.start()
        assert finished.wait(.75), 'a slow read-only payload queued unrelated work'
        assert responses == [200, 202]
    finally:
        release.set()
        slow.join(timeout=3)
        if 'fast' in locals():
            fast.join(timeout=3)


def test_slow_request_log_has_only_sanitized_route_and_elapsed_ms(service, monkeypatch, capsys):
    ticks = iter([10., 11.25])
    monkeypatch.setattr('app.functional.monotonic', lambda: next(ticks))
    status, _ = service.dispatch('GET', '/api/functional/options?body=never-log-this')
    assert status == 200
    logged = capsys.readouterr().err
    assert 'options' in logged and '1250' in logged
    assert 'never-log-this' not in logged


def test_poll_availability_does_not_wait_for_the_paid_ledger(service):
    identity, path = ready(service)
    # The frozen client can hold its ledger lock across an HTTP request. Model
    # presence/consent checks in a poll must not read that paid-call ledger.
    service.key_provider._entered_key = 'synthetic-test-key-only'
    service.key_provider._accepted = True
    service.test_models.spend_status = lambda **kwargs: pytest.fail('poll read the paid ledger')
    def availability(**kwargs):
        provider = kwargs['key_provider']
        assert isinstance(provider, dict)
        assert provider['present'] and provider['paid_calls_accepted']
        assert 'synthetic-test-key-only' not in json.dumps(provider)
        return service.test_available
    service.test_models.availability = availability
    assert service.dispatch('GET', f'calls/{identity}')[0] == 200


def test_cap_change_waiting_for_ledger_does_not_hold_credential_lock(service):
    identity, _ = ready(service)
    began, release, polled = threading.Event(), threading.Event(), threading.Event()
    def configure(**kwargs):
        began.set()
        assert release.wait(3)
        return dict(available=True, spent_usd=0., cap_usd=kwargs['cap_usd'])
    service.test_models.configure_spend = configure
    responses = []
    update = threading.Thread(target=lambda: responses.append(service.dispatch(
        'POST', 'key', {'key': 'synthetic-test-key-only', 'accept_paid_calls': True, 'cap_usd': 3.})[0]))
    poll = threading.Thread(target=lambda: (service.dispatch('GET', f'calls/{identity}'), polled.set()))
    update.start()
    try:
        assert began.wait(1)
        poll.start()
        assert polled.wait(.75), 'ledger wait held the credential lock needed by polls'
    finally:
        release.set()
        update.join(timeout=3)
        if poll.ident:
            poll.join(timeout=3)
    assert responses == [200]


def test_engine_reservation_is_atomic_between_independent_calls(service, monkeypatch):
    first, _ = ready(service)
    second, _ = ready(service)
    reserved, release = threading.Event(), threading.Event()
    original = service._control
    def control(path, **fields):
        if path.name == first and 'profile' in fields:
            reserved.set()
            assert release.wait(3)
        return original(path, **fields)
    monkeypatch.setattr(service, '_control', control)
    responses = []
    planner = threading.Thread(target=lambda: responses.append(service.dispatch(
        'POST', f'calls/{first}/plan', {'instruction': 'none'})[0]))
    planner.start()
    try:
        assert reserved.wait(1)
        status, error = service.dispatch('POST', f'calls/{second}/plan', {'instruction': 'none'})
        assert status == 409 and 'already running' in error['error']
        assert service._engine_call == first
    finally:
        release.set()
        planner.join(timeout=3)
    assert responses == [202]


def test_default_cap_is_resolved_by_model_layer_and_passed_to_workers(service):
    seen = []
    def status(**kwargs):
        seen.append(kwargs)
        return dict(available=True, spent_usd=0., cap_usd=12.5 if kwargs.get('cap_usd') is None else kwargs['cap_usd'])
    service.test_models.spend_status = status
    provider = KeyProvider(environ={})
    assert 'STOWLM_FUNCTIONAL_CAP_USD' not in provider.worker_env()
    assert provider.status()['cap_usd'] == 12.5
    assert seen[0]['cap_usd'] is None, 'the server must not override the model layer default'
    assert provider.worker_env()['STOWLM_FUNCTIONAL_CAP_USD'] == '12.5'


def test_key_entry_without_cap_uses_model_configure_default(service):
    seen = []
    def configure(**kwargs):
        seen.append(kwargs)
        return dict(available=True, cap_usd=10., spent_usd=0.)
    service.test_models.configure_spend = configure
    provider = KeyProvider(environ={})
    assert provider.update({'key': 'synthetic-test-key-only', 'accept_paid_calls': True})['cap_usd'] == 10.
    assert 'cap_usd' not in seen[0]
    assert provider.worker_env()['STOWLM_FUNCTIONAL_CAP_USD'] == '10.0'


@pytest.mark.parametrize('cap', [0., 2., 7.25])
def test_explicit_env_and_app_caps_survive_model_default(service, cap):
    provider = KeyProvider(env_values={'STOWLM_FUNCTIONAL_CAP_USD': '9'},
                           environ={'STOWLM_FUNCTIONAL_CAP_USD': str(cap)})
    assert provider.status()['cap_usd'] == cap
    assert provider.worker_env()['STOWLM_FUNCTIONAL_CAP_USD'] == str(cap)
    assert provider.update({'cap_usd': cap})['cap_usd'] == cap
    file_provider = KeyProvider(env_values={'STOWLM_FUNCTIONAL_CAP_USD': str(cap)}, environ={})
    assert file_provider.status()['cap_usd'] == cap


def test_plan_post_immediately_publishes_running_engine_with_launch_time(service):
    identity, path = ready(service)
    status, view = service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})
    assert status == 202
    engine, = [row for row in view['stages'] if row['stage'] == 'engine']
    assert engine['status'] == 'running'
    assert engine['started_utc'] == service._jobs[identity]['started_utc']
    assert engine['started_utc'].endswith('Z')
    assert engine['who'] == 'Frontier Engine 0.13.0' and engine['where'] == 'software'
    assert service.dispatch('GET', f'calls/{identity}')[1]['stages'][-1] == engine
    service._stage(path, 'engine', 'done', instance_id=engine['instance_id'],
                   started_utc=engine['started_utc'], wall_s=42., notes='Receipt-backed result.')
    finish(service, identity, 'done')
    engines = [row for row in service.view(path)['stages'] if row['stage'] == 'engine']
    assert len(engines) == 1 and engines[0]['status'] == 'done' and engines[0]['wall_s'] == 42.


def test_crashed_plan_worker_closes_its_running_engine_stage(service):
    identity, path = ready(service)
    _, view = service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})
    original, = [row for row in view['stages'] if row['stage'] == 'engine']
    finish(service, identity, 'failed', code=1)
    engine, = [row for row in service.view(path)['stages'] if row['stage'] == 'engine']
    assert engine['status'] == 'failed' and engine['instance_id'] == original['instance_id']
    assert engine['started_utc'] == original['started_utc']


def test_plan_spawn_failure_is_terminal_and_releases_engine_reservation(service, monkeypatch):
    identity, path = ready(service)
    def fail(*args, **kwargs):
        raise OSError('Synthetic worker launch failure')
    monkeypatch.setattr('app.functional.subprocess.Popen', fail)
    assert service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})[0] == 500
    engine, = [row for row in service.view(path)['stages'] if row['stage'] == 'engine']
    assert engine['status'] == 'failed' and engine['started_utc']
    assert service._engine_call is None


def test_blocked_booking_does_not_delay_status_call_view_or_key_handlers(service):
    from app import functional_worker
    from app.server import make_handler
    identity, path = ready(service)
    document_path = path / 'booking.txt'
    document_path.write_text('SYNTHETIC DOCUMENT\nBooking B1\n')
    document = dict(name='booking.txt', path=str(document_path), media_type='text/plain',
                    role='booking', booking_ref='B1')
    _write(path / 'worker-control.json', {'documents': [document]})
    service._state(path, 'reading_papers', 'Reading booking B1.')
    service.key_provider.update({'key': 'synthetic-test-key-only', 'accept_paid_calls': True})
    client_ledger_lock = threading.Lock()
    entered, release, answered = threading.Event(), threading.Event(), threading.Event()
    results, worker_results = [], []

    def read(docs, base, *, grant):
        assert grant is not None and grant.ledger_path and grant.cap_usd > 0
        with client_ledger_lock:
            entered.set()
            assert release.wait(4)
        return dict(complete=True, rows=[{'id': 'cargo'}], boxes=1, questions=[],
                    wall_s=.1, cost_usd=.02, model='muse-spark-1.3')

    service.test_models.read_papers = read
    service.test_models.answer_papers = None
    snapshot = service.test_models.spend_status
    def status(**kwargs):
        if entered.is_set() and not release.is_set():
            assert not client_ledger_lock.acquire(blocking=False), 'the fake model must still hold its client ledger'
        return snapshot(**kwargs)
    service.test_models.spend_status = status
    handler = make_handler({}, functional=service)
    worker = threading.Thread(target=lambda: worker_results.append(functional_worker.run_step('intake', path)))

    def poll():
        for method, route, body in [
            ('GET', '/api/functional/status', b''),
            ('GET', f'/api/functional/calls/{identity}', b''),
            ('POST', '/api/functional/key', b'{"cap_usd":12}'),
            ('DELETE', '/api/functional/key', b''),
        ]:
            response = _http(handler, method, route, body)
            results.append((response.status, json.load(response)))
        answered.set()

    requester = threading.Thread(target=poll)
    worker.start()
    try:
        assert entered.wait(2), 'booking did not begin'
        start = time.monotonic()
        requester.start()
        assert answered.wait(.9), 'a request handler waited on the blocked booking'
        assert time.monotonic() - start < 1.
        assert [code for code, _ in results] == [200] * 4
        assert results[0][1]['key']['cap_usd'] == 10.
        assert results[2][1]['key']['cap_usd'] == 12.
        assert results[3][1]['key']['present'] is False
        assert worker.is_alive(), 'requests must finish before the booking is released'
    finally:
        release.set()
        worker.join(timeout=4)
        if requester.ident:
            requester.join(timeout=4)
    assert not worker.is_alive() and worker_results == [0]


def test_real_grant_client_lock_does_not_block_http_status_or_key_updates(tmp_path, monkeypatch):
    from app import functional_models as models
    from app.server import make_handler
    monkeypatch.setattr(models, '_FUNCTIONAL_SPEND_ROOT', tmp_path / 'spend')
    monkeypatch.setitem(sys.modules, 'app.functional_cases', SimpleNamespace(options=lambda: {'envelope': {}}))
    provider = KeyProvider(environ={})
    provider.update({'key': 'synthetic-test-key-only', 'accept_paid_calls': True, 'cap_usd': 10.})
    app = FunctionalApp(root_dir=tmp_path / 'calls', key_provider=provider)
    app._availability = lambda: {}
    identity = 'c20261103-120000-abcdef'
    path = app.root_dir / identity
    _write(path / 'request.json', {})
    _write(path / 'case.json', {'ship': 'feeder_500', 'boxes': 1, 'papers': []})
    _write(path / 'base-instance.json', {'containers': [], 'load_list': []})
    app._state(path, 'reading_papers', 'A synthetic booking transport is blocked.')
    grant = models.grant_call(launch_id=provider.launch_id, call_id=identity, booking_ref='B1')
    assert grant is not None
    ledger = models.functional_ledger(grant=grant)
    ledger.append({'phase': 'reservation', 'reservation_id': 'synthetic-blocked-request',
                   'cost_usd': str(grant.cap_usd), 'cost_kind': 'conservative_hold', 'purpose': 'synthetic booking'})
    entered, release, answered = threading.Event(), threading.Event(), threading.Event()
    results = []

    def blocked_call():
        # This is the same frozen ledger lock held across a real HTTP call.
        with ledger.locked():
            entered.set()
            assert release.wait(4)

    handler = make_handler({}, functional=app)
    def requests():
        for method, route, body in [
            ('GET', '/api/functional/status', b''),
            ('GET', f'/api/functional/calls/{identity}', b''),
            ('POST', '/api/functional/key', b'{"cap_usd":12}'),
            ('DELETE', '/api/functional/key', b''),
        ]:
            response = _http(handler, method, route, body)
            results.append((response.status, json.load(response)))
        answered.set()

    booking = threading.Thread(target=blocked_call)
    requester = threading.Thread(target=requests)
    booking.start()
    try:
        assert entered.wait(2)
        start = time.monotonic()
        requester.start()
        assert answered.wait(.9), 'an HTTP handler waited on the frozen booking ledger lock'
        assert time.monotonic() - start < 1.
        assert [status for status, _ in results] == [200] * 4
        assert booking.is_alive(), 'the model call must remain blocked throughout the requests'
        assert results[0][1]['key']['spent_usd'] == 0.
        assert results[2][1]['key']['cap_usd'] == 12.
    finally:
        release.set()
        booking.join(timeout=4)
        if requester.ident:
            requester.join(timeout=4)
        models.release(grant)
    assert not booking.is_alive() and not requester.is_alive()


def test_worker_real_grants_refill_while_another_booking_client_is_blocked(tmp_path, monkeypatch):
    from app import functional_models as models, functional_worker as worker
    monkeypatch.setattr(models, '_FUNCTIONAL_SPEND_ROOT', tmp_path / 'spend')
    monkeypatch.setenv('STOWLM_FUNCTIONAL_LAUNCH_ID', 'synthetic-worker-grants')
    monkeypatch.delenv('MODEL_API_KEY', raising=False)
    models.configure_spend(launch_id='synthetic-worker-grants', cap_usd=2.8)
    call = tmp_path / 'calls' / 'c20261103-120000-abcdef'
    documents = []
    for ref in ('B1', 'B2', 'B3'):
        file = call / 'papers' / (ref + '.txt')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('SYNTHETIC DOCUMENT\nBooking ' + ref)
        documents.append(dict(name=file.name, path=str(file), media_type='text/plain', role='booking', booking_ref=ref,
                              container_ids=[ref]))
    _write(call / 'base-instance.json', {'containers': [], 'load_list': ['B1', 'B2', 'B3']})
    _write(call / 'worker-control.json', {'documents': documents})
    pair, third_started, release = threading.Barrier(2), threading.Event(), threading.Event()
    ledger_paths, results = {}, []

    def read(group, base, *, grant):
        ref = group[0]['booking_ref']
        ledger_paths[ref] = grant.ledger_path
        ledger = models.functional_ledger(grant=grant)
        ledger.append(dict(phase='reservation', reservation_id=ref, cost_usd=str(grant.cap_usd),
                           cost_kind='conservative_hold', purpose='synthetic booking'))
        with ledger.locked():
            if ref in ('B1', 'B2'):
                pair.wait(timeout=3)
            if ref == 'B3':
                third_started.set()
            if ref != 'B1':
                assert release.wait(4)
        ledger.append(dict(phase='settlement', reservation_id=ref, cost_usd='.02',
                           cost_kind='measured', purpose='synthetic booking'))
        return dict(complete=True, boxes=1, rows=[{'id': ref}], questions=[], cost_usd=.02, wall_s=.01)

    def merge(views):
        return dict(complete=True, boxes=len(views), rows=[row for view in views for row in view['rows']],
                    questions=[], cost_usd=sum(view['cost_usd'] for view in views), wall_s=.03)
    monkeypatch.setattr(models, 'read_papers', read)
    monkeypatch.setattr(models, 'merge_intake_views', merge)
    thread = threading.Thread(target=lambda: results.append(worker.run_step('intake', call)))
    thread.start()
    try:
        assert third_started.wait(3), 'the scheduler did not refill a released grant while B2 was blocked'
        assert thread.is_alive()
        assert len(set(ledger_paths.values())) == 3, 'bookings shared a frozen client ledger'
        index = _read(call / 'intake-bookings.json')['bookings']
        first, = [row for row in index if row['booking_ref'] == 'B1']
        assert first['status'] == 'done' and (call / first['file']).is_file()
        assert models.spend_status()['committed_usd'] <= 2.8
    finally:
        release.set()
        thread.join(timeout=4)
    assert not thread.is_alive() and results == [0]
    assert models.spend_status()['spent_usd'] == pytest.approx(.06)
    assert models.spend_status()['held_usd'] == models.spend_status()['grants_usd'] == 0.


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
@pytest.mark.parametrize('route,expected', [
    ('/api/functional/key', 403), ('/api/functional/%6bey', 403),
    ('/api/functional/%6B%65%79', 403), ('/api%2ffunctional/ke%79', 403),
    ('/api/functional/%256bey', 400), ('/api/functional/%25256bey', 400),
    ('/api/functional/%6bey%3fignored', 400), ('/api/functional/%6bey%23ignored', 400),
    ('/api/functional/%00key', 400), ('/api/functional/%5ckey', 400),
])
def test_encoded_key_routes_are_authorized_before_mutation(service, monkeypatch, method, route, expected):
    from app.server import make_handler
    mutations = []
    monkeypatch.setattr(service.key_provider, 'update', lambda body: mutations.append(body))
    monkeypatch.setattr(service.key_provider, 'clear', lambda: mutations.append(None))
    handler = make_handler({}, functional=service)
    handler.expected_port = 8794
    body = b'{"key":"synthetic-canonical-key"}' if method == 'POST' else b''
    assert _http(handler, method, route, body, Host='[::1]:8794').status == expected
    assert mutations == []


def test_once_encoded_local_key_path_reaches_one_canonical_action(service, monkeypatch):
    from app.server import make_handler
    seen = []
    dispatch = service._dispatch
    def inspect(method, route, body):
        seen.append((method, route))
        return dispatch(method, route, body)
    monkeypatch.setattr(service, '_dispatch', inspect)
    handler = make_handler({}, functional=service)
    body = b'{"key":"synthetic-canonical-key","accept_paid_calls":true}'
    assert _http(handler, 'POST', '/api/functional/%6bey?ignored=synthetic', body).status == 200
    assert _http(handler, 'DELETE', '/api%2ffunctional/ke%79').status == 200
    assert seen == [('POST', '/api/functional/key'), ('DELETE', '/api/functional/key')]


def test_runner_and_combined_envelopes_have_distinct_verdicts(service):
    identity, path = ready(service)
    raw = dict(in_envelope=True, reasons=[], numbers={'ship': 'feeder_1700'})
    case = dict(ship='feeder_1700', boxes=1, envelope=raw)
    _write(path / 'case.json', case)
    _write(path / 'runner-envelope.json', raw)
    view = service.view(path)
    assert view['runner_envelope'] == raw and view['runner_envelope']['in_envelope']
    assert not view['envelope']['in_envelope']
    assert 'Ship 1,700 TEU' in view['envelope']['reasons'][0]['message']
    assert service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})[0] == 409
    assert _read(path / 'runner-envelope.json') == raw


def test_generation_persists_raw_runner_gate_before_app_restrictions(service, monkeypatch):
    from app.functional_worker import generate
    _, path = ready(service)
    raw = dict(in_envelope=True, reasons=[], numbers={'ship': 'feeder_1700'})
    case = dict(ship='feeder_1700', envelope=raw, headline_envelope=dict(in_envelope=False, reasons=[]))
    monkeypatch.setattr(service._cases(), 'generate_case', lambda *args: case, raising=False)
    generate(path, {})
    assert _read(path / 'runner-envelope.json') == raw
    assert not _read(path / 'envelope.json')['in_envelope']
    assert _read(path / 'case.json')['envelope']['in_envelope']


@pytest.mark.parametrize('generated', [True, False])
def test_generated_voice_flag_is_saved_independently_of_upload_origin(service, generated):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/audio', dict(
        name='synthetic.wav', data_b64=wav_b64(), origin='upload', generated=generated))[0] == 202
    wait_for(lambda: not service._jobs[identity]['active'])
    view = service.view(path)
    assert view['voice']['generated'] is generated and view['voice']['origin'] == 'upload'
    assert view['voice']['generated_voice'] is generated
    assert _read(path / 'voice.json')['generated'] is generated
    assert view['stages'][-1]['notes'] == ('Generated voice' if generated else 'Uploaded audio')


@pytest.mark.parametrize('value', ['yes', 1, None, []])
def test_generated_voice_flag_rejects_non_boolean_before_upload(service, value):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/audio', dict(
        name='synthetic.wav', data_b64=wav_b64(), generated=value))[0] == 400
    assert not (path / 'papers').exists() and not (path / 'voice.json').exists()


@pytest.mark.parametrize('count', [0, 2])
def test_planner_stop_survives_reload_without_no_plan_claim_or_release(service, count):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/plan', {'instruction': 'none'})[0] == 202
    (path / 'run').mkdir(exist_ok=True)
    (path / 'run/events.jsonl').write_text(''.join(json.dumps(dict(kind='incumbent', t_s=index,
        data=dict(checked=True, lifts=10-index))) + '\n' for index in range(count)))
    assert service.dispatch('POST', f'calls/{identity}/stop')[0] == 200
    service._jobs[identity]['process'].event.set()
    wait_for(lambda: not service._jobs[identity]['active'])
    raw = dict(status='checked_plan', passed=True, message='A late result was not released.')
    _write(path / 'run/outcome.json', raw)
    reloaded = FunctionalApp(root_dir=service.root_dir, key_provider=KeyProvider(environ={}))
    reloaded._availability = lambda: {}
    view = reloaded.view(path)
    assert view['run']['status'] == 'stopped'
    assert 'No checked plan found' not in view['message']
    assert ('none was released' if count else 'before any checked plan was found') in view['message']
    assert view['outcome'] is None
    for route in ('plan-view', 'planner/plan-after', 'planner/package'):
        assert reloaded.dispatch('GET', f'calls/{identity}/{route}')[0] == 409
    assert _read(path / 'run/outcome.json') == raw


@pytest.mark.parametrize('status', ['error', 'needs_confirmation', 'admission_failed', 'no_checked_plan', 'constraint_unavailable'])
def test_no_plan_suffix_is_only_for_search_outcomes_and_advice_is_actionable(service, status):
    from app.functional_worker import outcome_view
    _, path = ready(service)
    raw = dict(status=status, passed=False, message='Retry with a longer deadline or reduce the load. Use a matching recording.',
               packet={'profile': 'standard'})
    _write(path / 'run/outcome.json', raw)
    projected = outcome_view(path)
    assert ('No checked plan found' in projected['message']) == (status in {'no_checked_plan', 'constraint_unavailable'})
    assert '1800 s standard deadline is the longest' in projected['message']
    assert 'never uses recordings' in projected['message']
    assert 'Use a matching recording' not in projected['message']
    assert _read(path / 'run/outcome.json') == raw


def test_live_profile_advice_requires_a_fresh_call_for_longer_profile(service):
    from app.functional_worker import display_message
    text = display_message('Retry with a longer deadline or reduce the load.', 'live')
    assert 'standard' in text and 'fresh call' in text


@pytest.mark.parametrize('confirmed', [True, False])
def test_runner_reader_has_explicit_revalidation_provenance_and_binding(service, confirmed):
    from app.functional_worker import _runner_stages
    _, path = ready(service)
    binding = dict(reading='r-confirmed', sentence_sha256='a'*64, rule_sha256='b'*64)
    _write(path / 'confirmation.json', binding)
    packet = dict(voyage_sentences=[{'text': 'Close bay 1.'}] if confirmed else [])
    outcome = dict(packet=packet, receipt={'steps': [dict(stage='reader', mode='live', wall_s=.1)]})
    _runner_stages(path, outcome)
    stage = service.view(path)['stages'][-1]
    assert stage['provenance'] == ('runner_revalidation' if confirmed else 'runner_no_instruction')
    assert stage['model_call'] is False and stage['where'] == 'software'
    assert stage['source'] == 'runner_receipt' and stage['revalidation'] is confirmed
    if confirmed:
        assert stage['reading_id'] == binding['reading']
        assert stage['sentence_sha256'] == binding['sentence_sha256'] and stage['rule_sha256'] == binding['rule_sha256']


def test_actual_reread_after_confirmation_is_still_a_model_call(service):
    identity, path = ready(service)
    service._stage(path, 'confirm', who='Planner', where='this laptop')
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, sentence, context):
            return dict(status='refused', sentence=sentence, answer={}, evidence={}, refusal={'reason': 'Synthetic refusal.'})
    service.test_models.Reader = Reader
    assert service.dispatch('POST', f'calls/{identity}/read', {'sentence': 'A new actual reading.'})[0] == 202
    wait_for(lambda: not service._jobs[identity]['active'])
    row = service.view(path)['stages'][-1]
    assert row['provenance'] == 'model_call' and row['model_call'] is True


def test_uploaded_host_booking_membership_is_saved_passed_and_public(service):
    identity, path = ready(service)
    status, view = service.dispatch('POST', f'calls/{identity}/papers', {'documents': [dict(
        name='booking.txt', text='SYNTHETIC DOCUMENT', booking_ref='B1', role='booking', container_ids=['cargo'])]})
    assert status == 202
    assert _read(path / 'papers/meta.json')['documents'][0]['container_ids'] == ['cargo']
    assert _read(path / 'worker-control.json')['documents'][0]['container_ids'] == ['cargo']
    assert view['papers']['documents'][0]['container_ids'] == ['cargo']


def test_cap_stop_view_exposes_retained_questions_accounting_and_unsent_facts(service):
    identity, path = ready(service)
    facts = dict(booking_ref='B2', sent=False, request_kind='initial', spent_usd=.67,
                 holds_usd=0., grants_usd=0., hold_usd=1.384448, cap_usd=2.,
                 completed_bookings=1, kept_bookings=1, unsent_bookings=1, open_questions=3)
    message = "Booking B2 wasn't sent. One reading is kept for a retry; nothing has been planned from a partial reading."
    _write(path / 'intake-view.json', dict(complete=False, rows=[{'id': 'partial'}], boxes=1,
        settled_cost_usd=.67, incremental_cost_usd=0., elapsed_s=10., booking_wall_s=18., spend_wait_s=2.,
        completed_bookings=1, kept_bookings=1, unsent_bookings=1, open_questions=3,
        cap_stop=facts, message=message))
    _write(path / 'intake-bookings.json', dict(bookings=[
        dict(booking_ref='B1', status='needs_answers', kept=True, open_questions=3),
        dict(booking_ref='B2', status='pending', kept=False, open_questions=None)]))
    service._state(path, 'failed', message)
    view = service.view(path)
    assert view['papers']['intake']['cap_stop'] == facts
    public = view['papers']['cap_refusal']
    assert all(public[key] == value for key, value in facts.items())
    assert public['charged_usd'] == 0 and public['reason'] == message
    kept, unsent = view['papers']['bookings']
    assert kept['kept'] is True and kept['open_questions'] == 3
    assert unsent['cap_refusal'] == public
    assert view['load_list']['origin'] == 'generator'
    assert {'id': 'partial'} not in view['load_list']['rows']
    assert view['run'] is None and view['outcome'] is None


def _synthetic_result(path, status='no_checked_plan', profile='standard', sentence=None):
    raw = dict(status=status, passed=status == 'checked_plan', level='L0' if status == 'checked_plan' else None,
               message='Synthetic engine result.', timing=dict(wall_s=6.5),
               packet=dict(profile=profile, voyage_sentences=[dict(text=sentence)] if sentence else []))
    _write(path / 'run/outcome.json', raw)
    return raw


@pytest.mark.parametrize('terminal', ['checked_plan', 'no_checked_plan', 'relaxed', 'failed', 'stopped'])
def test_plan_again_preserves_attempt_and_starts_fresh_after_every_terminal_state(service, terminal):
    identity, path = ready(service)
    route = f'calls/{identity}/plan'
    status, first = service.dispatch('POST', route, dict(instruction='none', profile='live'))
    assert status == 202 and first['run']['attempt'] == 1
    assert (path / 'run').is_symlink() and (path / 'run').resolve() == path / 'runs/1'
    assert _read(path / 'worker-control.json')['attempt'] == 1
    raw = _synthetic_result(path, terminal if terminal not in {'failed', 'stopped'} else 'error', 'live')
    if terminal == 'stopped':
        assert service.dispatch('POST', f'calls/{identity}/stop')[0] == 200
        service._jobs[identity]['process'].event.set()
        wait_for(lambda: not service._jobs[identity]['active'])
    else:
        finish(service, identity, 'failed' if terminal == 'failed' else 'done', code=1 if terminal == 'failed' else 0)
    status, second = service.dispatch('POST', route, dict(instruction='none', profile='standard'))
    assert status == 202
    assert second['run']['attempt'] == 2 and second['run']['status'] == 'running'
    assert second['run']['profile'] == 'standard' and second['run']['engine_deadline_s'] == 1800
    assert second['outcome'] is None and second['run']['incumbents'] == []
    assert not (path / 'runs/2/outcome.json').exists()
    assert (path / 'run').resolve() == path / 'runs/2'
    assert _read(path / 'runs/1/outcome.json') == raw
    previous = second['attempts'][0]
    assert previous['attempt'] == 1 and previous['instruction'] == 'none' and previous['profile'] == 'live'
    assert previous['status'] == ('error' if terminal == 'failed' else terminal)
    assert previous['wall_s'] == 6.5
    engines = [stage for stage in second['stages'] if stage['stage'] == 'engine']
    assert [row['attempt'] for row in engines] == [1, 2]
    assert engines[0]['status'] != 'running' and engines[1]['status'] == 'running'
    assert engines[1]['started_utc']


def test_completed_call_can_read_confirm_change_instruction_then_choose_none(service):
    identity, path = ready(service)
    route = f'calls/{identity}'
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, sentence, context):
            return dict(status='constraint_ready', sentence=sentence, wall_s=.01, evidence={},
                        answer=dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=1)))
    service.test_models.Reader = Reader
    def confirm(sentence):
        assert service.dispatch('POST', route + '/read', dict(sentence=sentence))[0] == 202
        wait_for(lambda: not service._jobs[identity]['active'])
        reading = service.view(path)['readings'][-1]
        body = {key: reading[key] for key in ('sentence_sha256', 'rule_sha256')}
        assert service.dispatch('POST', route + '/confirm', dict(reading=reading['id'], **body))[0] == 200
        return reading['id']
    first_reading = confirm('Close bay 1.')
    assert service.dispatch('POST', route + '/plan', dict(instruction='confirmed'))[0] == 202
    _synthetic_result(path, sentence='Close bay 1.')
    finish(service, identity, 'done')
    second_reading = confirm('Close bay 2.')
    previous = service.view(path)['run']
    assert previous['confirmed_reading'] == first_reading and previous['sentence'] == 'Close bay 1.'
    assert previous['status'] == 'done'
    status, view = service.dispatch('POST', route + '/plan', dict(instruction='confirmed', profile='live'))
    assert status == 202 and view['run']['confirmed_reading'] == second_reading
    assert view['run']['sentence'] == 'Close bay 2.' and view['run']['profile'] == 'live'
    assert view['attempts'][0]['sentence'] == 'Close bay 1.'
    _synthetic_result(path, profile='live', sentence='Close bay 2.')
    finish(service, identity, 'done')
    status, view = service.dispatch('POST', route + '/plan', dict(instruction='none'))
    assert status == 202 and view['run']['attempt'] == 3
    assert view['run']['confirmed_reading'] is None and view['run']['sentence'] is None
    assert [item['sentence'] for item in view['attempts']] == ['Close bay 1.', 'Close bay 2.']


def test_legacy_run_is_preserved_when_planning_again_with_a_new_confirmation(service):
    from app.v6 import _rule_digest, _sha256
    identity, path = ready(service)
    old = _synthetic_result(path, sentence='Close bay 1.')
    old_bytes = (path / 'run/outcome.json').read_bytes()
    _write(path / 'run/package/receipt.json', dict(synthetic='original receipt'))
    service._state(path, 'done', old['message'])
    readings = [dict(id=f'r{bay}', sentence=f'Close bay {bay}.', status='constraint_ready',
                     answer=dict(schema_version='stowlm.reader/2', constraint=dict(type='close_bay', bay=bay))) for bay in (1, 2)]
    _write(path / 'readings.json', readings)
    second = readings[1]
    assert service.dispatch('POST', f'calls/{identity}/confirm', dict(reading='r2',
        sentence_sha256=_sha256(second['sentence']), rule_sha256=_rule_digest(second['answer'])))[0] == 200
    status, view = service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='confirmed'))
    assert status == 202 and view['run']['attempt'] == 2
    assert (path / 'runs/1/outcome.json').read_bytes() == old_bytes
    assert _read(path / 'runs/1/package/receipt.json')['synthetic'] == 'original receipt'
    assert view['attempts'][0]['sentence'] == 'Close bay 1.' and view['attempts'][0]['reading'] == 'r1'
    assert view['run']['sentence'] == 'Close bay 2.'


def test_latest_attempt_routes_hide_old_plan_and_allow_concrete_downloads(service, monkeypatch):
    from app.functional_view import run_folder, NoPlan
    def payload(path, name):
        raw = _read(path / run_folder(path) / 'outcome.json')
        if not raw:
            raise NoPlan('This attempt has not released a plan.')
        return dict(marker=raw['marker'])
    monkeypatch.setitem(sys.modules, 'app.functional_planner', SimpleNamespace(payload=payload, NoPlan=NoPlan))
    monkeypatch.setattr('app.functional_view.plan_view', lambda path: payload(path, 'plan-after'))
    identity, path = ready(service)
    route = f'calls/{identity}'
    assert service.dispatch('POST', route + '/plan', dict(instruction='none'))[0] == 202
    _write(path / 'run/outcome.json', dict(status='checked_plan', marker='first'))
    finish(service, identity, 'done')
    assert service.dispatch('GET', route + '/planner/plan-after') == (200, dict(marker='first'))
    assert service.dispatch('POST', route + '/plan', dict(instruction='none'))[0] == 202
    for action in ('planner/plan-after', 'plan-view', 'file/run/outcome.json'):
        assert service.dispatch('GET', route + '/' + action)[0] in {404, 409}
    _write(path / 'run/outcome.json', dict(status='checked_plan', marker='second'))
    _write(path / 'run/package/receipt.json', dict(marker='second receipt'))
    finish(service, identity, 'done')
    assert service.dispatch('GET', route + '/planner/plan-after') == (200, dict(marker='second'))
    assert service.dispatch('GET', route + '/plan-view') == (200, dict(marker='second'))
    for name in ('run/outcome.json', 'runs/2/outcome.json', 'runs/2/package/receipt.json'):
        status, file = service.dispatch('GET', route + '/file/' + name)
        assert status == 200 and 'second' in file.read_text()
    assert service.dispatch('GET', route + '/file/runs/1/outcome.json')[0] == 200
    for name in ('runs/2/packet.json', 'runs/2/../1/outcome.json', 'runs/02/outcome.json'):
        assert service.dispatch('GET', route + '/file/' + name)[0] == 404


def test_missing_worker_after_reload_is_failed_and_can_plan_again(service):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))[0] == 202
    # A fresh server has no owned worker; an unfinished attempt is never called done.
    reloaded = FunctionalApp(root_dir=service.root_dir, key_provider=KeyProvider(environ={}))
    reloaded._availability = lambda: {}
    view = reloaded.view(path)
    assert view['run']['status'] == 'failed' and view['outcome'] is None
    assert 'no active worker record' in view['message'] and 'No checked plan found' not in view['message']
    # Finish the fake worker before simulating an abandoned saved call's retry.
    finish(service, identity, 'failed', code=1)
    _write(path / 'attempts.json', [{**_read(path / 'attempts.json')[0], 'status': 'running'}])
    status, view = service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))
    assert status == 202 and view['attempts'][0]['status'] == 'failed'


def test_attempt_preparation_failure_is_terminal_and_releases_engine_reservation(service, monkeypatch):
    identity, path = ready(service)
    original = service._control
    def fail_control(*args, **kwargs):
        if kwargs.get('attempt'):
            raise OSError('synthetic preparation failure')
        return original(*args, **kwargs)
    monkeypatch.setattr(service, '_control', fail_control)
    status, _ = service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))
    assert status == 500 and service._engine_call is None
    assert _read(path / 'attempts.json')[0]['status'] == 'failed'
    view = service.view(path)
    assert view['run']['status'] == 'failed' and view['outcome'] is None
    monkeypatch.setattr(service, '_control', original)
    status, view = service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))
    assert status == 202 and view['run']['attempt'] == 2


def test_attempt_preparation_rejects_outside_run_symlink_before_writing(service, tmp_path):
    identity, path = ready(service)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (path / 'runs').symlink_to(outside, target_is_directory=True)
    status, response = service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))
    assert status == 409 and 'inside this call' in response['error']
    assert list(outside.iterdir()) == [] and not (path / 'attempts.json').exists()
    assert service._engine_call is None


def test_stage_instance_deduplication_keeps_attempt_boundaries(service):
    _, path = ready(service)
    from app.functional_worker import append_stage
    for number in (1, 2):
        append_stage(path, 'checker', status='running', attempt=number, instance_id='same-id', started_utc='same-time')
        append_stage(path, 'checker', status='done', attempt=number, instance_id='same-id', started_utc='same-time')
    stages = [row for row in service.view(path)['stages'] if row['stage'] == 'checker']
    assert [(row['attempt'], row['status']) for row in stages] == [(1, 'done'), (2, 'done')]


def test_read_between_attempts_does_not_turn_finished_engine_back_to_running(service):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))[0] == 202
    _synthetic_result(path)
    finish(service, identity, 'done')
    entered, release = threading.Event(), threading.Event()
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, sentence, context):
            entered.set()
            assert release.wait(3)
            return dict(status='refused', sentence=sentence, answer={}, evidence={}, refusal={'reason': 'Synthetic refusal.'})
    service.test_models.Reader = Reader
    try:
        assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='A different instruction.'))[0] == 202
        assert entered.wait(1)
        view = service.view(path)
        assert view['run']['attempt'] == 1 and view['run']['status'] == 'done'
        assert view['run']['elapsed_s'] == 6.5
        reading_stage = view['stages'][-1]
        assert reading_stage['stage'] == 'reader' and reading_stage['status'] == 'running' and reading_stage['attempt'] == 2
    finally:
        release.set()
        wait_for(lambda: not service._jobs[identity]['active'])


def test_failed_reread_cannot_release_a_stopped_attempts_late_result(service, monkeypatch):
    identity, path = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))[0] == 202
    assert service.dispatch('POST', f'calls/{identity}/stop')[0] == 200
    service._jobs[identity]['process'].event.set()
    wait_for(lambda: not service._jobs[identity]['active'])
    assert _read(path / 'runs/1/attempt.json')['status'] == 'stopped'
    _synthetic_result(path, 'checked_plan')
    class Reader:
        def __init__(self, **kwargs):
            pass
        def read(self, *args):
            raise RuntimeError('Synthetic read failure.')
    service.test_models.Reader = Reader
    assert service.dispatch('POST', f'calls/{identity}/read', dict(sentence='Try another instruction.'))[0] == 202
    wait_for(lambda: not service._jobs[identity]['active'])
    assert not _read(path / 'worker-state.json').get('stopped'), 'the retry replaced the general state record'
    view = service.view(path)
    assert view['run']['status'] == 'stopped' and view['outcome'] is None
    for action in ('plan-view', 'planner/plan-after', 'planner/package'):
        assert service.dispatch('GET', f'calls/{identity}/' + action)[0] == 409
    assert service.dispatch('POST', f'calls/{identity}/plan', dict(instruction='none'))[0] == 202
    assert service.view(path)['run']['attempt'] == 2


@pytest.mark.parametrize('selection', [None, [], 'cargo', [None], [''], ['cargo', 'cargo'], [1]])
def test_generator_record_choice_rejects_invalid_ids_before_work(service, selection):
    identity, path = ready(service)
    before = len(service.test_processes)
    assert service.dispatch('POST', f'calls/{identity}/papers', dict(use_generator_records_for=selection))[0] == 400
    assert len(service.test_processes) == before and not _read(path / 'worker-control.json', {}).get('use_generator_records_for')
    assert (path / 'context.json').is_file()


@pytest.mark.parametrize('other', [dict(use_generated_load_list=True), dict(use_generated_papers=True), dict(documents=[])])
def test_generator_records_are_an_exclusive_papers_choice(service, other):
    identity, _ = ready(service)
    assert service.dispatch('POST', f'calls/{identity}/papers', dict(use_generator_records_for=['cargo'], **other))[0] == 400


def test_generator_record_choice_validates_without_key_and_preserves_read_receipts(service):
    identity, path = ready(service)
    prior = dict(complete=False, rows=[dict(id='cargo')], questions=[dict(box='cargo', field='iso_type')], raw=dict(synthetic=True))
    _write(path / 'intake.json', prior)
    _write(path / 'intake-view.json', {key: value for key, value in prior.items() if key != 'raw'})
    _write(path / 'worker-control.json', dict(answers={'old': 'old'}, use_generated_papers=True))
    service._state(path, 'needs_answers', 'A generator-only equipment question remains.')
    service.test_available['spark']['available'] = False
    seen = []
    def merge(view, base, ids):
        seen.append((view, base, ids))
        return dict(view, complete=True, questions=[], generator_record_ids=ids)
    service.test_models.merge_generator_records = merge
    status, _ = service.dispatch('POST', f'calls/{identity}/papers', dict(use_generator_records_for=['cargo']))
    assert status == 202 and seen == [(prior, path / 'base-instance.json', ['cargo'])]
    control = _read(path / 'worker-control.json')
    assert control['use_generator_records_for'] == ['cargo'] and not control['use_generated_load_list']
    assert 'answers' not in control
    assert _read(path / 'intake.json') == prior
    assert not (path / 'context.json').exists() and service.test_processes[-1].command[-2] == 'intake'


def test_generator_record_choice_cannot_bypass_other_question_kinds(service):
    identity, path = ready(service)
    _write(path / 'intake.json', dict(complete=False, rows=[], questions=[dict(box='cargo', field='mass_kg')]))
    def reject(*_):
        raise ValueError('cargo still needs a verified mass; generator records cannot answer that question.')
    service.test_models.merge_generator_records = reject
    before = len(service.test_processes)
    status, body = service.dispatch('POST', f'calls/{identity}/papers', dict(use_generator_records_for=['cargo']))
    assert status == 400 and 'verified mass' in body['error']
    assert len(service.test_processes) == before and (path / 'context.json').exists()


def test_mixed_load_list_projection_credits_each_row_without_polluting_packet_data(service):
    _, path = ready(service)
    intake = dict(complete=True, rows=[dict(id='spark', length_ft=20), dict(id='generator', length_ft=40)],
                  boxes=2, generator_record_ids=['generator'], questions=[], raw=dict(private='receipt'))
    _write(path / 'intake.json', intake)
    _write(path / 'intake-view.json', {key: value for key, value in intake.items() if key != 'raw'})
    view = service.view(path)
    assert view['load_list']['origin'] == 'Muse Spark, with 1 boxes from the generator (not read by a model)'
    assert [row['origin'] for row in view['load_list']['rows']] == ['muse_spark', 'generator, not read by a model']
    assert view['load_list']['teu'] == 3 and view['load_list']['boxes'] == 2
    from app.functional_worker import _packet
    assert _packet(path, {})['load_list']['rows'] == intake['rows']
    assert _read(path / 'intake.json') == intake


def test_real_retained_booking_generator_record_choice_and_public_sources(service, monkeypatch):
    import importlib.util
    from app import functional_worker as worker
    source = Path(__file__).resolve().parents[2] / 'experiments/mvp/ui/functional/calls/c20261103-212438-199686'
    if not (source / 'intake-bookings.json').is_file():
        pytest.skip('The local synthetic live-call receipts are not shipped with a fresh clone.')
    spec = importlib.util.spec_from_file_location('functional_models_generator_api_test', Path(__file__).resolve().parents[1] / 'functional_models.py')
    models = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, models)
    spec.loader.exec_module(models)
    identity, path = ready(service)
    original_index = (source / 'intake-bookings.json').read_bytes()
    (path / 'base-instance.json').write_bytes((source / 'base-instance.json').read_bytes())
    (path / 'intake-bookings.json').write_bytes(original_index)
    index = json.loads(original_index)
    originals = {}
    for entry in index['bookings']:
        relative = entry['file']
        data = (source / relative).read_bytes()
        originals[relative] = data
        destination = path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    service.test_models.merge_intake_views = models.merge_intake_views
    service.test_models.merge_generator_records = models.merge_generator_records
    service.test_models.read_papers = lambda *_args, **_kwargs: pytest.fail('Generator selection must not read papers again.')
    service.test_models.answer_papers = models.answer_papers
    service.test_available['spark']['available'] = False
    _write(path / 'intake.json', dict(skipped=True, why='The earlier choice used all generator records.'))
    prior = worker.generator_records_input(path)
    assert len(prior['rows']) == 25 and len(prior['questions']) == 1
    selected = ['QZXU0000117']
    status, _ = service.dispatch('POST', f'calls/{identity}/papers', dict(use_generator_records_for=selected))
    assert status == 202
    result = worker.intake(path, _read(path / 'worker-control.json'))
    assert result['complete'] and result['generator_record_ids'] == selected
    view = service.view(path)
    assert view['load_list']['origin'] == 'Muse Spark, with 1 boxes from the generator (not read by a model)'
    rows = {row['id']: row for row in view['load_list']['rows']}
    assert rows[selected[0]]['origin'] == 'generator, not read by a model'
    assert sum(row['origin'] == 'muse_spark' for row in rows.values()) == 24
    assert all(booking['status'] == 'done' for booking in view['papers']['bookings'])
    # This row retains the paid intake's values; no unselected base row replaces it.
    original = next(row for row in prior['rows'] if row['id'] == 'QZXU0000009')
    assert {key: value for key, value in rows['QZXU0000009'].items() if key != 'origin'} == original
    finish(service, identity, 'needs_answers')
    # Match production isolation: frozen assembly configures its worker's caches.
    # This validates cargo without starting engine search or changing server env.
    import os
    import subprocess
    root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    for key in ('MODEL_API_KEY', 'SPARK_CREDIT_READY', 'STOWLM_ACCEPT_PAID_CALLS'):
        environment.pop(key, None)
    environment['PYTHONPATH'] = os.pathsep.join(str(folder) for folder in (
        root / 'src', root / 'experiments/mvp/frontier', root / 'experiments/mvp/frontier/lab', root))
    with monkeypatch.context() as child_context:
        child_context.setattr(subprocess, 'Popen', _REAL_POPEN)
        check = subprocess.run([sys.executable, '-m', 'app.functional_worker', 'assemble', str(path)],
                               cwd=root, env=environment, capture_output=True, text=True, timeout=30)
    assert check.returncode == 0, check.stderr
    assembled = _read(path / 'assembled-instance.json')
    packet = _read(path / 'packet.json')
    assert len(packet['load_list']['rows']) == 25
    assert all('origin' not in row for row in packet['load_list']['rows'])
    assert packet['load_list']['intake_receipt_sha256'] == result['receipt_sha256']
    assembled_rows = {row['id']: row for row in assembled['containers']}
    base_rows = {row['id']: row for row in _read(path / 'base-instance.json')['containers']}
    assert assembled_rows[selected[0]] == base_rows[selected[0]]
    assert assembled_rows['QZXU0000009'] == original
    assert _read(path / 'worker-state.json')['state'] == 'ready'
    for relative, data in originals.items():
        assert (source / relative).read_bytes() == data and (path / relative).read_bytes() == data
    assert (source / 'intake-bookings.json').read_bytes() == original_index
