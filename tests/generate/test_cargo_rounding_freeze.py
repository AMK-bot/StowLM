"""D66 Coverage: whole-kg synthetic VGM only after activation."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


def profile():
    p = deepcopy(load_param_file('cargo', 'feeder_export'))
    e = next(e for e in p['equipment'] if e['id'] == 'dry_20')
    p['equipment'] = [e]
    e['empty_share']['value'] = 0
    p['dg']['share_of_eligible_loaded_boxes']['value'] = 0
    p['special_stowage']['share_of_loaded_boxes']['value'] = 0
    p['rotations'][0]['pods'] = p['rotations'][0]['pods'][:1]
    return p


@pytest.mark.parametrize('enabled', [False, True])
def test_vgm_rounding_precedes_mass_key_and_weight_class(monkeypatch, enabled):
    draws = iter((14999.6, 15000.1, 15000.6, 19999.6, 15000.5, 15001.5))
    expected = [15000., 15000., 15001., 20000., 15000., 15002.] if enabled else [14999.6,15000.1,15000.6,19999.6,15000.5,15001.5]
    monkeypatch.setattr(cargo, '_loaded_gross_mass', lambda *a: next(draws))
    with rule_freeze_v1(enabled=enabled):
        boxes = cargo.generate_load_list('straits', 0, 6, seed=1729, params=profile())
    assert [b.gross_mass_kg for b in boxes] == expected
    if enabled:
        assert boxes[0].category == boxes[1].category
        assert boxes[1].category != boxes[2].category
        assert boxes[3].weight_class == 'H'
    else:
        assert boxes[3].weight_class == 'M'


@pytest.mark.parametrize('mass,tare,rating,expected', [(2100.4,2100.4,30000.0,2101.), (29999.7,2100.,29999.7,29999.)])
def test_rounding_keeps_tare_and_individual_plate_bounds(monkeypatch, mass, tare, rating, expected):
    p = profile()
    p['equipment'][0]['tare_kg']['value'] = tare
    p['equipment'][0]['max_gross_kg']['value'] = rating
    p['equipment'][0]['loaded_weight_kg']['max']['value'] = min(29000., rating)
    monkeypatch.setattr(cargo, '_loaded_gross_mass', lambda *a: mass)
    with rule_freeze_v1(enabled=True):
        box, = cargo.generate_load_list('straits', 0, 1, seed=1729, params=p)
    assert box.gross_mass_kg == expected
    assert tare <= box.gross_mass_kg <= rating


def test_no_integer_mass_inside_physical_bounds_fails_explicitly(monkeypatch):
    p = profile()
    e = p['equipment'][0]
    e['tare_kg']['value'] = 2100.2
    e['max_gross_kg']['value'] = 2100.8
    for key in ('min', 'mode', 'max'):
        e['loaded_weight_kg'][key]['value'] = 2100.5
    monkeypatch.setattr(cargo, '_loaded_gross_mass', lambda *a: 2100.5)
    with rule_freeze_v1(enabled=True), pytest.raises(cargo.UnsupportedCargoError, match='whole-kg'):
        cargo.generate_load_list('straits', 0, 1, seed=1729, params=p)
