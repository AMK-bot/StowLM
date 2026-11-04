# Provenance: copied from lab/c4/c2-layout/pages_fast_dense.py:linprog.
"""Use one scheduler thread in every scout HiGHS call."""
import warnings

from scipy.optimize import linprog as _linprog, OptimizeWarning


def linprog(*args, **kwargs):
    options = dict(kwargs.pop('options', {}) or {})
    options['threads'] = 1
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='Unrecognized options detected.*', category=OptimizeWarning)
        return _linprog(*args, options=options, **kwargs)
