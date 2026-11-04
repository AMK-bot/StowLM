"""Soft cold hints preserve the restricted construction solution family."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from constructor_tests import forced_context
from constructor_fast import add_cold_hints, cold_yard_order
from bound_joint import solve_model


def test_cold_hint_keeps_yard_exports_top_first():
    instance, _ = forced_context()
    assert cold_yard_order(instance) == list(reversed(instance.load_list))


def test_soft_hint_does_not_forbid_required_export_relocations():
    instance, context = forced_context()
    before = len(context["model"].proto.constraints)
    order = add_cold_hints(context)
    assert order == list(reversed(instance.load_list))
    assert len(context["model"].proto.constraints) == before
    context["stop_after_first_solution"] = True
    result = solve_model(context)
    assert result.relaxation_assignment is not None
    assert result.first_aboard_order == instance.load_list
    assert result.current_order_flags["ship_restowed"] == []
    assert result.witness_current_order == 2


def test_cold_hint_is_deterministic_and_ignores_yard_nonloads():
    instance, _ = forced_context()
    first = cold_yard_order(instance)
    reduced = instance.model_copy(update={"load_list": instance.load_list[:2]})
    assert cold_yard_order(instance) == first
    assert cold_yard_order(reduced) == list(reversed(instance.load_list[:2]))
