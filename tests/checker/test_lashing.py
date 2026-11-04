"""IRS rule arithmetic and synthetic securing arrangements; no class approval implied."""
from types import SimpleNamespace
import math

import pytest

from stowage.checker import lashing as l
from stowage.checker.lashing import _end_response, _motions, check_lashing
from stowage.model import Cell, Placement, ShipState
from stowage.model.fixtures import build_instance
from stowage.model import VesselFieldProvenance


@pytest.fixture(autouse=True)
def explicit_unit_test_guide_design(monkeypatch):
    # These fixtures isolate IRS force/stress arithmetic and background evidence;
    # their placeholder hull has no declared lashing manual. Give the mocked
    # design an explicit synthetic cap; D56's real cap has dedicated tests.
    original_builder = build_instance
    def unit_instance(*args, **kwargs):
        instance = original_builder(*args, **kwargs)
        instance.vessel.hull.max_gm_lashing_m = 1e9
        instance.vessel.hull.max_gm_lashing_m_provenance = VesselFieldProvenance(
            assumption='Unit-test cap for fixed guide/stress arithmetic, not a vessel design')
        return instance
    monkeypatch.setitem(globals(), 'build_instance', unit_instance)
    design = l.CellGuideDesign(l.CELL_GUIDE_SECTION_MODULUS_M3,
        l.CELL_GUIDE_AREA_M2, 1e9, '00101H', 0., 0., 2)
    monkeypatch.setattr(l, 'derive_cell_guide_design', lambda _: design)



def _instance(**kwargs):
    # Isolate explicit-box securing calculations from the builder's unmodelled
    # background cargo. Dedicated background tests require that cargo to fail.
    instance = build_instance(**kwargs)
    instance.vessel.fixed_loads = []
    return instance


def _condition(monkeypatch, *, gm=1.):
    # Isolate structural arithmetic from separately-tested numerical hydrostatics.
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=gm, t_aft_m=6., t_fwd_m=6.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(volume_m3=.65*120*20*7))))


def _stack_instance(masses):
    instance = _instance()
    # Fresh fixture cargo IDs, all standard 20-foot boxes and no DG/OOG.
    cargo = [c.model_copy(update=dict(length_ft=20, gross_mass_kg=m, height_m=2.591,
                                      oog=None, dg=None))
             for c, m in zip(instance.containers, masses)]
    stack = next(s for s in instance.vessel.stacks if s.id == '00101D')
    source = next(c for c in instance.vessel.cells if c.id == '0010182')
    cell_ids = [f'00101{82+2*i:02}' for i in range(len(masses))]
    cells = [Cell(**{**source.model_dump(), 'id': cid, 'vcg_base_m': 9+i*2.591})
             for i, cid in enumerate(cell_ids)]
    # This test isolates dynamic securing from the independent stack rating.
    stack = stack.model_copy(update=dict(cell_ids=cell_ids, max_height_m=20., max_mass_kg=80000.))
    vessel = instance.vessel.model_copy(update=dict(stacks=[stack], cells=cells))
    state = ShipState(vessel_id=vessel.id, placements=[Placement(slot=cid, container_id=c.id)
                                                       for cid, c in zip(cell_ids, cargo)])
    return instance.model_copy(update=dict(vessel=vessel, containers=cargo)), state


def test_hand_one_box_end_force():
    # By hand, one end: inertial force 10 kN, wind 2 kN at 0.45h/0.5h.
    # Equivalent joint force F=.45*10000+.5*2000=5500 N.
    # h=2.438 m and reach=2.438 m => cos²=1/2, rod length=sqrt(2)*2.438.
    # EA/l=140e9*pi*.028²/(4*sqrt(2)*2.438)=25.00 MN/m (LR effective one-tier assembly).
    # Door end k=3.7 MN/m: displacement=5500/(3.7e6+EA/l/2).
    # Racking=k*u; rod tension=(EA/l)*u/sqrt(2); rack+T/sqrt(2)=5500.
    krod = 140e9*math.pi*.028**2/(4*math.sqrt(2)*2.438)
    u = 5500/(3.7e6+krod/2)
    rack, rod, co, si, anchor = _end_response([10000], [2000], [2.438], 3.7e6)
    assert rack[0] == pytest.approx(3.7e6*u)
    assert rod == pytest.approx(krod*u/math.sqrt(2))
    assert rack[0]+rod*co == pytest.approx(5500)
    assert si == pytest.approx(co)
    assert anchor == 0


def test_hand_motion_components():
    h = _instance().vessel.hull
    m = _motions(h, 6., 1., .65)
    # IRS §2.1.3: a0 hits cap .52, surge=.2*.52*g, sway=.3*.52*g.
    assert m.surge == pytest.approx(1.02024)
    assert m.sway == pytest.approx(1.53036)
    assert m.phi >= math.radians(25)
    al, at, av = m.at(.45*120, 0., m.rotation_z)
    assert al == pytest.approx(.7*math.sqrt(1.02024**2+120/325*(9.81*math.sin(m.theta))**2))
    assert at == pytest.approx(math.hypot(1.53036, 9.81*math.sin(m.phi)))
    assert av == pytest.approx(.7*.52*9.81/math.sqrt(.65))


def test_light_on_top_passes_heavy_on_top_fails(monkeypatch):
    _condition(monkeypatch)
    light, light_ship = _stack_instance([20000, 8000, 3500, 2500])
    heavy, heavy_ship = _stack_instance([2500, 8000, 30000, 30500])
    good = check_lashing(light, light_ship, call_index=0)
    bad = check_lashing(heavy, heavy_ship, call_index=2)
    assert good.ran and not good.violations
    assert bad.ran and bad.violations
    assert all(v.rule_id == 'iacs.member_irs.container_securing' and v.call_index == 2 for v in bad.violations)
    assert bad.metrics['max_corner_tension_ratio'] > good.metrics['max_corner_tension_ratio']
    assert good.margins['max_racking_ratio'] == 1-good.metrics['max_racking_ratio']
    assert check_lashing(light, light_ship, call_index=0) == good


def test_missing_hull_fails_closed():
    i = _instance()
    i.vessel.hull = None
    report = check_lashing(i, i.onboard, call_index=0)
    assert not report.ran
    assert report.violations[0].rule_id == 'checker.missing_vessel_data'


def test_gm_changes_acceleration_and_loads(monkeypatch):
    i, state = _stack_instance([20000, 8000])
    _condition(monkeypatch, gm=.6)
    low = check_lashing(i, state, call_index=0)
    _condition(monkeypatch, gm=3.)
    high = check_lashing(i, state, call_index=0)
    assert high.metrics['roll_period_s'] < low.metrics['roll_period_s']
    assert high.metrics['max_corner_post_ratio'] > low.metrics['max_corner_post_ratio']


def test_hold_cell_guides_no_accumulated_racking(monkeypatch):
    _condition(monkeypatch)
    i = _instance(layover=True)
    result = check_lashing(i, i.onboard, call_index=0)
    assert result.ran and not result.violations
    assert result.metrics['max_racking_ratio'] == 0
    assert result.metrics['max_corner_post_ratio'] > 0
    assert result.metrics['max_cell_guide_ratio'] > 0


def test_synthetic_45ft_passes(monkeypatch):
    _condition(monkeypatch)
    i = _instance()
    c = i.containers[0].model_copy(update={'length_ft': 45})
    i.containers = [c]+i.containers[1:]
    s = ShipState(vessel_id=i.vessel.id, placements=[Placement(slot='0020182', container_id=c.id)])
    result = check_lashing(i, s, call_index=0)
    assert result.ran and not result.violations
    assert result.metrics['physical_box_count'] == 1


def test_numerical_condition_integration():
    # No condition mock: use HullGeometry's free-trim equilibrium and actual GM.
    # The fixture's declared placeholder hull has no solid geometry; transplant
    # the feeder solid's particulars rather than invent hydrostatic data.
    from stowage.vessel.fleet import load_ship_params
    i, state = _stack_instance([20000, 3500])
    p = load_ship_params('feeder_500')['hull']
    for key in ('lpp_m', 'loa_m', 'beam_m', 'depth_m', 'hull_offsets_ref',
                'lightship_mass_kg', 'lightship_lcg_m', 'lightship_vcg_m', 'scantling_draught_m'):
        setattr(i.vessel.hull, key, p[key])
    i.vessel.tanks = []
    i.vessel.fixed_loads = []
    report = check_lashing(i, state, call_index=0)
    assert report.ran, report.error
    assert report.metrics['gm_m'] > 0
    assert report.metrics['max_corner_post_ratio'] > 0
