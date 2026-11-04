import pytest
from llm.exit.fitness import population_fitness


def row(identity, passed, moves=None):
    return dict(instance_id=identity, passed=passed, weighted_moves=moves)


def test_passes_dominate_moves_and_uses_all_member_mutual_maps():
    result = population_fitness({
        'a': [row('x',True,100),row('y',True,10),row('z',False)],
        'b': [row('x',True,1),row('y',False),row('z',False)],
        'c': [row('x',True,90),row('y',True,100),row('z',False)]})
    assert result['mutual_ids'] == ['x']
    assert result['best_adapter'] == 'c'


def test_no_mutual_passes_ties_keep_first_member():
    result = population_fitness({'a':[row('x',False)],'b':[row('x',False)]})
    assert result['best_adapter'] == 'a'
    assert result['mean_moves'] == {'a':None,'b':None}


def test_missing_paired_maps_and_invalid_measurements_fail_closed():
    with pytest.raises(ValueError):
        population_fitness({'a':[row('x',False)],'b':[row('y',False)]})
    with pytest.raises(ValueError):
        population_fitness({'a':[row('x',True,float('nan'))]})
