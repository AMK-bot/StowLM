"""Cheap set-content policy guards; no dataset, tokenizer or runtime imports."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("d132_guard", Path(__file__).with_name("guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

HEAT = {"remaining": ["heat-target"], "old_unrepresented_tanks": ["FO"],
        "correct_unrepresented_tanks": []}


@pytest.mark.parametrize("before,after,context,accepted", [
    ([1], [1, 2], HEAT, True),
    ([1, 2], [1], HEAT, False),
    ([1, 2], [1, 3], HEAT, False),
    ([1], [1, 2], {**HEAT, "remaining": []}, False),
    ([1], [1, 2], {**HEAT, "old_unrepresented_tanks": []}, False),
    ([1], [1, 2], {**HEAT, "correct_unrepresented_tanks": ["FO"]}, False),
    ([{"box": "a", "slot": "1"}], [{"box": "a", "slot": "2"}], HEAT, False),
])
def test_only_heat_relevant_additions_are_accepted(before, after, context, accepted):
    assert guard.classify_legal_delta(before, after, context)[2] is accepted


def test_forbidden_test_path_rejected_before_any_read():
    with pytest.raises(ValueError, match="D110 excluded"):
        guard.approved_map("experiments/mvp/corpus/test/small/must-not-open.json")
