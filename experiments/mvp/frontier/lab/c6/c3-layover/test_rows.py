"""The linear rows must reproduce the copied exact chain terms, and those must reproduce the checker's own metrics."""
import numpy as np
import pytest

from rows import stack_rows, HEIGHT
from chainlab import chain_ratios
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


def test_fixed_prefix_mass_and_wind_superpose_with_free_suffix():
    """A fixed edge contributes the same physical securing load as an ordinary occupied edge."""
    m = lashing._motions(Hull, 7.5, 1.8, .65)
    heights = [2.896, 2.591, 2.896, 2.591]
    masses = np.array([19000., 13000., 7000., 3500.])
    A, Bt, Bl, names = stack_rows(m, 60., 5., 12.5, len(heights), heights)
    fixed = A[:, :2]@masses[:2]+Bt[:, :2]@np.full(2, 31600.)+Bl[:, :2]@np.full(2, 6400.)
    free = A[:, 2:]@masses[2:]+Bt[:, 2:]@np.full(2, 31600.)+Bl[:, 2:]@np.full(2, 6400.)
    all_rows = A@masses+Bt@np.full(4, 31600.)+Bl@np.full(4, 6400.)
    assert fixed+free == pytest.approx(all_rows, abs=1e-12)
    from rows import _quantities, CG
    floor = 12.5+np.r_[0., np.cumsum(heights[:-1])]
    acc = np.array([m.at(60., 5., z+CG*h) for z, h in zip(floor, heights)])
    exact = _quantities(.5*acc[:, 1]*masses, np.full(4, .5*31600.),
                        .5*acc[:, 0]*masses, np.full(4, .5*6400.),
                        .25*(lashing.G+acc[:, 2])*masses, .25*lashing.G*masses, heights)
    assert all_rows == pytest.approx([exact[name] for name in names], rel=1e-9, abs=1e-12)
