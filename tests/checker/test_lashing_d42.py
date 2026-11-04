"""D42 background boxes enter the ordinary IRS securing graph, never equilibrium twice."""
from types import SimpleNamespace

import pytest

from stowage.checker import lashing as l
from stowage.model import Cell, FixedCargoBox, FixedLoad, Placement, ShipState
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



def condition(monkeypatch, gm=.6):
    monkeypatch.setattr('stowage.checker.visibility._loading_condition', lambda *_: SimpleNamespace(
        mass_kg=9600000., gm_m=gm, t_aft_m=6., t_fwd_m=6.,
        geometry=SimpleNamespace(submerged=lambda *_: SimpleNamespace(volume_m3=.65*120*20*7))))


def background(masses, *, length=20, tiers=None):
    i = build_instance()
    i.vessel.fixed_loads = []
    bases = [s for s in i.vessel.stacks if s.id in ('00101D', '00301D')]
    if length == 20:
        bases = bases[:1]
    tiers = tiers or [82+2*j for j in range(len(masses)+1)]
    cells, stacks = [], []
    for stack in bases:
        source = next(c for c in i.vessel.cells if c.id == stack.cell_ids[0])
        ids = [source.id[:5]+str(t) for t in tiers]
        cells.extend(Cell(**{**source.model_dump(), 'id': cid, 'vcg_base_m': 9+j*2.591})
                     for j, cid in enumerate(ids))
        stacks.append(stack.model_copy(update={'cell_ids': ids, 'max_height_m': 40., 'max_mass_kg': 80000., 'hatch_cover_id': None}))
    fixed_ids = {s.id for s in stacks}
    i.vessel.fixed_load_cells = cells
    i.vessel.fixed_load_stacks = stacks
    i.vessel.cells = [c for c in i.vessel.cells if c.stack_id not in fixed_ids]
    i.vessel.stacks = [s for s in i.vessel.stacks if s.id not in fixed_ids]
    boxes = []
    for j, mass in enumerate(masses):
        supports = [next(c for c in cells if c.id == s.cell_ids[j]) for s in stacks]
        boxes.append(FixedCargoBox(cell_ids=[c.id for c in supports], length_ft=length,
            height_m=2.591, gross_mass_kg=float(mass), x_m=sum(c.lcg_m for c in supports)/len(supports),
            y_m=supports[0].tcg_m, z_m=9+(j+.45)*2.591, stack_id=stacks[0].id, tier=tiers[j]))
    mass = sum(masses)
    i.vessel.fixed_loads = [FixedLoad(kind='cargo', mass_kg=mass,
        x_m=sum(b.gross_mass_kg*b.x_m for b in boxes)/mass,
        y_m=sum(b.gross_mass_kg*b.y_m for b in boxes)/mass,
        z_m=sum(b.gross_mass_kg*b.z_m for b in boxes)/mass, cargo_boxes=boxes)]
    i.onboard = ShipState(vessel_id=i.vessel.id)
    return i


def test_background_low_gm_failure_has_real_irs_ratios(monkeypatch):
    condition(monkeypatch, gm=.6)
    i = background([2500, 8000, 30000, 30500])
    r = l.check_lashing(i, i.onboard, call_index=4)
    assert r.ran, r.error
    assert r.metrics['physical_box_count'] == 4
    assert r.metrics['gm_m'] == .6
    assert r.violations
    assert all(v.rule_id == l.RULE and v.call_index == 4 for v in r.violations)
    condition(monkeypatch, gm=3.)
    high = l.check_lashing(i, i.onboard, call_index=4)
    assert high.metrics['roll_period_s'] < r.metrics['roll_period_s']
    assert high.metrics['max_corner_post_ratio'] > r.metrics['max_corner_post_ratio']


@pytest.mark.parametrize('length', [20, 40, 45])
def test_mixed_planned_background_stack_uses_one_graph(monkeypatch, length):
    condition(monkeypatch)
    i = background([12000], length=length)
    c = i.containers[0].model_copy(update={'length_ft': length, 'gross_mass_kg': 8000., 'height_m': 2.591, 'oog': None})
    i.containers = [c]
    slot = ('001' if length == 20 else '002')+'0184'
    i.onboard.placements = [Placement(slot=slot, container_id=c.id)]
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert r.ran, r.error
    assert not r.violations
    assert r.metrics['physical_box_count'] == 2
    motion = SimpleNamespace(at=lambda *_: (0., 0., 0.))
    graph = l._load_graph(i, i.onboard, motion)
    assert sorted(len(chain) for chain in l._chains(graph)) == [2, 2]
    lower = graph[next(s for s in graph if s != slot)]
    upper = graph[slot]
    assert upper.parents == ((lower.slot, 0), (lower.slot, 1))
    for q in graph.values():
        q.wind_t = q.wind_l = 0.
    reactions, _ = l._longitudinal_reactions(graph, 1., dynamic_vertical=True)
    assert reactions[lower.slot].sum() == pytest.approx(20000*9.81)


@pytest.mark.parametrize('kind', ['unknown', 'other'])
def test_positive_unknown_kind_fails_closed(monkeypatch, kind):
    condition(monkeypatch)
    i = build_instance()
    i.vessel.fixed_loads = [FixedLoad(x_m=50., y_m=0., z_m=4., mass_kg=1000., kind=kind)]
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert not r.ran
    assert r.violations[0].rule_id == 'checker.missing_vessel_data'


def test_explicit_non_cargo_requires_no_securing(monkeypatch):
    condition(monkeypatch)
    i = build_instance()
    i.vessel.fixed_loads = [FixedLoad(x_m=50., y_m=0., z_m=4., mass_kg=1000., kind='non_cargo')]
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert r.ran and not r.violations
    assert r.metrics['physical_box_count'] == 0


def test_background_declared_cg_controls_accelerations_and_moments():
    i = background([12000])
    b = i.vessel.fixed_loads[0].cargo_boxes[0]
    b.x_m += 1.
    b.y_m += .3
    b.z_m += .2
    load = i.vessel.fixed_loads[0]
    load.x_m, load.y_m, load.z_m = b.x_m, b.y_m, b.z_m
    seen = []
    motion = SimpleNamespace(at=lambda *xyz: (seen.append(xyz) or (1., 2., 3.)))
    graph = l._load_graph(i, i.onboard, motion)
    q = next(iter(graph.values()))
    assert seen == [(b.x_m, b.y_m, b.z_m)]
    assert q.dx == pytest.approx(1.)
    assert q.dy == pytest.approx(.3)
    assert q.cg_height == pytest.approx(b.z_m-9.)


def test_declared_stack_sequence_accepts_nonconsecutive_tiers(monkeypatch):
    condition(monkeypatch)
    i = background([12000, 8000], tiers=[82, 86, 90])
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert r.ran and not r.violations
    assert r.metrics['physical_box_count'] == 2


def test_mutated_background_aggregate_fails_closed(monkeypatch):
    condition(monkeypatch)
    i = background([12000])
    i.vessel.fixed_loads[0].cargo_boxes[0].gross_mass_kg = 13000.
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert not r.ran
    assert r.violations[0].rule_id == 'checker.missing_vessel_data'


def test_background_vertical_cg_sets_longitudinal_lever_and_joint_force(monkeypatch):
    condition(monkeypatch)
    i = background([12000])
    b = i.vessel.fixed_loads[0].cargo_boxes[0]
    b.z_m = i.vessel.fixed_loads[0].z_m = 9+.75*b.height_m
    m = SimpleNamespace(at=lambda *_: (4., 2., 0.), period_roll=10., phi=0.)
    monkeypatch.setattr(l, '_motions', lambda *_: m)
    graph = l._load_graph(i, i.onboard, m)
    q = next(iter(graph.values()))
    reactions, _ = l._longitudinal_reactions(graph, 1., dynamic_vertical=True)
    expected = 12000*9.81/4+(12000*4*.75*b.height_m+q.wind_l*.5*b.height_m)/(2*q.length)
    assert reactions[q.slot][1, 0] == pytest.approx(expected)
    rack, rod, co, *_ = l._end_response([12000*2/2], [q.wind_t/2], [q.height], 3.7e6,
                                      cg_heights=[q.cg_height])
    # Force at 75% height contributes .75F at the roof joint, wind stays .5W.
    assert rack[0]+rod*co == pytest.approx(.75*12000*2/2+.5*q.wind_t/2)
    actual = l.check_lashing(i, i.onboard, call_index=0)
    b.z_m = i.vessel.fixed_loads[0].z_m = 9+.25*b.height_m
    lower = l.check_lashing(i, i.onboard, call_index=0)
    assert actual.metrics['max_racking_ratio'] > lower.metrics['max_racking_ratio']
    assert actual.metrics['max_longitudinal_racking_ratio'] > lower.metrics['max_longitudinal_racking_ratio']
    assert actual.metrics['max_corner_post_ratio'] > lower.metrics['max_corner_post_ratio']


def test_background_overweight_records_emit_physical_slot_not_invented_container_id(monkeypatch):
    condition(monkeypatch)
    i = background([31000])
    r = l.check_lashing(i, i.onboard, call_index=0)
    assert r.ran
    v = next(v for v in r.violations if v.unit == 'kg')
    assert v.containers == []
    assert v.slots == ['0010182']


def test_background_eccentric_cg_does_not_redistribute_symmetric_wind(monkeypatch):
    condition(monkeypatch)
    i = background([12000])
    motion = SimpleNamespace(at=lambda *_: (0., 0., 0.), period_roll=10., phi=0.)
    monkeypatch.setattr(l, '_motions', lambda *_: motion)
    winds = []
    original = l._end_response
    def capture(forces, wind, heights, stiffness, **kwargs):
        winds.append(tuple(wind))
        return original(forces, wind, heights, stiffness, **kwargs)
    monkeypatch.setattr(l, '_end_response', capture)
    plain = l.check_lashing(i, i.onboard, call_index=0)
    plain_winds = list(winds)
    winds.clear()
    b = i.vessel.fixed_loads[0].cargo_boxes[0]
    b.x_m += 1.
    i.vessel.fixed_loads[0].x_m = b.x_m
    eccentric = l.check_lashing(i, i.onboard, call_index=0)
    assert plain.ran and eccentric.ran
    assert winds == plain_winds
    assert all(wind == pytest.approx((max(15700, 6.058*2.591*1000)/2,)) for wind in winds)


def test_background_stack_capacity_ignores_only_documented_roundoff(monkeypatch):
    condition(monkeypatch, gm=.6)
    i = background([30000., 30000.0000000128])
    stack = i.vessel.fixed_load_stacks[0]
    stack.max_mass_kg = 60000.
    stack.max_mass_20_kg = None
    report = l.check_lashing(i, i.onboard, call_index=0)
    assert report.ran, report.error
    assert not any(v.rule_id == 'expert.physical.stack_weight' for v in report.violations)
    # A genuine gram-scale excess still fails. Keep aggregate mass consistent
    # with the individual boxes; the CG perturbation is below 1e-6 m.
    i.vessel.fixed_loads[0].cargo_boxes[1].gross_mass_kg += .001
    i.vessel.fixed_loads[0].mass_kg += .001
    excessive = l.check_lashing(i, i.onboard, call_index=0)
    assert excessive.ran, excessive.error
    assert any(v.rule_id == 'expert.physical.stack_weight' for v in excessive.violations)
