"""Replay binding, held-cost and blocked-null tests. Fakes only: no physics, no cache runtime, no HTTP.

uv run pytest experiments/spark-planner/w2-pilot/test_replay_binding.py -q
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bind_existing  # noqa: E402
import blocked_metrics  # noqa: E402
import replay_pilot  # noqa: E402
import summarize  # noqa: E402
from binding import FULL_LABEL, PRE_FIX_PROMPT_BINDING, UNBOUND_LABEL, digest, instance_digest  # noqa: E402

IID = 'v3.simulated_yard.tiny.1'
VERSION = 'stowlm-prompt@3.0'
CAP = 65536
RESPONSE = 'P000 0010182\n<|eot|>\n'


class FakeInstance:
    def __init__(self, id, payload='boxes=4'):
        self.id, self.payload = id, payload

    def model_dump_json(self):
        return json.dumps({'id': self.id, 'payload': self.payload})


def build_prompt(instance):
    return f'compact observation of {instance.id}: {instance.payload}'


class FakePlanner:
    """Mimics SparkPlanner: one complete() call per plan, client exceptions become an error result."""
    def __init__(self, client, prompt_override=None):
        self.client, self.prompt_override = client, prompt_override

    def plan(self, instance, **kwargs):
        prompt = self.prompt_override or build_prompt(instance)
        try:
            response = self.client.complete(prompt, purpose='muse-spark-zero-shot', max_output_tokens=CAP)
        except Exception as exc:
            return SimpleNamespace(shippable=False, repaired=False, metrics={'error_type': type(exc).__name__})
        return SimpleNamespace(shippable=True, repaired=False,
                               metrics={'raw_plan_legal': int(response.text.endswith('<|eot|>\n'))})


@pytest.fixture
def setup(tmp_path):
    instance = FakeInstance(IID)
    (tmp_path/'responses').mkdir()
    (tmp_path/'prompts').mkdir()
    (tmp_path/'responses'/f'{IID}.txt').write_bytes(RESPONSE.encode())
    (tmp_path/'prompts'/f'{IID}.txt').write_bytes(build_prompt(instance).encode())
    row = dict(instance_id=IID, instance_path=f'instances/{IID}.json', instance_sha256=instance_digest(instance),
               prompt_version=VERSION, max_output_tokens=CAP, prompt_sha256=digest(build_prompt(instance)),
               prompt_binding='recorded at paid-call boundary', response_sha256=digest(RESPONSE), rung='tiny',
               rule_freeze_v1=True, input_tokens=10, output_tokens=20)
    state = SimpleNamespace(instance=instance, scored=0, prompt_override=None, version=VERSION,
                            build_prompt=build_prompt)

    def run_planner(planner, inst, **kwargs):
        state.scored += 1
        result = planner.plan(inst)
        return result, SimpleNamespace(passed=result.shippable, count=None)

    deps = replay_pilot.Deps(
        load_instance=lambda path: state.instance,
        build_prompt=lambda inst: state.build_prompt(inst),
        prompt_version=lambda inst: state.version,
        make_planner=lambda client, row: FakePlanner(client, state.prompt_override),
        run_planner=run_planner,
        make_usage=lambda row: (row['input_tokens'], row['output_tokens']),
        make_response=lambda text, usage: SimpleNamespace(text=text, usage=usage))
    return SimpleNamespace(root=tmp_path, row=row, deps=deps, state=state)


def replay(s, row=None, **kwargs):
    return replay_pilot.replay_row(row or s.row, s.deps, root=s.root, scoring_epoch={'git_head': 'abc'}, **kwargs)


def assert_refused(out, s, fragment):
    assert out['replayed'] is False
    assert fragment in out['reason']
    assert 'raw_plan_legal' not in out and 'own_checker_passed' not in out


def test_matching_binding_reaches_scoring(setup):
    out = replay(setup)
    assert out['replayed'] is True and out['binding'] == FULL_LABEL
    assert out['raw_plan_legal'] is True and out['own_checker_passed'] is True
    assert out['replay_prompt_sha256'] == setup.row['prompt_sha256']
    assert out['generation_identity']['instance_sha256'] == setup.row['instance_sha256']
    assert out['scoring_epoch'] == {'git_head': 'abc'}
    assert setup.state.scored == 1


def test_tampered_instance_refused(setup):
    setup.state.instance = FakeInstance(IID, payload='boxes=5')
    out = replay(setup)
    assert_refused(out, setup, 'instance sha256')
    assert setup.state.scored == 0


def test_substituted_instance_id_refused(setup):
    setup.state.instance = FakeInstance('REPLACEMENT-INSTANCE')
    assert_refused(replay(setup), setup, 'instance id')
    assert setup.state.scored == 0


def test_changed_prompt_builder_refused(setup):
    setup.state.build_prompt = lambda inst: 'DIFFERENT PROMPT BYTES'
    assert_refused(replay(setup), setup, 'rebuilt prompt')
    assert setup.state.scored == 0


def test_prompt_changed_at_client_boundary_refused(setup):
    # The planner hands the stub a different prompt than the one recorded: SavedClient refuses.
    setup.state.prompt_override = 'DIFFERENT PROMPT BYTES'
    out = replay(setup)
    assert_refused(out, setup, 'recorded prompt_sha256')


def test_tampered_saved_prompt_file_refused(setup):
    (setup.root/'prompts'/f'{IID}.txt').write_bytes(b'edited prompt')
    assert_refused(replay(setup), setup, 'saved prompt file')
    assert setup.state.scored == 0


def test_prompt_version_change_refused(setup):
    setup.state.version = 'stowlm-prompt@4.0'
    assert_refused(replay(setup), setup, 'prompt version')
    assert setup.state.scored == 0


def test_swapped_response_refused(setup):
    (setup.root/'responses'/f'{IID}.txt').write_bytes(b'P001 0010102\n<|eot|>\n')
    assert_refused(replay(setup), setup, 'response file sha256')
    assert setup.state.scored == 0


def test_missing_response_hash_refused(setup):
    row = dict(setup.row, response_sha256=None)
    assert_refused(replay(setup, row), setup, 'response hash not recorded')


def test_appendix_mismatch_refused(setup):
    row = dict(setup.row, prompt_appendix='rules-appendix@1')
    assert_refused(replay(setup, row), setup, 'appendix')
    assert setup.state.scored == 0


def test_unbound_prompt_refused_unless_accepted(setup):
    (setup.root/'prompts'/f'{IID}.txt').unlink()
    row = dict(setup.row, prompt_sha256=None, prompt_binding=PRE_FIX_PROMPT_BINDING)
    out = replay(setup, row)
    assert_refused(out, setup, 'prompt bytes not recorded')
    assert out['reconstructed_prompt_sha256'] == digest(build_prompt(setup.state.instance))
    assert setup.state.scored == 0
    accepted = replay(setup, row, accept_unbound_prompt=True)
    assert accepted['replayed'] is True and accepted['binding'] == UNBOUND_LABEL
    # The instance and response checks still apply under the flag.
    (setup.root/'responses'/f'{IID}.txt').write_bytes(b'swapped')
    assert_refused(replay(setup, row, accept_unbound_prompt=True), setup, 'response file sha256')


def test_main_writes_rows_without_runtime(setup):
    results = setup.root/'results.jsonl'
    other = dict(setup.row, instance_id='v3.no-response', response_sha256=None)
    results.write_text(json.dumps(setup.row)+'\n'+json.dumps(other)+'\n')
    replay_pilot.main(['--results', str(results), '--out', str(setup.root/'replay.jsonl')], deps=setup.deps)
    rows = [json.loads(line) for line in (setup.root/'replay.jsonl').read_text().splitlines()]
    assert [r['replayed'] for r in rows] == [True, False]
    assert 'git_head' in rows[0]['scoring_epoch']


def test_import_is_side_effect_free():
    assert 'pilot_cache_runtime' not in sys.modules


def test_bind_existing_marks_pre_fix_rows(tmp_path):
    (tmp_path/'responses').mkdir()
    (tmp_path/'responses'/'a.txt').write_bytes(b'text')
    rows = [dict(instance_id='a', attempted=True), dict(instance_id='b', attempted=False),
            dict(instance_id='c', prompt_sha256='f'*64, prompt_binding='recorded at paid-call boundary',
                 response_sha256='e'*64)]
    path = tmp_path/'results.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    a, b, c = bind_existing.main(path)
    assert a['response_sha256'] == digest(b'text') and a['prompt_sha256'] is None
    assert a['prompt_binding'] == PRE_FIX_PROMPT_BINDING
    assert b['response_sha256'] is None and b['prompt_binding'] == bind_existing.NO_REQUEST
    assert c == rows[2]
    assert bind_existing.main(path) == [a, b, c]  # idempotent


def test_unsettled_reservation_stays_charged():
    ledger = [
        dict(phase='reservation', reservation_id='old', purpose='harness-test', cost_usd='9'),  # before the job
        dict(phase='settlement', reservation_id='old', purpose='harness-test', cost_usd='1', cost_kind='measured'),
        dict(phase='reservation', reservation_id='t', purpose='harness-test', cost_usd='1.3'),
        dict(phase='settlement', reservation_id='t', purpose='harness-test', cost_usd='0.0005', cost_kind='measured'),
        dict(phase='reservation', reservation_id='x', purpose='muse-spark-explainer', cost_usd='2'),  # other job
        dict(phase='settlement', reservation_id='x', purpose='muse-spark-explainer', cost_usd='0.5',
             cost_kind='measured'),
        # Interrupted request: reserved, never settled.
        dict(phase='reservation', reservation_id='lost', purpose='muse-spark-zero-shot', cost_usd='1.359872'),
        dict(phase='reservation', reservation_id='p', purpose='muse-spark-zero-shot', cost_usd='0.3'),
        dict(phase='settlement', reservation_id='p', purpose='muse-spark-zero-shot', cost_usd='0.12',
             cost_kind='measured'),
    ]
    records = summarize.job_records(ledger, 2)
    assert {r['reservation_id'] for r in records} == {'t', 'lost', 'p'}
    measured, held, unsettled = summarize.job_totals(records)
    assert measured == Decimal('0.1205')
    assert held == Decimal('1.359872')
    assert unsettled == ['lost']


def test_blocked_metrics_null_and_measurable_aggregate():
    blocked = blocked_metrics.apply(dict(environment_blocked=True, raw_plan_legal=False, own_checker_passed=False,
                                         first_illegal_action_index=0, fallback_needed=True))
    assert blocked['raw_plan_legal'] is None and blocked['own_checker_passed'] is None
    assert blocked['first_illegal_action_index'] is None and blocked['fallback_needed'] is True
    measurable = dict(environment_blocked=False, raw_plan_legal=True)
    assert summarize.measurable_count([blocked, blocked], 'raw_plan_legal') == (None, 0)
    assert summarize.measurable_count([blocked, measurable], 'raw_plan_legal') == (1, 1)
