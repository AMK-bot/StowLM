"""Construction orders are restricted; their bounds are not global proofs."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import pytest

from bound_tests import fixture
from bound_joint import solve_model
from constructor import (Construction, build_constructor_model,
                         witness_actions, _exclude_ordered_witness)
from stowage.feasibility.tables import build_tables


def forced_context():
    instance = fixture()
    tables = build_tables(instance)
    tables.static[:] = False
    slots = ["0010102", "0010104", "0010182"]
    for bid, slot in zip(instance.load_list, slots):
        tables.static[tables.box_index[bid], tables.slot_index[slot]] = True
    return instance, build_constructor_model(instance, budget_s=3, tables=tables)


def test_load_once_constructor_enforces_real_support_and_cover_order():
    instance, context = forced_context()
    result = solve_model(context)
    assert result.status == "optimal"
    assert result.current_order_flags["ship_restowed"] == []
    assert result.first_aboard_order == instance.load_list
    assert result.current_order_flags["yard_relocated"] == instance.load_list[1:]
    assert result.witness_current_order == 2
    actions = witness_actions(result.relaxation_assignment,
                              result.first_aboard_order, instance.load_list)
    assert [a.box for a in actions] == instance.load_list
    assert [a.slot for a in actions] == ["0010102", "0010104", "0010182"]


def test_replay_nogood_excludes_exact_witness():
    instance, context = forced_context()
    result = solve_model(context)
    _exclude_ordered_witness(context, result.relaxation_assignment, result.first_aboard_order)
    assert solve_model(context).status == "infeasible"


@pytest.mark.parametrize("assignment,order", [
    ({"A": "0010102"}, ["A", "A"]),
    ({"A": "0010102"}, ["B"]),
    ({"A": "0010102", "B": "0010104"}, ["A"]),
])
def test_bad_witness_cannot_silently_become_partial_plan(assignment, order):
    with pytest.raises(ValueError, match="exactly once"):
        witness_actions(assignment, order, ["A", "B"])


def test_constructor_result_never_claims_global_bound_or_checker_pass():
    result = Construction()
    assert "not_a_global_bound" in result.family
    assert not hasattr(result, "lower_bound")
    assert not result.full_checker_pass
