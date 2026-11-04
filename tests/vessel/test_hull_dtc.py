"""Source-mesh slicing preserves section topology and provenance."""
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import ConvexHull

from stowage.params import repo_root
from stowage.vessel.hull_dtc import (DTC_SCALE,DTC_SOURCE_SHA256,dtc_reference_params,
    read_dtc_triangles,slice_dtc_half_contour,write_dtc_offsets)
from stowage.vessel.hydrostatics import HullGeometry,_moments


def box_triangles(bottom,top):
    points=np.array(list(product((0.,10.),(-2.,2.),(bottom,top))))
    return points[ConvexHull(points).simplices]


def test_disconnected_bulb_and_stem_add_no_fictitious_buoyancy():
    triangles=np.concatenate((box_triangles(0.,1.),box_triangles(3.,4.)))
    half=slice_dtc_half_contour(triangles,5.)
    full=np.vstack((half,half[::-1]*[-1.,1.]))
    moments=_moments(full)
    assert moments[0]==pytest.approx(8.)
    assert moments[1]==pytest.approx(0.)
    assert moments[2]/moments[0]==pytest.approx(2.)
    assert any(np.allclose(a,[0.,1.]) and np.allclose(b,[0.,3.]) for a,b in zip(half,half[1:]))


def test_source_checksum_rejected_before_decompression(tmp_path):
    source=tmp_path/'untrusted.gz'
    source.write_bytes(b'not a gzip file')
    with pytest.raises(ValueError,match='checksum'):
        read_dtc_triangles(source)


def test_actual_source_conversion_is_deterministic_and_geometry_consumable(tmp_path):
    source=repo_root()/'data/raw/dtc/DTC-scaled.stl.gz'
    destination=tmp_path/'offsets.json'
    first=write_dtc_offsets(source,destination,station_count=31)
    payload=destination.read_bytes()
    assert first==sha256(payload).hexdigest()
    assert write_dtc_offsets(source,destination,station_count=31)==first
    assert destination.read_bytes()==payload
    converted=json.loads(payload)
    assert converted['source_sha256']==DTC_SOURCE_SHA256
    assert converted['source_scale']==DTC_SCALE
    assert converted['station_count_requested']==31
    x=[r['x_m'] for r in converted['stations']]
    assert min(x)<0 and max(x)>355.
    assert 0. in x and 355. in x
    params=dtc_reference_params(destination)
    assert params['hull_form']['offsets_sha256']==first
    geometry=HullGeometry(params,nx=24,nz=32)
    result=geometry.submerged(14.5,14.5)
    # Published independent volume tolerance; no coefficient calibration.
    assert result.volume_m3==pytest.approx(173467.,rel=.02)
    assert result.waterplane_area_m2>0
