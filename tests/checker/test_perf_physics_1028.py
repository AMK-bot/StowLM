"""Content invalidation and bitwise equivalence for immutable physics reuse."""
import numpy as np
import pytest

from stowage.checker.perf import without_reuse
from stowage.checker import stability_reuse as reuse
from stowage.checker.stability_tables import _basis, _basis_reference, OutsideTable
from stowage.checker.stability_liquid_tables import _stencil, _stencil_reference


def identical(first, second):
    assert len(first) == len(second)
    for a, b in zip(first, second, strict=True):
        aa, bb = np.asarray(a), np.asarray(b)
        assert aa.dtype == bb.dtype
        assert aa.shape == bb.shape
        assert aa.tobytes() == bb.tobytes()


@pytest.mark.parametrize('query,reference', [(_basis, _basis_reference), (_stencil, _stencil_reference)])
def test_stencils_match_original_bitwise_and_isolate_returned_arrays(query, reference):
    axis = np.array([-2., -.3, 0., .13, 1.1, 2., 4.5])
    reuse._axis_query.cache_clear()
    for value in [*axis, -0., 0., .1, np.float64(.8), np.float32(.4)]:
        expected = reference(axis, value)
        identical(query(axis, value), expected)
        identical(query(axis, value), expected)
        with without_reuse():
            identical(query(axis, value), expected)
        result = query(axis, value)
        result[-1][:] = 1234.
        identical(query(axis, value), expected)
    assert reuse._axis_query.cache_info().hits > 0


@pytest.mark.parametrize('query,reference', [(_basis, _basis_reference), (_stencil, _stencil_reference)])
def test_stencils_invalidate_all_current_axis_bits(query, reference):
    axis = np.linspace(-1., 1., 12)
    query(axis, .13)
    axis[6] = np.nextafter(axis[6], 1.)
    identical(query(axis, .13), reference(axis, .13))
    axis[7] += .02
    identical(query(axis, .13), reference(axis, .13))
    with pytest.raises((ValueError, OutsideTable)):
        query(axis, 2.)


@pytest.mark.parametrize('name', ['basis', 'stencil'])
@pytest.mark.parametrize('change', ['searchsorted', 'code'])
def test_runtime_stencil_dependencies_bypass_old_results(monkeypatch, name, change):
    from stowage.checker import stability_tables, stability_liquid_tables
    module = stability_tables if name == 'basis' else stability_liquid_tables
    query, reference = getattr(module, '_'+name), getattr(module, '_'+name+'_reference')
    axis = np.arange(10, dtype=float)
    old = query(axis, 4.2)
    if change == 'searchsorted':
        original = np.searchsorted
        monkeypatch.setattr(np, 'searchsorted', lambda *args, **kwargs: original(*args, **kwargs)+1)
    else:
        if name == 'basis':
            def replacement(axis, value):
                return 0, np.ones(4)*.25
        else:
            def replacement(axis, value):
                return np.arange(4), np.ones(4)*.25
        monkeypatch.setattr(reference, '__code__', replacement.__code__)
    expected = reference(axis, 4.2)
    identical(query(axis, 4.2), expected)
    assert np.asarray(old[0]).tobytes() != np.asarray(expected[0]).tobytes()


@pytest.mark.parametrize('query,reference', [(_basis, _basis_reference), (_stencil, _stencil_reference)])
def test_custom_axis_and_scalar_types_keep_original_semantics(query, reference):
    class Axis(np.ndarray):
        def __getitem__(self, key):
            value = super().__getitem__(key)
            return np.asarray(value)+.125 if isinstance(key, (slice, np.ndarray)) else value
    axis = np.arange(10, dtype=float)
    query(axis, 4.2)
    custom = axis.view(Axis)
    before = reuse._axis_query.cache_info()
    identical(query(custom, 4.2), reference(custom, 4.2))
    class Scalar(float):
        pass
    identical(query(axis, Scalar(4.2)), reference(axis, Scalar(4.2)))
    assert reuse._axis_query.cache_info() == before


@pytest.mark.parametrize('query,reference', [(_basis, _basis_reference), (_stencil, _stencil_reference)])
@pytest.mark.parametrize('dtype', [np.float16, np.float32, np.longdouble])
def test_other_axis_precisions_retain_original_scalar_promotion(query, reference, dtype):
    axis = np.array([-2., -.3, 0., .13, 1.1, 2., 4.5], dtype=dtype)
    before = reuse._axis_query.cache_info()
    for value in (.1, 4, -0., np.float32(.1), np.float64(.1)):
        identical(query(axis, value), reference(axis, value))
    assert reuse._axis_query.cache_info() == before


@pytest.mark.parametrize('query,reference', [(_basis, _basis_reference), (_stencil, _stencil_reference)])
def test_python_and_numpy_scalar_types_have_distinct_keys(query, reference):
    reuse._axis_query.cache_clear()
    axis = np.array([-2., -.3, 0., .13, 1.1, 2., 4.5], dtype=np.float64)
    for count, value in enumerate((.1, np.float64(.1), 4, np.int64(4), True), 1):
        identical(query(axis, value), reference(axis, value))
        assert reuse._axis_query.cache_info().misses == count
    # Retain signed zero independently for both native and NumPy scalars.
    for value in (0., -0., np.float64(0.), np.float64(-0.)):
        identical(query(axis, value), reference(axis, value))
    assert reuse._axis_query.cache_info().misses == 9
