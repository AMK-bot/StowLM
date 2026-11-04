"""Scaling and missing-data guards; real checker comparison is parent-run."""
import math
from types import SimpleNamespace as NS

from physics import _safe, margin_scale


def test_containership_normalization_tracks_actual_form_factor_limit():
    report = NS(metrics={'area_0_flood_mrad': .24})
    # The .31 Code limit is recovered from the raw signed margin, rather
    # than frozen at the limit of another tank state or another voyage phase.
    scale = margin_scale('stability', 'containership_area_0_flood_mrad', -.07,
                         NS(), report)
    assert math.isclose(scale, .31)
    assert math.isclose(-.07/scale, .24/.31-1.)


def test_securing_ratios_keep_native_one_minus_ratio_scale():
    assert margin_scale('lashing', 'max_lashing_ratio', -.03, NS(), NS()) == 1.


def test_json_telemetry_never_contains_nonfinite_numbers():
    result = _safe({'bad': [math.inf, -math.inf, math.nan], 'good': 0.})
    assert result == {'bad': [None, None, None], 'good': 0.}
