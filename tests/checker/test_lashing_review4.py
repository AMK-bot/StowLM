"""Independent equilibrium regressions for the physics-review findings."""
import math
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from stowage.checker import lashing as l
from stowage.model import OutOfGauge, Placement, ShipState
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


def condition(monkeypatch, cb=.65, draft=6.):
    calls = []
    def submerged(aft, fore):
        calls.append((aft, fore))
        return SimpleNamespace(volume_m3=cb*120*20*7)
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=1., t_aft_m=draft, t_fwd_m=draft,
        geometry=SimpleNamespace(submerged=submerged)))
    return calls


def motion(monkeypatch, al=0., at=0., av=0.):
    m = SimpleNamespace(at=lambda *_: (al, at, av), period_roll=10., phi=0.)
    monkeypatch.setattr(l, '_motions', lambda *_: m)
    return m


def mixed():
    i = _instance()
    for c, mass in zip(i.containers, (12000., 10000., 10000., 3500.)):
        c.gross_mass_kg = mass
        c.oog = None
        c.height_m = 2.591
    i.onboard.placements = [Placement(slot=s, container_id=i.containers[j].id)
                             for s, j in [('0010182', 1), ('0030182', 2), ('0020184', 0)]]
    return i


def test_scantling_coefficient_does_not_follow_loading_draft(monkeypatch):
    i = _instance()
    seen = []
    real = l._motions
    monkeypatch.setattr(l, '_motions', lambda h, t, gm, cb: (seen.append(cb) or real(h, t, gm, cb)))
    calls = condition(monkeypatch, cb=.86, draft=4.)
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert r.ran
    assert calls == [(7., 7.)]
    assert seen == [.86]
    assert r.metrics['block_coefficient_scantling'] == .86


def test_pure_longitudinal_whole_box_moment_uses_two_l(monkeypatch):
    condition(monkeypatch)
    m = motion(monkeypatch, al=14.)
    i = _instance()
    # Single physical 20' box: per-post moment = M*a_l*.45h/(2*6.058).
    c = i.containers[1]
    c.gross_mass_kg, c.oog = 20000., None
    i.onboard.placements = [Placement(slot='0010182', container_id=c.id)]
    q = l._load_graph(i, i.onboard, m)
    forces, _ = l._longitudinal_reactions(q, 1., dynamic_vertical=True)
    expected = 20000*9.81/4+(20000*14*.45*c.height_m+6400*.5*c.height_m)/(2*6.058)
    assert forces['0010182'][1, 0] == pytest.approx(expected)
    report = l.check_lashing(i, i.onboard, call_index=0)
    # No transverse wind/rod down-pull contaminates this governing combination.
    assert report.metrics['max_corner_post_ratio'] == pytest.approx(expected/848000)


def test_effective_assembly_stiffness_and_working_loads():
    assert l._rod_stiffness(5., 2) == pytest.approx(175e9*math.pi*.028**2/(4*5))
    assert l._rod_stiffness(5., 1) == pytest.approx(140e9*math.pi*.028**2/(4*5))
    assert l.ROD_SWL == 250000
    assert (l.LOCK_TENSION_SWL, l.LOCK_SHEAR_SWL, l.LOCK_COMPRESSION_SWL) == (250000, 210000, 1000000)


def test_paired_twenty_supports_carry_long_box_only_at_outer_posts(monkeypatch):
    i = mixed()
    m = motion(monkeypatch)
    loads = l._load_graph(i, i.onboard, m)
    for q in loads.values():
        q.wind_t = q.wind_l = 0.
    reactions, _ = l._longitudinal_reactions(loads, 1., dynamic_vertical=True)
    # Hand: upper40's four posts each=12000*g/4=29430N.
    # Lower20 own post=10000*g/4=24525N. Outer=53955N, inner=24525N.
    assert reactions['0010182'][0] == pytest.approx([53955., 53955.])
    assert reactions['0010182'][1] == pytest.approx([24525., 24525.])
    assert reactions['0030182'][0] == pytest.approx([24525., 24525.])
    assert reactions['0030182'][1] == pytest.approx([53955., 53955.])
    assert sum(reactions[s].sum() for s in ('0010182', '0030182')) == pytest.approx(32000*9.81)
    chains = l._chains(loads)
    assert sorted(len(c) for c in chains) == [1, 1, 2, 2]
    assert sum(len(c) for c in chains) == 6  # exactly two end frames per physical box


def test_mixed_longitudinal_global_force_and_moment_balance(monkeypatch):
    i = mixed()
    m = motion(monkeypatch, al=4.)
    loads = l._load_graph(i, i.onboard, m)
    for q in loads.values():
        q.wind_t = q.wind_l = 0.
    reactions, shear = l._longitudinal_reactions(loads, 1., dynamic_vertical=True)
    # ISO gap between paired 20s: .076m, so outer support ends span 12.192m.
    positions = {'0010182': [-6.096, -.038], '0030182': [.038, 6.096]}
    total_weight = sum(q.mass*9.81 for q in loads.values())
    pitch_input = sum(q.mass*9.81*x for q, x in zip(loads.values(), [-3.067, 3.067, 0.]))
    pitch_input += sum(q.mass*4*(q.floor-9.+.45*q.height) for q in loads.values())
    pitch_output = sum(float(reactions[s][j].sum())*x for s, xs in positions.items() for j, x in enumerate(xs))
    assert sum(float(reactions[s].sum()) for s in positions) == pytest.approx(total_weight)
    assert sum(float(shear[s].sum()) for s in positions) == pytest.approx(sum(q.mass*4 for q in loads.values()))
    assert pitch_output == pytest.approx(pitch_input)


def test_valid_deck_mix_passes_and_invalid_supports_fail(monkeypatch):
    condition(monkeypatch)
    i = mixed()
    valid = l.check_lashing(i, i.onboard, call_index=0)
    assert valid.ran and not valid.violations
    assert valid.metrics['physical_box_count'] == 3
    # Removing one 20 makes the 40 hang from only one support.
    i.onboard.placements = [p for p in i.onboard.placements if p.slot != '0030182']
    bad = l.check_lashing(i, i.onboard, call_index=0)
    assert any('lower cell is empty' in v.message for v in bad.violations)
    i = mixed()
    i.containers[2].height_m = 2.896
    bad = l.check_lashing(i, i.onboard, call_index=0)
    assert any('equal actual height' in v.message for v in bad.violations)


def test_twenty_over_long_and_hold_mixed_fail(monkeypatch):
    condition(monkeypatch)
    i = mixed()
    i.onboard.placements = [Placement(slot='0020182', container_id=i.containers[0].id),
                            Placement(slot='0010184', container_id=i.containers[1].id)]
    bad = l.check_lashing(i, i.onboard, call_index=0)
    assert any('20-foot box over' in v.message for v in bad.violations)
    i = mixed()
    i.onboard.placements = [p.model_copy(update={'slot': p.slot[:-2]+('02' if p.slot.endswith('82') else '04')})
                            for p in i.onboard.placements]
    bad = l.check_lashing(i, i.onboard, call_index=0)
    assert any('hold stacks' in v.message for v in bad.violations)


def test_asymmetric_oog_changes_wind_and_eccentric_reactions(monkeypatch):
    condition(monkeypatch)
    m = motion(monkeypatch, at=1.)
    i = _instance()
    c = i.containers[1]
    c.gross_mass_kg, c.oog = 12000., None
    i.onboard.placements = [Placement(slot='0010182', container_id=c.id)]
    plain = l.check_lashing(i, i.onboard, call_index=0)
    c.oog = OutOfGauge(over_width_port_m=.2, over_length_fore_m=.2, over_height_m=.2)
    modest = l.check_lashing(i, i.onboard, call_index=0)
    assert modest.ran and not modest.violations
    assert modest.metrics['max_corner_post_ratio'] > plain.metrics['max_corner_post_ratio']
    graph = l._load_graph(i, i.onboard, m)
    q = graph['0010182']
    assert (q.dx, q.dy) == (.1, .1)
    assert q.wind_t >= (6.058+.2)*(2.591+.2)*1000
    c.oog = OutOfGauge(over_width_port_m=20., over_length_fore_m=20., over_height_m=10.)
    huge = l.check_lashing(i, i.onboard, call_index=0)
    assert huge.ran and huge.violations


def test_hold_guide_interface_combines_two_fittings(monkeypatch):
    condition(monkeypatch)
    motion(monkeypatch, at=4.)
    i = _instance(layover=True)
    for c in i.containers:
        c.oog = None
        c.gross_mass_kg = 12000.
    report = l.check_lashing(i, i.onboard, call_index=0)
    # Each fitting:12000*4/4=12kN. Interface two contacts=24kN.
    # D54 sizes the section per rated vessel; the IRS stress arithmetic and
    # 24 kN contact stay unchanged (IRS-G-DES-19 §3.6.4, PDF p.14).
    design = l.derive_cell_guide_design(i.vessel)
    bend = 24000*l.CELL_GUIDE_BRACKET_SPACING_M/design.section_modulus_m3
    shear = 24000/design.area_m2
    expected = max(bend/200e6, shear/100e6, math.hypot(bend, math.sqrt(3)*shear)/175e6)
    assert report.metrics['max_cell_guide_ratio'] == pytest.approx(expected)
    assert report.metrics['max_corner_post_ratio'] == pytest.approx(24000*9.81/(4*848000))
    # The shared bracket carries24kN, each separate fitting only12kN.
    expected_fitting = max(24000*9.81/(4*.85*1000000), 12000/(.85*210000))
    assert report.metrics['max_fitting_ratio'] == pytest.approx(expected_fitting)


def test_first_occupied_tier_requires_support(monkeypatch):
    condition(monkeypatch)
    i = _instance()
    i.onboard.placements = [Placement(slot='0010184', container_id=i.containers[1].id)]
    report = l.check_lashing(i, i.onboard, call_index=0)
    assert any('lower cell is empty' in v.message for v in report.violations)


def test_six_tier_rod_response_independent_closed_form():
    # Independent compatibility: Q_j=sum(F_k,k>=j). At the tier2 anchor,
    # u=(Q0+Q1)/k/(1+2*krod*cos²/k), since two end springs carry rod relief.
    heights = [2.591]*6
    inertial, wind = [100000.]*6, [8000.]*6  # per end frame
    joint = [108000.]*5+[49000.]
    q = [sum(joint[j:]) for j in range(6)]
    reach, rise = 2.438, 5.182
    length = math.hypot(reach, rise)
    co = reach/length
    krod = 175e9*math.pi*.028**2/(4*length)
    for kend in (3.7e6, 15.7e6):
        displacement = (q[0]+q[1])/kend/(1+2*krod*co**2/kend)
        horizontal = krod*co**2*displacement
        rack, rod, *_ = l._end_response(inertial, wind, heights, kend)
        assert rack == pytest.approx([qj-horizontal if j <= 1 else qj for j, qj in enumerate(q)])
        assert rod*co == pytest.approx(horizontal)


def test_fortyfive_keeps_forty_foot_corner_span_with_full_wind_envelope(monkeypatch):
    i = _instance()
    c = i.containers[0]
    c.length_ft = 45
    i.onboard.placements = [Placement(slot='0020182', container_id=c.id)]
    graph = l._load_graph(i, i.onboard, motion(monkeypatch))
    q = graph['0020182']
    assert q.length == 12.192
    assert q.wind_t == pytest.approx(13.716*c.height_m*1000)
    assert len(l._chains(graph)) == 2


def test_design_guide_section_passes_previous_worst_contact_preserves_limits(monkeypatch):
    # DESIGN.md §3.3 diagnosed old peak guide ratio 2.34 at GM 2–3.6m.
    # Reproduce that contact directly; this unit test changes structural section
    # only, leaving motions/GM and IRS 200/100/175 MPa stress limits unchanged.
    # Old combined-stress coefficient = sqrt((.5/1e-4)² + 3/.003²).
    # F=2.34*175MPa/coefficient=81.3594kN. Required W=2.41311e-4m³;
    # choose W=2.50e-4 rather than 2.35e-4 to retain the full shear term.
    condition(monkeypatch)
    contact = 2.34*175e6/math.hypot(.5/1e-4, math.sqrt(3)/.003)
    i = _instance(layover=True)
    for c in i.containers:
        c.oog = None
        c.gross_mass_kg = 12000.
    # Two 12t guide contacts each take M*a/4, together contact=6000*a.
    motion(monkeypatch, at=contact/6000)
    section = l.CELL_GUIDE_SECTION_MODULUS_M3
    assert section == 2.50e-4
    assert l.CELL_GUIDE_BRACKET_SPACING_M == .5
    assert l.CELL_GUIDE_AREA_M2 == .003
    # Pin the design result to independently exercise the unchanged guide
    # check. Production derives it from the rated ship, never these loads.
    design = l.derive_cell_guide_design(i.vessel)
    old_design = replace(design, section_modulus_m3=1e-4, area_m2=.003)
    monkeypatch.setattr(l, 'derive_cell_guide_design', lambda _: old_design)
    old = l.check_lashing(i, i.onboard, call_index=0)
    assert old.metrics['max_cell_guide_ratio'] == pytest.approx(2.34)
    assert any('max_cell_guide_ratio' in v.message for v in old.violations)
    current_design = replace(design, section_modulus_m3=section, area_m2=.003)
    monkeypatch.setattr(l, 'derive_cell_guide_design', lambda _: current_design)
    current = l.check_lashing(i, i.onboard, call_index=0)
    expected = math.hypot(contact*.5/section, math.sqrt(3)*contact/.003)/175e6
    assert current.metrics['max_cell_guide_ratio'] == pytest.approx(expected)
    assert expected < 1
    assert current.ran and not current.violations
    # Load just above the unchanged combined-stress limit must still fail.
    maximum_contact = 175e6/math.hypot(.5/section, math.sqrt(3)/.003)
    motion(monkeypatch, at=1.01*maximum_contact/6000)
    excessive = l.check_lashing(i, i.onboard, call_index=0)
    assert excessive.metrics['max_cell_guide_ratio'] == pytest.approx(1.01)
    assert any('max_cell_guide_ratio' in v.message for v in excessive.violations)
