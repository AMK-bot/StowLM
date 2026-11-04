"""Pure deterministic recipe checks; no parent generation or planner runs."""
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('fillsweep_generate_tested', Path(__file__).with_name('generate.py'))
generate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate)


class Classes:
    @staticmethod
    def classify(mass):
        return 'H' if mass >= 20000 else 'M' if mass >= 10000 else 'L'


def box(index, length=20, mass=24000, tare=2400):
    return dict(id=f'box-{index}', pod=('SGSIN' if index % 2 else 'MYPKG'), length_ft=length,
                weight_class=Classes.classify(mass), reefer=False, dg=None, empty=False,
                special_stowage=[], tare_kg=tare, gross_mass_kg=mass, category='original')


def test_grid_is_ninety_matched_train_inputs():
    specs = generate.specs()
    rows = [generate.map_row(spec, fill, mass) for spec in specs for fill in generate.FILLS for mass in generate.MASSES]
    assert len(rows) == len({row['map_id'] for row in rows}) == 90
    assert len({spec['seed'] for spec in specs}) == 9
    assert {spec['split'] for spec in specs} == {'train'}
    assert {spec['generator'] for spec in specs} == {'random_yard'}


def test_deterministic_nested_stratified_order():
    cargo = [box(index, length=20 if index % 3 else 40) for index in range(30)]
    first = generate.nested_order(cargo, 192200000)
    assert first == generate.nested_order(list(reversed(cargo)), 192200000)
    assert len({entry['id'] for entry in first}) == len(cargo)
    prior = set()
    for target in (10, 15, 20, 25, 30):
        prefix = generate.nearest_prefix(first, target)
        ids = {entry['id'] for entry in prefix}
        assert prior <= ids
        assert abs(sum(generate.footprint(entry) for entry in prefix) - target) <= 1
        prior = ids


def test_nearest_prefix_tie_prefers_lower_fill():
    cargo = [box(0, 40), box(1, 40)]
    assert len(generate.nearest_prefix(cargo, 3)) == 1
    assert generate.footprint(dict(length_ft=45)) == 2


def test_payload_preserves_full_yard_and_ashore_inventory():
    cargo = [box(0), box(1), box(2, mass=3000, tare=2400)]
    parent = dict(id='parent', containers=cargo, load_list=[entry['id'] for entry in cargo],
                  yard=dict(stacks=[dict(key='A-001-01', boxes=['box-0', 'box-1'])], quay_buffer=[]),
                  family={}, vessel=dict(cells=[1, 2, 3, 4]), onboard=dict(placements=[]))
    low = generate.derive_payload(parent, [cargo[0], cargo[2]], 'child', .65, Classes)
    high = generate.derive_payload(parent, [cargo[0], cargo[2]], 'child-high', 1., Classes)
    assert low['yard'] == high['yard'] == parent['yard']
    assert low['containers'][1] == parent['containers'][1]
    assert low['load_list'] == ['box-0', 'box-2']
    assert low['containers'][0]['gross_mass_kg'] == 15600
    assert low['containers'][0]['weight_class'] == 'M'
    assert low['containers'][2]['gross_mass_kg'] == 2400
    assert low['containers'][0]['category'] is None
    assert parent['containers'][0]['gross_mass_kg'] == 24000
    assert low['vessel'] == parent['vessel'] and low['onboard'] == parent['onboard']
