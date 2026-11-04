# Copied from experiments/mvp/frontier/lab/c6/c5-securing/test_rows.py; engine-local imports and guarded integration.
"""The linear rows must reproduce the copied exact chain terms, and those must reproduce the checker's own metrics."""
import numpy as np
import pytest

from .rows import stack_rows, HEIGHT
from .chainlab import chain_ratios
from stowage.checker import lashing


class Hull:
    lpp_m, beam_m, depth_m, scantling_draught_m, bilge_keel = 124.41, 22.5, 11.3, 8.7, True


def chain(m, masses, base=12.5):
    class C:
        background = False
    rows = []
    floor = base
    for mass in masses:
        al, at, av = m.at(60., 5., floor+.45*HEIGHT)
        rows.append((lashing.BoxLoad(None, C, True, floor, HEIGHT, .45*HEIGHT, mass, 12.192, 0., 0., al, at, av, 31600., 6400., (None, None)), 0))
        floor += HEIGHT
    return rows


@pytest.mark.parametrize('masses', [[20000., 16000., 8000., 4000., 3500.], [16000.]*5, [30000., 20000.], [9000., 9000., 9000.]])
def test_rows_match_exact(masses):
    m = lashing._motions(Hull, 7.5, 1.8, .65)
    n = len(masses)
    A, Bt, Bl, names = stack_rows(m, 60., 5., 12.5, n)
    row = A@np.array(masses)+Bt@np.full(n, 31600.)+Bl@np.full(n, 6400.)
    exact, _ = chain_ratios(chain(m, masses))
    assert max(row) == pytest.approx(max(exact.values()), rel=1e-9)


def test_empty_tiers_keep_rows_valid():
    m = lashing._motions(Hull, 7.5, 1.8, .65)
    masses = [20000., 12000., 5000.]
    A, Bt, Bl, names = stack_rows(m, 60., 5., 12.5, 6)
    occupied = np.array([1, 1, 1, 0, 0, 0.])
    row = A@np.array(masses+[0., 0., 0.])+Bt@(31600.*occupied)+Bl@(6400.*occupied)
    exact, _ = chain_ratios(chain(m, masses))
    assert max(row) == pytest.approx(max(exact.values()), rel=1e-9)
