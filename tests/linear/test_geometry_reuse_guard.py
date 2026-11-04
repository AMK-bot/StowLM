"""Same vessel IDs cannot authorize stale geometry or safety limits."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from stowage.linear.cache import _canonical, vessel_physics
from stowage.linear.model import evaluate_linear, linear_constraints, solve_ballast_lp
from tests.linear.test_operations import example


class Vessel(SimpleNamespace):
    def model_dump(self, **kwargs):
        def dump(value):
            if isinstance(value, SimpleNamespace):
                return {key: dump(item) for key, item in vars(value).items()}
            if isinstance(value, list):
                return [dump(item) for item in value]
            return value
        return dump(self)


@pytest.fixture
def bound_model():
    model, ship, instance = example()
    model = replace(model, max_trim_m=2., trim_line=(1., 0., 0.),
                    band=replace(model.band, max_abs_trim_m=3.))
    # These independent algebra fixtures have no hull integration/calibration.
    # The identity is exactly the metadata a builder must retain.
    instance.vessel = Vessel(id=model.vessel_id, cells=[dict(id=model.cell_ids[0], lcg_m=0., tcg_m=1.)],
        stacks=[dict(id="00101H", cell_ids=list(model.cell_ids), base_z_m=0.,
                     max_height_m=5., on_deck=False)],
        tanks=[SimpleNamespace(id=key, kind="ballast", content_density_kg_m3=1025., capacity_m3=1.,
                    adjustable=True, table=[]) for key in model.tank_ids],
        hull=dict(lpp_m=10., beam_m=2., depth_m=10., max_trim_m=2., min_gm_m=.5,
                  max_list_rad=.2, max_draught_m=10., min_draught_fwd_m=0.,
                  min_draught_aft_m=0., stations=[dict(x_m=0., max_shear_n=1e8,
                  min_shear_n=-1e8, max_bending_nm=1e8, min_bending_nm=-1e8)]))
    physical = vessel_physics(instance.vessel)
    physical["assembly_stack_limits"] = [
        {key: stack[key] for key in ("id", "max_height_m", "on_deck")}
        for stack in instance.vessel.stacks]
    physical["loading_manual_endpoints"] = {
        key: instance.vessel.hull.get(key) for key in
        ("min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg")}
    fingerprint = hashlib.sha256(_canonical(physical)).hexdigest()
    object.__setattr__(model, "_vessel_physics_fingerprint", fingerprint)
    return model, ship, instance


@pytest.mark.parametrize("operation", [evaluate_linear, solve_ballast_lp])
@pytest.mark.parametrize("path,value", [
    (("hull", "max_trim_m"), .5),
    (("hull", "min_gm_m"), 2.),
    (("hull", "max_list_rad"), .05),
    (("hull", "max_draught_m"), .5),
    (("hull", "min_draught_fwd_m"), 2.),
    (("hull", "min_draught_aft_m"), 2.),
    (("hull", "stations", 0, "max_shear_n"), 1.),
    (("hull", "stations", 0, "min_bending_nm"), -1.),
    (("cells", 0, "lcg_m"), 1.),
    (("cells", 0, "tcg_m"), 2.),
    (("stacks", 0, "base_z_m"), 2.),
    (("stacks", 0, "max_height_m"), 7.),
    (("stacks", 0, "on_deck"), True),
    (("tanks", 0, "capacity_m3"), 2.),
])
def test_same_id_changed_physical_inputs_require_rebuild(bound_model, operation, path, value):
    model, ship, instance = bound_model
    target = instance.vessel
    for key in path[:-1]:
        target = getattr(target, key) if isinstance(target, SimpleNamespace) else target[key]
    if isinstance(target, SimpleNamespace):
        setattr(target, path[-1], value)
    else:
        target[path[-1]] = value
    assert instance.vessel.id == model.vessel_id
    with pytest.raises(ValueError, match="vessel geometry or limits.*rebuild"):
        operation(model, ship, instance, **({"direction": "conservative"} if operation is solve_ballast_lp else {}))


def test_same_id_trim_reproducer_has_old_row_allowing_one_metre(bound_model):
    model, ship, instance = bound_model
    rows = linear_constraints(model, direction="conservative")
    trim = rows.names.index("trim")
    assert rows.lb[trim] <= 1. <= rows.ub[trim]
    assert evaluate_linear(model, ship, instance).trim_m == 1.
    instance.vessel.hull["max_trim_m"] = .5
    with pytest.raises(ValueError, match="vessel geometry or limits"):
        solve_ballast_lp(model, ship, instance, direction="conservative")


def test_equal_physics_independent_vessel_is_accepted(bound_model):
    model, ship, instance = bound_model
    instance.vessel = deepcopy(instance.vessel)
    assert evaluate_linear(model, ship, instance).trim_m == 1.
    assert solve_ballast_lp(model, ship, instance, direction="conservative").status == "optimal"


def test_display_metadata_does_not_change_physics_identity(bound_model):
    model, ship, instance = bound_model
    instance.vessel.name = "another display name"
    instance.vessel.sources = ["synthetic provenance only"]
    assert evaluate_linear(model, ship, instance).trim_m == 1.
