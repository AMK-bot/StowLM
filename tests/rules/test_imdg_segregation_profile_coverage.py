"""Every profile SG/SGG provision needs an explicit executable-support label.

A known data-table definition is distinct from an implemented assessment. In
particular a fail-closed default cannot silently classify a newly sampled code:
the code must have an intentional entry in the production support inventory.
"""
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

from stowage.rules.imdg.dgl import entries_for_un
from stowage.rules.imdg.labels import parse_hazard_label
from stowage.rules.imdg.segregation_support import (
    CLASS_SEPARATION, SEGREGATION_SUPPORT, SegregationSupport, classify_segregation_code,
)
from stowage.rules.imdg.tables import segregation_codes


ROOT = Path(__file__).resolve().parents[2]
PROFILES = ('feeder_export', 'dg_stress')
STATUSES = {'implemented_class', 'implemented_conditional', 'fail_closed'}
EXPECTED = {
    'SG6': 'implemented_class',
    'SG17': 'implemented_class',
    'SG19': 'implemented_class',
    'SG25': 'implemented_class',
    'SG26': 'implemented_class',
    'SG35': 'implemented_conditional',
    'SG36': 'implemented_conditional',
    'SG38': 'implemented_conditional',
    'SG49': 'implemented_conditional',
    'SG60': 'implemented_conditional',
    'SGG1': 'implemented_conditional',
    'SGG8': 'implemented_conditional',
    'SG53': 'fail_closed',
    'SG72': 'fail_closed',
}


def profile_entries(profile):
    params = tomllib.loads((ROOT / f'params/cargo/{profile}.toml').read_text())
    result = []
    for hazard in params['dg']['classes']:
        label = parse_hazard_label(hazard['id'])
        assert label is not None, (profile, hazard['id'])
        for declaration in hazard['un_numbers']:
            matches = [entry for entry in entries_for_un(int(declaration['un_number']))
                       if entry.hazard_class == label.division
                       and (label.compatibility_group is None
                            or entry.compatibility_group == label.compatibility_group)]
            assert matches, (profile, hazard['id'], declaration['un_number'])
            result.extend(matches)
    return result


@pytest.mark.parametrize('profile', PROFILES)
def test_every_sampled_variant_has_explicit_production_sg_support(profile):
    for entry in profile_entries(profile):
        for code in entry.segregation_codes:
            assert code in segregation_codes(), (profile, entry.id, code, 'missing source definition')
            assert code in SEGREGATION_SUPPORT, (profile, entry.id, code, 'missing explicit assessment classification')
            assessment = classify_segregation_code(code)
            assert isinstance(assessment, SegregationSupport)
            assert assessment == SEGREGATION_SUPPORT[code]
            assert assessment.code == code
            assert assessment.status in STATUSES
            assert isinstance(assessment.reason, str) and assessment.reason.strip()
            assert isinstance(assessment.required_facts, tuple) and assessment.required_facts
            assert all(isinstance(fact, str) and fact.strip() for fact in assessment.required_facts)


@pytest.mark.parametrize('code,status', EXPECTED.items())
def test_current_profile_code_assessment_is_intentional(code, status):
    assessment = classify_segregation_code(code)
    assert assessment.status == status
    assert assessment.reason.strip()
    if status == 'fail_closed':
        assert 'verified_segregation_codes' in assessment.required_facts


def test_direct_class_separation_uses_the_same_explicit_production_targets():
    assert CLASS_SEPARATION['SG17'] == frozenset({'5.1'})
    assert CLASS_SEPARATION['SG19'] == frozenset({'7'})
    assert CLASS_SEPARATION['SG25'] == frozenset({'2.1', '3'})
    for code in ('SG17', 'SG19', 'SG25'):
        assert isinstance(CLASS_SEPARATION[code], frozenset)
        assert classify_segregation_code(code).status == 'implemented_class'


@pytest.mark.parametrize('code', ['SG999', 'SGG999', '', 'unresolved'])
def test_unknown_codes_never_acquire_implemented_support_from_their_prefix(code):
    assert code not in SEGREGATION_SUPPORT
    assessment = classify_segregation_code(code)
    assert assessment.code == code
    assert assessment.status == 'fail_closed'
    assert assessment.reason.strip()
    assert assessment.required_facts


def test_profile_inventory_exercises_both_new_direct_class_provisions():
    for profile in PROFILES:
        sulphur = [entry for entry in profile_entries(profile) if entry.un_number == 1333]
        assert {entry.packing_group for entry in sulphur} == {'II'}
        assert all({'SG17', 'SG25'} <= set(entry.segregation_codes) for entry in sulphur)


_PROFILE_CODES = sorted({code for profile in PROFILES for entry in profile_entries(profile)
                         for code in entry.segregation_codes})


def _pair_only_case(code, reverse=False):
    """Synthetic UNs isolate executable SG dispatch, not DGL authorization."""
    from stowage.checker.rules_imdg import STOWAGE
    from stowage.model.cargo import DangerousGoods
    from tests.checker.test_rules_imdg import _case

    instance, ship, a, b = _case(2, far=True)
    a.dg = DangerousGoods(un_number='9998', imdg_class='4.1', stowage_category='A',
                          segregation_codes=[code])
    b.dg = DangerousGoods(un_number='9999', imdg_class='2.2', stowage_category='A')
    for box in (a, b):
        instance.rule_overrides[STOWAGE]['containers'][box.id]['verified_segregation_codes'] = []
    pair = (a, ship.placements[0].slot, b, ship.placements[1].slot)
    if reverse:
        pair = (pair[2], pair[3], pair[0], pair[1])
    return instance, pair


@pytest.mark.parametrize('code', [code for code in _PROFILE_CODES
                                 if classify_segregation_code(code).status != 'fail_closed'])
@pytest.mark.parametrize('reverse', [False, True])
def test_implemented_profile_labels_have_actual_pair_dispatch(monkeypatch, code, reverse):
    from stowage.checker import rules_imdg

    # Remove unrelated class-table requirements so an inventory label cannot
    # hide behind another rule. An unimplemented SG dispatch falls through to
    # the unverified-provision diagnostic even for this unrelated counterpart.
    monkeypatch.setattr(rules_imdg, 'general_segregation',
                        lambda *args, **kwargs: SimpleNamespace(value='0'))
    instance, pair = _pair_only_case(code, reverse)
    out = rules_imdg._pair(instance, *pair, instance.call_index,
                           {cell.id: cell for cell in instance.vessel.cells})
    assert out == [], (code, classify_segregation_code(code), out)


@pytest.mark.parametrize('code', [code for code in _PROFILE_CODES
                                 if classify_segregation_code(code).status == 'fail_closed'])
@pytest.mark.parametrize('reverse', [False, True])
def test_native_unsupported_profile_provisions_still_fail_closed(monkeypatch, code, reverse):
    from stowage.checker import rules_imdg

    monkeypatch.setattr(rules_imdg, 'general_segregation',
                        lambda *args, **kwargs: SimpleNamespace(value='0'))
    instance, pair = _pair_only_case(code, reverse)
    out = rules_imdg._pair(instance, *pair, instance.call_index,
                           {cell.id: cell for cell in instance.vessel.cells})
    assert any(v.rule_id == rules_imdg.GENERAL and code in v.message for v in out)
