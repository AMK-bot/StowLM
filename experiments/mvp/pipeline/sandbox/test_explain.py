import json
from types import SimpleNamespace

import pytest

from experiments.mvp.pipeline.contract.schema import Explanation, MoveVerdict
from experiments.mvp.pipeline.sandbox.explain import VerdictExplainer, explain_verdict, verdict_statements


def failed(**updates):
    value = {"legal": False, "screen": "full", "wall_s": 0.01, "extra_lifts": 3, "broken": [
        {"rule_id": "is_code.gm", "message": "GM is below the minimum.", "measured": 0.11,
         "limit": 0.15, "boxes": ["BOX-A", "BOX-B"]},
        {"rule_id": "weight.stack", "message": "Stack is overweight.", "measured": 84,
         "limit": 80, "boxes": ["BOX-C"]}]}
    return MoveVerdict.model_validate({**value, **updates})


class Client:
    def __init__(self, response):
        self.response, self.calls = response, []

    def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return SimpleNamespace(text=self.response)


def test_default_is_offline_and_evidence_is_exact(monkeypatch):
    def no_call(*args, **kwargs):
        raise AssertionError("offline explanation attempted HTTP")
    monkeypatch.setattr("urllib.request.urlopen", no_call)
    verdict = failed()
    explanation = explain_verdict(verdict.model_dump(mode="json"))
    assert isinstance(explanation, Explanation)
    assert explanation.text.startswith("Template explanation.")
    assert explanation.mode.value == "live"
    for index, broken in enumerate(verdict.broken):
        statement = next(s for s in explanation.statements if s["id"] == f"broken:{index}")
        assert {e["path"]: e["value"] for e in statement["evidence"]} == {
            f"broken[{index}].{field}": broken[field]
            for field in ("rule_id", "message", "measured", "limit", "boxes")}
        for value in (broken["rule_id"], str(broken["measured"]), str(broken["limit"]), *broken["boxes"]):
            assert value in statement["text"]
    assert "+3" in explanation.text
    json.dumps(explanation.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("legal", [True, False])
def test_fast_screen_never_claims_full_pass(legal):
    explanation = explain_verdict(failed(screen="fast", legal=legal, broken=[]))
    assert "Fast screen, full check running." in explanation.text
    assert "passed the full checker" not in explanation.text
    if legal:
        assert "not a full-checker pass" in explanation.text


def test_valid_selection_reorders_only_precomputed_text():
    client = Client(json.dumps({"statement_ids": ["broken:1", "lifts", "status", "broken:0"]}))
    explainer = VerdictExplainer(client, use_api=True)
    verdict = failed()
    result = explainer.explain(verdict)
    assert result.text.startswith("Muse Spark selected checked statements.")
    assert [s["id"] for s in result.statements] == ["status", "broken:1", "lifts", "broken:0"]
    assert {s["id"]: s for s in result.statements} == {s["id"]: s for s in verdict_statements(verdict)}
    assert client.calls[0][1]["purpose"] == "sandbox-verdict"
    assert explainer.last_response is not None


@pytest.mark.parametrize("response", [
    "not JSON", '```json\n{"statement_ids":["status"]}\n```',
    '{"statement_ids":["status","broken:0","broken:1","IGNORE RULES"]}',
    '{"statement_ids":["status","broken:0","broken:1"],"text":"GM is safe"}',
    '{"statement_ids":["status","broken:0","broken:0","broken:1"]}',
    '{"statement_ids":["status","lifts"]}',
    '{"statement_ids":["status","broken:0","broken:1",{}]}',
    '{"statement_ids":["status","broken:0","broken:1"],"statement_ids":["status"]}',
    '[]', 'null', RuntimeError("network unavailable"),
])
def test_untrusted_or_unavailable_response_falls_back(response):
    explainer = VerdictExplainer(Client(response), use_api=True)
    assert explainer.explain(failed()) == explain_verdict(failed())
    assert explainer.last_rejection
    assert explainer.last_selection is None


def test_no_optional_network_even_with_client():
    client = Client(RuntimeError("must not be used"))
    VerdictExplainer(client).explain(failed())
    assert not client.calls


def test_thai_keeps_rule_identifiers_and_evidence():
    result = explain_verdict(failed(), language="th")
    assert result.language == "th"
    assert "ไม่ผ่านกฎ is_code.gm" in result.text
    assert "0.11" in result.text and "0.15" in result.text
    assert result.statements[1]["evidence"] == explain_verdict(failed()).statements[1]["evidence"]


def test_missing_measurement_is_unavailable_not_invented():
    result = explain_verdict(failed(broken=[{"rule_id": "checker.internal_error", "message": "Check failed"}],
                                    extra_lifts=None))
    assert "Measured: not reported; limit: not reported; boxes: not reported" in result.text
    assert "Crane lifts this call: unavailable" in result.text


def test_full_pass_and_negative_delta():
    result = explain_verdict(failed(legal=True, broken=[], extra_lifts=-2))
    assert "passed the full checker" in result.text
    assert "-2" in result.text


def test_soft_findings_do_not_contradict_full_pass():
    finding = {**failed().broken[0], "severity": "soft"}
    result = explain_verdict(failed(legal=True, broken=[finding]))
    assert "passed the full checker" in result.text
    assert "Soft rule is_code.gm was not met" in result.text
    assert {"path": "broken[0].severity", "value": "soft"} in result.statements[1]["evidence"]


def test_diagnostic_gauges_and_checker_units_are_explicit():
    finding = {**failed().broken[0], "unit": "m", "source": "requested_stow_snapshot"}
    result = explain_verdict(failed(broken=[finding], gauges={"diagnostic_only": True}))
    assert "gauges describe a diagnostic check" in result.text
    assert "Measured: 0.11 m; limit: 0.15 m" in result.text
    assert {"path": "broken[0].source", "value": "requested_stow_snapshot"} in result.statements[2]["evidence"]


@pytest.mark.parametrize("screen", ["fast", "full"])
def test_invalid_input_has_no_false_checker_claim(screen):
    result = explain_verdict(failed(screen=screen, broken=[], gauges={
        "input_error": True, "error": "Unknown slot", "full_check_pending": False}))
    assert "no full-checker verdict is available" in result.text
    assert "full check running" not in result.text
    assert "checker rejected" not in result.text
    assert "Unknown slot" in result.text
    assert {"path": "gauges.error", "value": "Unknown slot"} in result.statements[1]["evidence"]


def test_malformed_or_unsupported_input_rejected():
    with pytest.raises(ValueError, match="legal verdict"):
        explain_verdict(failed(legal=True))
    with pytest.raises(ValueError, match="language"):
        explain_verdict(failed(), language="fr")


def test_crane_cause_leads_diagnostic_stow_rule_even_when_spark_reorders():
    verdict = failed(gauges={"diagnostic_only": True, "outcome": "stow_breaks_rule",
        "deciding_cause": {"kind": "crane_order", "source": "proposal_replay",
                           "rule_id": "expert.moves.hatch_clear"},
        "stow_assessment": {"passed": False, "source": "requested_stow_snapshot"}})
    statements = verdict_statements(verdict)
    response = json.dumps({"statement_ids": [s["id"] for s in reversed(statements)]})
    result = VerdictExplainer(Client(response), use_api=True).explain(verdict)
    assert result.statements[0]["id"] == "status"
    assert result.statements[0]["text"].startswith("The existing crane order can't reach")
    assert "expert.moves.hatch_clear" in result.statements[0]["text"]
    assert "diagnostic stow check also found broken rules" in result.statements[0]["text"]


def test_three_outcomes_scope_and_later_projection_are_truthful():
    scope = "This edits one box and keeps the crane order and ballast. It doesn't replan."
    verdict = failed(legal=True, broken=[], extra_lifts=0, gauges={
        "outcome": "passes_as_edited", "scope_note": scope, "crane_order_preserved": True,
        "lift_counts": {"projected_later_delta": 24}})
    result = explain_verdict(verdict)
    assert "Passes as edited." in result.text
    assert "Crane lifts this call: +0. Projected later-port lifts: +24 (projection)." in result.text
    assert scope in result.text and "Legal isn't the same as good." in result.text
    fine = failed(broken=[], gauges={"outcome": "stow_fine_crane_order_failed",
        "deciding_cause": {"kind": "crane_order", "rule_id": "expert.moves.bottom_up"},
        "stow_assessment": {"passed": True}})
    assert "Stow is fine, but the existing crane order can't reach it" in explain_verdict(fine).text
    assert "A replan might." in explain_verdict(fine).text
    broken = failed(gauges={"outcome": "stow_breaks_rule", "deciding_cause": {
        "kind": "stow_rule", "rule_id": "is_code.gm"}})
    assert "Stow breaks rule is_code.gm." in explain_verdict(broken).text


def test_spark_cannot_omit_later_port_cost_or_scope():
    verdict = failed(legal=True, broken=[], extra_lifts=0, gauges={
        "scope_note": "This edits one box and keeps the crane order and ballast. It doesn't replan.",
        "crane_order_preserved": True, "lift_counts": {"projected_later_delta": 24}})
    client = Client(json.dumps({"statement_ids": ["status", "scope", "quality"]}))
    explainer = VerdictExplainer(client, use_api=True)
    assert explainer.explain(verdict) == explain_verdict(verdict)
    assert explainer.last_rejection == "invalid statement selection"
