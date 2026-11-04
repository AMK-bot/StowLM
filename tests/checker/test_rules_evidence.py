"""Supplied evidence stays hard; absent evidence is an explicit assumption."""

import pytest

from stowage.checker.rules import check_static_rules, placement_violations
from stowage.model import Container, Placement
from stowage.model.fixtures import build_instance
from stowage.model.vessel import FixedLoad


def _case():
    instance = build_instance()
    instance.vessel.fixed_loads = []
    box = instance.containers[1]
    box.dg = None
    box.max_gross_mass_kg = 30000  # Synthetic CSC plate rating for this test only.
    box.pod = 'THBKK'
    ship = instance.onboard.model_copy(update={'placements': [
        Placement(container_id=box.id, slot='0010102')]})
    return instance, box, ship


def _check(instance, box, ship, entry):
    if entry == 'full':
        return check_static_rules(instance, ship, call_index=0).violations
    return placement_violations(instance, instance.onboard, box, ship.placements[0].slot, call_index=0)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('construction', ['mutated', 'model_construct'])
def test_supplied_csc_rating_enforced_at_checker_entry(entry, construction):
    instance, box, ship = _case()
    if construction == 'model_construct':
        box = Container.model_construct(**{**box.model_dump(), 'gross_mass_kg': 50000})
        instance.containers[1] = box
    else:
        box.gross_mass_kg = 50000
    out = _check(instance, box, ship, entry)
    rating = [v for v in out if v.rule_id == 'solas.vi2.verified_gross_mass']
    assert len(rating) == 1
    assert rating[0].severity == 'hard'
    assert rating[0].measured == 50000 and rating[0].limit == 30000
    assert 'CSC' in rating[0].message


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('rating', [0, -1, float('nan'), float('inf'), True, '30000'])
def test_unavailable_or_invalid_csc_rating_is_hard_missing_evidence(entry, rating):
    instance, box, ship = _case()
    box.max_gross_mass_kg = rating
    out = _check(instance, box, ship, entry)
    evidence = [v for v in out if v.rule_id == 'checker.missing_vessel_data' and 'CSC' in v.message]
    assert len(evidence) == 1
    assert evidence[0].severity == 'hard' and evidence[0].containers == [box.id]


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('gross', [29999, 30000])
def test_csc_rating_includes_exact_boundary(entry, gross):
    instance, box, ship = _case()
    box.gross_mass_kg = gross
    assert not [v for v in _check(instance, box, ship, entry) if 'CSC' in v.message]


@pytest.mark.parametrize('rating', [10000])
def test_csc_safety_evidence_is_never_tolerated_for_inherited_cargo(rating):
    instance, box, ship = _case()
    instance.stage = 'B'
    instance.onboard = ship.model_copy(deep=True)
    box.max_gross_mass_kg = rating
    out = check_static_rules(instance, ship, call_index=0).violations
    evidence = [v for v in out if 'CSC' in v.message]
    assert len(evidence) == 1 and evidence[0].severity == 'hard'
    assert evidence[0].inherited


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('kind,mass,missing', [('cargo', 70000, True), ('cargo', 1, True),
                                           ('cargo', 0, False), ('other', 70000, False)])
def test_legacy_background_without_qualification_is_not_a_violation(entry, kind, mass, missing):
    instance, box, ship = _case()
    # The legacy point representation has no original stack identifier or rating.
    instance.vessel.fixed_loads = [FixedLoad(x_m=70, y_m=0, z_m=4, mass_kg=mass, kind=kind)]
    out = _check(instance, box, ship, entry)
    evidence = [v for v in out if v.rule_id == 'checker.missing_vessel_data' and 'background' in v.message]
    assert not evidence
    report = check_static_rules(instance, ship, call_index=0)
    notes = [key for key in report.metrics if key.startswith('note: ') and 'background' in key]
    assert bool(notes) is missing
    assert all('ASSUMPTION:' in key and report.metrics[key] == 1 for key in notes)


@pytest.mark.parametrize('entry', ['full', 'placement'])
@pytest.mark.parametrize('length', [20, 40, 45])
@pytest.mark.parametrize('gross', [35999, 36000, 36001])
def test_absent_plate_uses_cited_iso668_r_boundary(entry, length, gross):
    instance, box, ship = _case()
    box.max_gross_mass_kg = None
    box.length_ft = length
    box.gross_mass_kg = gross
    slot = '0010102' if length == 20 else '0020102'
    ship.placements[0].slot = slot
    out = _check(instance, box, ship, entry)
    rating = [v for v in out if v.rule_id == 'solas.vi2.verified_gross_mass']
    assert bool(rating) is (gross > 36000)
    if rating:
        assert rating[0].severity == 'hard'
        assert rating[0].limit == 36000 and rating[0].measured == gross
        assert 'ISO 668' in rating[0].message
    assert not any(v.rule_id == 'checker.missing_vessel_data' for v in out)
    report = check_static_rules(instance, ship, call_index=0)
    notes = [key for key in report.metrics if 'plate rating unavailable; ISO 668 R fallback applied' in key]
    assert len(notes) == 1 and 'ASSUMPTION:' in notes[0] and box.id in notes[0]


def test_unavailable_iso_rating_is_a_note_and_never_invented(monkeypatch):
    import stowage.checker.rules_evidence as evidence
    monkeypatch.setattr(evidence, 'ISO668_R_KG_BY_LENGTH', {})
    instance, box, ship = _case()
    box.max_gross_mass_kg = None
    box.gross_mass_kg = 50000
    report = check_static_rules(instance, ship, call_index=0)
    assert not report.violations
    assert any('certified rating unverified' in key for key in report.metrics)


def test_supplied_rating_governs_even_above_iso_fallback():
    instance, box, ship = _case()
    box.max_gross_mass_kg, box.gross_mass_kg = 40000, 38000
    report = check_static_rules(instance, ship, call_index=0)
    assert not report.violations
    assert not any('plate rating unavailable' in key for key in report.metrics)


def test_missing_rating_note_survives_full_report_json_and_inherited_stage_b():
    from stowage.model import CheckReport
    instance, box, ship = _case()
    instance.stage = 'B'
    instance.onboard = ship.model_copy(deep=True)
    box.max_gross_mass_kg = None
    part = check_static_rules(instance, ship, call_index=0)
    assert not part.violations
    report = CheckReport(passed=True, parts={'rules': part})
    restored = CheckReport.model_validate_json(report.model_dump_json())
    assert restored.parts['rules'].metrics == part.metrics
    assert any('certified rating unverified' in key for key in part.metrics)
