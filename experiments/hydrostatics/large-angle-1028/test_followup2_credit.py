"""Small analytic geometry/credit-contract tests; no floating-condition jobs.

Created for the parent to run serially after its campaign. This author did not
run these tests. The box/prism expectations are independent closed forms.
"""
from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from followup2_binding import credit_geometry
from stowage.checker.stability_criteria import _coaming_depth
from stowage.vessel.hydrostatics import HullGeometry


def native_box_with_coaming():
    shell=np.array([[-10.,0.],[10.,0.],[10.,10.],[-10.,10.]])
    coaming=np.array([[-8.,10.],[8.,10.],[8.,10.6],[-8.,10.6]])
    geometry=HullGeometry.__new__(HullGeometry)
    geometry.__dict__.update(lpp_m=100.,beam_m=20.,depth_m=10.,max_z_m=10.6,
        design_draught_m=7.,density_kg_m3=1025.,x_m=np.array([12.5,50.,87.5]),
        integration_weights_m=np.array([25.,50.,25.]),
        sections=[[shell.copy()],[shell.copy(),coaming.copy()],[shell.copy()]],
        openings=[dict(id="hold",kind="hold_ventilator",x_m=50.,y_m=8.,z_m=14.5,
                       closure="not_weathertight_closable",progressive_flooding=True),
                  dict(id="er",kind="engine_room_air_inlet",x_m=10.,y_m=1.,z_m=16.,
                       closure="not_weathertight_closable",progressive_flooding=True)])
    geometry._finish_geometry()
    physical=dict(envelope=dict(hatch_coamings=[dict(id="hatch",x_min_m=25.,x_max_m=75.,
        y_min_m=-8.,y_max_m=8.,z_min_m=10.,z_max_m=10.6)]))
    return geometry,physical


@pytest.mark.parametrize("coaming,cover",[(0,0),(1,0),(0,1),(1,1)])
def test_factor_cells_have_independent_closed_form_volumes(coaming,cover):
    native,_=native_box_with_coaming()
    probe=credit_geometry(native,coaming,cover,None)
    # Shell100*20*10, each candidate volume50*16*.6. Fully immerse to
    # measure geometry credit independently of changing trim/immersion.
    assert probe.submerged(20.,20.).volume_m3==pytest.approx(20000.+480.*(coaming+cover),abs=1e-8)
    assert np.array_equal(probe.x_m,native.x_m)
    assert np.array_equal(probe.integration_weights_m,native.integration_weights_m)
    for a,b in zip(probe.sections,native.sections):
        assert np.array_equal(a[0],b[0])


def test_cover_only_stays_above_the_physical_coaming_top():
    native,_=native_box_with_coaming()
    probe=credit_geometry(native,0,1,None)
    assert len(probe.sections[1])==2
    cover=probe.sections[1][1]
    assert float(cover[:,1].min())==pytest.approx(10.6)
    assert float(cover[:,1].max())==pytest.approx(11.2)
    assert probe.depth_m==native.depth_m
    assert probe._geometry_digest!=native._geometry_digest


def test_physical_coaming_dprime_is_retained_for_every_credit_cell():
    native,physical=native_box_with_coaming()
    original=deepcopy(physical)
    for coaming,cover in ((0,0),(1,0),(0,1),(1,1)):
        probe=credit_geometry(native,coaming,cover,None)
        # h=.6, b=16, BD=20, sum(lH)=50, L=100 => D'=10.36.
        assert _coaming_depth(native,physical)==pytest.approx(10.36)
        assert _coaming_depth(probe,physical)==pytest.approx(10.36)
        assert physical==original
    assert float(native.sections[1][1][:,1].max())==pytest.approx(10.6)


def test_vent_probe_changes_only_hold_height_and_preserves_native_inventory():
    native,_=native_box_with_coaming()
    original=deepcopy(native.openings)
    probe=credit_geometry(native,1,0,7.5)
    assert probe.openings[0]["z_m"]==17.5
    assert probe.openings[1]==original[1]
    for key,value in original[0].items():
        if key!="z_m": assert probe.openings[0][key]==value
    assert native.openings==original
    probe.openings[0]["z_m"]=99.
    assert native.openings==original
