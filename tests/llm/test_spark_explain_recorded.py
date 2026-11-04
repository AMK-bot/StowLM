"""Regression tests on recorded live Muse Spark answers and on the w2 reviews' reproductions (no network).

``experiments/spark-explain/recorded.json`` holds every answer Spark returned during judging on the four cases in
``experiments/spark-explain/cases``. v1/v2 answers were free prose and v3 answers are statement selections. Since
v3.0 (review w2-rev-explain: the prose deny-list leaked), Spark may only choose statement ids, so no free prose,
faithful or not, can ever be displayed. Cases A and B are wording tests, never checker passes.
"""

from __future__ import annotations

from decimal import Decimal
import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm.spark.client import SparkClient
from stowage.model import CheckReport, Instance, MovePlan

explainer = importlib.import_module("llm.spark.explain")
HERE = Path(__file__).resolve().parents[2] / "experiments" / "spark-explain"
RECORDED = json.loads((HERE / "recorded.json").read_text())
A, B = "A_wording_test_only_not_a_pass", "B_wording_test_only_not_a_pass_restow"
C, D = "C_live_stale_vessel_data", "D_live_shut_out"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("Unmocked HTTP in explainer test")
    monkeypatch.setattr("urllib.request.urlopen", denied)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_CREDIT_READY", raising=False)


@pytest.fixture(scope="module")
def instance():
    return Instance.model_validate_json((HERE / "cases/instance.json").read_text())


def load(case):
    plan = MovePlan.model_validate_json((HERE / f"cases/{case}.plan.json").read_text())
    report = CheckReport.model_validate_json((HERE / f"cases/{case}.report.json").read_text())
    return plan, report


class Recorded:
    """Duck-typed client replaying one recorded or crafted answer."""

    def __init__(self, text):
        self.text = text

    def complete(self, prompt, *, purpose, max_output_tokens):
        return SimpleNamespace(text=self.text)


def shown(instance, case, answer, **kwargs):
    plan, report = load(case)
    explaining = explainer.PlanExplainer(Recorded(answer))
    return explaining.explain(instance, plan, report, **kwargs), explaining, (plan, report)


# --- 1. High: never PASS on a stub, modelled or incomplete report ---------------------------------------------
@pytest.mark.parametrize("case", [A, B, C])
def test_modelled_or_incomplete_report_is_not_validated_never_pass(instance, case):
    plan, report = load(case)
    text = explainer.template_explanation(instance, plan, report)
    assert text.splitlines()[1].startswith("Checker: NOT VALIDATED; there is no checker verdict on this plan")
    assert "PASS" not in text


def test_passing_report_with_a_check_not_run_is_not_validated(instance):
    plan, report = load(A)
    report.provenance.stubs.clear()  # not a stub, still stability did not run
    assert report.passed and not report.parts["stability"].ran
    text = explainer.template_explanation(instance, plan, report)
    assert "Checker: NOT VALIDATED" in text and "PASS" not in text
    assert "checks that did not run: stability" in text


def test_real_failure_keeps_fail_and_says_what_was_not_validated(instance):
    plan, report = load(D)
    line = explainer.template_explanation(instance, plan, report).splitlines()[1]
    assert line.startswith("Checker: FAIL; this plan must not be shipped. Not validated:")


# --- 2. Medium: every number is bound to what it counts (rendered from that fact) ----------------------------
def test_number_attached_to_wrong_quantity_cannot_be_shown(instance):
    # B: 4 loads and 4 later-port hatch-cover lifts, 0 later restows. v2 displayed this sentence.
    for answer in ('{"headline":"4 restow lifts later.","points":["4 boxes are loaded.","Stability did not run."]}',
                   '{"headline":["4 restow lifts later"],"points":[["loads"],["total"]]}'):
        text, explaining, (plan, report) = shown(instance, B, answer)
        assert text == explainer.template_explanation(instance, plan, report)
        assert "4 restow lifts later" not in text


@pytest.mark.parametrize("case", [A, B, C, D])
def test_rendered_numbers_come_from_their_own_fact(instance, case):
    plan, report = load(case)
    facts = explainer.explanation_facts(instance, plan, report)
    by_id = {s["id"]: s["text"] for s in facts["statements"]}
    assert by_id["later_ports"] == (f"Projected at later ports: {facts['later_restow_lifts']} restow lifts and "
                                    f"{facts['later_hatch_cover_lifts']} hatch-cover lifts.")
    loads = facts["planned_moves_by_type"]["load"]
    assert by_id["loads"] == f"{loads} boxes are loaded."
    assert by_id["total"].startswith(f"{report.count.total_lifts_now} crane lifts at this call")


# --- 3. Medium: baseline comparison, tightest limit, no developer words --------------------------------------
def test_baseline_comparison_is_offered_and_rendered_from_both_reports(instance):
    plan, report = load(B)
    base_plan, base_report = load(C)
    facts = explainer.explanation_facts(instance, plan, report, baseline=("two-step", base_plan, base_report))
    text = {s["id"]: s["text"] for s in facts["statements"]}["baseline"]
    assert text.startswith("Against the two-step plan (checker verdict NOT VALIDATED): 9 crane lifts now versus 7; "
                           "weighted move cost 9, 2 higher than its 7.")
    assert "not a proof of the best plan" in text
    shown_text, _, _ = shown(instance, B, '{"headline":["not_run:stability"],"points":[["baseline"],["total"]]}',
                             baseline=("two-step", base_plan, base_report))
    assert "- Against the two-step plan" in shown_text


def test_baseline_must_be_bound_to_the_same_instance(instance):
    plan, report = load(B)
    base_plan, base_report = load(C)
    with pytest.raises(ValueError, match="Baseline"):
        explainer.explanation_facts(instance, plan, report, baseline=("two-step", base_plan, load(D)[1]))


@pytest.mark.parametrize("case", [A, B, C, D])
def test_tightest_limit_is_stated_with_its_margin(instance, case):
    plan, report = load(case)
    text = explainer.template_explanation(instance, plan, report)
    assert "Tightest limit reported as a share of its limit: lashing, transverse racking, 26% of the limit spare." in text


@pytest.mark.parametrize("case", [C, D])
def test_developer_words_never_reach_the_explanation(instance, case):
    plan, report = load(case)
    text = explainer.template_explanation(instance, plan, report)
    for word in ("slicing", "audit", "digest", "canonical fleet policy", "relocate", "gate", "stub-tainted",
                 "checker.missing_vessel_data", "_ratio"):
        assert word not in text
    assert "vessel data missing or out of date" in text


# --- 4. Code review reproductions (w2-rev-explain): all must fall back, none displayed ------------------------
REVIEW_HEADLINES = [
    "GM was not checked, and stability is safe.",
    "7 boxes are loaded and 4 yard rehandles are required.",
    "Stack weight is 29765 tonnes inside the limit.",
    "Stack weight is -29765 kg inside the limit.",
    "Thirteen boxes were loaded.",
    "Stack rules have zero violations.",
    "Lashing did not run.",
    "The proposal will be approved.",
    "The plan has the lowest possible move count.",
    "Box qzwu9999999 is loaded.",
    "Cover hc-1-p is lifted.",
    "IBC Code requirements are met.",
    "แผนนี้ปลอดภัยและได้รับการอนุมัติแล้ว",
    "Stability has been checked and no boxes are shut out.",
    "The rules check has not found any violations.",
]


@pytest.mark.parametrize("sentence", REVIEW_HEADLINES)
@pytest.mark.parametrize("as_id", [False, True])
def test_review_reproductions_are_never_displayed(instance, sentence, as_id):
    answer = (json.dumps({"headline": [sentence], "points": [["loads"], ["not_run:stability"]]}) if as_id else
              json.dumps({"headline": sentence, "points": ["4 boxes are loaded.", "Stability did not run."]}))
    text, explaining, (plan, report) = shown(instance, C, answer)
    assert text == explainer.template_explanation(instance, plan, report)
    assert sentence not in text
    assert explaining.last_rejection


def test_deeply_nested_provider_envelope_falls_back(monkeypatch, tmp_path, instance):
    compute = tmp_path / "compute.md"
    compute.write_text("## Spend ledger\n")
    ledger = tmp_path / "calls.jsonl"
    client = SparkClient(api_key="synthetic-review-key", ledger_path=ledger, compute_path=compute,
                         max_retries=0, credit_ready=True)

    def send(request, **kwargs):
        body = io.BytesIO(b"[" * 10000 + b"0" + b"]" * 10000)
        body.headers = {}
        return body

    monkeypatch.setattr("urllib.request.urlopen", send)
    plan, report = load(C)
    assert explainer.PlanExplainer(client).explain(instance, plan, report) == explainer.template_explanation(
        instance, plan, report)
    # Billing unknown: the reservation stays charged as a conservative hold.
    assert json.loads(ledger.read_text().splitlines()[-1])["phase"] == "reservation"


# --- Recorded live answers ------------------------------------------------------------------------------------
@pytest.mark.parametrize("entry", RECORDED, ids=[f"{e['run']}-{e['case']}" for e in RECORDED])
def test_recorded_answers_follow_the_v3_contract(instance, entry):
    text, explaining, (plan, report) = shown(instance, entry["case"], entry["raw"])
    if entry["expect_shown"]:
        assert entry["run"].startswith("v3") and entry["judged_faithful"]
        assert text.startswith(explainer._SPARK_HEADER) and explaining.last_rejection == []
        statements = {s["text"] for s in explainer.explanation_facts(instance, plan, report)["statements"]}
        body = text.splitlines()[2:-1]
        for line in body:
            if line.startswith(("Status:", "Other checked statements:")):
                continue
            assert all(part in statements or not part for part in _split(line.removeprefix("- "), statements))
    else:
        assert text == explainer.template_explanation(instance, plan, report)
    assert "not a final stow plan" in text and "PASS" not in text


def _split(line, statements):
    """Greedy split of a rendered line back into statements; leftover text fails the membership test."""
    parts, rest = [], line
    while rest:
        match = next((s for s in sorted(statements, key=len, reverse=True) if rest.startswith(s)), None)
        if match is None:
            return [rest]
        parts.append(match)
        rest = rest[len(match):].lstrip()
    return parts


def test_every_prose_answer_is_now_rejected_and_v3_selections_are_shown():
    assert all(not e["expect_shown"] for e in RECORDED if not e["run"].startswith("v3"))
    assert sum(e["expect_shown"] for e in RECORDED if e["run"].startswith("v3")) == 4
