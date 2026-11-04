"""Earlier-plan diagnostics use fresh checks, never another search or causal claims."""
from copy import deepcopy
from itertools import count
import json

import pytest

from experiments.mvp.pipeline.runner import core
from experiments.mvp.pipeline.runner.test_runner import (
    packet, gates, fallback_package, Parser, bind, evidence, plan_for, result_for,
    sentence, verdict,
)
from llm.instructions.schema import CloseBay


def prior_run(packet, monkeypatch, tmp_path):
    bind(monkeypatch, lambda instance, receipt, **kw: (result_for(plan_for(instance, 'earlier')), evidence()))
    earlier = core.run_packet(packet, parser=Parser(), out_dir=tmp_path/'earlier')
    assert earlier['status'] == 'checked_plan', earlier['message']
    assert earlier['rules'] == [] and earlier['packet']['voyage_sentences'] == []
    return earlier


def new_engine(monkeypatch, calls, *, stream=False):
    def execute(instance, receipt, **kwargs):
        calls.append(receipt.evidence())
        plan = plan_for(instance, 'new')
        if stream:
            kwargs['on_incumbent'](dict(plan=plan.model_dump(mode='json'), lane='stub-new'))
        return result_for(plan), evidence()
    bind(monkeypatch, execute)


def diagnostic(outcome):
    value = outcome['earlier_plan_recheck']
    assert value == outcome['evidence']['earlier_plan_recheck']
    assert isinstance(value['message'], str) and value['message']
    return value


@pytest.mark.parametrize('violations', [[], ['occupied bay 1', 'occupied bay 1', 'occupied bay 1']])
def test_earlier_plan_checked_once_then_each_new_rule_without_extra_search(
        packet, gates, monkeypatch, tmp_path, violations):
    earlier = prior_run(packet, monkeypatch, tmp_path)
    gates.checks.clear()
    seen, calls = [], []
    def recheck(instance, answer, plan):
        if plan.id == 'earlier':
            seen.append(answer.model_dump())
            return violations
        return []
    monkeypatch.setattr(core, 'intrinsic_violations', recheck)
    new_engine(monkeypatch, calls)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    value = diagnostic(outcome)
    assert value['status'] == 'checked' and value['violations_count'] == len(violations)
    assert value['searched'] is False
    expected = ('The earlier plan broke this limit in 3 places.' if violations else
                'The earlier plan already obeyed it.')
    assert value['message'] == expected
    assert value['rules'][0]['violations_count'] == len(violations)
    assert value['rules'][0]['message'] == expected
    assert len(seen) == 1 and len(calls) == 1
    assert sum(plan.id == 'earlier' for _, plan in gates.checks) == 1
    assert not any(word in value['message'].lower() for word in ('caused', 'saved', 'improved'))


def test_multiple_readings_get_individual_counts_and_aggregate(packet, gates, monkeypatch, tmp_path):
    earlier = prior_run(packet, monkeypatch, tmp_path)
    gates.checks.clear()
    seen, calls = [], []
    def recheck(instance, answer, plan):
        if plan.id != 'earlier':
            return []
        seen.append(answer.bay)
        return ['occupied bay '+str(answer.bay)] * (2 if answer.bay == 1 else 3)
    monkeypatch.setattr(core, 'intrinsic_violations', recheck)
    new_engine(monkeypatch, calls)
    sentence(packet, 'Keep bay 1 clear.')
    sentence(packet, 'Keep bay 3 clear.')
    outcome = core.run_packet(packet, parser=Parser(CloseBay(type='close_bay', bay=1),
        CloseBay(type='close_bay', bay=3)), out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    value = diagnostic(outcome)
    assert value['status'] == 'checked' and value['violations_count'] == 5
    assert value['searched'] is False
    assert [row['violations_count'] for row in value['rules']] == [2, 3]
    assert [row['message'] for row in value['rules']] == [
        'The earlier plan broke this limit in 2 places.', 'The earlier plan broke this limit in 3 places.']
    assert seen == [1, 3] and len(calls) == 1
    assert sum(plan.id == 'earlier' for _, plan in gates.checks) == 1


@pytest.mark.parametrize('problem', ['different_instance', 'different_plan_instance', 'relaxed',
                                    'unconfirmed', 'previous_instructions', 'previous_reading',
                                    'missing_reader_proof', 'unchecked'])
def test_unrelated_or_unchecked_earlier_outcome_does_not_block_main_run(
        packet, gates, monkeypatch, tmp_path, problem):
    earlier = deepcopy(prior_run(packet, monkeypatch, tmp_path))
    if problem == 'different_instance':
        earlier['instance_sha256'] = 'f'*64
    elif problem == 'different_plan_instance':
        earlier['plan']['instance_id'] = 'another-voyage'
    elif problem == 'relaxed':
        earlier.update(status='relaxed_plan', passed=False)
    elif problem == 'unconfirmed':
        earlier.update(status='needs_confirmation', passed=False)
    elif problem == 'previous_instructions':
        earlier['rules'] = [{'reading': {'constraint': {'type': 'close_bay', 'bay': 1}}}]
        earlier['packet']['voyage_sentences'] = [{'text': 'Keep bay 1 clear.', 'origin': 'typed'}]
    elif problem == 'previous_reading':
        earlier['reading']['sentences'].append({'role': 'voyage', 'binds': True, 'sentence': 'Keep bay 1 clear.'})
    elif problem == 'missing_reader_proof':
        earlier.pop('reading')
    else:
        earlier['passed'] = False
    calls = []
    new_engine(monkeypatch, calls)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    value = diagnostic(outcome)
    assert value['status'] == 'unavailable' and value['violations_count'] is None
    assert 'already obeyed' not in value['message'] and len(calls) == 1


@pytest.mark.parametrize('earlier_problem', ['absent', 'checker_rejects', 'checker_raises'])
def test_missing_or_failed_fresh_prior_check_is_unknown_not_obeyed(
        packet, gates, monkeypatch, tmp_path, earlier_problem):
    earlier = None if earlier_problem == 'absent' else prior_run(packet, monkeypatch, tmp_path)
    gates.checks.clear()
    old_check = core.check
    def check(instance, plan):
        if plan.id == 'earlier':
            if earlier_problem == 'checker_raises':
                raise RuntimeError('synthetic prior checker failure')
            gates.checks.append((instance, plan))
            return verdict(False)
        return old_check(instance, plan)
    monkeypatch.setattr(core, 'check', check)
    seen, calls = [], []
    def recheck(instance, answer, plan):
        if plan.id == 'earlier':
            seen.append(plan.id)
        return []
    monkeypatch.setattr(core, 'intrinsic_violations', recheck)
    new_engine(monkeypatch, calls)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    value = diagnostic(outcome)
    assert value['status'] == ('unknown' if earlier_problem == 'absent' else 'unavailable')
    assert value['violations_count'] is None
    assert 'already obeyed' not in value['message'] and not seen and len(calls) == 1


@pytest.mark.parametrize('approval', [None, False])
def test_unconfirmed_current_reading_does_not_check_prior_plan(packet, gates, monkeypatch, tmp_path, approval):
    earlier = prior_run(packet, monkeypatch, tmp_path)
    gates.checks.clear()
    bind(monkeypatch, lambda *a, **kw: pytest.fail('Unconfirmed run executed engine'))
    callback = None if approval is None else lambda _: approval
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=callback)
    assert outcome['status'] == 'needs_confirmation'
    value = diagnostic(outcome)
    assert value['status'] != 'checked' and value['violations_count'] is None
    assert not gates.checks


def test_prior_instruction_recheck_exception_is_unavailable_and_main_run_continues(
        packet, gates, monkeypatch, tmp_path):
    earlier = prior_run(packet, monkeypatch, tmp_path)
    def recheck(instance, answer, plan):
        if plan.id == 'earlier':
            raise RuntimeError('synthetic prior instruction recheck failure')
        return []
    monkeypatch.setattr(core, 'intrinsic_violations', recheck)
    calls = []
    new_engine(monkeypatch, calls)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'checked_plan', outcome['message']
    value = diagnostic(outcome)
    assert value['status'] == 'unavailable' and value['violations_count'] is None
    assert len(calls) == 1


def test_earlier_diagnostic_does_not_count_as_current_first_checked_plan(packet, gates, monkeypatch, tmp_path):
    earlier = prior_run(packet, monkeypatch, tmp_path)
    gates.checks.clear()
    calls = []
    def execute(*a, **kw):
        calls.append(True)
        return None, evidence(status='no_checked_plan', passed=False)
    bind(monkeypatch, execute)
    outcome = core.run_packet(sentence(packet), parser=Parser(CloseBay(type='close_bay', bay=1)),
        out_dir=tmp_path/'new', earlier=earlier, confirm=lambda _: True)
    assert outcome['status'] == 'no_checked_plan'
    assert diagnostic(outcome)['status'] == 'checked'
    assert outcome['first_checked_wall_s'] is outcome['evidence']['first_checked_wall_s'] is None
    assert sum(plan.id == 'earlier' for _, plan in gates.checks) == 1 and len(calls) == 1


@pytest.mark.parametrize('stream', [False, True])
def test_first_checked_timestamp_is_since_run_start_and_matches_first_incumbent(
        packet, gates, monkeypatch, tmp_path, stream):
    ticks = count(1000., .25)
    monkeypatch.setattr(core, 'perf_counter', lambda: next(ticks))
    calls = []
    new_engine(monkeypatch, calls, stream=stream)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path/'new')
    assert outcome['status'] == 'checked_plan', outcome['message']
    first = outcome['first_checked_wall_s']
    assert first == outcome['evidence']['first_checked_wall_s'] and first > 0
    incumbents = [row for row in outcome['events'] if row['kind'] == 'incumbent']
    assert incumbents and first == incumbents[0]['data']['first_checked_wall_s']
    assert first <= incumbents[0]['t_s'] <= first+.5
    reader_end = next(row['t_s'] for row in outcome['events']
                      if row['stage'] == 'reader' and row['kind'] == 'stage_end')
    assert reader_end < first < outcome['wall_s']
    assert outcome['evidence']['runner_wall_s'] == outcome['wall_s']


def test_first_checked_timestamp_is_not_replaced_by_later_improvement(packet, gates, monkeypatch, tmp_path):
    def execute(instance, receipt, **kw):
        first, better = plan_for(instance, 'first'), plan_for(instance, 'better')
        gates.reports[first.id], gates.reports[better.id] = verdict(True, 9.), verdict(True, 7.)
        kw['on_incumbent'](dict(plan=first.model_dump(mode='json')))
        kw['on_incumbent'](dict(plan=better.model_dump(mode='json')))
        return result_for(better), evidence()
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path/'new')
    incumbents = [row for row in outcome['events'] if row['kind'] == 'incumbent']
    assert len(incumbents) == 2
    first = outcome['first_checked_wall_s']
    assert first == outcome['evidence']['first_checked_wall_s']
    assert all(row['data']['first_checked_wall_s'] == first for row in incumbents)
    assert first <= incumbents[0]['t_s'] <= incumbents[1]['t_s']


def test_unchecked_candidate_never_sets_first_checked_timestamp(packet, gates, monkeypatch, tmp_path):
    def execute(instance, receipt, **kw):
        bad = plan_for(instance, 'bad')
        gates.reports[bad.id] = verdict(False)
        kw['on_incumbent'](dict(plan=bad.model_dump(mode='json')))
        return None, evidence(status='no_checked_plan', passed=False)
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path/'new')
    assert outcome['status'] == 'no_checked_plan'
    assert outcome['first_checked_wall_s'] is None
    assert outcome['evidence']['first_checked_wall_s'] is None
    assert not any(row['kind'] == 'incumbent' for row in outcome['events'])


def test_relaxed_plan_is_not_a_first_checked_plan(packet, gates, monkeypatch, tmp_path):
    def execute(instance, receipt, **kw):
        plan = plan_for(instance, 'relaxed')
        gates.reports[plan.id] = verdict(False)
        gates.inspected[plan.id] = dict(level=2, completed=True, broken_rules=['expert.synthetic'],
            boxes_left_ashore=[], boxes_aboard_percent=100., lifts=7.)
        return result_for(plan), evidence(status='relaxed_plan', passed=False)
    bind(monkeypatch, execute)
    outcome = core.run_packet(packet, parser=Parser(), out_dir=tmp_path/'new')
    assert outcome['status'] == 'relaxed_plan' and outcome['passed'] is False
    assert outcome['first_checked_wall_s'] is outcome['evidence']['first_checked_wall_s'] is None


@pytest.mark.parametrize('offline', [False, True])
def test_cli_forwards_earlier_json_only_when_option_is_supplied(tmp_path, monkeypatch, capsys, offline):
    import experiments.mvp.pipeline.runner as runner
    import experiments.mvp.pipeline.runner.__main__ as cli
    import experiments.mvp.pipeline.runner.offline as replay
    packet_file, earlier_file = tmp_path/'packet.json', tmp_path/'earlier.json'
    packet_file.write_text(json.dumps({'packet_id': 'synthetic-cli-test'}))
    previous = {'status': 'checked_plan', 'passed': True, 'plan': {'id': 'synthetic-previous'}}
    earlier_file.write_text(json.dumps(previous))
    seen, parser = [], object()
    monkeypatch.setattr(cli, 'recorded_parser', lambda *a, **kw: parser)
    def run(packet, **kwargs):
        assert packet == {'packet_id': 'synthetic-cli-test'}
        if not offline:
            assert kwargs['parser'] is parser
        seen.append(kwargs)
        return {'status': 'checked_plan', 'passed': True}
    monkeypatch.setattr(runner, 'run_packet', run)
    monkeypatch.setattr(replay, 'run_recorded_packet', run)
    argv = [str(packet_file), '--out', str(tmp_path/'out'), '--confirm-all']
    if offline:
        argv.append('--offline')
    assert cli.main([*argv, '--earlier', str(earlier_file)]) == 0
    assert seen[-1]['earlier'] == previous
    assert json.loads(capsys.readouterr().out)['status'] == 'checked_plan'
    assert cli.main(argv) == 0
    assert 'earlier' not in seen[-1]
    assert json.loads(capsys.readouterr().out)['status'] == 'checked_plan'
