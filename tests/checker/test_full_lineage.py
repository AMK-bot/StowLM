"""The standalone checker report preserves input evidence and disclosures."""

import pytest

from stowage.checker.full import check
from stowage.frozen import StubTaintError, require_clean
from stowage.model import CheckReport, FixedLoad, Load, MovePlan, Provenance
from stowage.model.fixtures import build_instance


@pytest.mark.parametrize("evidence", ["doc", "shipment", "additional_shipment"])
def test_report_merges_evidence_assigned_after_instance_validation(evidence):
    instance = build_instance(with_dg_doc=True)
    instance.load_list = []
    assert not instance.provenance.tainted
    marker = {f"evidence.{evidence}": "synthetic post-validation assignment"}
    lineage = Provenance(stubs=marker, parents=["late-evidence-source"])
    if evidence == "doc":
        instance.vessel.dg_doc_areas[0].provenance = lineage
    else:
        dg = next(box.dg for box in instance.containers if box.dg is not None)
        if evidence == "additional_shipment":
            dg.additional = [dg.model_copy(deep=True, update={"additional": []})]
            dg = dg.additional[0]
        dg.shipment_provenance = lineage
    # Assignment deliberately bypasses Instance's validation-time merge.
    assert not instance.provenance.tainted
    plan = MovePlan(id="late-evidence", instance_id=instance.id, call_index=0)
    report = check(instance, plan)
    assert report.provenance.stubs == marker
    assert "late-evidence-source" in report.provenance.parents
    restored = CheckReport.model_validate_json(report.model_dump_json())
    with pytest.raises(StubTaintError, match=f"evidence.{evidence}"):
        require_clean(restored, what="standalone checker report")
    assert not instance.provenance.tainted  # Output aggregation must remain pure.


def test_full_report_json_preserves_csc_fallback_and_legacy_background_notes():
    instance = build_instance()
    box = instance.containers[0]
    box.max_gross_mass_kg = None
    instance.load_list = [box.id]
    # Legacy evidence remains readable but cannot become a securing certificate.
    instance.vessel.fixed_loads.append(FixedLoad(
        x_m=50, y_m=0, z_m=5, mass_kg=12000, kind="cargo"))
    plan = MovePlan(id="disclosures", instance_id=instance.id, call_index=0, moves=[
        Load(call_index=0, container_id=box.id, src="A-001-01-01", dst="0020182")])
    report = check(instance, plan)
    restored = CheckReport.model_validate_json(report.model_dump_json())
    notes = {key: value for key, value in restored.parts["rules"].metrics.items()
             if key.startswith("note: ")}
    assert any("plate rating unavailable; ISO 668 R fallback applied" in key for key in notes)
    assert any("background stack capacity unverified" in key for key in notes)
    assert all(value > 0 for value in notes.values())
    assert restored.parts["rules"].metrics == report.parts["rules"].metrics
    assert not any("fallback" in violation.message or "unavailable fields" in violation.message
                   for violation in restored.parts["rules"].violations)


def _bound_problem():
    instance = build_instance()
    instance.load_list = []
    plan = MovePlan(id="bound-inputs", instance_id=instance.id, call_index=0,
                    actions=[{"beta": 2, "alpha": 1}])
    return instance, plan, check(instance, plan)


def test_report_content_binding_survives_json_and_mapping_order():
    import stowage.checker as public
    import stowage.checker.full as full

    instance, plan, report = _bound_problem()
    restored = CheckReport.model_validate_json(report.model_dump_json())
    assert full.report_matches_inputs(restored, instance, plan)
    assert public.report_matches_inputs is full.report_matches_inputs
    same_plan = plan.model_copy(deep=True)
    same_plan.actions = [{"alpha": 1, "beta": 2}]
    assert full.report_matches_inputs(restored, instance, same_plan)
    assert any(parent.startswith("checker.instance.sha256:") for parent in restored.provenance.parents)
    assert any(parent.startswith("checker.plan.sha256:") for parent in restored.provenance.parents)


@pytest.mark.parametrize("changed", ["plan_moves", "ballast", "instance_objective", "nested_evidence"])
def test_report_content_binding_rejects_same_id_changed_inputs(changed):
    import stowage.checker.full as full
    from stowage.model import BallastCondition

    instance, plan, report = _bound_problem()
    original_ids = (instance.id, plan.id)
    if changed == "plan_moves":
        box = instance.containers[0]
        plan.moves = [Load(call_index=0, container_id=box.id,
                           src="A-001-01-01", dst="0020182")]
    elif changed == "ballast":
        plan.ballast = BallastCondition(tank_masses_kg={"WB-P": 1000}, source="planner")
    elif changed == "instance_objective":
        instance.objective.future_restow += 1
    else:
        dg = next(box.dg for box in instance.containers if box.dg is not None)
        dg.shipment_provenance = Provenance(parents=["new-shipment-evidence"])
    assert (instance.id, plan.id) == original_ids
    assert not full.report_matches_inputs(report, instance, plan)


@pytest.mark.parametrize("linkage", ["missing", "ambiguous_instance", "ambiguous_plan"])
def test_report_content_binding_rejects_missing_or_ambiguous_linkage(linkage):
    import stowage.checker.full as full

    instance, plan, report = _bound_problem()
    if linkage == "missing":
        report.provenance.parents = [instance.id, plan.id]
    else:
        kind = "instance" if linkage == "ambiguous_instance" else "plan"
        report.provenance.parents.append(f"checker.{kind}.sha256:" + "0" * 64)
    assert not full.report_matches_inputs(report, instance, plan)


def test_current_binding_replaces_inherited_hash_tokens_and_preserves_lineage():
    import stowage.checker.full as full

    instance = build_instance()
    instance.load_list = []
    instance.provenance.parents = ["original-vessel-evidence", "checker.instance.sha256:" + "0" * 64]
    plan = MovePlan(id="next-check", instance_id=instance.id, call_index=0,
                    provenance=Provenance(parents=["prior-plan-evidence", "checker.plan.sha256:" + "1" * 64]))
    report = check(instance, plan)
    assert full.report_matches_inputs(report, instance, plan)
    assert {"original-vessel-evidence", "prior-plan-evidence", instance.id, plan.id}.issubset(report.provenance.parents)
    for kind in ("instance", "plan"):
        assert sum(parent.startswith(f"checker.{kind}.sha256:")
                   for parent in report.provenance.parents) == 1


@pytest.mark.parametrize("changed", ["plan_id", "instance_id"])
def test_report_content_binding_rejects_swapped_identity(changed):
    import stowage.checker.full as full

    instance, plan, report = _bound_problem()
    if changed == "plan_id":
        plan.id = "different-plan"
    else:
        instance.id = "different-instance"
        plan.instance_id = instance.id
    assert not full.report_matches_inputs(report, instance, plan)


def test_unserializable_binding_input_returns_hard_report_instead_of_raising():
    import stowage.checker.full as full

    instance = build_instance()
    instance.load_list = []
    plan = MovePlan(id="unserializable-actions", instance_id=instance.id, call_index=0,
                    actions=[{"bad": object()}])
    report = check(instance, plan)
    assert not report.passed
    assert any(violation.severity == "hard" for violation in report.violations)
    assert not report.parts["constraints"].ran
    assert report.parts["constraints"].error
    assert not full.report_matches_inputs(report, instance, plan)
