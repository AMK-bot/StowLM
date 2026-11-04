"""Algebraic certificate checks, separate from nonlinear smoke evidence."""
import numpy as np

from ballast import farkas


def test_farkas_witness_checks_original_rows():
    # x<=0 and x>=1 is contradictory. No physics assumptions enter this test.
    a = np.array([[1.], [-1.]])
    b = np.array([0., -1.])
    proof = farkas(a, b)
    assert proof is not None
    y = np.asarray(proof['multipliers'])
    assert np.all(y >= 0.)
    assert np.max(np.abs(y@a)) < 1e-12
    assert y@b < 0.


def test_farkas_feasible_rows_do_not_return_witness():
    assert farkas(np.array([[1.], [-1.]]), np.array([1., 0.])) is None


def test_optional_gm_floor_only_tightens_local_search(monkeypatch):
    from types import SimpleNamespace as NS
    from stowage.checker import stability, stability_sounding
    from ballast import _assemble
    monkeypatch.setattr(stability, '_loading_policy', lambda instance: (0., 1., 0., 1000.))
    monkeypatch.setattr(stability, '_minimum_loading_gm', lambda instance: .15)
    monkeypatch.setattr(stability_sounding, 'sounding',
        lambda solid, volume: NS(lcg_m=0., tcg_m=0., vcg_m=0., fsm_m4=0.))
    hull = NS(design_draught_m=1., max_draught_m=2., min_draught_fwd_m=0.,
              lightship_mass_kg=100., max_list_rad=.1)
    inst = NS(vessel=NS(hull=hull), port=NS(max_draught_m=None), rule_overrides={})
    class Geometry:
        def full_submerged(self, ta, tf, heel, rho):
            return NS(displacement_kg=100.*(ta+tf), lcb_m=0., vcb_m=1., bm_t_m=0.)
    ctx = NS(instance=inst, direct_geometry=Geometry(), rho=1.,
             tanks=[NS(id='tank', capacity_m3=1., content_density_kg_m3=1.)],
             solids={'tank': None}, capacity=np.array([20.]),
             base=NS(mass_kg=100., fsm_kgm=0.), base_moments=np.array([0., 0., 70.]))
    ordinary = _assemble([('departure', ctx)])
    restricted = _assemble([('departure', ctx)], search_gm_floor=.4)
    index = ordinary[2].index('departure/gm')
    x = np.array([0., 1., 1.])
    assert ordinary[0][index]@x-ordinary[1][index] < 0.
    assert restricted[0][index]@x-restricted[1][index] > 0.
    assert inst.rule_overrides == {}
