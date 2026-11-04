"""Exact search initialization graph projection and size-lane dispatch."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from fast_engine import cellcompat, threshold_constructor, install
from stowage.feasibility.capacity import ClassFlow


def dense_original(static, slot_cells, n_cells):
    inc = np.zeros((static.shape[1], n_cells), dtype=bool)
    for n in range(2):
        ok = slot_cells[:, n] >= 0
        inc[np.flatnonzero(ok), slot_cells[ok, n]] = True
    return (static.astype(np.int32) @ inc.astype(np.int32)) > 0


@pytest.mark.parametrize('n_boxes,n_slots,n_cells', [(0,0,0),(0,4,3),(3,0,5),(7,13,9),(13,29,17)])
def test_scatter_exact_dense_projection(n_boxes,n_slots,n_cells):
    rng=np.random.default_rng(245100000)
    for _ in range(12):
        static=rng.random((n_boxes,n_slots))<.4
        slots=rng.integers(-1,n_cells,size=(n_slots,2))
        if n_slots:
            slots[0]=[-1,-1]                 # no occupied cell
        if n_slots>1:
            slots[1]=[0,0]                   # duplicated cell in one long slot
        if n_slots>2:
            slots[2]=[0,-1]                  # duplicate across different slots
        expected=dense_original(static,slots,n_cells)
        np.testing.assert_array_equal(cellcompat(static,slots,n_cells),expected)


def test_identical_classflow_groups_compat_and_flow():
    rng=np.random.default_rng(245100001)
    static=rng.random((15,19))<.3
    slots=rng.integers(-1,11,size=(19,2))
    # Explicitly retain an inaccessible empty cell (column12).
    sparse=ClassFlow(cellcompat(static,slots,13))
    dense=ClassFlow(dense_original(static,slots,13))
    for name in ('rows','box_class','cell_class','compat'):
        np.testing.assert_array_equal(getattr(sparse,name),getattr(dense,name))
    demand=np.bincount(sparse.box_class,minlength=sparse.nb)
    capacity=np.bincount(sparse.cell_class,minlength=sparse.nc)
    left,right=sparse.flow(demand,capacity),dense.flow(demand,capacity)
    assert left[0]==right[0]
    np.testing.assert_array_equal(left[1],right[1])


def test_dispatch_uses_load_demand_and_keeps_small_original():
    calls=[]
    def original(self,instance,space,config):
        calls.append(('original',self,instance,space,config))
    def transformed(self,instance,space,config):
        calls.append(('xl',self,instance,space,config))
    wrapped=threshold_constructor(original,transformed)
    for demand in (0,300,401,500,501):
        instance=NS(load_list=list(range(demand)),containers=list(range(2000)))
        wrapped('engine',instance,'space','config')
        assert calls[-1]==('xl' if demand>500 else 'original','engine',instance,'space','config')
    assert wrapped._xl_original_constructor is original


def test_install_asserted_constructor_is_idempotent():
    from stowage.feasibility import _engine
    original=_engine.IncrementalEngine.__init__
    install()
    first=_engine.IncrementalEngine.__init__
    install()
    assert _engine.IncrementalEngine.__init__ is first
    assert first._xl_original_constructor is original
