"""Independent analytic regression for D114's observed zeroed BLAS outputs.

No saved corpus, TRAIN or TEST instance is used. A rectangular synthetic prism
has exactly60m3 underwater volume at even-keel draught1.5m. The failure injector
models the observed backend symptom (zeroed batched contraction entries) while
all physical polygon inputs stay unchanged. The public scalar integration is a
second independent reference for non-even-keel cases.
"""
import numpy as np
import pytest
from stowage.vessel.hydrostatics import HullGeometry


class ZeroedBatchedProducts(np.ndarray):
    """Deterministic injection of the observed backend's incorrect zero entries."""
    def __matmul__(self, other):
        answer = np.asarray(self) @ other
        if self.ndim == 3 and self.shape[-1] == 2:
            answer = np.array(answer, copy=True)
            answer[0, 2:] = 0.0
        return answer


def rectangular_prism():
    geometry = object.__new__(HullGeometry)
    geometry.lpp_m = 10.0
    geometry.beam_m = 4.0
    geometry.depth_m = 3.0
    geometry.design_draught_m = 1.5
    geometry.max_z_m = 3.0
    geometry.density_kg_m3 = 1000.0
    geometry.x_m = np.array([0.0, 5.0, 10.0])
    geometry.integration_weights_m = np.array([2.5, 5.0, 2.5])
    polygon = np.array([[-2.0, 0.0], [2.0, 0.0], [2.0, 3.0], [-2.0, 3.0]])
    geometry.sections = [[polygon.copy()] for _ in geometry.x_m]
    geometry._finish_geometry()
    return geometry


@pytest.mark.parametrize("aft,forward,heel", [(1.5, 1.5, 0.0), (1.0, 2.0, 0.0), (1.5, 1.5, 0.03)])
def test_solver_polygon_integration_survives_zeroed_batched_backend(aft, forward, heel):
    geometry = rectangular_prism()
    reference = geometry.submerged(aft, forward, heel, 1000.0)
    geometry._solver_submerged(aft, forward, heel, 1000.0)  # Build immutable source arrays.
    polygons, following, stations, weights = geometry._solver_arrays
    original_bytes = polygons.tobytes()
    geometry._solver_arrays = (polygons.view(ZeroedBatchedProducts), following, stations, weights)
    actual = geometry._solver_submerged(aft, forward, heel, 1000.0)
    assert geometry._solver_arrays[0].tobytes() == original_bytes
    assert actual.volume_m3 == pytest.approx(reference.volume_m3, rel=0.0, abs=1e-12)
    assert actual.centroid_m == pytest.approx(reference.centroid_m, rel=0.0, abs=1e-12)
    if aft == forward == 1.5 and heel == 0.0:
        assert actual.volume_m3 == 60.0
        assert actual.centroid_m == (5.0, 0.0, 0.75)


def test_explicit_contraction_preserves_strided_polygon_inputs():
    geometry = rectangular_prism()
    reference = geometry.submerged(1.5, 1.5, 0.0, 1000.0)
    geometry._solver_submerged(1.5, 1.5, 0.0, 1000.0)
    polygons, following, stations, weights = geometry._solver_arrays
    backing = np.zeros((*polygons.shape[:-1], 4), dtype=float)
    backing[..., ::2] = polygons
    strided = backing[..., ::2].view(ZeroedBatchedProducts)
    assert not strided.flags.c_contiguous
    geometry._solver_arrays = (strided, following, stations, weights)
    actual = geometry._solver_submerged(1.5, 1.5, 0.0, 1000.0)
    assert actual.volume_m3 == 60.0
    assert actual.centroid_m == reference.centroid_m
