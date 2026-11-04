"""Analytic controls for necessary trim/list enclosures; no sampled fits."""
from fractions import Fraction as F
import importlib.util
from itertools import product
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('c4_necessary_trim_test', HERE/'trim_bound.py')
trim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trim)


def test_fractional_centroid_extrema_enclose_independent_integer_volume_grid():
    xs,lower,upper = [0,3,7],[1,0,1],[3,4,2]
    lo,hi = trim.centroid_range(xs,lower,upper,4,7)
    values = []
    for vs in product(range(1,4),range(5),range(1,3)):
        if 4 <= sum(vs) <= 7:
            values.append(sum(F(x)*v for x,v in zip(xs,vs))/sum(vs))
    assert lo <= min(values) <= max(values) <= hi
    assert trim.centroid_range([0,10],[0,0],[2,2],2,2) == (0,10)


def test_bernstein_controls_enclose_negative_lagrange_overshoot():
    # The fourth nodal basis is x(x-1)(x-2)/6. Its value at3/2 is-1/16,
    # despite all four interpolation-node values being0 or1.
    matrix = trim.basis_bernstein([0,1,2,3],1,2)
    coefficients = [row[3] for row in matrix]
    assert min(coefficients) <= F(-1,16) <= max(coefficients)
    assert min(coefficients) < 0
    assert all(sum(row) == 1 for row in matrix)


def test_tensor_transform_matches_known_polynomial_range():
    nodes = list(map(F,(0,1,2,3)))
    values = [[[x*x+y*y*y-z for z in nodes] for y in nodes] for x in nodes]
    matrices = [trim.basis_bernstein(nodes,1,2),trim.basis_bernstein(nodes,0,1),
                trim.basis_bernstein(nodes,1,2)]
    coefficients = trim.tensor_bernstein(values,matrices)
    # Over this box: x² in[1,4],y³ in[0,1],z in[1,2].
    assert min(coefficients) <= -1
    assert max(coefficients) >= 4


def test_table_centroid_enclosure_covers_signed_weight_overshoot():
    import numpy as np
    values = np.zeros((4,4,4,4))
    for i,j,k in product(range(4),repeat=3):
        values[i,j,k] = [100,100*(5+(i == 3)),0,100]
    table = SimpleNamespace(grid={name:np.arange(4,dtype=float) for name in
        ('immersion_m','trim_m','heel_rad')},values=values,lpp_m=10.,depth_m=4.)
    result = trim.table_enclosure(table,immersion_interval=(1,2),trim_interval=(0,0),
        list_upper=0,volume_lower=100,volume_upper=100)
    assert result['x'][0] < 5
    assert result['x'][0] <= F(79,16) <= result['x'][1]
    assert result['blocks'] == 1


def test_rectangular_hull_control_has_expected_centroid_image():
    polygon = [(-2.,0.),(2.,0.),(2.,4.),(-2.,4.)]
    geometry = SimpleNamespace(lpp_m=10.,depth_m=4.,x_m=[0.,10.],
        integration_weights_m=[5.,5.],sections=[[polygon],[polygon]])
    result = trim.direct_enclosure(geometry,draft_upper=2,forward_draft_lower=1,
        trim_lower=F(-1,10),trim_upper=F(1,10),list_upper=F(1,20),
        volume_lower=40,volume_upper=80)
    assert result['x'][0] <= 5 <= result['x'][1]
    assert result['y'][0] <= 0 <= result['y'][1]
    assert result['z'][0] <= F(3,4) <= result['z'][1]


def test_table_work_limit_refuses_every_box_instead_of_truncating():
    import numpy as np
    import pytest
    table = SimpleNamespace(grid={name:np.arange(4,dtype=float) for name in
        ('immersion_m','trim_m','heel_rad')},values=np.zeros((4,4,4,4)),lpp_m=10.,depth_m=4.)
    with pytest.raises(trim.EnvelopeWorkLimit,match='exceed explicit work limit'):
        trim.table_enclosure(table,immersion_interval=(1,2),trim_interval=(0,0),
            list_upper=0,volume_lower=1,volume_upper=2,max_blocks=0)


def test_uncertified_float_envelope_cannot_be_used_as_global_bound():
    import pytest
    with pytest.raises(ValueError,match='Uncertified trim envelope'):
        trim.attach_moment_rows(None,SimpleNamespace(id='control'),None,[],
            envelope_result={'instance_id':'control','certificate_eligible':False})
