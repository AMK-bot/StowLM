"""Activated audits distinguish profile expectations from sampled demand."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from stowage.generate import cargo, cargo_shipments
from stowage.generate.cargo import CargoGenerationReport, UnsupportedCargoError, generate_load_list
from stowage.model.cargo import DangerousGoods
from stowage.model.fixtures import build_instance
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


def audit_profile():
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    dry = next(row for row in params["equipment"] if row["id"] == "dry_20")
    params["equipment"] = [dry]
    dry["empty_share"]["value"] = .5
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = .5
    cls = deepcopy(next(row for row in params["dg"]["classes"] if row["id"] == "1.4"))
    cls["un_numbers"] = [{"un_number": 12, "share": {"value": 1}}]
    params["dg"]["classes"] = [cls]
    return params


def candidate(*, primary="1.4S", un="0012", mass=25000, pod="SGSIN", iso="22G1", reefer=False, oog=False):
    return dict(dg=None if primary is None else SimpleNamespace(imdg_class=primary, un_number=un),
                mass_kg=float(mass), pod=pod, iso_type=iso, reefer=reefer, oog=oog)


def emitted(fields):
    return SimpleNamespace(gross_mass_kg=fields["mass_kg"], **{key: value for key, value in fields.items() if key != "mass_kg"})


def test_actual_stages_track_candidate_identity_and_survive_failure():
    params = audit_profile()
    audit = cargo_shipments.BookingAudit(params, n_boxes=1, pods=params["rotations"][0]["pods"])
    first = candidate(primary="7", un="2915", mass=10000)
    retry = candidate(mass=25001)
    audit.record_draw(**first)
    audit.record_refusal(**first)
    audit.record_draw(**retry, is_redraw=True)
    audit.record_refusal(**retry)
    assert audit.data["requested"]["dg_by_un"] == {"2915": 1}
    assert audit.data["redrawn"]["dg_by_class"] == {"1.4S": 1}
    assert audit.data["drawn"]["n_boxes"] == 2
    assert audit.data["refused"]["n_boxes"] == 2
    assert audit.data["emitted"]["n_boxes"] == 0
    assert audit.data["refused"]["mass_summary"] == dict(count=2, mean_kg=17500.5, p95_kg=25001., count_gt_25000kg=1)


def test_expected_counts_are_fractional_and_resolve_full_primary_class():
    params = audit_profile()
    audit = cargo_shipments.BookingAudit(params, n_boxes=10, pods=params["rotations"][0]["pods"])
    expected = audit.data["demand_expectation"]
    assert expected["kind"] == "expected_fractional"
    assert expected["dg_by_class"] == {"1.4S": pytest.approx(2.5)}
    assert expected["dg_by_un"] == {"0012": pytest.approx(2.5)}
    assert expected["mass_bands"] is None
    assert audit.data["requested"]["n_boxes"] == 0
    teu_only = cargo_shipments.BookingAudit(params, n_boxes=None, pods=params["rotations"][0]["pods"])
    assert teu_only.data["demand_expectation"]["dg_by_un"] == {"0012": None}
    assert teu_only.data["demand_expectation"]["shares"]["dg_by_un"] == {"0012": pytest.approx(.25)}


def test_expected_primary_classes_keep_distinct_compatibility_groups():
    params = audit_profile()
    params["dg"]["classes"][0]["un_numbers"] = [
        {"un_number": 12, "share": {"value": 1}}, {"un_number": 336, "share": {"value": 3}},
    ]
    audit = cargo_shipments.BookingAudit(params, n_boxes=10, pods=params["rotations"][0]["pods"])
    expected = audit.data["demand_expectation"]
    assert expected["dg_by_class"] == {"1.4S": pytest.approx(.625), "1.4G": pytest.approx(1.875)}
    assert expected["dg_by_un"] == {"0012": pytest.approx(.625), "0336": pytest.approx(1.875)}
    assert expected["shares"]["configured_dg_by_class"] == {"1.4": pytest.approx(.25)}


def test_mass_boundaries_special_cargo_by_pod_and_snapshot_isolation():
    params = audit_profile()
    audit = cargo_shipments.BookingAudit(params, n_boxes=5, pods=params["rotations"][0]["pods"])
    for mass in (9999., 10000., 19999., 20000., 25000., 25001.):
        fields = candidate(primary=None, mass=mass, iso="45P3", reefer=True, oog=True)
        audit.record_draw(**fields)
        audit.record_emit(emitted(fields))
    output = audit.snapshot()
    stage = output["emitted"]
    assert stage["mass_bands"] == {"lt_10000": 1, "10000_to_lt_20000": 2, "20000_to_25000": 2, "gt_25000": 1}
    assert stage["by_pod"] == {"SGSIN": dict(n_boxes=6, reefer=6, oog=6, flat_rack=6)}
    assert stage["non_dg"] == 6
    assert stage["dg_by_class"] == {}
    stage["mass_bands"]["lt_10000"] = 999
    assert audit.data["emitted"]["mass_bands"]["lt_10000"] == 1


def test_nearest_rank_p95_matches_sorted_mass_population():
    params = audit_profile()
    audit = cargo_shipments.BookingAudit(params, n_boxes=100, pods=params["rotations"][0]["pods"])
    masses = [float(10000 + ((index * 37) % 100)) for index in range(100)]
    for count, mass in enumerate(masses, 1):
        audit.record_draw(**candidate(mass=mass))
        expected_index = (95 * count + 99) // 100 - 1
        assert audit.data["drawn"]["mass_summary"]["p95_kg"] == sorted(masses[:count])[expected_index]
    assert audit.data["drawn"]["mass_summary"]["mean_kg"] == pytest.approx(sum(masses) / len(masses))


def test_zero_dg_population_needs_no_dgl_distribution():
    params = audit_profile()
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0
    params["dg"]["classes"] = []
    audit = cargo_shipments.BookingAudit(params, n_boxes=10, pods=params["rotations"][0]["pods"])
    assert audit.data["demand_expectation"]["dg_by_class"] == {}
    assert audit.data["demand_expectation"]["dg_by_un"] == {}


@pytest.mark.parametrize("disabled_by", ["probability", "empty_equipment", "no_eligible_equipment"])
@pytest.mark.parametrize("distribution", ["empty", "zero_weights"])
def test_disabled_dg_generator_does_not_require_a_class_distribution(disabled_by, distribution):
    params = audit_profile()
    if disabled_by == "probability":
        params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0
    elif disabled_by == "empty_equipment":
        params["equipment"][0]["empty_share"]["value"] = 1
    else:
        params["dg"]["eligible_equipment_ids"] = []
    if distribution == "empty":
        params["dg"]["classes"] = []
    else:
        for row in params["dg"]["classes"]:
            row["share"]["value"] = 0
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list("straits", 0, 3, seed=51, params=params, report=report)
    assert all(box.dg is None for box in boxes)
    assert report.requested["booking_audit"]["demand_expectation"]["dg_by_class"] == {}


@pytest.mark.parametrize("enabled", [False, True])
def test_generator_integrates_audit_only_when_activated(enabled):
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=enabled):
        boxes = generate_load_list("straits", 0, 12, seed=51, report=report)
    if not enabled:
        assert "booking_audit" not in report.requested
        assert "booking_audit" not in report.realised
        return
    audit = report.requested["booking_audit"]
    assert audit["drawn"]["n_boxes"] == audit["requested"]["n_boxes"] + audit["redrawn"]["n_boxes"]
    assert audit["drawn"]["n_boxes"] == audit["refused"]["n_boxes"] + audit["emitted"]["n_boxes"]
    assert audit["emitted"]["n_boxes"] == len(boxes)
    assert report.realised["booking_audit"] == audit
    assert report.realised["booking_audit"] is not audit


def test_generation_failure_preserves_empirical_audit_without_claiming_emission():
    params = audit_profile()
    params["equipment"][0]["empty_share"]["value"] = 0
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
    original = load_param_file("cargo", "feeder_export")
    params["dg"]["classes"] = [deepcopy(next(row for row in original["dg"]["classes"] if row["id"] == "7"))]
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(UnsupportedCargoError, match="unsupported demand"):
        generate_load_list("straits", 0, 1, seed=51, params=params, report=report)
    audit = report.requested["booking_audit"]
    assert audit["requested"]["n_boxes"] == 1
    assert audit["drawn"]["n_boxes"] == audit["refused"]["n_boxes"]
    assert audit["redrawn"]["n_boxes"] == audit["drawn"]["n_boxes"] - 1
    assert audit["emitted"]["n_boxes"] == 0
    assert audit["refused"]["dg_by_class"] == {"7": audit["drawn"]["n_boxes"]}
    assert audit["drawn"]["n_boxes"] == 256
    assert report.realised == {}


def _deterministic_draws(monkeypatch, classes, masses):
    def dg(primary):
        return DangerousGoods(un_number="2915" if primary == "7" else "1263", imdg_class=primary,
                              packing_group=None if primary == "7" else "II", stowage_category="A")

    dg_draws = iter(dg(primary) for primary in classes)
    mass_draws = iter(masses)
    monkeypatch.setattr(cargo, "_dg", lambda *args, **kwargs: next(dg_draws))
    monkeypatch.setattr(cargo, "_loaded_gross_mass", lambda *args: next(mass_draws))
    params = audit_profile()
    params["equipment"][0]["empty_share"]["value"] = 0
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
    return params


def test_mixed_refusal_audit_uses_replacement_identity_and_rounded_mass(monkeypatch):
    params = _deterministic_draws(monkeypatch, ["7", "3", "7", "3"], [9999.4, 9999.6, 25000.4, 25000.6])
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list("straits", 0, 2, seed=51, params=params, report=report)
    audit = report.requested["booking_audit"]
    assert [box.gross_mass_kg for box in boxes] == [10000., 25001.]
    assert audit["requested"]["dg_by_class"] == audit["refused"]["dg_by_class"] == {"7": 2}
    assert audit["redrawn"]["dg_by_un"] == audit["emitted"]["dg_by_un"] == {"1263": 2}
    assert audit["requested"]["mass_bands"] == dict(lt_10000=1, **{"10000_to_lt_20000": 0, "20000_to_25000": 1}, gt_25000=0)
    assert audit["redrawn"]["mass_bands"] == dict(lt_10000=0, **{"10000_to_lt_20000": 1, "20000_to_25000": 0}, gt_25000=1)
    assert audit["requested"]["n_boxes"] + audit["redrawn"]["n_boxes"] == audit["drawn"]["n_boxes"]
    assert audit["drawn"]["n_boxes"] == audit["refused"]["n_boxes"] + audit["emitted"]["n_boxes"]
    for dimension in ("dg_by_un", "dg_by_class", "mass_bands"):
        for key, count in audit["drawn"][dimension].items():
            assert count == audit["requested"][dimension].get(key, 0) + audit["redrawn"][dimension].get(key, 0)
            assert count == audit["refused"][dimension].get(key, 0) + audit["emitted"][dimension].get(key, 0)


def test_failed_call_claims_no_emission_after_prior_successful_candidate(monkeypatch):
    params = _deterministic_draws(monkeypatch, ["3", "7", "7"], [12000.4, 23000.4, 27000.6])
    monkeypatch.setattr(cargo, "MAX_CONSECUTIVE_REFUSAL_REDRAWS", 2)
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(UnsupportedCargoError, match="unsupported demand"):
        generate_load_list("straits", 0, 2, seed=51, params=params, report=report)
    audit = report.requested["booking_audit"]
    assert audit["drawn"]["n_boxes"] == 3
    assert audit["requested"]["n_boxes"] == 2
    assert audit["redrawn"]["n_boxes"] == 1
    assert audit["refused"]["n_boxes"] == 2
    assert audit["emitted"]["n_boxes"] == 0
    assert audit["emitted"]["mass_summary"]["count"] == 0
    assert report.realised == {}


def test_emission_failure_resets_all_emission_counts_on_detached_snapshot():
    params = audit_profile()
    audit = cargo_shipments.BookingAudit(params, n_boxes=1, pods=params["rotations"][0]["pods"])
    fields = candidate(mass=27000, reefer=True, oog=True, iso="45P3")
    audit.record_draw(**fields)
    audit.record_emit(emitted(fields))
    detached = audit.snapshot()
    cargo_shipments.BookingAudit.mark_emission_failed(detached, "vessel lacks cells")
    assert detached["emission_failure"] == "vessel lacks cells"
    assert detached["emitted"]["n_boxes"] == detached["emitted"]["non_dg"] == 0
    assert detached["emitted"]["dg_by_class"] == detached["emitted"]["dg_by_un"] == detached["emitted"]["by_pod"] == {}
    assert all(value == 0 for value in detached["emitted"]["mass_bands"].values())
    assert detached["emitted"]["mass_summary"] == dict(count=0, mean_kg=None, p95_kg=None, count_gt_25000kg=0)
    assert detached["drawn"]["n_boxes"] == 1
    assert audit.data["emitted"]["n_boxes"] == 1


def test_onboard_vessel_failure_keeps_inventory_without_claiming_public_emission():
    vessel = build_instance().vessel
    for cell in vessel.cells:
        cell.accepts_20 = False
    params = audit_profile()
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(UnsupportedCargoError, match="no cells accepting 20-foot") as error:
        cargo.generate_onboard_cargo(vessel, "straits", 0, seed=51, n_boxes=2, params=params, report=report)
    assert report.realised["n_boxes"] == 2
    for audit in (report.requested["booking_audit"], report.realised["booking_audit"]):
        assert audit["drawn"]["n_boxes"] == 2
        assert audit["emitted"]["n_boxes"] == 0
        assert audit["emission_failure"] == str(error.value)
