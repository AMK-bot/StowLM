# Provenance: copied from experiments/mvp/frontier/lab/c2/c2-layout/test_cache.py.
"""Cache ownership must follow a problem and must not alias model masks."""
from types import SimpleNamespace
import numpy as np

from .layout_pages import FastModel as BaseModel, Model


def test_unary_cache_is_per_problem_and_copy_isolated(monkeypatch):
    calls = []

    def expensive(model):
        calls.append(model.p)
        return np.array([[True, False]])

    monkeypatch.setattr(BaseModel, '_static', expensive)
    p = SimpleNamespace()
    a, b, c = (object.__new__(Model) for _ in range(3))
    a.p = b.p = p
    c.p = SimpleNamespace()
    first = a._static()
    first[0, 0] = False
    assert b._static()[0, 0]
    assert c._static()[0, 0]
    assert len(calls) == 2
