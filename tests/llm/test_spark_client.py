"""Muse Spark harness contract tests; HTTP is always replaced locally."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from urllib.error import HTTPError, URLError

import pytest

from llm.spark import client


KEY = "synthetic-secret-DO-NOT-LOG"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("A Spark test attempted an unmocked HTTP request")

    monkeypatch.setattr("urllib.request.urlopen", denied)
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)


@pytest.fixture
def harness(tmp_path):
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n\n| Date | Source | Amount | What for | Remaining |\n|---|---|---|---|---|\n")
    ledger = tmp_path / "calls.jsonl"

    def make(**kwargs):
        defaults = dict(
            ledger_path=ledger,
            compute_path=compute,
            spend_cap_usd=Decimal("20"),
            sleep=lambda _: None,
            jitter=lambda: 0,
            credit_ready=True,  # Fake transport only; no live credit is enabled.
        )
        defaults.update(kwargs)
        return client.SparkClient(**defaults)

    return make, ledger, compute


def payload(*, cached=200):
    return {
        "id": "resp-test",
        "object": "response",
        "status": "completed",
        "model": "muse-spark-1.3",
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Synthetic ship ready."}
        ]}],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens": 100,
            "total_tokens": 1100,
        },
    }


class Response(io.BytesIO):
    def __init__(self, body=None, *, headers=None):
        super().__init__(json.dumps(payload() if body is None else body).encode())
        self.headers = {"x-request-id": "req-test"} if headers is None else headers
        self.status = 200


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def settlements(path):
    return [row for row in rows(path) if row.get("phase") != "reservation"]


def test_request_usage_price_and_append_only_log(monkeypatch, harness):
    make, ledger, compute = harness
    captured = []

    def send(request, *, timeout):
        captured.append((request, timeout))
        # The durable reservation must exist before an HTTP attempt can spend.
        assert rows(ledger)[-1]["phase"] == "reservation"
        assert Decimal(str(rows(ledger)[-1]["cost_usd"])) > 0
        # The compute ledger must expose the hold before transport can spend.
        assert "spark-jsonl" in compute.read_text()
        assert str(rows(ledger)[-1]["cost_usd"]) in compute.read_text()
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", send)
    result = make(timeout=17).complete("Synthetic test prompt", purpose="unit-test", max_output_tokens=128)
    request, timeout = captured[0]
    assert request.full_url == "https://api.meta.ai/v1/responses"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert timeout == 17
    body = json.loads(request.data)
    assert body["model"] == "muse-spark-1.3"
    assert body["input"] == "Synthetic test prompt"
    assert body["max_output_tokens"] == 128
    assert body.get("stream", False) is False
    assert body["store"] is False
    assert result.text == "Synthetic ship ready."
    assert result.usage.input_tokens == 1000
    assert result.usage.output_tokens == 100
    assert result.usage.cached_tokens == 200
    # Cached tokens are part of input_tokens, charged at their own lower price.
    expected = Decimal("0.001455")
    assert Decimal(str(result.cost_usd)) == expected
    assert result.request_id == "req-test"
    assert str(expected) in compute.read_text()
    first = ledger.read_bytes()
    make().complete("Another synthetic prompt", purpose="unit-test", max_output_tokens=128)
    assert ledger.read_bytes().startswith(first)
    logged = settlements(ledger)
    assert len(logged) == 2
    for row in logged:
        assert row["model"] == "muse-spark-1.3"
        assert row["purpose"] == "unit-test"
        assert row["request_id"] == "req-test"
        assert row["input_tokens"] == 1000
        assert row["output_tokens"] == 100
        assert row["cached_tokens"] == 200
        assert Decimal(str(row["cost_usd"])) == expected
    assert KEY not in ledger.read_text()
    assert "Synthetic test prompt" not in ledger.read_text()
    assert "Synthetic ship ready" not in ledger.read_text()


def test_missing_cached_usage_defaults_to_zero(monkeypatch, harness):
    make, _, _ = harness
    body = payload()
    body["usage"].pop("input_tokens_details")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(body))
    result = make().complete("Synthetic", purpose="unit-test")
    assert result.usage.cached_tokens == 0
    assert Decimal(str(result.cost_usd)) == Decimal("0.001675")


def test_response_id_is_fallback_request_id(monkeypatch, harness):
    make, _, _ = harness
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(headers={}))
    assert make().complete("Synthetic", purpose="unit-test").request_id == "resp-test"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_http_status_then_success(monkeypatch, harness, status):
    make, ledger, _ = harness
    attempts = []
    sleeps = []

    def send(request, *, timeout):
        attempts.append(request)
        if len(attempts) <= 2:
            raise HTTPError(request.full_url, status, "synthetic failure", {}, io.BytesIO(b"{}"))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", send)
    result = make(max_retries=2, sleep=sleeps.append, jitter=lambda: 0.25).complete("Synthetic", purpose="retry-test")
    assert result.text == "Synthetic ship ready."
    assert len(attempts) == 3
    assert sleeps == [0.75, 1.25]
    assert len(settlements(ledger)) == 3


def test_retry_after_is_respected(monkeypatch, harness):
    make, _, _ = harness
    sleeps = []
    attempts = []

    def send(request, **kwargs):
        attempts.append(request)
        if len(attempts) == 1:
            raise HTTPError(request.full_url, 429, "rate limit", {"Retry-After": "7"}, io.BytesIO(b"{}"))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", send)
    make(sleep=sleeps.append).complete("Synthetic", purpose="retry-after")
    assert len(sleeps) == 1
    assert sleeps[0] >= 7


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_validation_and_auth_errors_are_not_retried(monkeypatch, harness, status, capsys, caplog):
    make, ledger, _ = harness
    attempts = []
    sleeps = []

    def send(request, **kwargs):
        attempts.append(request)
        raise HTTPError(request.full_url, status, KEY, {}, io.BytesIO(KEY.encode()))

    monkeypatch.setattr("urllib.request.urlopen", send)
    with pytest.raises(client.SparkHTTPError) as error:
        make(sleep=sleeps.append).complete("Synthetic", purpose="bad-request")
    assert len(attempts) == 1
    assert sleeps == []
    assert KEY not in str(error.value)
    assert KEY not in ledger.read_text()
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("failure", [TimeoutError("synthetic timeout"), URLError("synthetic connection failure")])
def test_transport_failure_retries_are_bounded(monkeypatch, harness, failure):
    make, ledger, _ = harness
    attempts = []

    def send(*args, **kwargs):
        attempts.append(1)
        raise failure

    monkeypatch.setattr("urllib.request.urlopen", send)
    with pytest.raises(client.SparkError):
        make(max_retries=1).complete("Synthetic", purpose="timeout-test")
    assert len(attempts) == 2
    assert len(settlements(ledger)) == 2


def test_cap_refuses_before_any_http(monkeypatch, harness):
    from llm.spark.spend import SpendCapExceeded

    make, ledger, _ = harness
    # The autouse HTTP guard ensures refusal precedes sending a request.
    with pytest.raises(SpendCapExceeded):
        make(spend_cap_usd=Decimal("0.000001")).complete("Synthetic", purpose="cap-test")
    assert not ledger.exists() or not ledger.read_text().strip()


def test_running_total_blocks_later_call(monkeypatch, harness):
    from llm.spark.spend import SpendCapExceeded

    make, ledger, _ = harness
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    make().complete("Synthetic", purpose="first-call")
    # Actual first-call cost already exceeds this new cap.
    with pytest.raises(SpendCapExceeded):
        make(spend_cap_usd=Decimal("0.001")).complete("Synthetic", purpose="over-budget")
    assert len(settlements(ledger)) == 1


@pytest.mark.parametrize("usage", [{}, {"input_tokens": -1, "output_tokens": 1}, {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": {"cached_tokens": 2}}])
def test_malformed_usage_fails_closed(monkeypatch, harness, usage):
    make, ledger, _ = harness
    body = payload()
    body["usage"] = usage
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(body))
    with pytest.raises(client.SparkProtocolError):
        make(max_retries=0).complete("Synthetic", purpose="malformed-usage")
    assert sum(Decimal(str(row["cost_usd"])) for row in settlements(ledger)) > 0


def test_missing_key_fails_without_http(monkeypatch, harness):
    make, _, _ = harness
    monkeypatch.delenv("MODEL_API_KEY")
    with pytest.raises((client.SparkError, ValueError)):
        make().complete("Synthetic", purpose="missing-key")


def test_cli_test_call_has_one_attempt_on_transient_failure(monkeypatch, harness, capsys):
    make, ledger, _ = harness
    attempts = []
    options = []

    def send(request, **kwargs):
        attempts.append(request)
        raise HTTPError(request.full_url, 503, KEY, {}, io.BytesIO(KEY.encode()))

    instance = make(max_retries=0)

    def factory(**kwargs):
        options.append(kwargs)
        assert kwargs["max_retries"] == 0
        return instance

    monkeypatch.setattr(client, "SparkClient", factory)
    monkeypatch.setattr("urllib.request.urlopen", send)
    assert client.main(["--test-call"]) == 1
    assert len(attempts) == 1
    assert len(options) == 1
    assert len(settlements(ledger)) == 1
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err + ledger.read_text()


def test_cli_success_prints_accounting_and_summary(monkeypatch, harness, capsys):
    make, ledger, _ = harness
    instance = make(max_retries=0)
    monkeypatch.setattr(client, "SparkClient", lambda **kwargs: instance)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    assert client.main(["--test-call"]) == 0
    output = capsys.readouterr().out
    assert "muse-spark-1.3" in output
    assert "0.001455" in output
    assert "spark-jsonl" in output
    assert "Synthetic ship ready" not in output
    assert KEY not in output
    assert len(settlements(ledger)) == 1


def test_cli_summary_needs_no_key_or_network(monkeypatch, harness, capsys):
    from llm.spark.spend import SpendLedger

    _, ledger, compute = harness
    monkeypatch.delenv("MODEL_API_KEY")
    monkeypatch.setattr(client, "SpendLedger", lambda **kwargs: SpendLedger(ledger, compute))
    assert client.main(["--summary"]) == 0
    assert "spark-jsonl" in capsys.readouterr().out


def test_provider_metadata_and_purpose_cannot_leak_key(monkeypatch, harness):
    make, ledger, _ = harness
    body = payload()
    body["status"] = KEY
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(body, headers={"x-request-id": KEY}))
    with pytest.raises(client.SparkProtocolError):
        make().complete("Synthetic", purpose=KEY)
    assert KEY not in ledger.read_text()
    assert settlements(ledger)[0]["status"] == "response_error"


@pytest.mark.parametrize("text", [None, "", "Truncated synthetic explanation"])
def test_incomplete_response_logs_measured_usage_then_raises(monkeypatch, harness, text):
    make, ledger, _ = harness
    body = payload()
    body["status"] = "incomplete"
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    if text is None:
        body["output"] = []
    else:
        body["output"][0]["content"][0]["text"] = text
    attempts = []

    def send(*args, **kwargs):
        attempts.append(1)
        return Response(body)

    monkeypatch.setattr("urllib.request.urlopen", send)
    with pytest.raises(client.SparkIncompleteError) as error:
        make().complete("Synthetic", purpose="incomplete-test")
    assert len(attempts) == 1
    assert error.value.reason == "max_output_tokens"
    assert error.value.usage.input_tokens == 1000
    assert error.value.cost_usd == Decimal("0.001455")
    assert error.value.request_id == "req-test"
    assert "Truncated synthetic explanation" not in str(error.value)
    measured = settlements(ledger)
    assert len(measured) == 1
    assert measured[0]["status"] == "incomplete"
    assert measured[0]["cost_kind"] == "measured"
    assert Decimal(measured[0]["cost_usd"]) == Decimal("0.001455")


@pytest.mark.parametrize("details", [None, [], {"reason": KEY, "detail": "private-provider-body"}, {"reason": []}])
def test_incomplete_reason_never_exposes_provider_detail_or_credentials(monkeypatch, harness, details):
    make, ledger, _ = harness
    body = payload()
    body["status"] = "incomplete"
    body["incomplete_details"] = details
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(body))
    with pytest.raises(client.SparkIncompleteError) as error:
        make().complete("Synthetic", purpose="incomplete-safe-error")
    assert error.value.reason == "unspecified"
    assert KEY not in str(error.value)
    assert "private-provider-body" not in str(error.value)
    assert KEY not in ledger.read_text()


@pytest.mark.parametrize("flag", [None, "", "0", "false", "true", "yes"])
def test_credit_hold_is_default_and_precedes_reservation_or_http(monkeypatch, harness, flag):
    make, ledger, compute = harness
    if flag is not None:
        monkeypatch.setenv("SPARK_CREDIT_READY", flag)
    before = compute.read_bytes()
    with pytest.raises(client.SparkCreditNotReady):
        make(credit_ready=None).complete("Synthetic", purpose="credit-hold")
    assert not ledger.exists()
    assert compute.read_bytes() == before
    assert not ledger.with_suffix(".jsonl.lock").exists()
    assert not compute.with_suffix(".md.spark.lock").exists()


def test_credit_readiness_environment_is_checked_for_every_call(monkeypatch, harness):
    make, ledger, _ = harness
    instance = make(credit_ready=None)
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    instance.complete("Synthetic", purpose="credit-ready-fake-transport")
    before = ledger.read_bytes()
    monkeypatch.delenv("SPARK_CREDIT_READY")
    with pytest.raises(client.SparkCreditNotReady):
        instance.complete("Synthetic", purpose="credit-returned-to-hold")
    assert ledger.read_bytes() == before


def test_explicit_credit_hold_overrides_ready_environment(monkeypatch, harness):
    make, ledger, _ = harness
    monkeypatch.setenv("SPARK_CREDIT_READY", "1")
    with pytest.raises(client.SparkCreditNotReady):
        make(credit_ready=False).complete("Synthetic", purpose="explicit-hold")
    assert not ledger.exists()


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_credit_ready_argument_requires_a_real_boolean(harness, value):
    make, _, _ = harness
    with pytest.raises(ValueError):
        make(credit_ready=value)


@pytest.mark.parametrize("effort", [None, "minimal", "low", "medium", "high", "xhigh"])
def test_reasoning_effort_is_sent_only_when_given_and_ledgered(monkeypatch, harness, effort):
    make, ledger, _ = harness
    captured = []

    def send(request, *, timeout):
        captured.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", send)
    kwargs = {} if effort is None else {"reasoning_effort": effort}
    make().complete("Synthetic test prompt", purpose="unit-test", max_output_tokens=128, **kwargs)
    if effort is None:
        assert "reasoning" not in captured[0]  # provider default, unchanged
    else:
        assert captured[0]["reasoning"] == {"effort": effort}
    recorded = {row["phase"]: row["reasoning_effort"] for row in rows(ledger)}
    assert recorded == {"reservation": effort or "provider-default", "settlement": effort or "provider-default"}


@pytest.mark.parametrize("effort", ["none", "LOW", "", 3])
def test_unsupported_reasoning_effort_is_refused_before_any_request(monkeypatch, harness, effort):
    make, ledger, _ = harness
    with pytest.raises(ValueError, match="reasoning_effort"):
        make().complete("Synthetic test prompt", purpose="unit-test", reasoning_effort=effort)
    assert not ledger.exists() or rows(ledger) == []
