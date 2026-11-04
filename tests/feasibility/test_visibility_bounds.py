"""Focused algebra/content regressions for D55's outward roof-plane bound."""
from pathlib import Path
import gzip
from types import SimpleNamespace
import math

import numpy as np
import pytest

from stowage.feasibility.visibility_bounds import compute_visibility_caps
from stowage.model import Instance
from stowage.vessel.hydrostatics import HullGeometry


@pytest.fixture
def frozen_small():
    path = Path(__file__).with_name("data") / "d55" / "small-100100015-2a1913beea-instance.json.gz"
    return Instance.model_validate_json(gzip.decompress(path.read_bytes()))


def test_d55_content_cache_is_slice_and_mutation_sensitive(frozen_small):
    first = compute_visibility_caps(frozen_small)
    assert compute_visibility_caps(frozen_small) is first
    changed = frozen_small.model_copy(deep=True)
    changed.vessel.bridge.eye_z_m += .5
    second = compute_visibility_caps(changed)
    assert second.cache_key != first.cache_key
    assert second.top_by_stack["00101D"] > first.top_by_stack["00101D"]
    changed.vessel.stacks = [s for s in changed.vessel.stacks if s.id != "00101D"]
    third = compute_visibility_caps(changed)
    assert third.cache_key != second.cache_key
    assert "00101D" not in third.top_by_stack
    with pytest.raises(TypeError):
        first.top_by_stack["00101D"] = 100.


def test_d55_frozen_roofs_exceed_outer_manual_caps(frozen_small):
    result = compute_visibility_caps(frozen_small)
    assert result.top_by_stack["00101D"] < 24.270
    assert result.top_by_stack["00301D"] < 24.270
    assert result.top_by_stack["00103D"] < 24.575
    assert result.top_by_stack["00303D"] < 24.575
    assert math.isinf(result.top_by_stack["00101H"])


def test_d55_outer_bound_dominates_exact_waterplane_sight_planes(frozen_small):
    """Seeded geometry sweep uses production axes, including both list signs."""
    v = frozen_small.vessel
    hull, bridge = v.hull, v.bridge
    result = compute_visibility_caps(frozen_small)
    rng = np.random.default_rng(55004)
    geometry = SimpleNamespace(lpp_m=hull.lpp_m)
    cell_by_id = {c.id:c for c in v.cells}
    eye = np.array([bridge.x_m,0.,bridge.eye_z_m])
    limit = min(2*hull.loa_m,500.)
    checked = 0
    for _ in range(100):
        trim = rng.uniform(hull.min_trim_m,hull.max_trim_m)
        aft = rng.uniform(hull.min_draught_fwd_m+trim,hull.max_draught_m)
        fore = aft-trim
        # Only retain states satisfying both end-draught limits.
        if fore > hull.max_draught_m:
            continue
        heel = rng.uniform(-hull.max_list_rad,hull.max_list_rad)
        n, _, _ = HullGeometry._axes(geometry,aft,fore,heel)
        along = np.array([1.,0.,0.])-n*n[0]
        along /= np.linalg.norm(along)
        h = n@eye-n[2]*aft
        bow_z = aft+(fore-aft)*bridge.bow_x_m/hull.lpp_m
        bow = along@(np.array([bridge.bow_x_m,0.,bow_z])-eye)
        boundary = bow+limit
        for stack in v.stacks:
            cap = result.top_by_stack[stack.id]
            if not math.isfinite(cap):
                continue
            for cid in stack.cell_ids:
                c = cell_by_id[cid]
                # Solve the checker's exact linear roof-plane equation for z.
                point = np.array([c.lcg_m,c.tcg_m,0.])
                constant = h*(along@(point-eye))+boundary*(n@(point-eye))
                coefficient = h*along[2]+boundary*n[2]
                exact_top = -constant/coefficient
                assert exact_top <= cap
                checked += 1
    assert checked > 1000


def test_d55_uncertified_wedge_and_long_footprint_disable_cap(frozen_small):
    changed = frozen_small.model_copy(deep=True)
    # The support point cannot witness the roof of its 40' box if its paired
    # support is farther away than that roof's whole length.
    for cell in changed.vessel.cells:
        if cell.stack_id == "00301D":
            cell.lcg_m -= 25.
    result = compute_visibility_caps(changed)
    assert math.isinf(result.top_by_stack["00101D"])
    assert math.isinf(result.top_by_stack["00301D"])
    changed = frozen_small.model_copy(deep=True)
    for cell in changed.vessel.cells:
        if cell.stack_id == "00101D":
            cell.tcg_m = 100.
    assert math.isinf(compute_visibility_caps(changed).top_by_stack["00101D"])


def test_d55_missing_manual_endpoint_disables_cap(frozen_small):
    frozen_small.vessel.hull.min_trim_m = None
    result = compute_visibility_caps(frozen_small)
    assert all(math.isinf(cap) for cap in result.top_by_stack.values())
    assert "unavailable" in result.diagnostics[0]
