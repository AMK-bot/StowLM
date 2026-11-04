"""Independent analytical regressions for the Claude physics review."""
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import visibility
from stowage.model import Placement
from stowage.model.fixtures import build_instance


class _EvenWaterplane:
    def waterplane_normal(self, *_):
        return np.array([0., 0., 1.])


def test_conning_eye_setback_increases_forward_blind_distance(monkeypatch):
    instance = build_instance()
    instance.vessel.fixed_loads = []
    instance.vessel.bridge.x_m = 0.
    instance.vessel.bridge.eye_z_m = 25.
    instance.vessel.bridge.bow_x_m = 128.
    box = instance.containers[1]
    box.height_m, box.oog = 10., None
    placement = Placement(slot='0010182', container_id=box.id)
    instance.onboard.placements = [placement]
    cell = next(c for c in instance.vessel.cells if c.id == placement.slot)
    cell.lcg_m, cell.tcg_m = 60., 0.
    monkeypatch.setattr(visibility, '_loading_condition', lambda *_: SimpleNamespace(
        t_aft_m=5., t_fwd_m=5., heel_rad=0., geometry=_EvenWaterplane()))

    report = visibility.check_visibility(instance, instance.onboard, call_index=0)
    assert report.ran
    # SOLAS V/22.1.1 (IRS Pt5 Ch31 §3.1.4.3, printed p6 / PDF p2439)
    # requires the view "from the conning position". Synthetic eye setback
    # is 2m aft of the declared bridge front. Sea-relative eye/roof heights
    # are 20m/14m; the forward roof corner is x=63.029m and bow x=128m.
    setback = 2.
    expected = 20.*(63.029+setback)/6.-(128.+setback)
    bridge_front_result = 20.*63.029/6.-128.
    assert report.metrics['blind_distance_m'] == pytest.approx(expected)
    assert report.metrics['blind_distance_m'] > bridge_front_result


def test_cover_obstruction_spans_every_twenty_foot_support_stack(monkeypatch):
    """L9: one cover is represented by its separate physical cell rectangles."""
    instance = build_instance()
    instance.vessel.fixed_loads = []
    instance.onboard.placements = []
    instance.vessel.bridge.x_m = 0.
    instance.vessel.bridge.eye_z_m = 25.
    instance.vessel.bridge.bow_x_m = 128.
    monkeypatch.setattr(visibility, '_loading_condition', lambda *_: SimpleNamespace(
        t_aft_m=5., t_fwd_m=5., heel_rad=0., geometry=_EvenWaterplane()))
    cells = {c.id: c for c in instance.vessel.cells}
    stacks = {s.id: s for s in instance.vessel.stacks}
    for stack in stacks.values():
        stack.base_z_m = 5.
    # Both 20-foot support columns belong to HC-02-1. Move only its second
    # rectangle; a single 6.058m obstacle for the whole cover would miss it.
    for sid, x in [('00101D', 60.), ('00301D', 100.)]:
        stacks[sid].base_z_m = 19.
        for cid in stacks[sid].cell_ids:
            cells[cid].lcg_m, cells[cid].tcg_m = x, 0.
    far = visibility.check_visibility(instance, instance.onboard, call_index=0)
    for cid in stacks['00301D'].cell_ids:
        cells[cid].lcg_m = 60.
    near = visibility.check_visibility(instance, instance.onboard, call_index=0)
    assert far.ran and near.ran
    assert far.metrics['blind_distance_m']-near.metrics['blind_distance_m'] == pytest.approx(
        20.*(100.-60.)/6.)
