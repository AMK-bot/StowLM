"""Adversarial release cases. All ships, services and failures are synthetic.

Search/checker seams isolate most failure handling. Refusal cases use the real
binding and authenticated lattice proof; envelope cases use the real gate.
No network, model loading or GPU is needed to replay this set.
"""
import errno
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import time
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import Status, sha256_file, sha256_json
from experiments.mvp.pipeline.runner import core
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import CloseBay, Refusal
from stowage.conventions import make_container_id
from stowage.model import MovePlan
from stowage.model.fixtures import build_instance
from stowage.model.results import CheckReport, MoveCount, PartReport


FIXTURES = Path(__file__).with_name('fixtures') / 'failures'
CASES = json.loads((FIXTURES / 'cases.json').read_text())['cases']
PROVIDER = dict(parser='synthetic-failure-fixture', model='synthetic fixture', adapter_sha256=None,
                recorded=True)
ISOLATED_ENGINE_CALL = core.engine_call


@pytest.fixture(scope='module', autouse=True)
def reset_recording():
    if os.environ.get('RUNNER_RECORD_FAILURES') == '1':
        (FIXTURES / 'observed.jsonl').write_text('')


class Parser:
    def __init__(self, answer=None, failure=None):
        self.answer, self.failure = answer, failure
        self.calls = []

    def parse(self, sentence, context):
        self.calls.append(sentence)
        if self.failure is not None:
            raise self.failure
        answer = OnePortPerHatch(type='one_port_per_hatch') if sentence == 'One port per hatch.' else self.answer
        return answer, dict(PROVIDER)


@pytest.fixture
def packet(tmp_path):
    path = tmp_path / 'synthetic-base.json'
    path.write_text(build_instance().model_dump_json(indent=2))
    return dict(packet_id='synthetic-failures', created_utc='2026-11-01T00:00:00Z',
                base_instance=dict(path=str(path), sha256=sha256_file(path)))


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch, request):
    def network_forbidden(*args, **kwargs):
        pytest.fail('Failure replay attempted a network connection')
    monkeypatch.setattr(socket.socket, 'connect', network_forbidden)
    monkeypatch.setattr(socket, 'create_connection', network_forbidden)
    monkeypatch.setattr(core, '_package_writer', lambda: None)
    if request.node.name in {'test_impossible_confirmed_instruction_has_reason',
                              'test_every_instruction_refused_never_plans'}:
        return
    monkeypatch.setattr(core.binding, 'execute_brief', lambda *a, **kw:
                        (None, dict(status='no_checked_plan', reason='No checked plan found; increase the budget and retry.')))
    from experiments.mvp.pipeline.runner.limits import run_in_process_with_limit
    def execute(instance, receipt, profile, on_incumbent, *, timeout_s):
        return run_in_process_with_limit(core.binding.execute_brief, instance, receipt,
            timeout_s=timeout_s, stage='engine', on_incumbent=on_incumbent,
            **{key: profile[key] for key in ('budget_s', 'finalize_s', 'seed', 'config')})
    monkeypatch.setattr(core, 'engine_call', execute)


def verdict(outcome, status, directory, *, has_package=False):
    assert outcome['status'] == status
    assert outcome['status'] in {item.value for item in Status}
    assert outcome['passed'] is (status == 'checked_plan')
    assert len(outcome['message'].strip()) > 15
    assert 'Traceback' not in outcome['message']
    json.dumps(outcome, allow_nan=False)
    if status not in {'checked_plan', 'relaxed_plan'}:
        assert outcome.get('plan') is None
        assert outcome.get('package') is None
    if not has_package:
        # An absent committed index cannot verify as a handover package.
        assert not (directory / 'package' / 'package.json').exists()
    if os.environ.get('RUNNER_RECORD_FAILURES') == '1':
        row = dict(test=os.environ.get('PYTEST_CURRENT_TEST', '').removesuffix(' (call)'),
                   status=outcome['status'], message=outcome['message'].replace(str(directory.parent), '<temporary>'),
                   passed=outcome['passed'], package_committed=(directory / 'package' / 'package.json').exists(),
                   label='Measured synthetic failure replay; no model or network call')
        with (FIXTURES / 'observed.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')


def voyage(packet, *sentences):
    packet['voyage_sentences'] = [dict(text=text, origin='typed') for text in sentences]
    return packet


@pytest.mark.parametrize('case', ['malformed_json', 'schema_invalid', 'base_missing', 'base_hash', 'instance_invalid'])
def test_bad_packets_stop_before_reader(packet, tmp_path, case):
    if case == 'malformed_json':
        packet = (FIXTURES / 'malformed-packet.json').read_text()
    elif case == 'schema_invalid':
        packet = json.loads((FIXTURES / 'schema-invalid-packet.json').read_text())
    elif case == 'base_missing':
        packet['base_instance']['path'] = str(tmp_path / 'missing-base.json')
    elif case == 'base_hash':
        packet['base_instance']['sha256'] = '0' * 64
    else:
        path = Path(packet['base_instance']['path'])
        path.write_text((FIXTURES / 'invalid-instance.json').read_text())
        packet['base_instance']['sha256'] = sha256_file(path)
    parser = Parser()
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=parser, out_dir=out)
    verdict(outcome, 'error', out)
    assert not parser.calls


def test_unknown_intake_identity_asks_instead_of_guessing(packet, tmp_path):
    row = build_instance().containers[0].model_dump(mode='json')
    row['id'] = make_container_id('SYNU', 999)
    packet['load_list'] = dict(origin='intake', rows=[row])
    parser, out = Parser(), tmp_path / 'run'
    outcome = core.run_packet(packet, parser=parser, out_dir=out)
    verdict(outcome, 'needs_answers', out)
    assert outcome['questions'] and outcome['questions'][0]['row'] == 0
    assert 'yard' in outcome['questions'][0]['reason'].lower()
    assert not parser.calls


@pytest.mark.parametrize('failure', [ModuleNotFoundError('synthetic unavailable reader'),
                                    TimeoutError('synthetic reader timeout'),
                                    ConnectionError('synthetic network down during Spark reading')])
def test_reader_service_failures_are_safe(packet, tmp_path, failure):
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(failure=failure), out_dir=out)
    verdict(outcome, 'error', out)
    assert 'reader' in outcome['message'].lower()


def test_voice_network_failure_offers_typed_instruction(tmp_path):
    from experiments.mvp.pipeline.runner.preflight import prepare_voice
    def offline(path):
        raise ConnectionError('synthetic voice network unavailable')
    outcome = prepare_voice('synthetic.wav', transcriber=SimpleNamespace(transcribe=offline))
    verdict(outcome, 'needs_answers', tmp_path / 'voice-preflight')
    assert 'Type the instruction' in outcome['message']
    assert not outcome['transcript']


def test_malformed_reader_output_needs_correction(packet, tmp_path):
    voyage(packet, 'Keep bay 1 clear.')
    out = tmp_path / 'run'
    answer = SimpleNamespace(model_dump=lambda: {'bogus': 'not a reading'})
    outcome = core.run_packet(packet, parser=Parser(answer=answer), out_dir=out)
    verdict(outcome, 'needs_confirmation', out)
    assert any(row['status'] == 'malformed' for row in outcome['reading']['sentences'])


def test_wrong_reader_return_type_is_safe(packet, tmp_path):
    voyage(packet, 'Keep bay 1 clear.')
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(answer={'bogus': 'not a typed reading'}), out_dir=out)
    verdict(outcome, 'error', out)


@pytest.mark.parametrize('case', ['ship', 'boxes', 'fill', 'layover', 'two_load_calls'])
def test_real_envelope_gate_stops_reader(packet, monkeypatch, tmp_path, case):
    instance = build_instance(layover=case == 'layover')
    if case == 'ship':
        instance.size_class, instance.vessel.slice_of = 'medium', 'feeder_1700'
    elif case == 'boxes':
        # Gate-unit seam: repeated identities deliberately exercise demand >195
        # without building a second yard fixture. Assembly is tested above.
        instance.load_list = [instance.load_list[0]] * 196
    elif case == 'fill':
        instance.vessel.cells = instance.vessel.cells[:6]
    elif case == 'two_load_calls':
        instance.plan_calls = [1]
    monkeypatch.setattr(core, 'assemble', lambda _: instance)
    parser, out = Parser(), tmp_path / 'run'
    outcome = core.run_packet(packet, parser=parser, out_dir=out)
    verdict(outcome, 'out_of_envelope', out)
    assert not parser.calls
    assert outcome['envelope']['reasons']
    assert all('limit' in row['message'] for row in outcome['envelope']['reasons'])


def test_impossible_confirmed_instruction_has_reason(packet, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.lattice import refusal_lookup
    from experiments.mvp.pipeline.lattice import runtime, store
    from experiments.mvp.pipeline.runner.admission import wait_for_capacity
    from experiments.mvp.pipeline.runner.limits import deadline
    from experiments.mvp.reader.schema import validate_answer, context_from_instance
    from stowage.model import Instance
    pin = json.loads((FIXTURES / 'real-refusal-source.json').read_text())
    store_dir = FIXTURES / 'lattice-refusal-store'
    row = json.loads((store_dir / (pin['key'] + '.json')).read_text())
    assert sha256_file(store_dir / 'catalogue.json') == pin['catalogue_sha256']
    # Runtime placement only: no binding/proof/checker implementation is replaced.
    monkeypatch.setattr(runtime, 'HERE', tmp_path / 'lattice-runtime')
    admission = wait_for_capacity()
    store._catalogue(store_dir, refresh=True, expected_sha256=pin['catalogue_sha256'])
    with deadline(15, stage='lattice refusal proof'):
        refusal = refusal_lookup(row['instance_sha256'], row['canonical_rules'], store_dir=store_dir)
    assert refusal is not None and refusal['proof_rechecked']
    assert refusal['proof']['proven_impossible'] and refusal['planning_permitted'] is False
    assert refusal['checker'] is None
    instance = Instance.model_validate(refusal['instance'])
    assert sha256_json(instance.model_dump(mode='json')) == refusal['instance_sha256']
    path = Path(packet['base_instance']['path'])
    path.write_text(instance.model_dump_json())
    packet['base_instance']['sha256'] = sha256_file(path)
    packet['standing'] = ''
    voyage(packet, *refusal['sentences'])
    context = context_from_instance(instance)
    answers = {sentence: validate_answer(json.dumps(next(rule for rule in refusal['canonical_rules']
        if rule['constraint']['zone'] in sentence)), context) for sentence in refusal['sentences']}
    class RecordedParser:
        def parse(self, sentence, context):
            answer = answers[sentence] if sentence else Refusal(code='ambiguous', reason='Empty standing preference.')
            return answer, dict(PROVIDER)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=RecordedParser(), out_dir=out,
                              confirm=lambda _: True, engine_timeout_s=15)
    verdict(outcome, 'constraint_unavailable', out)
    assert outcome['evidence']['refusal_kind'] == 'proved_full_load'
    assert outcome['evidence']['engine_manifest']['passed']
    assert not outcome['evidence']['plan_released']
    assert 'every box aboard' in outcome['message'] and "partial plan wasn't tried" in outcome['message']
    _record_real_refusal('impossible-instruction', outcome, admission=admission,
                         lattice_proof=refusal['proof'], source=pin)


def test_every_instruction_refused_never_plans(packet, monkeypatch, tmp_path):
    from experiments.mvp.pipeline import binding
    from experiments.mvp.reader import public_brief
    from experiments.mvp.pipeline.runner.limits import run_in_process_with_limit
    from experiments.mvp.pipeline.runner.assembly import assemble
    voyage(packet, 'Keep it clear.', 'Make it perfect.')
    parser = Parser(Refusal(code='ambiguous', reason='Specify which bay.'))
    instance = assemble(packet)
    receipt = public_brief.read_brief(instance, public_brief.VoyageBrief(
        standing=packet.get('standing', 'One port per hatch.'),
        voyage=tuple(row['text'] for row in packet['voyage_sentences'])), parser=parser)
    # This labelled host approval deliberately cannot resolve refused sentences.
    confirmed = binding.confirm_brief(receipt, 'synthetic-refusal-regression')
    result, evidence = run_in_process_with_limit(binding.execute_brief, instance, confirmed,
        budget_s=0.1, finalize_s=0, timeout_s=15, stage='binding refused brief')
    assert result is None and evidence['status'] == 'needs_confirmation'
    assert evidence['requires_reading_resolution'] is True
    assert evidence['release_condition'] == 'explicit_sentence_correction_or_removal'
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=parser,
                              out_dir=out, confirm=lambda _: True)
    verdict(outcome, 'needs_confirmation', out)
    assert len([row for row in outcome['reading']['sentences'] if row['status'] == 'refused']) == 2
    assert not any(step['stage'] == 'engine' for step in outcome['receipt']['steps'])
    _record_real_refusal('every-instruction-refused', outcome, binding_evidence=evidence)


def _record_real_refusal(case, outcome, **details):
    if os.environ.get('RUNNER_RECORD_REAL_REFUSALS') == '1':
        row = dict(case=case, status=outcome['status'], message=outcome['message'],
                   evidence=outcome.get('evidence'), passed=outcome['passed'],
                   package=outcome.get('package'), measured_utc=datetime.now(timezone.utc).isoformat(),
                   label='Measured synthetic fixture; real binding and proof; recorded reader answers; no network or GPU',
                   **details)
        (FIXTURES / (case + '-real.json')).write_text(json.dumps(row, indent=2, allow_nan=False)+'\n')


def test_empty_brief_plans_normally_and_says_so(packet, tmp_path):
    packet['standing'] = ''
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(Refusal(code='ambiguous', reason='No standing preference.')),
                              out_dir=out)
    verdict(outcome, 'no_checked_plan', out)
    assert any(row['data'].get('empty_voyage') is True and 'planning normally' in row['data']['message']
               for row in outcome['events'])


@pytest.mark.parametrize('failure,status', [(TimeoutError('synthetic engine deadline'), 'no_checked_plan'),
                                         (ChildProcessError('synthetic engine process killed'), 'error'),
                                         (BrokenPipeError('synthetic worker process killed'), 'error')])
def test_engine_failure_never_releases_plan(packet, monkeypatch, tmp_path, failure, status):
    def execute(*a, **kw):
        raise failure
    monkeypatch.setattr(core.binding, 'execute_brief', execute)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, status, out)


def checked_engine(monkeypatch):
    def execute(instance, receipt, **kwargs):
        return SimpleNamespace(plan=MovePlan(id='synthetic-plan', instance_id=instance.id,
                                           call_index=instance.call_index)), dict(status='checked_plan')
    monkeypatch.setattr(core.binding, 'execute_brief', execute)
    monkeypatch.setattr(core, 'check', lambda *a: CheckReport(passed=True,
        count=MoveCount(weighted_nonproductive=0), parts={'fixture': PartReport(name='fixture')}))
    monkeypatch.setattr(core, 'inspect', lambda *a: dict(level=0, broken_rules=[], boxes_left_ashore=[],
        completed=True, lifts=0, t0_peeled=False))


def test_checker_raises_withholds_engine_claim(packet, monkeypatch, tmp_path):
    checked_engine(monkeypatch)
    def broken(*a):
        raise RuntimeError('synthetic checker exception')
    monkeypatch.setattr(core, 'check', broken)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert 'checker' in outcome['message'].lower()


@pytest.mark.parametrize('failure', [OSError(errno.ENOSPC, 'synthetic disk full'),
                                    PermissionError(errno.EACCES, 'synthetic unwritable output')])
def test_unwritable_out_dir_is_clear(packet, monkeypatch, tmp_path, failure):
    out = tmp_path / 'run'
    original = Path.mkdir
    def mkdir(path, *args, **kwargs):
        if path == out:
            raise failure
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)


@pytest.mark.parametrize('failure', [OSError(errno.ENOSPC, 'synthetic disk full in package'),
                                    PermissionError(errno.EACCES, 'synthetic package unwritable'),
                                    ConnectionError('synthetic Spark note network down')])
def test_partial_package_is_never_committed(packet, monkeypatch, tmp_path, failure):
    checked_engine(monkeypatch)
    def partial_writer(outcome, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'package.json').write_text('{"synthetic_partial": true}')
        (directory / 'plan.json').write_text(json.dumps(outcome['plan']))
        raise failure
    monkeypatch.setattr(core, '_package_writer', lambda: partial_writer)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert not list(out.glob('.package-*'))


def test_output_with_existing_package_is_preserved(packet, tmp_path):
    out = tmp_path / 'run'
    package = out / 'package'
    package.mkdir(parents=True)
    (package / 'package.json').write_text('existing package marker\n')
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out, has_package=True)
    assert 'fresh output directory' in outcome['message']
    assert (package / 'package.json').read_text() == 'existing package marker\n'


def test_stale_killed_process_lock_is_actionable(packet, tmp_path):
    out = tmp_path / 'run'
    out.mkdir()
    (out / '.runner.lock').write_text('2147483647\n')
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert 'output directory' in outcome['message']
    assert any(word in outcome['message'].lower() for word in ('fresh', 'stale', 'retry'))


def test_confirmation_callback_raises_without_plan(packet, tmp_path):
    voyage(packet, 'Keep bay 1 clear.')
    def broken(_):
        raise RuntimeError('synthetic confirmation screen disconnected')
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(CloseBay(type='close_bay', bay=1)),
                              out_dir=out, confirm=broken)
    verdict(outcome, 'error', out)
    assert 'confirmation' in outcome['message'].lower()


def test_reader_is_interrupted_at_real_deadline(packet, tmp_path):
    class SlowParser:
        def parse(self, *args):
            time.sleep(5)
            pytest.fail('Reader deadline did not interrupt')
    out = tmp_path / 'run'
    started = time.monotonic()
    outcome = core.run_packet(packet, parser=SlowParser(), out_dir=out, reader_timeout_s=.03)
    verdict(outcome, 'error', out)
    assert time.monotonic() - started < 3
    assert 'reader' in outcome['message'].lower()


def test_engine_is_interrupted_at_real_deadline(packet, monkeypatch, tmp_path):
    def slow(*args, **kwargs):
        time.sleep(5)
        pytest.fail('Engine deadline did not interrupt')
    monkeypatch.setattr(core.binding, 'execute_brief', slow)
    out = tmp_path / 'run'
    started = time.monotonic()
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out, engine_timeout_s=.03)
    verdict(outcome, 'no_checked_plan', out)
    assert time.monotonic() - started < 3


def test_owned_engine_subprocess_killed_is_reported(packet, monkeypatch, tmp_path):
    from experiments.mvp.pipeline.runner.limits import call_with_limit
    def killed():
        own_pid = os.getpid()
        os.kill(own_pid, signal.SIGKILL)
    def execute(*args, timeout_s, **kwargs):
        return call_with_limit(killed, timeout_s=2, stage='engine')
    monkeypatch.setattr(core, 'engine_call', execute)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert 'worker' in outcome['message'].lower() or 'process' in outcome['message'].lower()


def test_disk_full_while_writing_events_is_safe(packet, monkeypatch, tmp_path):
    out = tmp_path / 'run'
    original = Path.open
    def open_path(path, mode='r', *args, **kwargs):
        if path == out / 'events.jsonl' and mode == 'a':
            raise OSError(errno.ENOSPC, 'synthetic disk full during event write')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_path)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)


def successful_package(monkeypatch):
    checked_engine(monkeypatch)
    def writer(outcome, directory):
        (Path(directory) / 'package.json').write_text('{"synthetic_index": true}')
        return dict(schema_version='stowlm.plan-package/1', synthetic=True)
    monkeypatch.setattr(core, '_package_writer', lambda: writer)


def test_outcome_atomic_replace_failure_cannot_publish_package(packet, monkeypatch, tmp_path):
    successful_package(monkeypatch)
    original = os.replace
    out = tmp_path / 'run'
    def replace(source, target, *args, **kwargs):
        if Path(target) == out / 'outcome.json':
            raise OSError(errno.ENOSPC, 'synthetic disk full committing outcome')
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(os, 'replace', replace)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert not list(out.glob('.package-*'))
    assert not list(out.glob('.outcome.json-*'))


def test_package_rename_failure_cannot_publish_package(packet, monkeypatch, tmp_path):
    successful_package(monkeypatch)
    original = Path.rename
    out = tmp_path / 'run'
    def rename(source, target):
        if Path(target) == out / 'package':
            raise PermissionError(errno.EACCES, 'synthetic package commit denied')
        return original(source, target)
    monkeypatch.setattr(Path, 'rename', rename)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out)
    verdict(outcome, 'error', out)
    assert not list(out.glob('.package-*'))
    assert json.loads((out / 'outcome.json').read_text())['status'] == 'error'


def test_failure_manifest_has_contract_statuses_and_remedies():
    assert len(CASES) >= 25
    assert len({row['case'] for row in CASES}) == len(CASES)
    for row in CASES:
        assert row['status'] in {item.value for item in Status}
        assert len(row['message']) > 15
        assert row['seam']


@pytest.mark.parametrize('failure', ['killed', 'timeout'])
def test_isolated_engine_boundary_with_injected_binding(packet, monkeypatch, tmp_path, failure):
    import signal
    import time
    from experiments.mvp.pipeline.runner import admission
    # This test's owned process only exits or sleeps; it does no engine work.
    # Admission behavior is exercised independently by test_admission.py.
    monkeypatch.setattr(admission, 'wait_for_capacity', lambda: dict(mask=255,
        enabled_cores=8, load1=0., reading_age_s=0., waited_s=0.))
    def execute(*args, **kwargs):
        if failure == 'killed':
            os.kill(os.getpid(), signal.SIGKILL)
        time.sleep(1.)
    monkeypatch.setattr(core.binding, 'execute_brief', execute)
    monkeypatch.setattr(core, 'engine_call', ISOLATED_ENGINE_CALL)
    out = tmp_path / 'run'
    outcome = core.run_packet(packet, parser=Parser(), out_dir=out,
                              engine_timeout_s=2. if failure == 'killed' else .05)
    verdict(outcome, 'error' if failure == 'killed' else 'no_checked_plan', out)
