"""Nested shipment/DoC evidence must survive the standalone report boundary."""

import pytest

from stowage.checker.full import check
from stowage.frozen import StubTaintError, require_clean
from stowage.model import Instance, MovePlan, Provenance
from stowage.model.fixtures import build_instance


@pytest.mark.parametrize("evidence", ["doc", "shipment", "additional_shipment"])
def test_nested_evidence_taints_instance_and_standalone_report(evidence):
    payload = build_instance(with_dg_doc=True).model_dump(mode="json")
    marker = {f"evidence.{evidence}": "synthetic evidence stub"}
    nested = Provenance(stubs=marker, parents=["evidence-source"]).model_dump(mode="json")
    if evidence == "doc":
        payload["vessel"]["dg_doc_areas"][0]["provenance"] = nested
    else:
        dg = next(box["dg"] for box in payload["containers"] if box["dg"] is not None)
        if evidence == "additional_shipment":
            dg["additional"] = [{**dg, "additional": []}]
            dg = dg["additional"][0]
        dg["shipment_provenance"] = nested
    assert not payload["provenance"]["stubs"]
    instance = Instance.model_validate(payload)
    plan = MovePlan(id="lineage-test", instance_id=instance.id, call_index=0)
    # Real checker: no approving/stubbed physics parts can mask this assertion.
    report = check(instance, plan)
    assert report.provenance.stubs == marker
    assert "evidence-source" in report.provenance.parents
    with pytest.raises(StubTaintError, match=f"evidence.{evidence}"):
        require_clean(report, what="standalone checker report")
    assert instance.provenance.stubs == marker
    assert Instance.model_validate_json(instance.model_dump_json()) == instance


def test_nested_provenance_merge_preserves_versions_and_handles_shared_cycles():
    source = Provenance(versions={"checker": "old"}, stubs={"evidence.test": "tiny"}, parents=["source"])
    root = {"sources": [source, source]}
    root["cycle"] = root
    merged = Provenance(versions={"checker": "current"}, created_by="test").merge_nested(root)
    assert merged.versions == {"checker": "current"}
    assert merged.input_versions == {"checker": ["old"]}
    assert merged.stubs == source.stubs
    assert merged.parents == ["source"]
    assert merged.created_by == "test"
