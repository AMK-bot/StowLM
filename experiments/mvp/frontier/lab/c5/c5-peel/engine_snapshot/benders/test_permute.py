# Copied from experiments/mvp/frontier/lab/c3/c2-benders/test_permute.py; engine package import adapted.
"""Pure grouping/objective regressions, without numerical solver imports."""
from types import SimpleNamespace as NS
from . import permute


def model():
    ids = ['a', 'b', 'named', 'dg', 'tall']
    boxes = {name: NS(length_ft=40, height_m=2.591, iso_type='42G1',
                      reefer=False, empty=False, category=None, special_stowage=[], pod='P1')
             for name in ids}
    boxes['b'].pod = 'P2'
    boxes['tall'].height_m = 2.896
    boxes['tall'].iso_type = '45G1'
    t = NS(box_index={b: k for k, b in enumerate(ids)}, is_dg=[False, False, False, True, False],
           fallback=[False]*5, heat=[False]*5, closes_stack=[False]*5,
           mass=[22000., 26000., 24000., 23000., 23000.], rank=[2]*5,
           e1_threshold=20000., e6_threshold=20000.)
    p = NS(boxes=boxes, instance=NS(plan_constraints=[NS(params={'containers': ['named']})]))
    return NS(T=t, p=p), dict(zip(ids, ['1', '2', '3', '4', '5']))


def test_mass_can_change_within_group_but_protected_ids_and_height_cannot():
    m, assignment = model()
    groups = list(permute._groups(m, assignment).values())
    assert ['a', 'b'] in groups
    assert ['named'] in groups
    assert ['dg'] in groups
    assert ['tall'] in groups


def test_optional_port_preservation_tightens_only_the_search_family():
    m, assignment = model()
    groups = list(permute._groups(m, assignment, preserve_pod=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in groups)


def test_rank_relaxation_opens_groups_without_changing_heavy_status():
    m, assignment = model()
    m.T.mass[:2] = [14000., 9000.]
    m.T.rank[:2] = [1, 0]
    fixed = list(permute._groups(m, assignment).values())
    relaxed = list(permute._groups(m, assignment, relax_rank=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in fixed)
    assert ['a', 'b'] in relaxed


def test_heavy_relaxation_opens_classes_and_implicitly_relaxes_rank():
    m, assignment = model()
    m.T.mass[:2] = [22000., 9000.]
    m.T.rank[:2] = [2, 0]
    fixed = list(permute._groups(m, assignment, relax_rank=True).values())
    relaxed = list(permute._groups(m, assignment, relax_heavy=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in fixed)
    assert ['a', 'b'] in relaxed


def test_category_relaxation_removes_encoded_port_and_rank_grouping():
    m, assignment = model()
    m.p.boxes['a'].category = 'SGSIN:42G1:H:loaded'
    m.p.boxes['b'].category = 'NLRTM:42G1:L:loaded'
    fixed = list(permute._groups(m, assignment, relax_heavy=True).values())
    relaxed = list(permute._groups(m, assignment, relax_heavy=True,
                                   relax_category=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in fixed)
    assert ['a', 'b'] in relaxed
    port_preserved = list(permute._groups(m, assignment, preserve_pod=True,
        relax_heavy=True, relax_category=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in port_preserved)


def test_heavy_relaxation_allows_empty_loaded_exchange_when_category_relaxed():
    m, assignment = model()
    m.p.boxes['a'].empty = False
    m.p.boxes['b'].empty = True
    fixed = list(permute._groups(m, assignment, relax_category=True).values())
    relaxed = list(permute._groups(m, assignment, relax_heavy=True,
                                   relax_category=True).values())
    assert all(not {'a', 'b'} <= set(ids) for ids in fixed)
    assert ['a', 'b'] in relaxed


def test_native_and_expanded_height_objectives_are_separately_reported():
    p = NS(boxes={'light': NS(gross_mass_kg=10000.), 'heavy': NS(gross_mass_kg=20000.)})
    positions = {'low': dict(z=1., visibility_z=1., x=0., y=-1.),
                 'high': dict(z=11., visibility_z=11.2, x=10., y=1.)}
    before = permute._moments(p, {'light': 'low', 'heavy': 'high'}, positions)
    after = permute._moments(p, {'light': 'high', 'heavy': 'low'}, positions)
    assert after['stability_cargo_kg_m'] < before['stability_cargo_kg_m']
    assert after['visibility_cargo_kg_m'] > after['stability_cargo_kg_m']
    assert after['mass_kg'] == before['mass_kg']
