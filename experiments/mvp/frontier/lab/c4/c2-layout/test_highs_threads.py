"""Dense fallback must initialize the same single-thread HiGHS scheduler."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent))
import pages_fast_dense
from test_moments import example


def test_dense_fallback_forwards_one_thread_and_preserves_other_options(monkeypatch):
    seen={}
    def fake(*args,**kwargs):
        seen.update(kwargs)
        return 'done'
    monkeypatch.setattr(pages_fast_dense,'_linprog',fake)
    assert pages_fast_dense.linprog([1.],options={'time_limit':2.,'threads':8})=='done'
    assert seen['options']=={'time_limit':2.,'threads':1}


def test_dense_fallback_then_moment_master_share_scheduler_without_notset():
    # This is deliberately the first real LP in this test file/process.
    first=pages_fast_dense.linprog([1.],A_ub=[[-1.]],b_ub=[-1.],method='highs')
    assert first.status==0,first.message
    model=example()
    model.cargo_vmoment_max=model.column_moments(model.columns[0])[1]
    _,_,_,_,uncovered=model.solve_lp(active_boxes=set(model.boxes),active_stacks=set(model.stacks),
                                      hold_full=False,phase=1)
    assert uncovered<1e-7
    assert model.moment_slacks['vertical_max']==pytest.approx(0.)
