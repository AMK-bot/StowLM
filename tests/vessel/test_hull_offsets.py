"""Pinned real offset reading and geometry-preserving numerical construction."""
import hashlib
import json

import numpy as np
import pytest

from stowage.params import repo_root
from stowage.vessel.hull_offsets import build_offset_geometry, read_offsets, wetted_surface_area_m2
from stowage.vessel.hydrostatics import HullGeometry


KCS_SHA='aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190'


def _box_params(tmp_path):
    path=tmp_path/'offsets.json'
    path.write_text(json.dumps(dict(schema='stowlm_offsets_json_v1',stations=[
        dict(x_m=x,half_section_yz_m=[[0,0],[2,0],[2,1],[2,3]]) for x in (-1,0,5,10,11)])))
    return dict(hull=dict(lpp_m=10,beam_m=4,design_draught_m=1,depth_m=3,water_density_kg_m3=1000),
        hull_form=dict(family='stowlm_offsets_v1',offsets_path=str(path),
            offsets_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),offsets_format='stowlm_offsets_json_v1',
            reference_lpp_m=10,reference_beam_m=4,reference_draught_m=1,reference_depth_m=3),
        envelope=dict(deck_z_m=3,hatch_coamings=[],openings=[]))


def test_checksum_is_verified_before_parsing(tmp_path):
    path=tmp_path/'bad.fix';path.write_text('this is not an offsets file')
    with pytest.raises(ValueError,match='checksum'):
        read_offsets(path,expected_sha256='0'*64,format='kcs_fix')


def test_kcs_fullscale_reader_retains_flat_bottom_and_overhangs():
    table=read_offsets('data/raw/kcs/kcs.fix',expected_sha256=KCS_SHA,format='kcs_fix')
    assert len(table.stations)==57
    assert table.stations[0].x_m==-6
    assert table.stations[-1].x_m==236.9
    middle=min(table.stations,key=lambda s:abs(s.x_m-115))
    assert np.max(middle.half_section_yz_m[:,0])==pytest.approx(16.1,abs=.01)
    assert np.count_nonzero(middle.half_section_yz_m[:,1]==0)>20
    assert not middle.half_section_yz_m.flags.writeable


def test_generic_offsets_preserve_extents_and_box_hydrostatics(tmp_path):
    p=_box_params(tmp_path)
    g=HullGeometry.from_params(p,nx=8,nz=8)
    s=g.submerged(1,1)
    assert s.volume_m3==pytest.approx(12*4)
    assert s.centroid_m==pytest.approx((5,0,.5))
    assert s.waterplane_area_m2==pytest.approx(48)
    assert g.buoyant_x_min_m==-1 and g.buoyant_x_max_m==11
    assert np.sum(g.integration_weights_m)==pytest.approx(12)
    assert wetted_surface_area_m2(g,1)==pytest.approx(48+2*12+2*4)


def test_affine_dimensions_scale_actual_sections(tmp_path):
    p=_box_params(tmp_path)
    p['hull'].update(lpp_m=20,beam_m=8,design_draught_m=2,depth_m=8)
    p['envelope']['deck_z_m']=8
    g=HullGeometry.from_params(p,nx=8,nz=8)
    s=g.submerged(2,2)
    assert s.volume_m3==pytest.approx(24*8*2)
    assert s.centroid_m==pytest.approx((10,0,1))
    assert np.max(g.sections[len(g.sections)//2][0][:,1])==pytest.approx(8)


def test_build_keeps_source_vertices_in_interpolated_contours(tmp_path):
    p=_box_params(tmp_path)
    attrs=build_offset_geometry(p,nx=8,nz=8)
    # Original flat-bottom corner and waterline kink survive a coarser request.
    for polys in attrs['sections']:
        assert any(np.allclose(v,[2,0]) for v in polys[0])
        assert any(np.allclose(v,[2,1]) for v in polys[0])


def test_kcs_csv_and_original_fix_are_identical():
    path=repo_root()/'data/raw/kcs/offsets_published.csv'
    if not path.exists():pytest.skip('derived public CSV not installed')
    csv_table=read_offsets(path,expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),format='station_waterline_csv_v1')
    fix_table=read_offsets('data/raw/kcs/kcs.fix',expected_sha256=KCS_SHA,format='kcs_fix')
    assert len(csv_table.stations)==len(fix_table.stations)
    for a,b in zip(csv_table.stations,fix_table.stations,strict=True):
        assert a.x_m==b.x_m
        np.testing.assert_array_equal(a.half_section_yz_m,b.half_section_yz_m)


def test_wetted_area_excludes_internal_zero_breadth_centreplane(tmp_path):
    p=_box_params(tmp_path)
    path=p['hull_form']['offsets_path']
    obj=json.loads(open(path).read())
    for station in obj['stations']:
        station['half_section_yz_m']=[[0,0],[0,1],[2,1],[2,3]]
    from pathlib import Path
    Path(path).write_text(json.dumps(obj))
    p['hull_form']['offsets_sha256']=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    g=HullGeometry.from_params(p,nx=8,nz=8)
    # The entire shell is a raised rectangular box. The doubled centreplane
    # below its bottom is a zero-volume contour trace, not exterior hull skin.
    assert wetted_surface_area_m2(g,2)==pytest.approx(48+24+8)


def test_kcs_coefficients_survive_anisotropic_piecewise_affine_scaling():
    f=dict(family='stowlm_offsets_v1',offsets_path='data/raw/kcs/kcs.fix',offsets_sha256=KCS_SHA,
           offsets_format='kcs_fix',reference_lpp_m=230,reference_beam_m=32.2,
           reference_draught_m=10.8,reference_depth_m=16)
    coefficients=[]
    for L,B,T,D in ((230,32.2,10.8,16),(107.8,19.2,7.06,8.25)):
        p=dict(hull=dict(lpp_m=L,beam_m=B,design_draught_m=T,depth_m=D,water_density_kg_m3=1025),
               hull_form=f,envelope=dict(deck_z_m=D,hatch_coamings=[],openings=[]))
        g=HullGeometry.from_params(p,nx=48,nz=64)
        coefficients.append(g.submerged(T,T).volume_m3/(L*B*T))
    assert coefficients[0]==pytest.approx(coefficients[1],abs=2e-13)
