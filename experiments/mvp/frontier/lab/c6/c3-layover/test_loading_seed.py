"""Calibration direction/accounting without calling expensive area or securing."""
import pytest
from loading_seed import _corrections


def test_native_draft_and_trim_errors_correct_affine_policy_once():
    rows = [dict(phase='departure', draft_m=[7.1, 6.8], trim_m=.3),
            dict(phase='arrival', draft_m=[6.5, 6.4], trim_m=.1)]
    corrections = _corrections(rows, [[7., 6.85], [6.4, 6.4]])
    assert corrections['departure/draft_0'] == pytest.approx(.1)
    assert corrections['departure/draft_1'] == pytest.approx(-.05)
    assert corrections['departure/trim'] == pytest.approx(.15)
    assert corrections['arrival/draft_0'] == pytest.approx(.1)
    assert corrections['arrival/draft_1'] == 0.


def test_gm_bias_has_correct_sign_and_mass_units():
    from ballast_loading import _gm_bound
    import numpy as np
    # Native GM is .4m greater than affine GM because the reference FSM is
    # pessimistic. Enforcing native 1.9m requires affine 1.5m, not 2.3m.
    assert _gm_bound(1.9, .4) == pytest.approx(1.5)
    base_mass, variable_mass = 1_000_000., 200_000.
    km, base_mz, tank_mz, fsm = 8., 5_000_000., 1_500_000., 300_000.
    affine = km-(base_mz+tank_mz+fsm)/(base_mass+variable_mass)
    native = affine+.4
    required = _gm_bound(1.9, .4)
    lhs = required*variable_mass-(km*variable_mass-tank_mz)
    rhs = km*base_mass-base_mz-fsm-required*base_mass
    assert (lhs <= rhs) is (native >= 1.9)
    assert rhs-lhs == pytest.approx((native-1.9)*(base_mass+variable_mass))


def test_joint_cargo_elimination_matches_fixed_cargo_rows(monkeypatch):
    from types import SimpleNamespace as NS
    import numpy as np
    import ballast_loading as bl
    from stowage.checker import stability
    monkeypatch.setattr(stability, '_loading_policy', lambda inst: (-1., 1., 0., 2e6))
    monkeypatch.setattr(stability, '_minimum_loading_gm', lambda inst: .15)
    monkeypatch.setattr(bl, '_tank_reference', lambda ctx: (np.array([[45., 0., 2.]]), 30000.))
    class Geometry:
        def full_submerged(self, ta, tf, heel, rho):
            return NS(displacement_kg=rho*100.*(ta+tf), lcb_m=50.+ta-tf, vcb_m=2., bm_t_m=6.)
    hull=NS(design_draught_m=5., max_draught_m=10., min_draught_fwd_m=1., max_list_rad=.1)
    inst=NS(vessel=NS(hull=hull), port=NS(max_draught_m=10.), rule_overrides={})
    ctx=NS(instance=inst, tanks=[object()], direct_geometry=Geometry(), rho=1025.,
           capacity=np.array([100000.]), base=NS(mass_kg=1000000.),
           base_moments=np.array([50_000_000., 0., 5_000_000.]))
    oldA, oldb, names, _ = bl._assemble([('departure',ctx)])
    pairmass=np.array([20000.,20000.])
    pairmom=np.array([[1_000_000.,0.,60000.],[1_000_000.,0.,120000.]])
    newA, newb, newnames, meta = bl._assemble([('departure',ctx)],
        cargo_mass=pairmass,cargo_moments=pairmom,removed_cargo=[[20000.,1_000_000.,0.,60000.]])
    assert names == newnames
    for row in range(len(names)):
        q=int(np.argmax(np.abs(oldA[row])))
        if oldA[row,q] == 0.:continue
        scale=newA[row,2+q]/oldA[row,q]
        assert newA[row,2:] == pytest.approx(scale*oldA[row],abs=1e-12)
        assert newb[row]-newA[row,0] == pytest.approx(scale*oldb[row],abs=1e-12)
    assert meta[0]['pair_columns'] == 2
    bandA, bandb, bandnames, bandmeta = bl._assemble([('departure',ctx)],
        cargo_mass=pairmass,cargo_moments=pairmom,removed_cargo=[[20000.,1_000_000.,0.,60000.]],
        auxiliary_quantum=[1e-4,1e-3,1e-3])
    assert all(v > 0. for v in bandmeta[0]['equality_bands_unscaled'].values())
    # Grid bands touch construction equilibria only; policy rows stay exact.
    for i,name in enumerate(names):
        if not ('buoyancy' in name or 'longitudinal_equilibrium' in name):
            assert bandA[i] == pytest.approx(newA[i])
            assert bandb[i] == pytest.approx(newb[i])


def test_native_mass_longitudinal_anchor_makes_reference_satisfy_equalities(monkeypatch):
    from types import SimpleNamespace as NS
    import numpy as np
    import ballast_loading as bl
    from stowage.checker import stability
    monkeypatch.setattr(stability,'_loading_policy',lambda inst:(-1.,1.,0.,2e6))
    monkeypatch.setattr(stability,'_minimum_loading_gm',lambda inst:.15)
    monkeypatch.setattr(bl,'_tank_reference',lambda ctx:(np.array([[45.,0.,2.]]),30000.))
    class Geometry:
        def full_submerged(self,ta,tf,heel,rho):
            return NS(displacement_kg=rho*100.*(ta+tf),lcb_m=50.+ta-tf,vcb_m=2.,bm_t_m=6.)
    hull=NS(design_draught_m=5.,max_draught_m=10.,min_draught_fwd_m=1.,max_list_rad=.1)
    inst=NS(vessel=NS(hull=hull),port=NS(max_draught_m=10.),rule_overrides={})
    ctx=NS(instance=inst,tanks=[object()],direct_geometry=Geometry(),rho=1025.,
           capacity=np.array([100000.]),base=NS(mass_kg=1000000.),
           base_moments=np.array([50_000_000.,0.,5_000_000.]))
    ref=(5.,5.);fraction=.3
    submerged=ctx.direct_geometry.full_submerged(*ref,0.,ctx.rho)
    delta_mass=ctx.base.mass_kg+fraction*ctx.capacity[0]-submerged.displacement_kg
    delta_x=ctx.base_moments[0]+fraction*ctx.capacity[0]*45.-submerged.displacement_kg*submerged.lcb_m
    A,b,names,metadata=bl._assemble([('departure',ctx)],references=[ref],corrections={
        'departure/buoyancy_kg':delta_mass,'departure/longitudinal_kgm':delta_x})
    x=np.array([fraction,*ref])
    for i,name in enumerate(names):
        if 'buoyancy' in name or 'longitudinal_equilibrium' in name:
            assert float(A[i]@x-b[i]) == pytest.approx(0.,abs=1e-12)
    assert metadata[0]['native_buoyancy_anchor_kg'] == delta_mass
    assert metadata[0]['native_longitudinal_anchor_kgm'] == delta_x


def test_pressed_reference_uses_full_arms_and_no_adjustable_fsm(monkeypatch):
    from types import SimpleNamespace as NS
    import numpy as np
    from stowage.checker import stability_sounding
    from ballast_loading import _tank_reference
    calls = []
    def fake_sounding(solid, volume):
        calls.append(volume)
        return NS(lcg_m=50.,tcg_m=0.,vcg_m=volume/100.,fsm_m4=20.)
    monkeypatch.setattr(stability_sounding, 'sounding', fake_sounding)
    tank = NS(id='water',capacity_m3=100.,content_density_kg_m3=1025.)
    ctx = NS(tanks=[tank],solids={'water':object()},base=NS(fsm_kgm=123.),
             instance=NS(vessel=NS(hull=NS(lightship_mass_kg=1e6))))
    half_arms, half_fsm = _tank_reference(ctx)
    full_arms, full_fsm = _tank_reference(ctx, mode='pressed')
    assert calls == [50.,100.]
    assert half_arms[0,2] == .5 and full_arms[0,2] == 1.
    assert half_fsm == 123.+20.*1025.
    assert full_fsm == 123.  # retained fixed-liquid FSM is not removed


def test_tcg_search_window_counts_fixed_cargo_tanks_and_background_once(monkeypatch):
    from types import SimpleNamespace as NS
    import numpy as np
    import ballast_loading as bl
    from stowage.checker import stability
    monkeypatch.setattr(stability,'_loading_policy',lambda inst:(-1.,1.,0.,2e6))
    monkeypatch.setattr(stability,'_minimum_loading_gm',lambda inst:.15)
    monkeypatch.setattr(bl,'_tank_reference',lambda ctx:(np.array([[45.,4.,2.]]),30000.))
    class Geometry:
        def full_submerged(self,ta,tf,heel,rho):
            return NS(displacement_kg=rho*100.*(ta+tf),lcb_m=50.+ta-tf,vcb_m=2.,bm_t_m=6.)
    hull=NS(design_draught_m=5.,max_draught_m=10.,min_draught_fwd_m=1.,max_list_rad=.1)
    inst=NS(vessel=NS(hull=hull),port=NS(max_draught_m=10.),rule_overrides={})
    ctx=NS(instance=inst,tanks=[object()],direct_geometry=Geometry(),rho=1025.,
           capacity=np.array([100000.]),base=NS(mass_kg=1000000.),
           base_moments=np.array([50_000_000.,10000.,5_000_000.]))
    kwargs=dict(cargo_mass=np.array([20000.,20000.]),
        cargo_moments=np.array([[1e6,40000.,60000.],[1e6,-40000.,60000.]]),
        removed_cargo=[[20000.,1e6,40000.,60000.]])
    _,_,old_names,_=bl._assemble([('departure',ctx)],**kwargs)
    assert not any(name.endswith(('/tcg_upper','/tcg_lower')) for name in old_names)
    A,b,names,meta=bl._assemble([('departure',ctx)],tcg_halfwidth_m=.01,**kwargs)
    hi=names.index('departure/tcg_upper');lo=names.index('departure/tcg_lower')
    # Same reference cargo, empty adjustable tank: total My=10,000kg*m and
    # mass=1,000,000kg gives exactly the +0.01m total TCG boundary.
    x=np.array([1.,0.,0.,5.,5.])
    assert A[hi]@x-b[hi] == pytest.approx(0.,abs=1e-12)
    assert A[lo]@x-b[lo] < 0.
    x[2]=.1  # variable water adds +40,000kg*m, crossing the positive boundary
    assert A[hi]@x-b[hi] > 0.
    x=np.array([0.,1.,0.,5.,5.])  # moved cargo crosses negative boundary
    assert A[lo]@x-b[lo] > 0.
    assert meta[0]['tcg_search_halfwidth_m'] == .01
