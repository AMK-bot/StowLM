"""Small soundness checks; no benchmark map solving or physics workers."""
import importlib.util
from pathlib import Path


def _module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _module('audit')


def test_farkas_exact_contradiction():
    # x <= 0 and -x <= -1 sum to 0 <= -1.
    assert audit.verify_farkas([[1], [-1]], [0, -1], [1, 1])
    assert audit.verify_farkas([['0.1'], ['-0.1']], [0, '-0.01'], [1, 1])


def test_farkas_rejects_nonproofs():
    assert not audit.verify_farkas([[1], [-1]], [0, 1], [1, 1])
    assert not audit.verify_farkas([[1], [-1]], [0, -1], [1, 0])
    assert not audit.verify_farkas([[1], [-1]], [0, -1], [-1, -1])
    assert not audit.verify_farkas([[1, 2], [-1]], [0, -1], [1, 1])


def test_raw_census_uses_physical_45_foot_occupancy():
    raw = dict(containers=[dict(id='a', length_ft=45, gross_mass_kg=100),
                           dict(id='b', length_ft=20, gross_mass_kg=200)],
               load_list=['a', 'b'], vessel=dict(stacks=[dict(on_deck=False, cell_ids=['1']),
                                                        dict(on_deck=True, cell_ids=['2', '3'])]))
    assert audit.raw_census(raw) == dict(boxes=2, cells=3, mass_kg=300., hold_cells=1, deck_cells=2)


def test_unapproved_input_is_rejected_before_read():
    import pytest
    with pytest.raises(ValueError, match='outside the fixed approved'):
        audit.load_raw('do-not-open.json')


def _witness_fixture():
    box = dict(id='a', length_ft=20, gross_mass_kg=100, height_m=2,
               oog=None, reefer=False, special_stowage=[])
    stack = dict(id='s', on_deck=False, cell_ids=['0010102'], base_z_m=5,
                 max_mass_kg=150, max_mass_20_kg=None, max_mass_40_kg=None,
                 max_height_m=3)
    cell = dict(id='0010102', stack_id='s', accepts_20=True, reefer_plug=False)
    raw = dict(containers=[box], load_list=['a'], vessel=dict(stacks=[stack], cells=[cell]),
               port=dict(quay_crane=dict(max_on_deck_tiers=5)))
    cls = dict(members=['a'], length_ft=20, reefer=False, mass_kg=100, height_m=2,
               physical_height_m=2, units=1, under_deck=False, on_deck=False)
    witness = dict(classes=[cls], capacities=[dict(bay=1, deck=False, k=0, cells=1)],
                   allocations=[dict(class_index=0, count=1, representative_slot='0010102',
                                     signature=[[1, False, 0, 1]], z_lower_m=6)],
                   cargo_vertical_moment_lower_kg_m=600,
                   deck_boxes_by_odd_bay={}, deck_mass_by_odd_bay_kg={})
    return raw, witness


def test_independent_primal_audit_checks_raw_capacities():
    raw, witness = _witness_fixture()
    assert audit.verify_witness(raw, witness)['verified']
    raw['vessel']['stacks'][0]['max_mass_kg'] = 90
    result = audit.verify_witness(raw, witness)
    assert not result['verified']
    assert any('mass capacity' in error for error in result['errors'])


def test_independent_primal_audit_rejects_wrong_class_and_coefficient():
    raw, witness = _witness_fixture()
    witness['classes'][0]['mass_kg'] = 90
    witness['allocations'][0]['z_lower_m'] = 7
    result = audit.verify_witness(raw, witness)
    assert not result['verified']
    assert any('differs from raw box' in error for error in result['errors'])
    assert 'incorrect cargo moment coefficient' in result['errors']
