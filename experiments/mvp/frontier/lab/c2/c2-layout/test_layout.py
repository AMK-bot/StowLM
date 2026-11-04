"""Small design-graph tests; no optimiser or checker solve is run."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = spec_from_file_location('c2_layout_under_test', HERE / 'method.py')
method = module_from_spec(spec)
sys.modules[spec.name] = method
spec.loader.exec_module(method)


def test_asymmetric_deck_rule_recovers_safe_late_cover_capacity():
    model = method.Model.__new__(method.Model)
    model.layout = {'early': 1, 'late': 2}
    model.ports = [1, 2]
    model.layout_deck = 'no_restow'
    early_hold = SimpleNamespace(cover='early', deck=False)
    late_hold = SimpleNamespace(cover='late', deck=False)
    early_deck = SimpleNamespace(cover='early', deck=True)
    late_deck = SimpleNamespace(cover='late', deck=True)
    assert model.port_of(early_hold) == 1
    assert model.port_of(late_hold) == 2
    assert model.port_of(early_deck) == 1
    # None means either port, as both discharge before/at the late hold call.
    assert model.port_of(late_deck) is None
    assert model.port_of(SimpleNamespace(cover=None, deck=True)) is None
    model.layout_deck = True
    assert model.port_of(late_deck) == 2
    model.layout_deck = False
    assert model.port_of(early_deck) is None
    assert model.port_of(early_hold) == 1


def fake_design():
    pages = []
    for cover in ('A', 'B', 'C'):
        for deck in (False, True):
            pages.append(SimpleNamespace(kind='20', cover=cover, deck=deck,
                                         slots=(0, 1), limit_kg=50000.))
            pages.append(SimpleNamespace(kind='long', cover=cover, deck=deck,
                                         slots=(0, 1), limit_kg=50000.))
    return SimpleNamespace(
        pages=pages, ports=[1, 2], boxes=[0, 1, 2, 3],
        pod={0: 1, 1: 1, 2: 2, 3: 2},
        heavy={0: True, 1: False, 2: True, 3: False},
        T=SimpleNamespace(units=[1, 2, 1, 2], mass=[20000., 15000., 18000., 12000.]))


def test_enumeration_covers_all_binary_designs_once_and_retains_anchor():
    model = fake_design()
    layouts = method.enumerate_layouts(model)
    assert layouts[0] == method.block_layout(model)
    keys = {tuple(sorted(layout.items())) for layout in layouts}
    assert len(layouts) == len(keys) == 8
    assert all(set(layout) == {'A', 'B', 'C'} for layout in layouts)
    assert all(set(layout.values()) <= {1, 2} for layout in layouts)
    assert {'A': 1, 'B': 1, 'C': 1} in layouts
    assert {'A': 2, 'B': 2, 'C': 2} in layouts


def test_enumeration_limit_is_deterministic_and_keeps_anchor():
    model = fake_design()
    first = method.enumerate_layouts(model, limit=3)
    second = method.enumerate_layouts(model, limit=3)
    assert first == second
    assert len(first) == 3
    assert first[0] == method.block_layout(model)
