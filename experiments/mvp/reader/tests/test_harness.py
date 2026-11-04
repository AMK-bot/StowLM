"""CPU-only harness checks: no sealed examples, model weights, or MLX imports."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from experiments.mvp.reader.evaluate import gate_report, normalized, prompt_messages, score_predictions
from experiments.mvp.reader.schema import messages
from experiments.mvp.reader.train import TokenDataset, check_model, iterations_for, validate_splits

CONTEXT = {"bays": [1, 2, 3], "max_tiers": {"hold": 4, "deck": 3},
           "has_accommodation": True, "hatch_covers": ["H1"]}
TIGHT = {"schema_version": "stowlm.reader/2", "constraint": {"type": "close_bay", "bay": 1}}
REFUSE = {"schema_version": "stowlm.reader/2", "refusal": {
    "refused": True, "code": "loosening", "reason": "Do not relax the cap."}}


def row(identifier="r1", family="family_a", expected=None):
    expected = copy.deepcopy(expected or TIGHT)
    return {"id": identifier, "family": family, "source": "test-fixture",
            "sentence": "Keep bay 1 closed." if expected == TIGHT else "Remove the tier cap.",
            "context": CONTEXT,
            "expected": expected,
            "messages": messages("Keep bay 1 closed.", CONTEXT) + [
                {"role": "assistant", "content": json.dumps(expected)}]}


def test_exact_ignores_only_reason_and_counts_loosening_acceptance():
    rows = [row(), row("r2", expected=REFUSE)]
    different_reason = copy.deepcopy(REFUSE)
    different_reason["refusal"]["reason"] = "No."
    metric = score_predictions(rows, [{"id": "r1", "prediction": json.dumps(TIGHT)},
                                     {"id": "r2", "prediction": different_reason}])
    assert metric["exact_match_pct"] == 100
    assert metric["false_acceptance_count"] == 0
    metric = score_predictions(rows, [{"id": "r2", "prediction": TIGHT}])
    assert metric["false_acceptance_count"] == 1
    assert metric["missing_count"] == 1


def test_invalid_json_is_incorrect_not_helpfully_refused():
    metric = score_predictions([row(expected=REFUSE)], [{"id": "r1", "prediction": "```json\n{}\n```"}])
    assert metric["exact_match_count"] == 0
    assert metric["malformed_count"] == 1
    with pytest.raises(ValueError):
        normalized('{"schema_version":"stowlm.reader/2","constraint":{"type":"close_bay","bay":1,"bay":3}}')


def test_invalid_context_constraint_still_counts_false_acceptance():
    wrong_bay = copy.deepcopy(TIGHT)
    wrong_bay["constraint"]["bay"] = 99
    metric = score_predictions([row(expected=REFUSE)], [{"id": "r1", "prediction": wrong_bay}])
    assert metric["false_acceptance_count"] == 1
    assert metric["malformed_count"] == 1
    assert metric["exact_match_count"] == 0


def test_wrong_refusal_code_is_incorrect():
    changed = copy.deepcopy(REFUSE)
    changed["refusal"]["code"] = "unsupported"
    metric = score_predictions([row(expected=REFUSE)], [{"id": "r1", "prediction": changed}])
    assert metric["exact_match_count"] == 0


def test_duplicate_and_unknown_prediction_ids_rejected():
    with pytest.raises(ValueError):
        score_predictions([row()], [{"id": "r2", "prediction": TIGHT}])
    with pytest.raises(ValueError):
        score_predictions([row()], [{"id": "r1", "prediction": TIGHT}] * 2)


def test_family_overlap_and_sealed_admission_rejected():
    train, valid = [row()], [row("r2", "family_b", REFUSE)]
    assert validate_splits(train, valid)["train_count"] == 1
    valid[0]["family"] = "family_a"
    with pytest.raises(ValueError, match="families overlap"):
        validate_splits(train, valid)
    valid[0]["family"] = "family_b"
    valid[0]["source"] = "sealed-reviewer"
    with pytest.raises(ValueError, match="sealed"):
        validate_splits(train, valid)


def test_inference_prompt_excludes_assistant_answer():
    assert [m["role"] for m in prompt_messages(row())] == ["system", "user"]


def test_model_guard_rejects_llama(tmp_path: Path):
    (tmp_path / "config.json").write_text('{"model_type":"llama"}')
    with pytest.raises(ValueError, match="Glimmer"):
        check_model(tmp_path)


class FakeTokenizer:
    def apply_chat_template(self, chat, *, tokenize, add_generation_prompt, return_dict=False):
        if not tokenize:
            return "header"
        return [1, 2, 3, 4] if chat[-1]["role"] == "assistant" else [1, 2]

    def encode(self, text, *, add_special_tokens):
        return [1, 2]


def test_token_dataset_completion_offset_and_no_truncation():
    dataset = TokenDataset([row()], FakeTokenizer(), 4)
    assert dataset[0] == ([1, 2, 3, 4], 2)
    with pytest.raises(ValueError, match="truncation forbidden"):
        TokenDataset([row()], FakeTokenizer(), 3)


def test_iteration_budget_rounded_to_complete_accumulation():
    cfg = {"epochs": 2, "batch_size": 1, "grad_accumulation_steps": 4}
    assert iterations_for(3000, cfg) == 6000
    assert iterations_for(3001, cfg) == 6004


def test_preregistered_gates_and_identical_population():
    metric = score_predictions([row(), row("r2", expected=REFUSE)], [
        {"id": "r1", "prediction": TIGHT}, {"id": "r2", "prediction": REFUSE}])
    untrained = dict(metric, exact_match_pct=80)
    spark = dict(metric, exact_match_pct=100)
    assert gate_report(metric, untrained, spark) == {
        "R1": True, "R2": True, "R3": True,
        "gain_over_untrained_pp": 20, "difference_from_spark_pp": 0}
    with pytest.raises(ValueError):
        gate_report(metric, dict(untrained, count=3), spark)
