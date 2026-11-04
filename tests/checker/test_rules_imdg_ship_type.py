"""Use declared IMDG ship classification; legacy evidence cannot contradict it."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from stowage.checker import rules_imdg
from stowage.rules.catalogue import rule_freeze_v1
from stowage.rules.imdg.ship_type import resolve_ship_type
from tests.checker.test_rules_imdg import _case


@pytest.fixture(params=[False, True], autouse=True)
def _activation_state(request):
    """IMDG classification must resolve the same way in both staged regimes."""
    with rule_freeze_v1(enabled=request.param):
        yield


@pytest.mark.parametrize('native', ['closed_holds', 'hatchless'])
def test_native_ship_type_is_authoritative_without_legacy_override(native):
    assert resolve_ship_type(SimpleNamespace(imdg_ship_type=native), {}) == native


@pytest.mark.parametrize('native', ['closed_holds', 'hatchless'])
def test_identical_native_and_legacy_ship_types_agree(native):
    assert resolve_ship_type(SimpleNamespace(imdg_ship_type=native), {'ship_type': native}) == native


@pytest.mark.parametrize('native,legacy', [('closed_holds', 'hatchless'), ('hatchless', 'closed_holds')])
def test_conflicting_native_and_legacy_ship_types_fail_closed(native, legacy):
    assert resolve_ship_type(SimpleNamespace(imdg_ship_type=native), {'ship_type': legacy}) is None


@pytest.mark.parametrize('invalid', ['', 'ro_ro', 'closed_holds ', True, 1, [], {}])
def test_present_invalid_native_ship_type_cannot_revive_legacy_clearance(invalid):
    assert resolve_ship_type(SimpleNamespace(imdg_ship_type=invalid), {'ship_type': 'closed_holds'}) is None


@pytest.mark.parametrize('legacy', ['closed_holds', 'hatchless'])
@pytest.mark.parametrize('missing', [True, False])
def test_unknown_native_retains_explicit_legacy_classification(legacy, missing):
    vessel = SimpleNamespace() if missing else SimpleNamespace(imdg_ship_type=None)
    assert resolve_ship_type(vessel, {'ship_type': legacy}) == legacy


@pytest.mark.parametrize('legacy', [{}, {'ship_type': None}, {'ship_type': ''}, {'ship_type': 'ro_ro'},
                                  {'ship_type': True}, {'ship_type': []}, None, []])
def test_unknown_native_and_unknown_or_malformed_legacy_remain_unknown(legacy):
    assert resolve_ship_type(SimpleNamespace(imdg_ship_type=None), legacy) is None


@pytest.mark.parametrize('covers', [[], [SimpleNamespace(id='HC1')]])
def test_no_ship_type_is_inferred_from_presence_or_absence_of_covers(covers):
    vessel = SimpleNamespace(hatch_covers=covers, stacks=[SimpleNamespace(on_deck=False)])
    assert resolve_ship_type(vessel, {}) is None


def _native_instance(instance, value, *, omit=False):
    fields = dict(instance.vessel.__dict__)
    fields.pop('imdg_ship_type', None)
    if not omit:
        fields['imdg_ship_type'] = value
    # The model owner adds the native field separately. A structural proxy lets
    # this checker contract fail first without mutating the model's schema.
    return instance.model_copy(update={'vessel': SimpleNamespace(**fields)})


@pytest.mark.parametrize('native', ['closed_holds', 'hatchless'])
@pytest.mark.parametrize('far,expected', [(True, True), (False, False)])
def test_pair_geometry_consumes_native_type_without_legacy_ship_type(native, far, expected):
    instance, ship, a, b = _case(2, far=far)
    instance.rule_overrides[rules_imdg.SHIP].pop('ship_type')
    cells = {cell.id: cell for cell in instance.vessel.cells}
    instance = _native_instance(instance, native)
    assert rules_imdg._geometry(instance, a, ship.placements[0].slot, b,
        ship.placements[1].slot, 2, cells) is expected
    violations = rules_imdg.imdg_violations(instance, ship, call_index=0)
    if expected:
        assert violations == []
    else:
        assert any(v.rule_id == rules_imdg.SHIP and 'level 2' in v.message for v in violations)


@pytest.mark.parametrize('native', [None, 'ro_ro', [], 'hatchless'])
def test_unresolved_or_conflicting_native_classification_has_missing_geometry_verdict(native):
    instance, ship, a, b = _case(2, far=True)
    if native is None:
        instance.rule_overrides[rules_imdg.SHIP].pop('ship_type')
    # hatchless conflicts with the fixture's explicit closed_holds evidence.
    instance = _native_instance(instance, native)
    cells = {cell.id: cell for cell in instance.vessel.cells}
    assert rules_imdg._geometry(instance, a, ship.placements[0].slot, b,
        ship.placements[1].slot, 2, cells) is None
    assert any(v.rule_id == rules_imdg.MISSING and 'level 2' in v.message
               for v in rules_imdg.imdg_violations(instance, ship, call_index=0))


@pytest.mark.parametrize('ship_type', ['closed_holds', 'hatchless'])
@pytest.mark.parametrize('far', [False, True])
@pytest.mark.parametrize('omit', [False, True])
def test_legacy_verdicts_are_identical_with_absent_or_unknown_native_type(ship_type, far, omit):
    instance, ship, _, _ = _case(2, far=far)
    instance.rule_overrides[rules_imdg.SHIP]['ship_type'] = ship_type
    baseline = [v.model_dump() for v in rules_imdg.imdg_violations(instance, ship, call_index=0)]
    migrated = _native_instance(instance, None, omit=omit)
    assert [v.model_dump() for v in rules_imdg.imdg_violations(migrated, ship, call_index=0)] == baseline


def test_reader_does_not_modify_native_or_legacy_evidence():
    vessel = SimpleNamespace(imdg_ship_type='closed_holds')
    legacy = {'ship_type': 'closed_holds', 'slots': {'0010182': {'closed': True}}}
    before = deepcopy(legacy)
    assert resolve_ship_type(vessel, legacy) == 'closed_holds'
    assert legacy == before
    assert vessel.imdg_ship_type == 'closed_holds'
