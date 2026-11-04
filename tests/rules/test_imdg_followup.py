"""Default-profile coverage and actual checker fail-closed integration."""
from pathlib import Path
import tomllib

import pytest

from stowage.checker.rules_imdg import MISSING, STOWAGE, imdg_violations
from stowage.model.cargo import DangerousGoods, PackingForm
from stowage.model.fixtures import build_instance
from stowage.model.ship import Placement, ShipState
from stowage.rules.imdg.dgl import entries_for_un, lookup_dgl, stowage_handling_provision


def test_default_cargo_profile_has_complete_class_matched_sampling_pools():
    profile_path = Path(__file__).resolve().parents[2] / 'params/cargo/feeder_export.toml'
    profile = tomllib.loads(profile_path.read_text())
    for cls in profile['dg']['classes']:
        for un in cls['un_numbers']:
            assert any(e.hazard_class == cls['id'] for e in entries_for_un(int(un['un_number']))), un
    expected_pgs = {
        336: {None}, 1978: {None}, 1333: {'II'}, 1361: {'II', 'III'},
        1402: {'I', 'II'}, 3109: {None}, 2810: {'I', 'II', 'III'},
        3373: {None}, 2915: {None},
    }
    for un, pgs in expected_pgs.items():
        assert {e.packing_group for e in entries_for_un(un)} == pgs


def _lone_dg_case(un):
    instance = build_instance(with_dg_doc=True)
    box = instance.containers[1]
    entry = lookup_dgl(un)
    box.dg = DangerousGoods(
        un_number=f'{un:04d}', imdg_class=entry.hazard_class + (entry.compatibility_group or ''),
        subsidiary_risks=list(entry.subsidiary_risks), packing_group=entry.packing_group,
        stowage_category=entry.stowage_category,
        # Deliberately use only authentic source codes: entry guards must be
        # enforced even when the declaration/generator omits internal metadata.
        stowage_codes=list(entry.source_stowage_codes),
        segregation_codes=list(entry.segregation_codes),
        packing_form=PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS,
        ctu_closed=True, protected_from_heat=True, subsidiary_hazards_verified=True,
        verified_provisions=['SW20', 'SW21'],
    )
    for stack in instance.vessel.stacks:
        stack.dg_classes_allowed = [box.dg.imdg_class]
    # Synthetic DoC permissions isolate the shipment/provision checks below.
    for area in instance.vessel.dg_doc_areas:
        area.classes_allowed = [box.dg.imdg_class]
    ship = ShipState(vessel_id=instance.vessel.id,
                     placements=[Placement(slot='0010182', container_id=box.id)])
    return instance, ship, box


def test_fireworks_remain_fail_closed_for_unsupported_explosives_arrangements():
    # PDF590: FIREWORKS '1.4G', category '02', SW1. Class-1 categories and
    # compatible mixed stowage are in 7.1.3.1 p478 and 7.2.7.1.4 pp491-492.
    entry = lookup_dgl(336)
    assert (entry.hazard_class, entry.compatibility_group, entry.stowage_category) == ('1.4', 'G', '02')
    instance, ship, _ = _lone_dg_case(336)
    assert any(v.rule_id == STOWAGE and v.severity == 'hard' and 'Class-1 stowage' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


@pytest.mark.parametrize('verified', [[], ['SW20'], ['SW20', 'SW21']])
def test_radioactive_limits_cannot_be_cleared_by_category_or_sw_overrides(verified):
    # PDF699: UN2915 category A SW20/SW21, See SP172. Radiation-specific
    # stowage/segregation 7.1.4.5 spans PDF480-484, beyond those category codes.
    entry = lookup_dgl(2915)
    assert entry.source_stowage_codes == ('SW20', 'SW21')
    assert [p.code for p in entry.unresolved_provisions] == ['IMDG:7.1.4.5']
    guard = stowage_handling_provision('IMDG:7.1.4.5')
    assert guard.source.pdf_page == 480 and guard.source.table == '7.1.4.5'
    instance, ship, box = _lone_dg_case(2915)
    box.dg.verified_provisions = verified
    assert any(v.rule_id == MISSING and v.severity == 'hard' and 'IMDG:7.1.4.5' in v.message
               for v in imdg_violations(instance, ship, call_index=0))


def test_radioactive_guard_uses_existing_external_verification_contract():
    instance, ship, box = _lone_dg_case(2915)
    box.dg.verified_provisions.append('IMDG:7.1.4.5')
    # This represents an external certificate author, never a planner assertion.
    assert imdg_violations(instance, ship, call_index=0) == []


@pytest.mark.parametrize('field,diagnostic', [
    ('packing_form', 'packing_form'),
    ('ctu_closed', 'CTU closed/open classification'),
])
def test_radioactive_verification_does_not_clear_missing_shipment_facts(field, diagnostic):
    instance, ship, box = _lone_dg_case(2915)
    box.dg.verified_provisions.append('IMDG:7.1.4.5')
    setattr(box.dg, field, None)
    violations = imdg_violations(instance, ship, call_index=0)
    assert any(v.rule_id == MISSING and v.severity == 'hard' and diagnostic in v.message
               for v in violations)
