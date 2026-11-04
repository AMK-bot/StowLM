"""C2 uses outward D49 rows; failure of a sufficient shift never proves infeasibility."""
from types import SimpleNamespace as NS
from pathlib import Path
import hashlib
import json

import numpy as np
import pytest

from stowage.feasibility._engine import IncrementalEngine
from stowage.feasibility.global_bounds import GlobalBounds, GlobalScreen


@pytest.mark.parametrize("legacy", [False, True])
def test_c2_constructors_explicitly_request_necessary_direction(monkeypatch, legacy):
    import stowage.linear as linear
    import stowage.feasibility._legacy as old

    model = NS(cell_ids=("0010102",), tank_ids=(), tank_adjustable=np.zeros(0, bool),
               tank_capacity_kg=np.zeros(0))
    rows = NS(names=("gm",), a_cell=np.ones((1, 1)), a_tank=np.zeros((1, 0)),
              const=np.zeros(1), lb=np.array([-np.inf]), ub=np.array([10.]))
    calls = []

    def constraints(model, *, direction, margin_scale=1.):
        calls.append((direction, margin_scale))
        return rows

    for module in (linear, old):
        monkeypatch.setattr(module, "band_for_instance", lambda instance: None)
        monkeypatch.setattr(module, "build_linear_model", lambda instance, **kw: model)
        monkeypatch.setattr(module, "linear_constraints", constraints)
    stack = NS(id="00101H", cell_ids=["0010102"], max_mass_kg=20.,
               max_mass_20_kg=None, max_mass_40_kg=None)
    instance = NS(vessel=NS(hull=NS(stations=[object()]), stacks=[stack]), containers=[],
                  discharge_list=[])
    tables = NS(cell_ids=list(model.cell_ids), cell_index={"0010102": 0},
                stack_ids=[stack.id], stack_cells=[[0]], stack_lim_mixed=np.array([20.]),
                stack_lim_20=np.array([20.]), stack_lim_40=np.array([20.]), share=np.array([10.]))
    if legacy:
        assert GlobalBounds(instance).available
    else:
        screen = GlobalScreen.from_instance(instance, tables)
        assert screen.reference(np.zeros(1), np.ones(1, bool), 5.) is not None
    assert calls == [("necessary", 1.)]


def _shift_failure_engine(*, budget=0):
    # A feasible reference has moment 11, but globally scaling it after putting
    # 2kg in cell0 gives moment8.8. The local donor cell1 gives moment9.
    # An independent completion (2,5,3) still has moment11: shift failure alone
    # cannot reject this candidate.
    screen = GlobalScreen([[0., 1., 2.]], np.zeros((1, 0)), [0.], [11.], [11.], [], [], 10.)
    ref = screen._make(np.zeros(3), np.ones(3, bool), np.array([0., 9., 1.]),
                       np.zeros(0), 10., centered=True)
    state = NS(ref=ref, c2_dead=False, c2_sols={})
    eng = NS(T=NS(slot_cells=np.array([[0, -1]]), slot_n=np.array([1]), mass=np.array([2., 3.])),
             _global=screen, _lp_budget=budget, _recenter_below=.5,
             _donors=np.array([[1]]), _sync_lp=lambda: None,
             diagnostics={"c2_unresolved": 0, "c2_slot_lps": 0})
    return eng, state


def test_c2_zero_lp_budget_does_not_cut_feasible_completion():
    eng, state = _shift_failure_engine()
    assert eng._global.slot_interval(state.ref, [0], 2., 2.) is not None
    result = IncrementalEngine._c2(eng, state, np.array([0]), np.array([0]))
    assert result.tolist() == [True]
    assert 0 in state.c2_sols


def test_c2_slot_interval_cache_covers_later_candidate_subsets():
    eng, state = _shift_failure_engine(budget=10)
    result = IncrementalEngine._c2(eng, state, np.array([0]), np.array([0]))
    assert result.tolist() == [True]
    before = eng._global.lp_calls
    # A singleton query must not cache only that box's mass. The same state's
    # later query has another feasible mass and needs no repeated LP.
    result = IncrementalEngine._c2(eng, state, np.array([1]), np.array([0]))
    assert result.tolist() == [True]
    assert eng._global.lp_calls == before


def test_reference_centres_slack_without_charging_exact_mass_equality():
    # Equality cargo_mass=10 cannot gain slack. Charging it the centering
    # objective forces tau=0 and returns a corner, needlessly failing shifts.
    # The other physical row can have five units of slack on both sides.
    screen = GlobalScreen([[1., 1.], [0., 1.]], np.zeros((2, 0)), [0., 0.],
                          [10., 0.], [10., 10.], [], [], 10.)
    reference = screen.reference(np.zeros(2), np.ones(2, bool), 10.)
    assert reference is not None
    np.testing.assert_allclose(reference.y, [5., 5.], atol=1e-7)
    assert screen.feasible(reference)


@pytest.fixture(scope="module")
def current_hull():
    """Current KCS-derived hull and an untouched D49 physical input vector.

    Saved arrays specify masses and physical centres only; both the affine
    model and independent public checkers run afresh. This fractional loading
    fixture is not a legal container-plan/full-checker certificate.
    """
    from stowage.feasibility.tables import build_tables
    from stowage.linear import band_for_instance, build_linear_model
    from stowage.model import ShipState
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_ship_params, load_vessel

    root = Path(__file__).resolve().parents[2]
    data = json.loads((Path(__file__).with_name("data") / "d49-feeder500-light.json").read_text())
    # No epoch retagging: the seed arrays belong to these exact source hulls.
    params = load_ship_params("feeder_500")
    paths = ["params/fleet/feeder_500.toml", params["hull_form"]["offsets_path"]]
    actual = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in paths}
    fleet_path = "params/fleet/feeder_500.toml"
    if data["parameter_sha256"][fleet_path] != actual[fleet_path]:
        pytest.skip("D173 F: the D49 numerical fixture pins an older feeder-500 tank model; "
                    "params/fleet/feeder_500.toml has a different physical epoch. Regenerate and independently "
                    "qualify the fixture; do not retag its checksums. "
                    "See experiments/mvp/postfreeze/FAILURES-D171.md (F).")
    assert data["parameter_sha256"] == {path: hashlib.sha256((root / path).read_bytes()).hexdigest()
                                          for path in paths}
    base = build_instance()
    vessel = load_vessel("feeder_500", with_hull=True)
    # Exact implied presolve for this <=30.48t box universe (source fixture's
    # original construction); never changes any physically realizable stow.
    vessel = vessel.model_copy(update={"stacks": [
        s.model_copy(update={"max_mass_kg": min(s.max_mass_kg, 30_480. * len(s.cell_ids))})
        for s in vessel.stacks]})
    template = base.containers[1]
    boxes = [template.model_copy(update={**spec, "pol": "THLCH", "pod": "THBKK", "dg": None,
               "oog": None, "reefer": False, "reefer_setpoint_c": None})
             for spec in data["cargo_specs"]]
    payload = base.model_dump(mode="python")
    payload.update(id="feasibility-d49-feeder500-light", vessel=vessel.model_dump(),
                   onboard=ShipState(vessel_id=vessel.id).model_dump(),
                   containers=[b.model_dump() for b in boxes], load_list=[b.id for b in boxes],
                   discharge_list=[], yard=data["yard"])
    payload["port"]["yard"] = data["port_yard"]
    payload["port"]["max_draught_m"] = vessel.hull.max_draught_m
    instance = type(base).model_validate(payload)
    sample = data["sample"]
    model = build_linear_model(instance, band=band_for_instance(instance))
    cargo = np.array(sample["cell_masses_kg"])
    tanks = np.array(sample["tank_masses_kg"])
    heights = np.array(sample["cell_vcg_m"])
    assert len(cargo) == len(model.cell_ids)
    assert cargo.sum() == pytest.approx(sum(b.gross_mass_kg for b in instance.containers))
    assert np.all(tanks >= 0) and np.all(tanks <= model.tank_capacity_kg + 1e-6)
    loads = [*instance.vessel.fixed_loads,
             *((float(x), float(y), float(z), float(m))
               for x, y, z, m in zip(model.cell_lcg_m, model.cell_tcg_m, heights, cargo) if m > 0)]
    physical = instance.model_copy(update={
        "vessel": instance.vessel.model_copy(update={"fixed_loads": loads})})
    ship = ShipState(vessel_id=instance.vessel.id,
                     tank_masses_kg=dict(zip(model.tank_ids, map(float, tanks))))
    return instance, build_tables(instance), model, cargo, tanks, physical, ship


def test_current_hull_conservative_reject_necessary_admit_is_no_longer_cut(current_hull, monkeypatch):
    import stowage.linear as linear
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength

    instance, tables, model, cargo, tanks, physical, ship = current_hull
    screen = GlobalScreen.from_instance(instance, tables)
    # Independently assembled 3.7 rows must survive cell-order projection and
    # adjustable-tank selection unchanged. Include every loading/strength row.
    rows = linear.linear_constraints(model, direction="necessary")
    keep = np.isfinite(rows.lb) | np.isfinite(rows.ub)
    col = [tables.cell_index[c] for c in model.cell_ids]
    count = int(keep.sum())
    np.testing.assert_array_equal(screen.A[:count, col], rows.a_cell[keep])
    np.testing.assert_array_equal(screen.At[:count], rows.a_tank[keep][:, model.tank_adjustable])
    np.testing.assert_array_equal(screen.lo[:count], rows.lb[keep])
    np.testing.assert_array_equal(screen.hi[:count], rows.ub[keep])
    assert screen.names[:count] == tuple(n for n, k in zip(rows.names, keep) if k)

    actual = {"stability": check_stability(physical, ship, call_index=0),
              "strength": check_strength(physical, ship, call_index=0)}
    for part in actual.values():
        assert part.ran and part.error is None
    modeled_stability_rules = {"iscode.loading.gm", "iscode.loading.trim", "iscode.loading.list",
                               "iscode.loading.draft", "iscode.loading.displacement"}
    assert not (modeled_stability_rules & {v.rule_id for v in actual["stability"].violations})
    assert not actual["strength"].violations
    # Other exact criteria (e.g. the weather criterion) remain outside C2.
    fixed = np.zeros(len(tables.cell_ids))
    fixed[col] = cargo
    free = np.zeros(len(fixed), bool)
    adjustable = tanks[model.tank_adjustable]
    frozen = GlobalScreen(screen.A, screen.At, screen.const, screen.lo, screen.hi,
                          adjustable, adjustable, screen.u0, screen.names)
    assert frozen.reference(fixed, free, 0.) is not None

    original = linear.linear_constraints

    def conservative(model, *, direction, margin_scale=1.):
        assert direction == "necessary"
        return original(model, direction="conservative", margin_scale=margin_scale)

    monkeypatch.setattr(linear, "linear_constraints", conservative)
    inner = GlobalScreen.from_instance(instance, tables)
    inner = GlobalScreen(inner.A, inner.At, inner.const, inner.lo, inner.hi,
                         adjustable, adjustable, inner.u0, inner.names)
    assert inner.reference(fixed, free, 0.) is None


@pytest.mark.parametrize("quantity", ["trim", "strength"])
def test_current_hull_infeasible_stability_and_strength_rows(current_hull, quantity):
    from stowage.checker.stability import check_stability
    from stowage.checker.strength import check_strength

    instance, tables, model, cargo, tanks, physical, ship = current_hull
    hull = instance.vessel.hull
    overrides = instance.rule_overrides
    if quantity == "trim":
        # SYNTHETIC tighter operating limit exposes an unmistakable trim failure;
        # hull geometry, cargo/liquid physics and D49 reserves remain intact.
        changed = hull
        overrides = {**overrides, "iscode.loading.trim": {"minimum": 1.0, "maximum": hull.max_trim_m}}
    else:
        # SYNTHETIC tighter permissible loads exercise real SF/BM rejection.
        stations = [s.model_copy(update={
            "min_shear_n": s.min_shear_n * .01, "max_shear_n": s.max_shear_n * .01,
            "min_bending_nm": s.min_bending_nm * .01, "max_bending_nm": s.max_bending_nm * .01})
            for s in hull.stations]
        changed = hull.model_copy(update={"stations": stations})
    candidate = instance.model_copy(update={"vessel": instance.vessel.model_copy(update={"hull": changed}),
                                           "rule_overrides": overrides})
    reference = physical.model_copy(update={"vessel": physical.vessel.model_copy(update={"hull": changed}),
                                           "rule_overrides": overrides})
    screen = GlobalScreen.from_instance(candidate, tables)
    fixed = np.zeros(len(tables.cell_ids))
    fixed[[tables.cell_index[c] for c in model.cell_ids]] = cargo
    adjustable = tanks[model.tank_adjustable]
    frozen = GlobalScreen(screen.A, screen.At, screen.const, screen.lo, screen.hi,
                          adjustable, adjustable, screen.u0, screen.names)
    assert frozen.reference(fixed, np.zeros(len(fixed), bool), 0.) is None
    if quantity == "trim":
        part = check_stability(reference, ship, call_index=0)
        assert part.ran and any(v.rule_id == "iscode.loading.trim" for v in part.violations)
    else:
        part = check_strength(reference, ship, call_index=0)
        assert part.ran and any(v.rule_id.startswith("iacs.") for v in part.violations)
