"""Zero-shot planner contract: real client/ledger and engine, mocked HTTP only."""
from __future__ import annotations

import io
import json
from decimal import Decimal
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from llm.spark.client import SparkClient
from llm.spark.planner import SparkPlanner, build_prompt
from llm.stowlm.format import ActionCodec, END_PLAN
from llm.stowlm.format_prompt import instance_to_prompt
from stowage.actions import get_action_space
from stowage.feasibility import make_feasibility_engine
from stowage.actions.samples import tiny_instance
from stowage.planner import Planner


class CharTokenizer:
    """CPU-only exact codec stand-in; no tokenizer download or model loading."""
    def encode(self, text, add_special_tokens=False):
        return SimpleNamespace(ids=[ord(c) for c in text])

    def decode(self, ids, skip_special_tokens=False):
        return "".join(chr(i) for i in ids)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    def deny(*args, **kwargs):
        pytest.fail("Unmocked HTTP request")
    monkeypatch.setattr("urllib.request.urlopen", deny)


@pytest.fixture
def instance():
    inst = tiny_instance(0, n_load=3, n_other=1, p40=0)
    # The tiny fixture's hydrostatics are intentionally incomplete: the normal
    # hull-free fixture mode exercises exact move/static checks, with no C2 LP.
    return inst.model_copy(update={
        "vessel": inst.vessel.model_copy(update={"hull": None}),
        # SYNTHETIC test thresholds, matching the feasibility fixture suite.
        "rule_overrides": {"expert.e1.heavy_bottom": {"heavy_threshold": 20000},
                           "expert.e2.weight_order": {"tolerance": 2000},
                           "expert.e6.heavy_under_deck": {"heavy_threshold": 30000}},
    })


@pytest.fixture
def setup(tmp_path, monkeypatch, instance):
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n\n| Date | Source | Amount | What for | Remaining |\n|---|---|---|---|---|\n")
    ledger = tmp_path / "calls.jsonl"
    client = SparkClient("synthetic", ledger_path=ledger, compute_path=compute,
                         max_retries=0, spend_cap_usd=Decimal("60"), credit_ready=True)
    tokenizer = CharTokenizer()
    codec = ActionCodec(tokenizer, [box.id for box in instance.containers])
    engine = make_feasibility_engine(instance, get_action_space())
    actions = []
    while not engine.is_complete():
        action = engine.legal_actions()[0]
        actions.append(action)
        engine.push(action)
    good_text = tokenizer.decode(codec.encode_plan(actions))
    calls = []

    def respond(text=good_text, status=200, response_status="completed"):
        def send(request, **kwargs):
            calls.append(json.loads(request.data))
            if status != 200:
                raise HTTPError(request.full_url, status, "mock", {}, io.BytesIO(b"{}"))
            data = {"id": "mock", "status": response_status, "output": [
                {"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 100, "output_tokens": 50}}
            body = io.BytesIO(json.dumps(data).encode())
            body.headers = {}
            return body
        monkeypatch.setattr("urllib.request.urlopen", send)

    respond()
    return SimpleNamespace(planner=SparkPlanner(client, tokenizer=tokenizer), respond=respond,
                           calls=calls, good=good_text, codec=codec, actions=actions, ledger=ledger)


def test_protocol_prompt_and_complete_replay(instance, setup):
    planner = setup.planner
    assert isinstance(planner, Planner)
    assert planner.name == "muse-spark-zero-shot"
    assert not planner.deterministic and not planner.sets_ballast
    result = planner.plan(instance, seed=3)
    assert result.status == "ok", result
    assert result.plan.ballast is None
    assert result.metrics["raw_plan_legal"] == 1
    assert result.metrics["legal_action_count"] == len(setup.actions)
    assert result.metrics["tokens_in"] == 100
    assert result.metrics["tokens_out"] == 50
    assert Decimal(result.metrics["cost_usd"]) > 0
    assert setup.calls[0]["input"] == build_prompt(instance)
    assert instance_to_prompt(instance) in setup.calls[0]["input"]
    assert len(setup.ledger.read_text().splitlines()) == 2
    assert result.provenance.parents == [instance.id]


def test_first_illegal_action_stops_and_retains_prefix(instance, setup):
    first = "".join(setup.codec.action_fragments(setup.actions[0]))
    # Repeating a placement is syntactically valid, but cannot be pushed twice.
    setup.respond(first + first + setup.good)
    result = setup.planner.plan(instance)
    assert result.status == "dead_end"
    assert result.metrics["first_illegal_action_index"] == 1
    assert result.metrics["legal_action_count"] == 1
    assert result.metrics["raw_plan_legal"] == 0
    assert len(result.plan.actions) == 1


@pytest.mark.parametrize("bad", ["P999 0010102\n", "prose\n", "P000 0010102", "```\n", "P0000 0010102\n"])
def test_parse_errors_do_not_discard_legal_prefix(instance, setup, bad):
    first = "".join(setup.codec.action_fragments(setup.actions[0]))
    setup.respond(first + bad + END_PLAN)
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert result.metrics["first_parse_error_index"] == 1
    assert result.metrics["legal_action_count"] == 1
    assert result.plan is not None


@pytest.mark.parametrize("tail", ["\n", " \n\n"])
def test_whitespace_after_end_marker_is_accepted(instance, setup, tail):
    setup.respond(setup.good + tail)
    result = setup.planner.plan(instance)
    assert result.status == "ok", result
    assert result.metrics["raw_plan_legal"] == 1
    assert result.metrics["first_parse_error_index"] is None


def test_text_after_end_marker_is_still_rejected(instance, setup):
    setup.respond(setup.good + "P000 0010102\n")
    result = setup.planner.plan(instance)
    assert result.metrics["raw_plan_legal"] == 0


def test_incomplete_and_missing_end_marker(instance, setup):
    setup.respond(END_PLAN)
    result = setup.planner.plan(instance)
    assert result.status == "dead_end"
    assert result.metrics["output_parse_ok"] == 1
    assert result.metrics["raw_plan_legal"] == 0
    setup.respond(setup.good.removesuffix(END_PLAN))
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert result.metrics["complete"] == 1
    assert result.metrics["raw_plan_legal"] == 0


def test_api_failure_is_safe_and_logged(instance, setup):
    setup.respond(status=401)
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert result.metrics["error_type"] == "SparkHTTPError"
    assert result.plan is None
    assert len(setup.calls) == 1
    assert "synthetic" not in (result.message or "")


def test_budget_refuses_before_http(instance, setup):
    setup.planner.allocation_usd = Decimal("0")
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert setup.calls == []


def test_injected_high_cap_client_cannot_exceed_allocation(instance, setup):
    setup.planner.client.ledger.cap_usd = Decimal("100")
    # The proxy prompt estimate fits, but the authoritative context reservation
    # costs more than $0.50. No optimistic proxy may authorize that spend.
    setup.planner.allocation_usd = Decimal("0.50")
    result = setup.planner.plan(instance)
    assert result.metrics["error_type"] == "SpendCapExceeded"
    assert setup.calls == []
    assert setup.planner.client.ledger.total_usd() == 0


def test_uncertain_retry_cannot_exceed_planner_allocation(instance, setup, monkeypatch):
    import urllib.request

    client = setup.planner.client
    client.ledger.cap_usd = Decimal("100")
    client.max_retries = 1
    client._sleep = lambda _: None
    setup.planner.allocation_usd = Decimal("2")
    setup.respond(status=500)
    fake_send = urllib.request.urlopen

    def assert_durable_before_send(*args, **kwargs):
        assert "cap-usd=100;" in client.ledger.compute_path.read_text()
        return fake_send(*args, **kwargs)

    monkeypatch.setattr("urllib.request.urlopen", assert_durable_before_send)
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert result.metrics["error_type"] == "SpendCapExceeded"
    assert len(setup.calls) == 1
    assert Decimal("1") < client.ledger.total_usd() < Decimal("2")
    # The job allowance must not consume another job's project ceiling.
    other = SparkClient("synthetic", ledger_path=client.ledger.path,
                        compute_path=client.ledger.compute_path,
                        spend_cap_usd=Decimal("100"), credit_ready=True)
    other.ledger.check_capacity(Decimal("2"))
    assert other.ledger.cap_usd == Decimal("100")
    assert len(setup.calls) == 1


def test_spark_prompt_does_not_claim_a_generation_mask(instance):
    prompt = build_prompt(instance)
    assert "The mask and checker enforce" not in prompt
    assert "No feasibility mask is available" in prompt
    assert prompt.split("Codes:", 1)[1].lstrip().startswith("c=")


def test_provider_incomplete_discards_partial_plan_and_retains_usage(instance, setup):
    setup.respond(setup.good, response_status="incomplete")
    result = setup.planner.plan(instance)
    assert result.status == "error"
    assert result.plan is None
    assert result.metrics["raw_plan_legal"] == 0
    assert result.metrics["error_type"] == "SparkIncompleteError"
    assert result.metrics["tokens_in"] == 100
    assert result.metrics["tokens_out"] == 50
    assert Decimal(result.metrics["cost_usd"]) > 0
    assert len(setup.ledger.read_text().splitlines()) == 2


def test_default_client_path_keeps_credit_gate_closed(instance, setup, monkeypatch):
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)
    # Keep accounting isolated while using the production constructor default.
    compute = setup.planner.client.ledger.compute_path
    original_compute = compute.read_text()
    client = SparkClient("synthetic", ledger_path=setup.ledger,
                         compute_path=compute, max_retries=0)
    constructor_options = []

    def make_client(**options):
        constructor_options.append(options)
        return client

    monkeypatch.setattr("llm.spark.planner.SparkClient", make_client)
    planner = SparkPlanner(tokenizer=CharTokenizer())
    result = planner.plan(instance)
    assert result.status == "error"
    assert result.metrics["error_type"] == "SparkCreditNotReady"
    assert setup.calls == []
    assert not setup.ledger.exists()
    assert constructor_options == [{}]
    assert planner.client is client
    assert compute.read_text() == original_compute


def test_timeout_and_missing_key_do_not_call_http(instance, setup):
    result = setup.planner.plan(instance, time_limit_s=0)
    assert result.status == "timeout"
    assert setup.calls == []
    result = SparkPlanner(tokenizer=CharTokenizer()).plan(instance)
    assert result.status == "error"
    assert setup.calls == []


def test_taint_propagates(instance, setup):
    tainted = instance.model_copy(update={"provenance": instance.provenance.model_copy(
        update={"stubs": {"checker.check": "unit test"}})})
    result = setup.planner.plan(tainted)
    assert result.provenance.tainted
    assert result.plan.provenance.tainted


def test_local_glimmer_codec_when_available(instance, setup):
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH, load_tokenizer
    if not (DEFAULT_TOKENIZER_PATH / "tokenizer.json").exists():
        pytest.skip("local Glimmer tokenizer is not installed")
    setup.planner.tokenizer = load_tokenizer()
    result = setup.planner.plan(instance)
    assert result.status == "ok", result
    assert result.metrics["raw_plan_legal"] == 1


def test_harness_runs_full_checker_and_does_not_approve_hull_free_fixture(instance, setup):
    from stowage.planner import run_planner

    result, report = run_planner(setup.planner, instance, seed=7, allow_repair=False)
    assert result.metrics['raw_plan_legal'] == 1
    assert report is not None
    # This deliberately incomplete synthetic hull cannot become shippable merely
    # because its action list replayed legally.
    assert not report.passed
    assert not result.metrics['shippable']
    assert result.metrics['repair_status'] == 'disabled'


def test_default_output_cap_is_the_measured_need_per_rung(instance, setup):
    from llm.spark.planner import DEFAULT_MAX_OUTPUT_TOKENS, output_cap
    assert DEFAULT_MAX_OUTPUT_TOKENS["tiny"] == 65_536 and DEFAULT_MAX_OUTPUT_TOKENS["small"] == 131_072
    assert output_cap(instance) == DEFAULT_MAX_OUTPUT_TOKENS[instance.size_class]
    assert output_cap(instance, 2048) == 2048
    setup.respond(setup.good)
    result = setup.planner.plan(instance)
    assert setup.calls[0]["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS[instance.size_class]
    assert result.metrics["max_output_tokens"] == setup.calls[0]["max_output_tokens"]
    assert result.metrics["reasoning_setting"] == "provider-default"
    assert result.metrics["prompt_condition"].startswith("rules-appendix@")


def test_unknown_rung_requires_an_explicit_cap(instance):
    from llm.spark.planner import output_cap
    with pytest.raises(ValueError, match="pass max_output_tokens"):
        output_cap(instance.model_copy(update={"size_class": "huge"}))


def test_paid_prompt_has_no_inherited_reasoning_cue(instance, setup):
    setup.respond(setup.good)
    setup.planner.plan(instance)
    assert "Reasoning strength" not in setup.calls[0]["input"]
    assert "reasoning" not in setup.calls[0]  # recorded as provider-default, not sent


def test_reasoning_effort_reaches_request_and_run_identity(instance, setup):
    planner = SparkPlanner(setup.planner.client, tokenizer=setup.planner.tokenizer, reasoning_effort="high")
    setup.respond(setup.good)
    result = planner.plan(instance)
    assert setup.calls[-1]["reasoning"] == {"effort": "high"}
    assert planner.version.endswith("/reasoning=high") and result.planner_version.endswith("/reasoning=high")
    assert result.metrics["reasoning_setting"] == "high"
    assert SparkPlanner().version.endswith("/reasoning=provider-default")
    with pytest.raises(ValueError, match="reasoning_effort"):
        SparkPlanner(reasoning_effort="none")
