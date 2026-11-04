"""Public KCS/DTC geometry versus independent published hydrostatics.

No target is fitted. L5 supplies a 2% displacement and max(1%,5cm) KM/GM
comparison bar; Cb, Cm, LCB and bare wetted surface have explicit supplemental
tolerances. KM is derived from published model KG+GM by geometric similarity.
"""
import math
import runpy

import pytest

from stowage.params import repo_root
from stowage.vessel.hydrostatics import HullGeometry


@pytest.fixture(scope="module")
def kcs_module():
    return runpy.run_path(str(repo_root() / "experiments/hydrostatics/validate_kcs.py"))


@pytest.fixture(scope="module")
def kcs_report(kcs_module):
    return kcs_module["generate_report"](include_kn=False)


@pytest.mark.parametrize("quantity", ["volume","cb","cm","lcb","km_model2","km_model3","wetted"])
def test_kcs_independent_reference_comparison(kcs_report, quantity):
    check = kcs_report["checks"][quantity]
    assert check["passes"], check


def test_kcs_published_reference_provenance(kcs_module):
    text=(repo_root()/"data/raw/kcs/kcs_gc.html").read_text()
    for fact in ("52030", "9424", "0.6505", "0.9849", "-1.48", "0.378", "0.0158"):
        assert fact in text
    r=kcs_module["REFERENCE"]
    assert r["lcb_m"]==pytest.approx(111.596)
    assert r["km_model2_m"]==pytest.approx((.378+.0158)*230/6.0702)
    assert r["km_model3_m"]==pytest.approx((.168+.007)*230/2.7)


@pytest.mark.parametrize("ship", ["feeder_500","feeder_800","feeder_1700"])
def test_affine_scaled_fleet_preserves_kcs_coefficients(kcs_report,ship):
    row=next(r for r in kcs_report["affine_fleet"] if r["ship"]==ship)
    # The actual integrated source Cb is ~.6494; the rounded published .6505
    # is checked independently within 2%, and must not be fitted here.
    for coefficient in ("cb","cm","cp","cwp"):
        assert row["computed"][coefficient]==pytest.approx(
            kcs_report["fine"][coefficient],rel=1e-9,abs=1e-11)


def test_kcs_discretisation_and_small_angle_kn(kcs_module):
    p=kcs_module["reference_params"]()
    coarse=HullGeometry.from_params(p,nx=48,nz=64)
    fine=HullGeometry.from_params(p,nx=96,nz=128)
    a=coarse.submerged(10.8,10.8,0)
    b=fine.submerged(10.8,10.8,0)
    assert a.volume_m3==pytest.approx(b.volume_m3,rel=1e-5)
    km=b.vcb_m+b.transverse_inertia_m4/b.volume_m3
    assert fine.kn(0,b.displacement_kg,lcg_m=b.lcb_m)==pytest.approx(0,abs=1e-8)
    angle=math.radians(.5)
    assert fine.kn(angle,b.displacement_kg,lcg_m=b.lcb_m)==pytest.approx(
        km*math.sin(angle),rel=.01,abs=.001)
    for degrees in (30,60):
        angle=math.radians(degrees)
        assert coarse.kn(angle,b.displacement_kg,lcg_m=b.lcb_m)==pytest.approx(
            fine.kn(angle,b.displacement_kg,lcg_m=b.lcb_m),abs=.005)


def test_kcs_kn_curve_matches_independent_scalar_roots(kcs_module):
    from stowage.vessel.hydrostatics import clear_hydrostatic_caches

    g=HullGeometry.from_params(kcs_module["reference_params"](),nx=96,nz=128)
    r=g.submerged(10.8,10.8,0)
    angles=[math.radians(deg) for deg in (0,2,10,30,60)]
    clear_hydrostatic_caches()
    scalar=[g.kn(angle,r.displacement_kg,lcg_m=r.lcb_m) for angle in angles]
    clear_hydrostatic_caches()
    curve=g.kn_curve(angles,r.displacement_kg,lcg_m=r.lcb_m)
    assert curve==pytest.approx(scalar,abs=1e-7)
    curve[0]=999
    assert g.kn_curve(angles,r.displacement_kg,lcg_m=r.lcb_m)[0]==pytest.approx(scalar[0],abs=1e-7)


@pytest.fixture(scope="module")
def dtc_report(tmp_path_factory):
    from stowage.vessel.hull_dtc import write_dtc_offsets

    # Rebuild independently from the checksum-pinned public mesh; the checked-in
    # preprocessing output is a reviewable artifact, not a trusted reference.
    output=tmp_path_factory.mktemp("dtc-independent")/"offsets.json"
    write_dtc_offsets(repo_root()/"data/raw/dtc/DTC-scaled.stl.gz",output,station_count=121)
    module=runpy.run_path(str(repo_root()/"experiments/hydrostatics/validate_dtc.py"))
    return module["generate_report"](output)


@pytest.mark.parametrize("draft",[12.0,14.0,14.5])
def test_dtc_independent_published_volume_km_and_surface(dtc_report,draft):
    row=next(r for r in dtc_report["rows"] if r["draught_m"]==draft)
    for quantity,check in row["checks"].items():
        assert check["passes"],(draft,quantity,check)


def test_dtc_slice_refinement_and_source_provenance(dtc_report):
    source=(repo_root()/"data/raw/dtc/el-moctar-shigunov-zorn-2012.txt").read_text()
    assert "59.407" in source
    assert "136617.5" in source
    assert "165868" in source
    assert dtc_report["source_station_count_requested"]==121
    for row in dtc_report["source_slice_convergence"]:
        assert row["volume_change_percent"]<0.1
        assert row["absolute_change"]["km_m"]<0.01
