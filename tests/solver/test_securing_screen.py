"""Agreement with the original checker on saved failing synthetic deck stacks."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from stowage.actions.space import Place
from stowage.checker import lashing, visibility
from stowage.checker.full import departure_condition
from stowage.conventions import cells_of_slot, parse_slot
from stowage.model import Instance, MovePlan, ShipState, Placement
from stowage.solver.securing_screen import DeckSecuringScreen

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ('slack/mass-bracket/mass-5500-slack-2500-small-feeder_800-random_yard-00', 'raw_plan.json'),
    ('slack/mass-bracket/mass-5500-slack-2500-medium-feeder_1700-simulated_yard-00', 'raw_plan.json'),
    ('slack/bracket/slack-2500-small-feeder_800-simulated_yard-00', 'raw_plan.json'),
    ('construct/run-v4/small-feeder_800-simulated_yard-47290011', 'raw-plan.json'),
]


def load_case(case):
    directory, plan_name = case
    base = ROOT / 'experiments/mvp/solve47' / directory
    ip = base / 'instance.json'
    if directory.startswith('construct/'):
        ip = ROOT / 'experiments/mvp/solve47/construct/inputs' / base.name / 'instance.json'
    if not ip.exists() or not (base / plan_name).exists():
        pytest.skip('local saved diagnostic plans unavailable')
    instance = Instance.model_validate_json(ip.read_text())
    plan = MovePlan.model_validate_json((base / plan_name).read_text())
    return instance, departure_condition(instance, plan)


@pytest.fixture(scope='module')
def sample():
    return load_case(CASES[0])


@pytest.mark.parametrize('case', CASES)
def test_saved_failing_stack_verdict_agrees_with_original_checker(case, monkeypatch):
    instance, ship = load_case(case)
    screen = DeckSecuringScreen(instance)
    monkeypatch.setattr(visibility, '_loading_condition', lambda instance, ship: screen.condition)
    # Evaluate each row separately, retaining both physical columns for every
    # long box. This includes 20/40 mixtures and background support records.
    rows = sorted({parse_slot(p.slot).row for p in ship.placements if parse_slot(p.slot).on_deck})
    failures = passes = 0
    for row in rows:
        placements = [p for p in ship.placements if parse_slot(p.slot).on_deck and parse_slot(p.slot).row == row]
        partial = ShipState(vessel_id=instance.vessel.id, placements=placements)
        reference = lashing.check_lashing(instance, partial, call_index=instance.call_index)
        assert reference.ran, reference.error
        verdict = screen.evaluate(placements)
        assert verdict.passed == (not reference.violations)
        failures += not verdict.passed
        passes += verdict.passed
    assert rows
    if case != CASES[0]:
        assert failures, 'fixture must exercise an actual securing failure at design GM'


def test_single_component_ratios_are_exact_and_cached(sample, monkeypatch):
    instance, ship = sample
    screen = DeckSecuringScreen(instance)
    base = next(p for p in ship.placements if parse_slot(p.slot).on_deck)
    # A single lowest-tier box is a standalone connected component.
    placements = [base]
    if any(parse_slot(p.slot).tier < parse_slot(base.slot).tier and
           {screen.cells[c].stack_id for c in cells_of_slot(p.slot)} &
           {screen.cells[c].stack_id for c in cells_of_slot(base.slot)} for p in ship.placements):
        placements = sorted((p for p in ship.placements if parse_slot(p.slot).on_deck), key=lambda p: parse_slot(p.slot).tier)[:1]
    monkeypatch.setattr(visibility, '_loading_condition', lambda instance, ship: screen.condition)
    # Full background contributes its own (smaller) ratios. Choose no fixed
    # cargo for this isolated equality check, without changing either model.
    empty_background = instance.vessel.model_copy(update={'fixed_loads': []})
    isolated = instance.model_copy(update={'vessel': empty_background})
    screen = DeckSecuringScreen(isolated)
    reference = lashing.check_lashing(isolated, ShipState(vessel_id=isolated.vessel.id, placements=placements), call_index=isolated.call_index)
    result = screen.evaluate(placements)
    expected = max(v for k, v in reference.metrics.items() if k.startswith('max_') and k.endswith('_ratio'))
    assert result.worst_ratio == pytest.approx(expected, rel=1e-12)
    assert result.passed == (not reference.violations)
    assert screen.evaluate(placements) == result
    assert screen.metrics['evaluations'] == 1
    assert screen.metrics['cache_hits'] == 1
    assert screen.metrics['evaluation_seconds'] > 0


def test_private_bindings_do_not_mutate_checker_and_are_independent(sample):
    instance, ship = sample
    original_condition = visibility._loading_condition
    original_graph = lashing._load_graph
    a, b = DeckSecuringScreen(instance), DeckSecuringScreen(instance)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda screen: screen.evaluate(ship.placements), (a, b)))
    assert results[0] == results[1]
    assert visibility._loading_condition is original_condition
    assert lashing._load_graph is original_graph
    assert lashing._check_lashing.__globals__['_load_graph'] is original_graph


def test_placement_api_matches_explicit_state(sample):
    instance, ship = sample
    screen = DeckSecuringScreen(instance)
    candidate = next(p for p in ship.placements if parse_slot(p.slot).on_deck)
    expander = SimpleNamespace(where={p.container_id: ('ship', p.slot) for p in ship.placements if p != candidate})
    result = screen.check_placement(expander, Place(candidate.container_id, candidate.slot))
    assert screen.metrics['placement_calls'] == 1
    assert screen.metrics['placement_seconds'] >= screen.metrics['evaluation_seconds'] >= 0.
    targets = {screen.cells[c].stack_id for c in cells_of_slot(candidate.slot)}
    assert result == screen.evaluate(ship.placements, stack_ids=targets)


def test_shallow_motion_envelope_bounds_deck_acceleration(sample):
    instance, _ = sample
    screen = DeckSecuringScreen(instance)
    hull = instance.vessel.hull
    cb = screen.condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
    envelope = lashing._motions(hull, screen.condition.t_aft_m, screen.design_gm, cb)
    for gm_fraction in (.25, .5, .75, 1.):
        for draft_fraction in (.1, .5, .8, 1.2):
            motion = lashing._motions(hull, hull.scantling_draught_m*draft_fraction, screen.design_gm*gm_fraction, cb)
            for stack in list(s for s in screen.stacks.values() if s.on_deck)[::10]:
                cell = screen.cells[stack.cell_ids[0]]
                point = (cell.lcg_m, cell.tcg_m, stack.base_z_m+stack.max_height_m)
                assert all(a <= b+1e-12 for a,b in zip(motion.at(*point), envelope.at(*point)))


def synthetic_stack(masses, length=20):
    from stowage.model.fixtures import build_instance
    from stowage.vessel.fleet import load_vessel
    from stowage.model import Cell
    instance = build_instance()
    physical = load_vessel('feeder_500')
    stacks, cells = [], []
    for bay in ((1,) if length == 20 else (1, 3)):
        original = next(s for s in instance.vessel.stacks if s.id == f'{bay:03}01D')
        source = next(c for c in instance.vessel.cells if c.id == original.cell_ids[0])
        ids = [f'{bay:03}01{82+2*j}' for j in range(len(masses))]
        stacks.append(original.model_copy(update={'cell_ids': ids, 'max_height_m': 30., 'max_mass_kg': 200000.}))
        cells.extend(Cell(**{**source.model_dump(), 'id': cid, 'vcg_base_m': 9+2.591*j}) for j,cid in enumerate(ids))
    boxes = [box.model_copy(update={'length_ft': length, 'gross_mass_kg': mass, 'height_m': 2.591, 'oog': None, 'dg': None})
             for box,mass in zip(instance.containers,masses)]
    vessel = instance.vessel.model_copy(update={'hull': physical.hull, 'cells': cells, 'stacks': stacks,
                                               'fixed_loads': [], 'fixed_load_cells': [], 'fixed_load_stacks': [], 'fixed_load_hatch_covers': []})
    instance = instance.model_copy(update={'vessel': vessel, 'containers': boxes})
    placements = [Placement(container_id=box.id, slot=f'{1 if length==20 else 2:03}01{82+2*j}') for j,box in enumerate(boxes)]
    return instance, placements


@pytest.mark.parametrize('masses,length', [([5000.],20), ([20000.,10000.],20),
    ([27000.]*4,20), ([5000.,27000.,20000.,27000.],40), ([10000.]*4,40)])
def test_always_run_synthetic_stack_matches_checker_and_lower_gm_samples(masses, length, monkeypatch):
    instance, placements = synthetic_stack(masses,length)
    screen = DeckSecuringScreen(instance)
    result = screen.evaluate(placements)
    ship = ShipState(vessel_id=instance.vessel.id, placements=placements)
    monkeypatch.setattr(visibility, '_loading_condition', lambda *_: screen.condition)
    reference = lashing.check_lashing(instance, ship, call_index=0)
    assert reference.ran, reference.error
    assert result.passed == (not reference.violations)
    worst = max(v for k,v in reference.metrics.items() if k.startswith('max_') and k.endswith('_ratio'))
    assert result.worst_ratio == pytest.approx(worst, rel=1e-12)
    # Empirical regression of the whole model, including rod relief, rather
    # than a claim that each individual force ratio is mathematically monotone.
    if result.passed:
        for fraction in (.25,.5,.75,1.):
            condition = SimpleNamespace(**{**vars(screen.condition), 'gm_m': screen.design_gm*fraction,
                                           't_aft_m': 4., 't_fwd_m': 4.})
            monkeypatch.setattr(visibility, '_loading_condition', lambda *_, c=condition: c)
            lower = lashing.check_lashing(instance, ship, call_index=0)
            assert lower.ran and not lower.violations


def test_synthetic_heavy_tall_stack_rejects():
    instance, placements = synthetic_stack([27000.]*4)
    result = DeckSecuringScreen(instance).evaluate(placements)
    assert not result.passed
    assert result.worst_ratio > 1.


def test_long_over_paired_twenty_uses_both_support_columns(monkeypatch):
    instance, placements = synthetic_stack([10000.,15000.],40)
    from stowage.model.fixtures import build_instance
    left = instance.containers[0].model_copy(update={'length_ft': 20, 'gross_mass_kg': 8000.})
    right = left.model_copy(update={'id': build_instance().containers[2].id, 'gross_mass_kg': 12000.})
    instance = instance.model_copy(update={'containers': [left,right,instance.containers[1]]})
    placements = [Placement(container_id=left.id,slot='0010182'),
                  Placement(container_id=right.id,slot='0030182'),placements[1]]
    screen = DeckSecuringScreen(instance)
    monkeypatch.setattr(visibility,'_loading_condition',lambda *_:screen.condition)
    reference = lashing.check_lashing(instance,ShipState(vessel_id=instance.vessel.id,placements=placements),call_index=0)
    assert reference.ran, reference.error
    result = screen.evaluate(placements,stack_ids={'00101D'})
    assert result.passed == (not reference.violations)
    expected = max(v for k,v in reference.metrics.items() if k.startswith('max_') and k.endswith('_ratio'))
    assert result.worst_ratio == pytest.approx(expected,rel=1e-12)


def test_invalid_placement_geometry_fails_closed_and_records_cost():
    instance, _ = synthetic_stack([5000.])
    screen = DeckSecuringScreen(instance)
    action = Place(instance.containers[0].id, '0990182')
    result = screen.check_placement(SimpleNamespace(where={}),action)
    assert not result.passed
    assert screen.metrics['placement_calls'] == 1
    assert screen.metrics['placement_seconds'] >= 0.
