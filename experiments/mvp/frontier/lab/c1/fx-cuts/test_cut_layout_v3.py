"""Resource merge regressions for fixed specials; no solver runs in these tests."""
from pathlib import Path
import importlib.util
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('cut_layout_v3', Path(__file__).with_name('cut_layout_v3.py'))
cuts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cuts)


def test_fixed_load_and_retained_cargo_both_consume_initial_resources():
    retained = [SimpleNamespace(container_id='through', slot='0010202')]
    fixed = {'special': '0060202'}
    assert cuts._initial_resource_positions(retained, fixed) == {
        'through': '0010202', 'special': '0060202'}
    assert fixed == {'special': '0060202'}


def test_fixed_long_footprint_cannot_overlap_retained_short_cell():
    retained = [SimpleNamespace(container_id='through', slot='0010202')]
    # The long slot spans odd-bay cells 0010202 and 0030202.
    with pytest.raises(ValueError, match='resource overlap'):
        cuts._initial_resource_positions(retained, {'special': '0020202'})


def test_fixed_specials_cannot_overlap_each_other():
    with pytest.raises(ValueError, match='resource overlap'):
        cuts._initial_resource_positions([], {'wide': '0020202', 'other': '0030202'})


def test_conflicting_position_for_same_id_is_explicit_failure():
    retained = [SimpleNamespace(container_id='through', slot='0010202')]
    with pytest.raises(ValueError, match='conflicts with retained'):
        cuts._initial_resource_positions(retained, {'through': '0050202'})
