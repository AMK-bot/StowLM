"""D70 audit lists retain each class's actual UN coverage, without plan claims."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from stowage.generate.cargo import CargoGenerationReport, generate_load_list
from stowage.generate.cargo_shipments import BookingAudit
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1


STAGES = ("requested", "drawn", "redrawn", "refused", "emitted")


def _audit():
    params = load_param_file("cargo", "feeder_export")
    return BookingAudit(params, n_boxes=4, pods=params["rotations"][0]["pods"])


def _candidate(primary, un):
    return dict(dg=SimpleNamespace(imdg_class=primary, un_number=un), mass_kg=17000.,
                pod="SGSIN", iso_type="22G1", reefer=False, oog=None)


def _box(candidate):
    return SimpleNamespace(gross_mass_kg=candidate["mass_kg"], **{
        key: value for key, value in candidate.items() if key != "mass_kg"
    })


@pytest.mark.parametrize("stage", STAGES)
def test_every_empirical_stage_initializes_per_class_un_lists(stage):
    assert _audit().data[stage]["uns_by_class"] == {}


def _populated_audit():
    audit = _audit()
    original = _candidate("9", "3481")
    first_retry = _candidate("9", "3480")
    second_retry = _candidate("9", "3481")
    explosive_s = _candidate("1.4S", "0012")
    explosive_g = _candidate("1.4G", "0336")
    audit.record_draw(**original)
    audit.record_refusal(**original)
    audit.record_draw(**first_retry, is_redraw=True)
    audit.record_refusal(**first_retry)
    audit.record_draw(**second_retry, is_redraw=True)
    audit.record_emit(_box(second_retry))
    audit.record_draw(**explosive_s)
    audit.record_emit(_box(explosive_s))
    audit.record_draw(**explosive_g)
    audit.record_refusal(**explosive_g)
    return audit


def test_un_lists_are_sorted_unique_per_full_primary_class_with_counts_retained():
    audit = _populated_audit().data
    assert audit["requested"]["uns_by_class"] == {"9": ["3481"], "1.4S": ["0012"], "1.4G": ["0336"]}
    assert audit["drawn"]["uns_by_class"] == {"9": ["3480", "3481"], "1.4S": ["0012"], "1.4G": ["0336"]}
    assert audit["redrawn"]["uns_by_class"] == {"9": ["3480", "3481"]}
    assert audit["refused"]["uns_by_class"] == {"9": ["3480", "3481"], "1.4G": ["0336"]}
    assert audit["emitted"]["uns_by_class"] == {"9": ["3481"], "1.4S": ["0012"]}
    assert audit["drawn"]["dg_by_class"] == {"9": 3, "1.4S": 1, "1.4G": 1}
    assert audit["drawn"]["dg_by_un"] == {"3481": 2, "3480": 1, "0012": 1, "0336": 1}


def test_non_dg_draw_does_not_invent_a_class_or_un():
    audit = _audit()
    fields = _candidate("9", "3480")
    fields["dg"] = None
    audit.record_draw(**fields)
    audit.record_emit(_box(fields))
    assert audit.data["drawn"]["uns_by_class"] == {}
    assert audit.data["emitted"]["uns_by_class"] == {}
    assert audit.data["drawn"]["non_dg"] == 1


def test_snapshot_and_failed_emission_keep_un_lists_independent():
    audit = _populated_audit()
    snapshot = audit.snapshot()
    snapshot["drawn"]["uns_by_class"]["9"].append("9999")
    assert audit.data["drawn"]["uns_by_class"]["9"] == ["3480", "3481"]
    BookingAudit.mark_emission_failed(snapshot, "synthetic wrapper rejection")
    assert snapshot["emitted"]["uns_by_class"] == {}
    assert snapshot["emission_failure"] == "synthetic wrapper rejection"
    assert audit.data["emitted"]["uns_by_class"] == {"9": ["3481"], "1.4S": ["0012"]}


def test_activated_generator_lists_actual_raw_return_uns_without_admission_claim():
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    equipment = next(row for row in params["equipment"] if row["id"] == "dry_20")
    params["equipment"] = [equipment]
    equipment["empty_share"]["value"] = 0
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 1
    cls = deepcopy(next(row for row in params["dg"]["classes"] if row["id"] == "9"))
    cls["un_numbers"] = [dict(un_number=number, share=dict(value=1)) for number in (3480, 3481)]
    params["dg"]["classes"] = [cls]
    report = CargoGenerationReport()
    with rule_freeze_v1(enabled=True):
        boxes = generate_load_list("straits", 0, 40, seed=170, params=params, report=report)
    actual_uns = sorted({box.dg.un_number for box in boxes})
    assert actual_uns == ["3480", "3481"]
    for stage in ("requested", "drawn", "emitted"):
        assert report.requested["booking_audit"][stage]["uns_by_class"] == {"9": actual_uns}
    assert report.realised["booking_audit"]["emitted"]["uns_by_class"] == {"9": actual_uns}
    assert report.realised["booking_audit"]["emitted"]["dg_by_class"] == {"9": len(boxes)}
