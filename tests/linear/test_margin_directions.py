"""D49 requires explicit inward/outward use of independent residual evidence."""
from copy import deepcopy
from dataclasses import replace
import inspect
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.linear.model import evaluate_linear, linear_constraints, solve_ballast_lp
from tests.linear.test_operations import example


@pytest.fixture
def policies(monkeypatch):
    import stowage.linear.margin_policy as margins
    metrics = ("gm_m", "trim_m", "list_rad", "draught_m", "sf_n", "bm_nm",
               "draught_aft_m", "draught_forward_m")
    def envelope(lower, upper, moments):
        record = {}
        for side, values in (("lower_unsafe", lower), ("upper_unsafe", upper)):
            record[side] = {name: np.asarray([value] if name in ("sf_n", "bm_nm") else value)
                            for name, value in zip(metrics, values, strict=True)}
        record["list_moment_kgm"] = dict(zip(("lower_unsafe", "upper_unsafe"), moments, strict=True))
        return record
    banks = {
        "conservative": envelope((.02,.03,.04,.05,6.,7.,.08,.09),
                                 (.01,.11,.12,.13,14.,15.,.16,.17), (20.,30.)),
        "necessary": envelope((1.2,.4,.4,.3,40.,50.,.6,.7),
                              (.3,.5,.4,.35,45.,55.,.65,.75), (2000.,3000.)),
    }
    calls = []
    def qualified(model, *, direction):
        calls.append(direction)
        return deepcopy(banks[direction])
    monkeypatch.setattr(margins, "default_reserves", qualified)
    return banks, calls


@pytest.mark.parametrize("function", [linear_constraints, solve_ballast_lp])
def test_margin_direction_is_required_keyword(function):
    parameter = inspect.signature(function).parameters.get("direction")
    assert parameter is not None
    assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize("direction", [None, "auto", "outward", "", 1])
def test_invalid_direction_fails_before_policy_lookup(policies, direction):
    model, ship, instance = example()
    with pytest.raises(ValueError, match="direction"):
        linear_constraints(model, direction=direction)
    with pytest.raises(ValueError, match="direction"):
        solve_ballast_lp(model, ship, instance, direction=direction)
    assert not policies[1]


def test_conservative_direction_preserves_existing_row_algebra(policies):
    model, _, _ = example()
    model = replace(model, hull_max_draught_m=8., hull_min_draught_fwd_m=.5)
    rows = linear_constraints(model, direction="conservative", margin_scale=2.)
    at = rows.names.index
    assert rows.const[at("gm")] == pytest.approx(9000.+(.5+.04-10.)*1000.)
    assert rows.lb[at("trim")] == pytest.approx(-1.+.06)
    assert rows.ub[at("trim")] == pytest.approx(1.-.22)
    assert rows.lb[at("draught")] == pytest.approx(.1)
    assert rows.ub[at("draught")] == pytest.approx(10.-.26)
    assert rows.ub[at("draught_aft")] == pytest.approx(8.-.32)
    assert rows.lb[at("draught_forward")] == pytest.approx(.5+.18)
    assert rows.ub[at("sf@0")] == pytest.approx(1e8-28.)
    assert rows.lb[at("bm@0")] == pytest.approx(-1e8+14.)
    restore = (10.-.04)*1000.-9000.
    assert rows.const[at("list_port")] == pytest.approx(-np.tan(.2)*restore+60.)
    assert policies[1] == ["conservative"]


def test_necessary_direction_uses_opposite_evidence_and_raw_list_restore(policies):
    model, _, _ = example()
    model = replace(model, hull_max_draught_m=8., hull_min_draught_fwd_m=.5)
    rows = linear_constraints(model, direction="necessary", margin_scale=2.)
    at = rows.names.index
    assert rows.const[at("gm")] == pytest.approx(9000.+(.5-2.4-10.)*1000.)
    assert rows.lb[at("trim")] == pytest.approx(-1.-.8)
    assert rows.ub[at("trim")] == pytest.approx(1.+1.)
    assert rows.lb[at("draught")] == pytest.approx(-.6)
    assert rows.ub[at("draught")] == pytest.approx(10.+.7)
    assert rows.ub[at("draught_aft")] == pytest.approx(8.+1.3)
    assert rows.lb[at("draught_forward")] == pytest.approx(.5-1.4)
    assert rows.ub[at("sf@0")] == pytest.approx(1e8+90.)
    assert rows.lb[at("bm@0")] == pytest.approx(-1e8-100.)
    # Reverse LIST evidence was measured on raw maximum-FSM restoring moment;
    # adding the outward GM allowance here would change that measured predictor.
    assert rows.const[at("list_port")] == pytest.approx(-np.tan(.2)*1000.-6000.)
    assert rows.const[at("list_starboard")] == pytest.approx(-np.tan(.2)*1000.-4000.)
    assert policies[1] == ["necessary"]


def test_necessary_rows_contain_every_conservative_accepted_mass_vector(policies):
    model, _, _ = example()
    model = replace(model, hull_max_draught_m=3., hull_min_draught_fwd_m=.5,
                    _constraint_cell_vcg_m=np.array([12.]),
                    tank_fsm_max_kgm=np.array([50.,75.]))
    inner = linear_constraints(model, direction="conservative")
    outer = linear_constraints(model, direction="necessary")
    assert inner.names == outer.names
    count = 0
    for cargo in (0., 10., 25., 50.):
        for low in (0., 100., 300., 500.):
            for high in (0., 100., 300., 500.):
                c, t = np.array([cargo]), np.array([low, high])
                iv = inner.a_cell@c+inner.a_tank@t+inner.const
                if np.all(iv >= inner.lb) and np.all(iv <= inner.ub):
                    count += 1
                    ov = outer.a_cell@c+outer.a_tank@t+outer.const
                    assert np.all(ov >= outer.lb)
                    assert np.all(ov <= outer.ub)
    assert count > 20


def test_empty_tank_fsm_bias_is_widened_by_measured_reverse_gm(policies):
    model, ship, instance = example(adjustable=False)
    model = replace(model, tank_fsm_max_kgm=np.array([1000., 1000.]))
    instance.vessel.tanks = [SimpleNamespace(id=key, kind="ballast") for key in model.tank_ids]
    policies[0]["necessary"]["lower_unsafe"]["gm_m"] = np.asarray(2.)
    # Empty rectangular tanks have zero actual upright FSM. The unchanged
    # affine predictor still charges their maxima; reverse residual evidence
    # accounts for that deliberate bias without dropping physical terms.
    assert evaluate_linear(model, ship, instance).gm_m == pytest.approx(1.)
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "infeasible"
    assert solve_ballast_lp(model, ship, instance, direction="necessary").status == "optimal"


def test_necessary_uses_same_upper_vcg_and_max_fsm_predictor(policies):
    model, _, _ = example()
    model = replace(model, _constraint_cell_vcg_m=np.array([12.]),
                    tank_fsm_max_kgm=np.array([30.,40.]))
    rows = linear_constraints(model, direction="necessary")
    gm = rows.names.index("gm")
    assert rows.a_cell[gm, 0] == pytest.approx(12.+(.5-1.2-10.))
    assert rows.const[gm] == pytest.approx(9000.+(.5-1.2-10.)*1000.+70.)


def test_necessary_list_never_uses_conditioned_interior_angular_envelope(policies):
    model, _, _ = example()
    del policies[0]["necessary"]["list_moment_kgm"]
    with pytest.raises(ValueError, match="necessary.*LIST|necessary.*boundary"):
        linear_constraints(model, direction="necessary")


def test_increasing_scale_only_widens_necessary_row_feasibility(policies):
    model, _, _ = example()
    small = linear_constraints(model, direction="necessary", margin_scale=1.)
    big = linear_constraints(model, direction="necessary", margin_scale=3.)
    c, t = np.array([100.]), np.array([100.,100.])
    sv = small.a_cell@c+small.a_tank@t+small.const
    bv = big.a_cell@c+big.a_tank@t+big.const
    assert np.all(bv-big.lb >= sv-small.lb)
    assert np.all(big.ub-bv >= small.ub-sv)
