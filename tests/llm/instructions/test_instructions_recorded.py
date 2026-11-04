"""Recorded live Muse Spark answers (experiments/mvp/instruct/responses) still validate and score as reported."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm.instructions import Refusal, validate_answer

HERE = Path(__file__).resolve().parents[3] / "experiments/mvp/instruct"
RUNS = {  # run file -> (set, expected overall score as reported in report.md)
    "v1": ("testset", "20/20"), "v1-repeat": ("testset", "20/20"),
    "testset-v2-a": ("testset", "20/20"), "testset-v2-b": ("testset", "20/20"),
    "adversarial-v1": ("adversarial", "4/6"), "adversarial-v2": ("adversarial", "6/6"),
    "heldout-v2": ("heldout", "7/7"),
}


def rescore(run: str):
    data = json.loads((HERE / f"responses/{run}.json").read_text())
    cases = {c["id"]: c for c in json.loads((HERE / f"{RUNS[run][0]}.json").read_text())}
    correct = 0
    for row in data["rows"]:
        outcome = validate_answer(row["raw"].strip())
        got = ({"refusal": outcome.code} if isinstance(outcome, Refusal)
               else {"constraint": outcome.model_dump(mode="json")})
        expected = cases[row["id"]]["expected"]
        correct += ("refusal" in got) if "refusal" in expected else (got == expected)
        assert got == row["got"]
    return f"{correct}/{len(data['rows'])}", data


@pytest.mark.parametrize("run", sorted(RUNS))
def test_recorded_run_rescores_as_reported(run):
    score, data = rescore(run)
    assert score == RUNS[run][1] == data["summary"]["overall"]


def test_loosening_permission_was_misparsed_by_v1_and_refused_by_v2():
    v1 = {r["id"]: r for r in rescore("adversarial-v1")[1]["rows"]}
    v2 = {r["id"]: r for r in rescore("adversarial-v2")[1]["rows"]}
    assert "constraint" in v1["a01"]["got"] and v2["a01"]["got"] == {"refusal": "loosening"}
