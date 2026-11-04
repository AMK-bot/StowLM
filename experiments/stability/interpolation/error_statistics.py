"""Independent IACS UR L5 rev4 Table 1 interpolation-error accounting.

The larger of the absolute and relative validation tolerances applies. The
engineering target here is one fifth of that allowance, not an extra margin
that can be used to pass a statutory stability criterion.
"""
from __future__ import annotations

import numpy as np

L5_TOLERANCES = {
    "gz_m": (.05, .05),
    "area_mrad": (.0012, .05),
    "gm_m": (.05, .01),
}


def error_statistics(reference, interpolated, quantity):
    """Return reproducible absolute and tolerance-normalised error statistics."""
    reference = np.asarray(reference, dtype=float)
    interpolated = np.asarray(interpolated, dtype=float)
    if reference.shape != interpolated.shape or reference.size == 0:
        raise ValueError("nonempty arrays of the same shape required")
    if not np.isfinite(reference).all() or not np.isfinite(interpolated).all():
        raise ValueError("finite reference and interpolated results required")
    absolute, relative = L5_TOLERANCES[quantity]
    error = np.abs(reference-interpolated)
    fraction = error/np.maximum(absolute, relative*np.abs(reference))
    return {
        "count": int(error.size),
        "max_absolute": float(np.max(error)),
        "p99_absolute": float(np.quantile(error, .99)),
        "rms_absolute": float(np.sqrt(np.mean(error**2))),
        "max_l5_fraction": float(np.max(fraction)),
        "p99_l5_fraction": float(np.quantile(fraction, .99)),
        "target_l5_fraction": .2,
        "target_met": bool(np.max(fraction) <= .2),
    }
