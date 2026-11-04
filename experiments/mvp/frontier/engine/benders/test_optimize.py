# Copied from experiments/mvp/frontier/lab/c3/c2-benders/test_optimize.py; package imports adapted.
import numpy as np

from .optimize import difference_step


def test_difference_remains_above_pressed_threshold():
    x=np.array([.985,.985])
    step=difference_step(x,np.ones(2))
    assert step and np.all(x+step>=.98)


def test_full_pair_gets_inward_difference():
    x=np.ones(2)
    step=difference_step(x,np.ones(2))
    assert step<0 and np.all(x+step>=.98)


def test_split_difference_keeps_empty_tank_nonnegative():
    x=np.array([.2,0.])
    direction=np.array([1.,-1.])
    step=difference_step(x,direction)
    assert step<0 and np.all(x+step*direction>=0.)
