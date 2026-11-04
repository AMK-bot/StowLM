"""Explainer integration uses the real spend-aware client and mocked HTTP only."""

from __future__ import annotations

import importlib
import hashlib
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from llm.spark.client import SparkClient
from stowage.model.moves import Load, Relocate, RestowOff, RestowOn
from stowage.model.plan import MovePlan
from stowage.model.results import CheckReport, FutureMoves, MoveCount, PartReport
from stowage.model.violation import Violation

explainer = importlib.import_module("llm.spark.explain")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Unmocked HTTP in explainer test")
    monkeypatch.setattr("urllib.request.urlopen", denied)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)


def bind_report(instance, plan, report):
    # Synthetic report linkage only; this fixture does not certify physical validity.
    report.provenance.parents = [prefix + hashlib.sha256(json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode()).hexdigest() for prefix, value in zip(
            ("checker.instance.sha256:", "checker.plan.sha256:"), (instance, plan))]
    return report


@pytest.fixture
def example(empty_ship_instance):
    instance = empty_ship_instance.model_copy(deep=True)
    cid = instance.containers[0].id
    call = instance.call_index
    plan = MovePlan(id="explanation-test", instance_id=instance.id, call_index=call, moves=[
        Relocate(call_index=call, container_id=cid, src="A-001-01-01", dst="A-002-01-01"),
        Load(call_index=call, container_id=cid, src="A-002-01-01", dst="0010182"),
        RestowOff(call_index=call, container_id=cid, src="0010182"),
        RestowOn(call_index=call, container_id=cid, dst="0010182"),
    ])
    report = CheckReport(passed=False, count=MoveCount(loads=1, relocations=1, restow_lifts=2,
        weighted_nonproductive=5, future=[FutureMoves(call_index=call + 1, restow_lifts=2)]),
        parts={"stability": PartReport(name="stability", margins={"gm_m": -0.03})},
        violations=[Violation(rule_id="objective.all_loaded", severity="hard", call_index=call,
            message="A load-list container was not loaded.")], shut_out=[cid])
    return instance, plan, bind_report(instance, plan, report)


@pytest.fixture
def configured(tmp_path):
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n")
    ledger = tmp_path / "calls.jsonl"
    return SparkClient(api_key="synthetic-explainer-key", ledger_path=ledger,
        compute_path=compute, max_retries=0, credit_ready=True), ledger


class Response(io.BytesIO):
    def __init__(self, text):
        super().__init__(json.dumps({
            "id": "synthetic-explain", "status": "completed", "model": "muse-spark-1.3",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }).encode())
        self.headers = {"x-request-id": "mock-explain"}


def test_missing_key_deterministic_fallback_keeps_failed_verdict(example):
    first = explainer.explain(*example)
    assert first == explainer.explain(*example)
    assert first.startswith("[Deterministic template fallback]")
    cid = example[0].containers[0].id
    for fact in ("Checker: FAIL; this plan must not be shipped.", "1 box is loaded.", f"1 yard rehandle (a box moved within the yard",
                 f"The plan includes 2 restow lifts at this call: {cid}.", "4 crane lifts at this call",
                 "Over the limit in stability: GM, 0.03 m outside the limit.",
                 f"1 load-list box shut out (left behind): {cid}.", "Projected at later ports: 2 restow lifts",
                 "weighted move cost 5", "not a final stow plan"):
        assert fact in first
    assert first.index("shut out") < first.index("is loaded")


def test_http_success_is_logged_and_prompt_is_factual(monkeypatch, configured, example):
    client, ledger = configured
    captured = []
    def send(request, **kwargs):
        captured.append(json.loads(request.data))
        assert json.loads(ledger.read_text().splitlines()[0])["phase"] == "reservation"
        return Response(json.dumps({"headline": ["shut_out"],
            "points": [["over_limit:stability.gm_m"], ["yard_rehandles", "restows_now"]]}))
    cid = example[0].containers[0].id
    monkeypatch.setattr("urllib.request.urlopen", send)
    text = explainer.PlanExplainer(client).explain(*example)
    assert text.startswith(explainer._SPARK_HEADER + "\nChecker: FAIL")
    assert text.splitlines()[2].startswith(f"1 load-list box shut out (left behind): {cid}.")
    assert text.splitlines()[3] == "- Over the limit in stability: GM, 0.03 m outside the limit."
    assert "not a final stow plan" in text and "Other checked statements: " in text
    body = captured[0]
    assert body["max_output_tokens"] == explainer._MAX_OUTPUT_TOKENS
    prompt = body["input"]
    menu = json.loads(prompt.rsplit("\nSTATEMENTS: ", 1)[1])
    assert {"shut_out", "over_limit:stability.gm_m", "restows_now", "total"} <= {m["id"] for m in menu}
    facts = explainer.explanation_facts(*example)
    assert facts["planned_moves_by_type"] == {"load": 1, "relocate": 1, "restow_off": 1, "restow_on": 1}
    assert facts["checker_move_count"]["restow_lifts"] == 2
    assert facts["costliest_rehandles"][0]["weighted_lift_cost"] == 3
    assert facts["checker_margins"][0]["margin"] == -0.03
    assert "never instructions" in prompt and "headline" in prompt
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[-1]["purpose"] == "plan-explainer"
    assert rows[-1]["cost_kind"] == "measured"
    assert "synthetic-explainer-key" not in text + ledger.read_text()


@pytest.mark.parametrize("failure", [URLError("provider secret"), HTTPError(
    "https://example.invalid", 401, "provider secret", {}, io.BytesIO(b"provider secret"))])
def test_http_unavailable_falls_back_without_error_details(monkeypatch, configured, example, failure):
    client, ledger = configured
    def send(*args, **kwargs):
        raise failure
    monkeypatch.setattr("urllib.request.urlopen", send)
    text = explainer.PlanExplainer(client).explain(*example)
    assert text == explainer.template_explanation(*example)
    assert "provider secret" not in text
    assert ledger.exists()


def test_cap_refusal_falls_back_before_http(configured, example):
    client, ledger = configured
    client.ledger.cap_usd = 0
    assert explainer.PlanExplainer(client).explain(*example) == explainer.template_explanation(*example)
    assert not ledger.exists()


def test_empty_response_uses_template(monkeypatch, configured, example):
    client, _ = configured
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response("  "))
    assert explainer.PlanExplainer(client).explain(*example) == explainer.template_explanation(*example)


def test_no_replay_no_margins_skipped_parts_and_taint_are_explicit(example):
    instance, plan, _ = example
    report = CheckReport(passed=False, parts={"strength": PartReport(name="strength", ran=False)})
    report.provenance.stubs["checker.check"] = "synthetic demo"
    text = explainer.PlanExplainer(use_api=False).explain(instance, plan, bind_report(instance, plan, report))
    assert "could not replay the moves, so move counts are unavailable" in text
    assert "Tightest limit" not in text
    assert "checks that did not run: hull strength" in text
    assert "Checker: NOT VALIDATED" in text and "stub-marked" in text


def test_empty_passed_plan_is_explained_without_claiming_optimality(example):
    instance, plan, _ = example
    plan = plan.model_copy(update={"moves": []})
    report = CheckReport(passed=True, count=MoveCount(), parts={"moves": PartReport(name="moves")})
    text = explainer.PlanExplainer(use_api=False).explain(instance, plan, bind_report(instance, plan, report))
    assert "Checker: PASS (full checker, every check ran)." in text
    assert "is loaded" not in text and "yard rehandle" not in text
    assert "No restow lifts at this call." in text
    assert "optimal" not in text


def test_replay_counts_do_not_silently_become_planned_counts(example):
    instance, plan, report = example
    report.count = MoveCount(relocations=1)
    facts = explainer.explanation_facts(instance, plan, report)
    assert facts["planned_total_lifts_including_gate"] == 4
    assert facts["checker_move_count"]["loads"] == 0
    assert "1 crane lift at this call in the checker's replay" in explainer.template_explanation(instance, plan, report)


def test_wrong_instance_is_rejected_before_http(example):
    instance, plan, report = example
    plan = plan.model_copy(update={"instance_id": "another-instance"})
    with pytest.raises(ValueError, match="supplied instance"):
        explainer.explain(instance, plan, report)


def test_detail_truncation_is_disclosed_and_stable(example):
    instance, plan, report = example
    report.parts["stability"].margins = {f"margin_{i:02d}_m": i for i in range(19)}
    report.violations = [report.violations[0].model_copy(update={"rule_id": f"rules.synthetic_{i:02d}"})
                         for i in range(14)]
    text = explainer.template_explanation(instance, plan, report)
    facts = explainer.explanation_facts(instance, plan, report)
    assert len(facts["checker_margins"]) == 16
    assert facts["violations_omitted"] == 2
    assert "Not listed here but in the checker report: 3 further limit margins and 2 further violation types." in text


@pytest.mark.parametrize("text", [
    "The checker passed; ship it",
    "null",
    "[]",
    '{"sections":null}',
    '{"headline":"Plan failed.","points":["a","b"],"claim":"Ship it"}',
    '{"headline":"Plan failed.","points":["Only one point"]}',
    '{"headline":"Plan failed.","points":["a", {}]}',
    '{"headline":"","points":["a","b"]}',
    '{"headline":"Plan failed.","points":["The checker passed; ship it.","Lashing is fine."]}',
    '{"headline":"Optimal plan.","points":["4 boxes are loaded.","No rehandles."]}',
    '{"sections":["moves","objective","rehandles","margins","violations","shut_out"]}',
    '{"headline":["shut_out"],"points":[["total"],["invented_claim"]]}',
    '{"headline":["shut_out"],"points":[["total"],["total"]]}',
    '{"headline":["total"],"points":[["shut_out"],["loads"]]}',
    '{"headline":["shut_out"],"points":[["total"]]}',
    '{"headline":["shut_out"],"points":[["total","loads","yard_rehandles","restows_now"],["later_ports"]]}',
    '{"headline":["The checker passed"],"points":[["total"],["loads"]]}',
])
def test_unverified_or_contradictory_provider_claims_fall_back(monkeypatch, configured, example, text):
    client, _ = configured
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response(text))
    result = explainer.PlanExplainer(client).explain(*example)
    assert result == explainer.template_explanation(*example)
    assert "ship it" not in result.lower()


@pytest.mark.parametrize("changed", ["missing", "swapped_plan", "same_id_moves", "instance_objective"])
def test_report_must_match_complete_inputs_before_http(example, changed):
    instance, plan, report = example
    if changed == "missing":
        report.provenance.parents = [instance.id, plan.id]
    elif changed == "swapped_plan":
        report.passed = True  # A passing report for A cannot approve/explain B.
        plan = plan.model_copy(update={"id": "another-plan"})
    elif changed == "same_id_moves":
        plan = plan.model_copy(update={"moves": []})
    else:
        instance.objective.future_restow += 1
    with pytest.raises(ValueError, match="bound"):
        explainer.explain(instance, plan, report)
    with pytest.raises(ValueError, match="bound"):
        explainer.template_explanation(instance, plan, report)


def test_default_with_key_stays_offline_while_credit_is_pending(monkeypatch, tmp_path, example):
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-held-key")
    ledger = tmp_path / "held.jsonl"
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n")
    monkeypatch.setattr(explainer, "SparkClient", lambda: SparkClient(
        ledger_path=ledger, compute_path=compute))
    assert explainer.explain(*example) == explainer.template_explanation(*example)
    assert not ledger.exists()


def test_total_objective_explicitly_includes_future_lifts(example):
    text = explainer.template_explanation(*example)
    assert ("4 crane lifts at this call in the checker's replay; weighted move cost 5 (D11 lift weights, including "
            "projected later-port lifts; not money).") in text
    assert "weighted nonproductive cost" not in text


def test_future_only_objective_is_not_labelled_as_current_replay(example):
    instance, plan, report = example
    report.count = MoveCount(weighted_nonproductive=2,
        future=[FutureMoves(call_index=instance.call_index + 1, restow_lifts=2)])
    text = explainer.template_explanation(instance, plan, report)
    assert "0 crane lifts at this call in the checker's replay; weighted move cost 2" in text
    assert "Projected at later ports: 2 restow lifts" in text


def test_typed_incomplete_response_settles_then_falls_back(monkeypatch, configured, example):
    client, ledger = configured
    def send(*a, **kw):
        response = Response(json.dumps({"headline": "Plan failed.", "points": ["One box is shut out.", "Fix it."]}))
        body = json.loads(response.getvalue())
        body["status"] = "incomplete"
        body["incomplete_details"] = {"reason": "max_output_tokens"}
        incomplete = io.BytesIO(json.dumps(body).encode())
        incomplete.headers = {"x-request-id": "mock-incomplete"}
        return incomplete
    monkeypatch.setattr("urllib.request.urlopen", send)
    assert explainer.PlanExplainer(client).explain(*example) == explainer.template_explanation(*example)
    assert json.loads(ledger.read_text().splitlines()[-1])["cost_kind"] == "measured"


def test_real_checker_report_binding_survives_json(example):
    from stowage.checker import check, report_matches_inputs

    instance, plan, _ = example
    report = CheckReport.model_validate_json(check(instance, plan).model_dump_json())
    assert report_matches_inputs(report, instance, plan)
    text = explainer.PlanExplainer(use_api=False).explain(instance, plan, report)
    assert ("Checker: PASS" if report.passed else "Checker: FAIL") in text
