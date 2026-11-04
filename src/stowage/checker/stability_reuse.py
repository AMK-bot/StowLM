"""Exact-content reuse of immutable stability interpolation inputs.

Only numerical intermediates live here. Every invocation still validates its
mutable vessel/loading state and evaluates every criterion. Reference dispatch
keeps the preceding implementation callable for equivalence measurements.
"""
from functools import lru_cache

import numpy as np

from stowage.checker.perf import reuse_enabled


_SCALAR_TYPES = (bool, int, float, np.float16, np.float32, np.float64, np.longdouble,
                 np.int8, np.int16, np.int32, np.int64,
                 np.uint8, np.uint16, np.uint32, np.uint64)


def _axis_dependencies():
    functions = (np.asarray, np.frombuffer, np.dtype, np.ndarray)
    return (*functions, *(getattr(function, '__code__', None) for function in functions))


_ORIGINAL_AXIS_DEPENDENCIES = _axis_dependencies()


@lru_cache(maxsize=8192)
def _axis_query(function, code, dtype, shape, payload, scalar_type, scalar_dtype, scalar_payload):
    axis = np.frombuffer(payload, dtype=np.dtype(dtype)).reshape(shape)
    value = np.frombuffer(scalar_payload, dtype=np.dtype(scalar_dtype))[0]
    # Python weak scalars and NumPy strong scalars have different promotion
    # rules. Preserve both the type in the key and the original scalar kind.
    value = scalar_type(value)
    result = function(axis, value)
    # Results stay private: callers receive fresh arrays below.
    for item in result:
        if isinstance(item, np.ndarray):
            item.flags.writeable = False
    return result


def axis_query(function, axis, value):
    """Reuse a stencil by every axis/scalar bit and its implementation.

    Axis contents are read on every call, including writable/custom axes. The
    scalar's representation retains signed zero and precision. No rounded
    lookup, array identity, mtime, or mutable provider enters the cache key.
    """
    if (not reuse_enabled() or _axis_dependencies() != _ORIGINAL_AXIS_DEPENDENCIES
            or type(axis) is not np.ndarray or type(value) not in _SCALAR_TYPES):
        return function(axis, value)
    array, scalar = np.asarray(axis), np.asarray(value)
    if (array.dtype != np.dtype('float64') or array.dtype.type is not np.float64
            or scalar.dtype.hasobject or scalar.shape):
        return function(axis, value)
    result = _axis_query(function, getattr(function, '__code__', None),
                         array.dtype.str, array.shape, array.tobytes(),
                         type(value), scalar.dtype.str, scalar.tobytes())
    return tuple(item.copy() if isinstance(item, np.ndarray) else item for item in result)
