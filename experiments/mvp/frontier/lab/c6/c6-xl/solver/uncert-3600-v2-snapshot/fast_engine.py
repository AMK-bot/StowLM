"""Exact sparse graph projection in the *search engine*, process-local and XL only.

The original constructor expands sparse slot/cell incidence into a dense matrix
and uses integer matrix multiplication. Every slot has at most two cells, so
Boolean scatter computes exactly the same existence relation in O(B*S) work.
The checker and all capacity/completion screens remain unchanged.
"""
from functools import wraps
import inspect
from textwrap import dedent
from time import perf_counter

import numpy as np

THRESHOLD = 500
ORIGINAL_BLOCK = '''    S, C = len(T.slot_ids), len(T.cell_ids)
    inc = np.zeros((S, C), dtype=bool)
    for n in range(2):
        ok = T.slot_cells[:, n] >= 0
        inc[np.flatnonzero(ok), T.slot_cells[ok, n]] = True
    self._cellcompat = (T.static.astype(np.int32) @ inc.astype(np.int32)) > 0
'''
REPLACEMENT_BLOCK = '''    S, C = len(T.slot_ids), len(T.cell_ids)
    self._cellcompat = _xl_project_and_record(self, T.static, T.slot_cells, C)
'''


def cellcompat(static, slot_cells, n_cells):
    """Box/cell Boolean existence relation, including repeated cell indices.

    logical_or.at performs unbuffered OR scatter, so two different slots aimed
    at the same cell accumulate rather than overwriting each other. No S*C
    incidence matrix or int32 copy of B*S static eligibility is allocated.
    """
    static = np.asarray(static, dtype=bool)
    slot_cells = np.asarray(slot_cells)
    if static.ndim != 2 or slot_cells.shape != (static.shape[1], 2):
        raise ValueError('Expected static B*S and two cell indices per slot')
    out = np.zeros((static.shape[0], n_cells), dtype=bool)
    for n in range(2):
        ok = slot_cells[:, n] >= 0
        np.logical_or.at(out.T, slot_cells[ok, n], static[:, ok].T)
    return out


def _project_and_record(engine, static, slot_cells, n_cells):
    started = perf_counter()
    out = cellcompat(static, slot_cells, n_cells)
    engine.diagnostics['cellcompat_projection'] = 'exact Boolean sparse scatter'
    engine.diagnostics['cellcompat_projection_s'] = perf_counter()-started
    return out


def threshold_constructor(original, transformed):
    """Demand count selects the lane, never parent inventory/blocker count."""
    @wraps(original)
    def constructor(self, instance, space, config):
        selected = transformed if len(instance.load_list) > THRESHOLD else original
        return selected(self, instance, space, config)
    constructor._xl_projection_adapter = True
    constructor._xl_original_constructor = original
    return constructor


def install():
    """Replace only the asserted dense projection block in the private process.

    The complete original constructor is retained verbatim for <=500 load boxes.
    The XL copy keeps ClassFlow and every subsequent screen/initialization line.
    Exact block matching fails closed if a future constructor changes shape.
    """
    from stowage.feasibility import _engine
    original = _engine.IncrementalEngine.__init__
    if getattr(original, '_xl_projection_adapter', False):
        return
    source = dedent(inspect.getsource(original))
    if source.count(ORIGINAL_BLOCK) != 1:
        raise RuntimeError('Search constructor projection block differs; refuse unverified adapter')
    transformed = source.replace(ORIGINAL_BLOCK, REPLACEMENT_BLOCK)
    namespace = dict(original.__globals__, _xl_project_and_record=_project_and_record)
    exec(compile(transformed, str(__file__)+'::<exact-projection-constructor>', 'exec'), namespace)
    _engine.IncrementalEngine.__init__ = threshold_constructor(original, namespace['__init__'])
