"""The cross-process gate must reject subtle prefix and final-plan changes."""
import os

import pytest

_environment = dict(os.environ)
try:
    from llm.maskspeed.crossprocess_guard import baseline_verified, compare_event
finally:
    os.environ.clear()
    os.environ.update(_environment)
    del _environment


def test_archived_runtime_matches_predecessor_recorded_hashes():
    hashes = baseline_verified()
    assert len(hashes) == 3


@pytest.mark.parametrize("field,before,after", [
    ("legal", [11, 22], [11, 23]),
    ("tokens", [11, 22], [11, 23]),
    ("status", "running", "dead_end"),
    ("plan", {"moves": [{"kind": "load", "box": "a"}]},
              {"moves": [{"kind": "load", "box": "b"}]}),
])
def test_every_prefix_and_entire_plan_are_compared(field, before, after):
    common = {"type": "final" if field == "plan" else "prefix", "step": 7}
    with pytest.raises(AssertionError, match="injected event 7 differs"):
        compare_event(dict(common, **{field: before}), dict(common, **{field: after}), "injected", 7)


def test_equal_event_passes():
    event = {"type": "prefix", "step": 0, "legal": [1, 2], "tokens": [], "status": "running"}
    compare_event(event, dict(event), "equal", 0)
