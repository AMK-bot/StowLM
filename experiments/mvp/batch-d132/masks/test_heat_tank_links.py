"""Synthetic batch/scalar E8 parity; no engine construction or hull solving."""
from __future__ import annotations

import __future__
import ast
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker.rules_special import special_violations
from stowage.feasibility import _engine
from stowage.model import ShipState
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement
from stowage.model.vessel import HeatSource


@pytest.fixture
def heat_case(monkeypatch):
    from stowage.vessel import fleet

    def forbidden(*args, **kwargs):
        pytest.fail("Synthetic heat test must not construct a numerical vessel")

    monkeypatch.setattr(fleet, "load_vessel", forbidden)
    instance = build_instance()
    instance.vessel.fixed_loads = []
    box = instance.containers[1]
    box.special_stowage = ["away_from_heat"]
    instance.rule_overrides["expert.e8.away_from_heat"] = {"minimum_clearance": 1.0}
    cell = instance.vessel.cells[0]
    stack = next(s for s in instance.vessel.stacks if s.id == cell.stack_id)
    tank = next(t for t in instance.vessel.tanks if t.heated)
    source = instance.vessel.heat_sources[0]
    tables = SimpleNamespace(cell_ids=(cell.id,), slot_cells=np.array([[0, -1]]),
        slot_stacks=np.array([[0, -1]]), length=np.array([20]), height=np.array([box.height_m]))
    engine = SimpleNamespace(instance=instance, T=tables,
        _slot_xy=np.array([[cell.lcg_m, cell.tcg_m]]),
        _env_state=lambda state: (None, None, np.array([stack.base_z_m])))
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot=cell.id, container_id=box.id)])
    method = _engine.IncrementalEngine._heat_many
    if source_path := os.environ.get("D130_HEAT_ENGINE_SOURCE"):
        tree = ast.parse(Path(source_path).read_text())
        function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_heat_many")
        namespace = dict(_engine.__dict__)
        exec(compile(ast.Module(body=[function], type_ignores=[]), source_path, "exec",
                     flags=__future__.annotations.compiler_flag), namespace)
        method = namespace["_heat_many"]
    return instance, ship, tank, source, engine, method


def assert_parity(case, expected):
    instance, ship, _, _, engine, method = case
    relevant = [v for v in special_violations(instance, ship, call_index=0)
                if v.rule_id == "expert.e8.away_from_heat" or (
                    v.rule_id == "checker.missing_vessel_data" and "expert.e8.away_from_heat" in v.message)]
    scalar = not relevant
    assert scalar is expected
    assert method(engine, None, np.array([0]), np.array([0])).tolist() == [scalar]


def test_explicit_link_with_different_source_id_matches_checker(heat_case):
    instance, _, tank, source, _, _ = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"id": f"heat-{tank.id}", "tank_id": tank.id})]
    assert_parity(heat_case, True)


def test_legacy_same_id_matches_checker(heat_case):
    assert_parity(heat_case, True)


@pytest.mark.parametrize("tank_id", ["UNKNOWN-TANK", "", "WB-P"])
def test_explicit_wrong_link_never_falls_back_to_source_id(heat_case, tank_id):
    instance, _, _, source, _, _ = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"tank_id": tank_id})]
    assert_parity(heat_case, False)


@pytest.mark.parametrize("kind", ["engine_room", "funnel", "other"])
def test_non_tank_id_collision_never_represents_heated_tank(heat_case, kind):
    instance, _, _, source, _, _ = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"kind": kind, "tank_id": None})]
    assert_parity(heat_case, False)


def test_missing_heat_geometry_fails_closed(heat_case):
    heat_case[0].vessel.heat_sources = []
    assert_parity(heat_case, False)


def test_prefix_without_explicit_link_fails_closed(heat_case):
    instance, _, tank, source, _, _ = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"id": f"heat-{tank.id}", "tank_id": None})]
    assert_parity(heat_case, False)


def test_unrepresented_second_heated_tank_fails_closed(heat_case):
    instance, _, tank, _, _, _ = heat_case
    instance.vessel.tanks.append(tank.model_copy(update={"id": "UNREPRESENTED-FUEL"}))
    assert_parity(heat_case, False)


def test_link_fix_does_not_drop_nearby_engine_heat(heat_case):
    instance, _, tank, source, _, _ = heat_case
    instance.vessel.heat_sources = [source.model_copy(update={"id": f"heat-{tank.id}", "tank_id": tank.id}),
        HeatSource(id="near-engine", kind="engine_room", x_min_m=46, x_max_m=47,
                   y_min_m=-2, y_max_m=0, z_min_m=2, z_max_m=3)]
    assert_parity(heat_case, False)
