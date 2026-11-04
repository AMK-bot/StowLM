"""Runner safety gates, independent of search, network and GPU availability."""
from copy import deepcopy
import base64
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from experiments.mvp.pipeline.contract.schema import (
    Event, Receipt, VoyagePacket, chain_intact, sha256_file, sha256_json,
)
from experiments.mvp.pipeline.runner import core
from experiments.mvp.reader.schema import OnePortPerHatch
from llm.instructions.schema import CloseBay, Refusal
from stowage.model.plan import MovePlan
from stowage.model.results import CheckReport, MoveCount, PartReport, PlannerResult


ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'experiments/mvp/corpus/val/tiny/feeder_800/random_yard/seed-020000004.json'
PROVIDER = {'parser': 'runner-test', 'model': 'synthetic fixture', 'adapter_sha256': None}


@pytest.fixture(autouse=True)
def fallback_package(monkeypatch):
    # Package task may land while this suite runs; runner unit tests stay isolated.
    monkeypatch.setattr(core, '_package_writer', lambda: None)
    # Orchestration tests observe mutable fixtures in this process. Production
    # and the process-death integration tests use the real isolated engine seam.
    from experiments.mvp.pipeline.runner.limits import run_in_process_with_limit
    def execute(instance, receipt, profile, on_incumbent, *, timeout_s):
        return run_in_process_with_limit(core.binding.execute_brief, instance, receipt,
            timeout_s=timeout_s, stage='engine', on_incumbent=on_incumbent,
            **{key: profile[key] for key in ('budget_s', 'finalize_s', 'seed', 'config')})
    monkeypatch.setattr(core, 'engine_call', execute)


class Parser:
    def __init__(self, *answers, failure=None):
        self.answers = iter(answers)
        self.failure = failure
        self.calls = []

    def parse(self, sentence, context):
        self.calls.append((sentence, deepcopy(context)))
        if self.failure:
            raise self.failure
        answer = (OnePortPerHatch(type='one_port_per_hatch') if sentence == 'One port per hatch.'
                  else next(self.answers))
        return answer, dict(PROVIDER)


@pytest.fixture
def packet():
    return VoyagePacket(packet_id='runner-synthetic-test', created_utc='2026-11-01T00:00:00Z',
                        base_instance={'path': str(BASE), 'sha256': sha256_file(BASE)}).model_dump(mode='json')


def sentence(packet, text='Keep bay 1 clear.'):
    packet['voyage_sentences'].append({'text': text, 'origin': 'typed'})
    return packet


@pytest.fixture
def gates(monkeypatch):
    """Control only the deterministic checker seam, never engine claim fields."""
    reports, inspected, checks = {}, {}, []
    def check(instance, plan):
        checks.append((instance, plan))
        return reports.get(plan.id, verdict(True))
    def inspect(instance, plan, report=None):
        report = report or check(instance, plan)
        return inspected.get(plan.id, dict(level=0 if report.passed else None,
            broken_rules=[], boxes_left_ashore=[], completed=True,
            lifts=report.count.weighted_nonproductive, t0_peeled=False))
    monkeypatch.setattr(core, 'check', check)
    monkeypatch.setattr(core, 'inspect', inspect)
    monkeypatch.setattr(core, 'intrinsic_violations', lambda *a: [])
    return NS(reports=reports, inspected=inspected, checks=checks)


def verdict(passed, lifts=7.):
    names = ('moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints')
    return CheckReport(passed=passed, count=MoveCount(weighted_nonproductive=lifts),
                       parts={name: PartReport(name=name) for name in names})


def plan_for(instance, ident='checked'):
    return MovePlan(id=ident, instance_id=instance.id, call_index=instance.call_index)


def result_for(plan):
    return PlannerResult(status='ok', plan=plan, planner='runner-test', planner_version='fixture')


def evidence(**update):
    return dict(status='checked_plan', passed=True, checked=True,
        engine_version='0.13.0', engine_manifest_sha256='a'*64, lane='stub-page',
        stages={}, rules=[], checker={'passed': True}, lifts=7., level=0,
        broken_rules=[], boxes_ashore=[], wall_s=0., budget_s=0.,
        instruction_recheck_passed=True) | update


def bind(monkeypatch, fn):
    monkeypatch.setattr(core.binding, 'execute_brief', fn)


def assert_clear(outcome, expected):
    assert outcome['status'] == expected
    assert isinstance(outcome['message'], str) and outcome['message'].strip()
    assert 'Traceback' not in outcome['message']
    assert outcome['passed'] is (expected == 'checked_plan')
    json.dumps(outcome, allow_nan=False)


def test_empty_voyage_uses_reader_preference_without_confirmation(packet, gates, monkeypatch, tmp_path):
    calls = []
    def execute(instance, receipt, **kwargs):
        calls.append(receipt.evidence())
        return result_for(plan_for(instance)), evidence()
    bind(monkeypatch, execute)
    parser = Parser()
    outcome = core.run_packet(packet, parser=parser, out_dir=tmp_path,
                              confirm=lambda _: pytest.fail('Standing text is not an instruction'))
    assert_clear(outcome, 'checked_plan')
    assert parser.calls and calls[0]['standing_is_preference']
    assert calls[0]['sentences'][0]['role'] == 'standing'
    assert gates.checks


def test_reading_requires_explicit_planner_confirmation(packet, monkeypatch, tmp_path):
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Engine ran before confirmation'))
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)), out_dir=tmp_path)
    assert_clear(outcome, 'needs_confirmation')
    assert outcome.get('plan') is None
    assert any(e['kind'] == 'question' for e in outcome['events'])


def test_confirmation_is_per_voyage_reading_and_authenticated(packet, gates, monkeypatch, tmp_path):
    sentence(packet, 'Keep bay 1 clear.')
    sentence(packet, 'Do not load in bay 1.')
    seen, calls = [], []
    def execute(instance, receipt, **kwargs):
        calls.append(receipt.evidence())
        return result_for(plan_for(instance)), evidence()
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(*[CloseBay(type='close_bay', bay=1)]*2),
                              out_dir=tmp_path, confirm=lambda row: seen.append(deepcopy(row)) or True)
    assert_clear(outcome, 'checked_plan')
    assert len(seen) == 2 and all(row['role'] == 'voyage' for row in seen)
    assert len(calls) == 1 and sum(row['binds'] for row in calls[0]['sentences']) == 2


@pytest.mark.parametrize('confirm', [None, lambda row: False, lambda row: True])
def test_refused_sentence_never_reaches_engine(packet, monkeypatch, tmp_path, confirm):
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Refusal reached engine'))
    parser = Parser(Refusal(code='ambiguous', reason='Which bay should stay clear?'))
    outcome = core.run_packet(sentence(packet, 'Keep it clear.'), parser=parser, out_dir=tmp_path, confirm=confirm)
    assert_clear(outcome, 'needs_confirmation')
    assert outcome.get('plan') is None
    assert any(e['kind'] == 'refusal' for e in outcome['events'])


def test_confirmation_decline_stops_binding(packet, monkeypatch, tmp_path):
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Declined instruction reached engine'))
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
                              out_dir=tmp_path, confirm=lambda _: False)
    assert_clear(outcome, 'needs_confirmation')


def test_envelope_gate_precedes_reader(packet, monkeypatch, tmp_path):
    monkeypatch.setattr(core.envelope, 'check', lambda _: dict(in_envelope=False, size_class='tiny',
        reasons=[dict(test='fill', measured=.71, limit=.70, message='Fill 71 % exceeds the 70 % limit')],
        numbers={'fill': 0.71, 'fill_limit': 0.7}))
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Out-of-envelope input reached engine'))
    parser = Parser(failure=AssertionError('Reader ran before gate'))
    outcome = core.run_packet(packet, parser=parser, out_dir=tmp_path)
    assert_clear(outcome, 'out_of_envelope')
    assert not parser.calls
    assert '71' in outcome['message'] and '70' in outcome['message']


def test_binding_claims_cannot_release_failed_checker_candidate(packet, gates, monkeypatch, tmp_path):
    def execute(instance, receipt, **kwargs):
        bad = plan_for(instance, 'failed-full-check')
        gates.reports[bad.id] = verdict(False)
        kwargs['on_incumbent'](dict(plan=bad.model_dump(mode='json'), **evidence()))
        return result_for(bad), evidence()
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'no_checked_plan')
    assert outcome.get('plan') is None
    assert not [e for e in outcome['events'] if e['kind'] == 'incumbent']
    assert gates.checks


def test_binding_claims_cannot_release_instruction_violation(packet, gates, monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'intrinsic_violations', lambda *a: ['bay 1 is occupied'])
    def execute(instance, receipt, **kwargs):
        bad = plan_for(instance)
        kwargs['on_incumbent'](dict(plan=bad.model_dump(mode='json'), **evidence()))
        return result_for(bad), evidence()
    bind(monkeypatch, execute)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
                              out_dir=tmp_path, confirm=lambda _: True)
    assert_clear(outcome, 'no_checked_plan')
    assert outcome.get('plan') is None
    assert not [e for e in outcome['events'] if e['kind'] == 'incumbent']


def test_checked_incumbents_stream_as_json_with_matching_file_events(packet, gates, monkeypatch, tmp_path):
    streamed = []
    def execute(instance, receipt, **kwargs):
        plan = plan_for(instance)
        kwargs['on_incumbent'](dict(plan=plan.model_dump(mode='json'), **evidence()))
        return result_for(plan), evidence()
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, on_event=streamed.append)
    assert_clear(outcome, 'checked_plan')
    rows = [json.loads(line) for line in (tmp_path/'events.jsonl').read_text().splitlines()]
    assert rows == streamed == outcome['events']
    assert [e['seq'] for e in rows] == list(range(rows[0]['seq'], rows[0]['seq']+len(rows)))
    assert all(a['t_s'] <= b['t_s'] for a, b in zip(rows, rows[1:]))
    for row in rows:
        Event.model_validate(row)
    incumbents = [e for e in rows if e['kind'] == 'incumbent']
    assert incumbents and all(e['data']['checked'] is True for e in incumbents)
    assert incumbents[-1]['data']['plan_sha256'] == sha256_json(outcome['plan'])
    assert rows[-1]['kind'] == 'done'


def test_receipt_is_chained_and_names_exact_input_and_plan(packet, gates, monkeypatch, tmp_path):
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'checked_plan')
    receipt = Receipt.model_validate(outcome['receipt'])
    assert chain_intact(receipt.steps) and receipt.steps
    assert receipt.packet_sha256 == sha256_json(VoyagePacket.model_validate(packet).model_dump(mode='json'))
    assert receipt.instance_sha256 == sha256_json(core.assemble(packet).model_dump(mode='json'))
    assert receipt.plan_sha256 == sha256_json(outcome['plan'])
    assert receipt.checker['passed'] is True and receipt.synthetic is True
    assert receipt.lifts == 7.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from experiments.mvp.pipeline.contract.schema import canonical_json
    signed = outcome['receipt']
    unsigned = {key: value for key, value in signed.items() if key != 'signature'}
    Ed25519PublicKey.from_public_bytes(base64.b64decode(signed['public_key'], validate=True)).verify(
        base64.b64decode(signed['signature'], validate=True), canonical_json(unsigned).encode())
    changed = receipt.steps[0].model_copy(update={'notes': 'tampered'})
    assert len(receipt.steps) > 1 and not chain_intact([changed, *receipt.steps[1:]])


@pytest.mark.parametrize('stage', ['reader', 'engine', 'checker', 'confirmation'])
def test_exceptions_end_with_clear_error(packet, gates, monkeypatch, tmp_path, stage):
    def broken(*a, **kw):
        raise RuntimeError('synthetic '+stage+' failure')
    parser = Parser(failure=RuntimeError('synthetic reader failure')) if stage == 'reader' else Parser()
    confirm = None
    if stage == 'confirmation':
        sentence(packet)
        parser = Parser(CloseBay(type='close_bay', bay=1))
        confirm = broken
    if stage == 'checker':
        monkeypatch.setattr(core, 'check', broken)
    bind(monkeypatch, broken if stage == 'engine' else
         lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(packet, parser=parser, out_dir=tmp_path, confirm=confirm)
    assert_clear(outcome, 'error')
    assert outcome.get('plan') is None
    assert outcome['events'][-1]['kind'] == 'done'


def test_invalid_packet_hash_is_clear_and_never_reaches_reader(packet, monkeypatch, tmp_path):
    packet['base_instance']['sha256'] = '0'*64
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Bad input hash reached binding'))
    parser = Parser()
    outcome = core.run_packet(packet, parser=parser, out_dir=tmp_path)
    assert_clear(outcome, 'error')
    assert not parser.calls and outcome.get('plan') is None


def test_package_fallback_writes_complete_json(packet, gates, monkeypatch, tmp_path):
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'checked_plan')
    assert json.loads((tmp_path/'outcome.json').read_text()) == outcome
    assert any('Package writer unavailable' in text for text in outcome['warnings'])


def test_package_writer_is_called_with_checked_outcome(packet, gates, monkeypatch, tmp_path):
    seen = []
    def writer(outcome, directory):
        assert outcome['status'] == 'checked_plan' and outcome['receipt']['checker']['passed']
        seen.append((deepcopy(outcome), Path(directory)))
        return {'schema_version': 'stowlm.plan-package/1', 'path': str(directory/'package.json')}
    monkeypatch.setattr(core, '_package_writer', lambda: writer)
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'checked_plan')
    assert len(seen) == 1 and seen[0][1].parent == tmp_path
    assert seen[0][1].name.startswith('.package-')
    assert (tmp_path / 'package').is_dir()
    assert not seen[0][1].exists()
    assert outcome['package']['schema_version'] == 'stowlm.plan-package/1'


def test_relaxed_plan_never_counts_as_pass_and_lists_broken_rules(packet, gates, monkeypatch, tmp_path):
    broken = ['expert.e2.weight_order']
    def execute(instance, receipt, **kwargs):
        plan = plan_for(instance, 'relaxed')
        gates.reports[plan.id] = verdict(False)
        gates.inspected[plan.id] = dict(level=2, broken_rules=broken, boxes_left_ashore=[],
            completed=True, lifts=7., t0_peeled=False)
        return result_for(plan), evidence(status='relaxed_plan', passed=False, level=2, broken_rules=broken)
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'relaxed_plan')
    assert outcome['plan'] and outcome['evidence']['broken_rules'] == broken
    assert broken[0] in outcome['message']
    assert outcome['receipt']['checker']['passed'] is False
    assert not [e for e in outcome['events'] if e['kind'] == 'incumbent']


@pytest.mark.parametrize('final', ['absent', 'unchecked'])
def test_legal_incumbent_survives_unusable_final_result(packet, gates, monkeypatch, tmp_path, final):
    def execute(instance, receipt, **kwargs):
        plan = plan_for(instance)
        kwargs['on_incumbent'](dict(plan=plan.model_dump(mode='json'), **evidence()))
        if final == 'unchecked':
            bad = plan_for(instance, 'failed-final')
            gates.reports[bad.id] = verdict(False)
            return result_for(bad), evidence()
        return None, evidence(status='no_checked_plan', passed=False)
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path)
    assert_clear(outcome, 'checked_plan')
    assert outcome['plan'] and outcome['receipt']['checker']['passed']


def test_app_recorded_parser_keeps_recorded_stage_label(packet, gates, monkeypatch, tmp_path):
    from app.brief import RecordedParser
    class Readings:
        def find(self, text, context):
            answer = (OnePortPerHatch(type='one_port_per_hatch') if text == 'One port per hatch.'
                      else CloseBay(type='close_bay', bay=1))
            return answer, dict(PROVIDER, recorded=True, source='synthetic runner fixture'), 0.01
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(sentence(packet), parser=RecordedParser(Readings()),
                              out_dir=tmp_path, confirm=lambda _: True)
    assert_clear(outcome, 'checked_plan')
    rows = [row for row in outcome['receipt']['steps'] if row['stage'] == 'reader']
    assert rows and all(row['mode'] == 'recorded' for row in rows)


def stored_for(packet):
    instance = core.assemble(packet)
    identity = core._engine_identity()
    return dict(instance_sha256=sha256_json(instance.model_dump(mode='json')), canonical_rules=[],
                computed_utc='2026-10-31T00:00:00Z', engine_version=identity['engine_version'],
                engine_manifest_sha256=identity['engine_manifest_sha256'], plan=plan_for(instance).model_dump(mode='json'))


def test_stored_plan_is_labelled_and_rechecked_without_engine(packet, gates, monkeypatch, tmp_path):
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Stored lookup ran search'))
    stored = stored_for(packet)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored, stored_sha256=sha256_json(stored))
    assert_clear(outcome, 'checked_plan')
    assert gates.checks and outcome['label'] == outcome['receipt']['label'] == 'stored'
    assert '2026-10-31' in outcome['stored_message'] and 'rechecked now' in outcome['stored_message']


@pytest.mark.parametrize('tamper', ['instance_sha256', 'canonical_rules', 'computed_utc'])
def test_stored_plan_requires_exact_identity_rules_and_date(packet, gates, monkeypatch, tmp_path, tamper):
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Invalid stored entry ran search'))
    stored = stored_for(packet)
    stored[tamper] = 'wrong' if tamper != 'canonical_rules' else [{'constraint': 'wrong'}]
    if tamper == 'computed_utc':
        stored[tamper] = None
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored, stored_sha256=sha256_json(stored))
    assert_clear(outcome, 'error')
    assert outcome.get('plan') is None


def test_stored_plan_failing_fresh_checker_is_withheld(packet, gates, monkeypatch, tmp_path):
    stored = stored_for(packet)
    gates.reports[stored['plan']['id']] = verdict(False)
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Stored recheck ran search'))
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, stored=stored, stored_sha256=sha256_json(stored))
    assert_clear(outcome, 'no_checked_plan')
    assert outcome.get('plan') is None and gates.checks


def test_event_callback_failure_does_not_erase_checked_plan(packet, gates, monkeypatch, tmp_path):
    def unavailable(_):
        raise RuntimeError('synthetic screen disconnected')
    bind(monkeypatch, lambda instance, receipt, **kwargs: (result_for(plan_for(instance)), evidence()))
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path, on_event=unavailable)
    assert_clear(outcome, 'checked_plan')
    assert outcome['plan'] and any('Event callback failed' in row for row in outcome['warnings'])
