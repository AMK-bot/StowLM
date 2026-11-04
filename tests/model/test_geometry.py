"""Structural model invariants; operational policy remains the checker's job."""

import pytest
from pydantic import ValidationError

from stowage.conventions import make_container_id
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import Placement, ShipState
from stowage.model.vessel import Cell, HatchCover, Stack, Vessel
from stowage.model.yard import YardStack, YardState


def geometry() -> dict:
    return {
        "id": "slice",
        "name": "Synthetic slice",
        "nominal_teu": 1,
        "cells": [Cell(id="0010102", stack_id="00101H", lcg_m=5, tcg_m=-1, vcg_base_m=1)],
        "stacks": [Stack(id="00101H", bay=1, row=1, on_deck=False, cell_ids=["0010102"],
                         max_mass_kg=30000, max_height_m=3, base_z_m=1, hatch_cover_id="HC-01")],
        "hatch_covers": [HatchCover(id="HC-01", bays=[1], hold_stack_ids=["00101H"],
                                    deck_stack_ids=[], mass_kg=1000, lcg_m=5, tcg_m=-1, vcg_m=4)],
    }


def test_geometry_reciprocal_membership():
    payload = geometry()
    assert Vessel(**payload).cells[0].stack_id == "00101H"
    payload["stacks"][0].cell_ids = []
    with pytest.raises(ValidationError, match="missing from stack"):
        Vessel(**payload)


def test_unknown_cover_reference():
    payload = geometry()
    payload["stacks"][0].hatch_cover_id = "HC-missing"
    with pytest.raises(ValidationError, match="unknown hatch cover"):
        Vessel(**payload)


def test_ship_rejects_overlapping_20_and_40_slots():
    with pytest.raises(ValidationError, match="overlap physical cells"):
        ShipState(vessel_id="slice", placements=[
            Placement(slot="0010182", container_id=make_container_id("QZXU", 1)),
            Placement(slot="0020182", container_id=make_container_id("QZXU", 2)),
        ])


def test_yard_preserves_stack_order_and_sorts_catalog():
    lower, upper = (make_container_id("QZXU", n) for n in (2, 1))
    state = YardState(yard_id="yard", stacks=[
        YardStack(key="B-001-01", length_ft=20, boxes=[lower, upper]),
        YardStack(key="A-001-01", length_ft=20, boxes=[make_container_id("QZXU", 3)]),
    ])
    assert [stack.key for stack in state.stacks] == ["A-001-01", "B-001-01"]
    assert state.stacks[1].boxes == [lower, upper]


def test_yard_rejects_overlapping_footprints():
    with pytest.raises(ValidationError, match="overlaps another stack footprint"):
        YardState(yard_id="yard", stacks=[
            YardStack(key="A-001-01", length_ft=40, boxes=[make_container_id("QZXU", 1)]),
            YardStack(key="A-002-01", length_ft=20, boxes=[make_container_id("QZXU", 2)]),
        ])


def test_rotation_does_not_reorder_calls():
    with pytest.raises(ValidationError, match="call indexes"):
        Rotation(service="synthetic", calls=[PortCall(locode="THLCH", index=1)])


def test_tank_table_requires_capacity_endpoint():
    from stowage.model.vessel import Tank

    with pytest.raises(ValidationError, match="zero to capacity_m3"):
        Tank(id="ballast", kind="ballast", content_density_kg_m3=1025,
             capacity_m3=100, table=[], x_aft_m=0, x_fore_m=10)


def test_cell_address_matches_its_stack():
    with pytest.raises(ValidationError, match="stack_id .* disagree"):
        Cell(id="0010102", stack_id="00301H", lcg_m=5, tcg_m=-1, vcg_base_m=1)


def test_cover_cannot_rest_on_another_open_cover():
    with pytest.raises(ValidationError, match="cannot rest on an open cover"):
        ShipState(vessel_id="slice", covers_off={"HC-01": "HC-02", "HC-02": "quay"})
