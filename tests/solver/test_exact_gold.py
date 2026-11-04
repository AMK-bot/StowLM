"""Tiny-gold release gates using synthetic manifests and mocked solver phases.

These tests never open released test shards. Mocked checker approval tests
writer policy; the bounded rejection integration below runs the full checker.
"""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from eval.splits import canonical_spec, load_inventory, require_membership, spec_from_instance, spec_key
from stowage.actions import get_action_space
from stowage.actions.space import Place
from stowage.model import (BallastCondition, CheckReport, ConsumableTankGroup, Instance,
                           MovePlan, PartReport, PlannerResult, Provenance,
                           VoyageConditionIdentity, VoyageSpec, VoyageTankPolicy)
from stowage.model.fixtures import build_instance
from stowage.moves import count_moves
from stowage.versions import CHECKER_VERSION


@pytest.fixture(scope="module")
def driver():
    path = Path(__file__).resolve().parents[2] / "experiments/gold-plans/tiny/driver.py"
    spec = importlib.util.spec_from_file_location("exact_gold_test_driver", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def frozen_inventory():
    return load_inventory()


@lru_cache(maxsize=1)
def frozen_train_specs():
    """Trusted release metadata only; never open released validation/test shards."""
    inventory = frozen_inventory()
    return tuple(entry["spec"] for entry in inventory.manifest["entries"]
                 if entry["split"] == "train" and entry["spec"]["rung"] == "tiny"
                 and entry["spec"]["generator"] == "random_yard")


def tagged_instance():
    instance = build_instance()
    box = instance.containers[1].model_copy(update={"dg": None, "oog": None, "special_stowage": []})
    instance.containers = [box]
    instance.load_list = [box.id]
    instance.discharge_list = []
    instance.yard.stacks = [instance.yard.stacks[0].model_copy(update={
        "key": "A-001-01", "length_ft": 20, "boxes": [box.id]})]
    # Synthetic policy fixture carrying one actual allocated v2 request identity.
    # Requested auto demand remains independent of this fixture's realised box count.
    spec = frozen_train_specs()[0]
    instance.seed = spec["seed"]
    instance.family.update({key: str(spec[key]) for key in
        ("vessel", "slice", "port", "blocking_ratio", "seed", "cargo_profile", "equipment_profile")})
    instance.family.update(size=spec["rung"], requested_n_boxes="auto" if spec["n_boxes"] is None else str(spec["n_boxes"]),
        **{key: str(spec[key]).lower() for key in ("dense_20ft", "tiny_dg_enabled", "without_dg")})
    instance.provenance.created_by = "generate.random_yard"
    instance.provenance.parents.append("SYNTHETIC: writer-policy fixture, not a generated gold witness")
    # Explicit declaration for the synthetic fixture's single consumable tank.
    instance.vessel.voyage_policy = VoyageTankPolicy(
        groups=[ConsumableTankGroup(id="synthetic-fuel", kind="fuel_oil", tank_ids=["FO-1"])],
        drawdown_order=["FO-1"], provenance=Provenance(created_by="synthetic.writer_policy_fixture"))
    instance.voyage = VoyageSpec(**{phase: VoyageConditionIdentity(
        id=f"policy-gold.{phase}", phase=phase, call_index=index,
        locode=instance.rotation.calls[index].locode, environment_id=f"synthetic-{phase}",
        density_id="synthetic-seawater", water_density_kg_m3=instance.port.water_density_kg_m3)
        for index, phase in enumerate(("departure", "arrival"))})
    assert canonical_spec(spec_from_instance(instance)) == spec
    frozen_inventory().require_membership(spec_from_instance(instance), split="train")
    return Instance.model_validate(instance.model_dump())


def write_manifest(root, *, instance=None, extra_rows=(), extra_groups=None):
    instance = instance or tagged_instance()
    spec = canonical_spec(spec_from_instance(instance))
    filename = "seed-synthetic.json"
    folder = root / "random_yard/tiny"
    folder.mkdir(parents=True)
    payload = instance.model_dump_json().encode()
    (folder / filename).write_bytes(payload)
    row = dict(instance=filename, seed=instance.seed, sha256=sha256(payload).hexdigest(),
               split="train", split_spec=spec, spec_key=spec_key(spec))
    groups = {"random_yard/tiny": {"shards": [row, *extra_rows]}}
    groups.update(extra_groups or {})
    manifest = {"split_status": "assigned", "groups": groups}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return instance, row


def config(driver, tmp_path):
    return driver.Config(output=tmp_path / "output", stop_at="2030-01-01T00:00:00+00:00",
                         hard_stop_at="2030-01-01T00:10:00+00:00")


def synthetic_bound_report(instance, plan):
    """Complete bound evidence for policy tests only; no physical approval claim."""
    parents = [f"checker.{name}.sha256:" + sha256(json.dumps(
        value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        for name, value in (("instance", instance), ("plan", plan))]
    names = ["moves", "rules", "stability", "strength", "visibility", "lashing", "constraints", "voyage"]
    names += [f"condition:{identity.id}:{part}"
              for identity in (instance.voyage.departure, instance.voyage.arrival)
              for part in ("stability", "strength")]
    return CheckReport(passed=True, count=count_moves(instance, plan), shut_out=[],
        parts={name: PartReport(name=name) for name in names},
        provenance=Provenance(parents=parents, created_by="synthetic.writer_policy_oracle"))


def install_good_phases(driver, monkeypatch, *, mock_mask=True):
    calls = []
    def plan(instance, kind, cfg):
        calls.append(kind)
        space = get_action_space()
        result = space.expand(instance, [Place(instance.load_list[0], "0010182")])
        result.ballast = BallastCondition(source="planner", tank_masses_kg={})
        return PlannerResult(status="ok", plan=result, planner=f"solver.{kind}",
            planner_version="synthetic-test", metrics={"mask_verified": True, "shippable": False,
                "objective": 0., "optimality_status": "OPTIMAL", "global_screen": "on",
                "feasibility_config": {"extra": {}}})
    monkeypatch.setattr(driver, "_plan", plan)
    monkeypatch.setattr(driver, "_finalize", lambda instance, result, cfg: result)
    monkeypatch.setattr(driver, "_check", synthetic_bound_report)
    if mock_mask:
        # Deliberately synthetic admission evidence isolates persistence/policy.
        # Tests named real_mask/actual_full_checker do not install this mock.
        monkeypatch.setattr(driver, "_replay_mask", lambda *args: dict(
            verified=True, status="complete", global_screen="on", c2="on/default",
            evidence="synthetic.writer_policy_oracle"))
    # Match the loaded checker epoch; only planner phases are synthetic.
    # The driver independently checks this against migration component evidence.
    versions = {"checker": CHECKER_VERSION, "exact": "synthetic-test", "scalable": "synthetic-test"}
    monkeypatch.setattr(driver, "component_versions", lambda: versions)
    monkeypatch.setattr(driver, "source_fingerprint", lambda: {"sha256": "synthetic-sources", "sources": {}})
    def phase(function, args, limit_s):
        return function(*args), {"versions_before": versions, "versions_after": versions,
            "sources_before": driver.source_fingerprint(),
            "sources_after": driver.source_fingerprint(),
            "wall_time_s": 0., "limit_s": limit_s, "timed_out": False}
    monkeypatch.setattr(driver, "_phase", phase)
    if hasattr(driver, "_full_hull"):
        monkeypatch.setattr(driver, "_full_hull", lambda instance: True)
    return calls


def test_manifest_selection_filters_train_tiny_before_any_shard_read(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root, extra_rows=[{"split": "val", "instance": "forbidden-val.json"},
                                    {"split": "test", "instance": "forbidden-test.json"}],
        extra_groups={"random_yard/small": {"shards": [{"split": "train", "instance": "forbidden-small.json"}]}})
    original_open = Path.open
    def metadata_only(path, *args, **kwargs):
        assert path.name == "manifest.json", f"selection read an input shard: {path}"
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", metadata_only)
    records = driver.inspect_manifest(root)
    assert len(records) == 1
    assert records[0].group == "random_yard/tiny"
    assert records[0].instance == "seed-synthetic.json"


def test_synthetic_policy_fixture_has_genuine_frozen_v2_membership(driver, tmp_path):
    instance, _ = write_manifest(tmp_path)
    spec = spec_from_instance(instance)
    member = require_membership(spec, split="train")
    assert member["spec"] == canonical_spec(spec)
    record = driver.inspect_manifest(tmp_path)[0]
    assert record.split_version == "v2"
    assert record.split_entries_sha256 == "7fefd88f6afda2f185e3b252e61e831fc3fe09972c1ec82b2776b6f841f821f4"


@pytest.mark.parametrize("mismatch", ["sha", "spec", "spec_key", "path"])
def test_input_identity_mismatch_fails_closed_before_solving(driver, tmp_path, monkeypatch, mismatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    if mismatch == "sha":
        record = replace(record, input_sha256="0" * 64)
    elif mismatch in ("spec", "spec_key"):
        # A second genuine member isolates the input/spec mismatch from release admission.
        spec = dict(frozen_train_specs()[1])
        record = replace(record, spec=spec,
                         spec_key=spec_key(spec) if mismatch == "spec" else record.spec_key)
    else:
        record = replace(record, instance="../escape.json")
    with pytest.raises(ValueError):
        driver.process_one(record, config(driver, tmp_path))
    assert calls == []
    assert not list((tmp_path / "output").rglob("plans/*.json"))


def test_checked_clean_plan_accepts_explicit_tiny_g1_exception_and_structured_actions(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    instance, _ = write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    outcome = driver.process_one(record, config(driver, tmp_path))
    assert outcome["accepted"]
    assert calls == ["exact", "scalable"]
    gold = json.loads(Path(outcome["gold_file"]).read_text())
    assert gold["format"] == "tiny-gold@1.0" and gold["split"] == "train"
    assert gold["tiny_g1_exception"] is True and gold["production_g1_required"] is False
    # This is mocked evidence for writer policy, not a real mask certificate.
    assert gold["exact"]["metrics"]["mask_verified"] is True
    action_schema = f"{get_action_space().name}@{get_action_space().version}"
    assert gold["c2"] == "on/default" and gold["actions_schema"] == action_schema
    plan = MovePlan.model_validate(gold["plan"])
    report = CheckReport.model_validate(gold["check_report"])
    assert report.passed and plan.ballast is not None
    assert plan.action_space == action_schema
    assert gold["mask_verified"] and gold["shippable"]
    assert gold["exact"]["report_binding_valid"]
    assert gold["split_version"] == "v2"
    assert plan.actions and all(isinstance(action, dict) for action in plan.actions)
    space = get_action_space()
    expanded = space.expand(instance, [space.decode(action) for action in plan.actions])
    assert expanded.moves == plan.moves
    assert count_moves(instance, plan) == report.count
    assert gold["objective"] == report.count.weighted_nonproductive
    assert gold["input_sha256"] == record.input_sha256 and gold["spec_key"] == record.spec_key
    assert "sources_before" in gold["exact"]["phases"]["plan"]


@pytest.mark.parametrize("tainted", ["instance", "result", "plan", "finalized_result", "report"])
def test_stub_taint_at_every_boundary_blocks_gold(driver, tmp_path, monkeypatch, tainted):
    root = tmp_path / "inputs"
    instance = tagged_instance()
    if tainted == "instance":
        instance.provenance.stubs["synthetic.input"] = "test-only taint"
    write_manifest(root, instance=instance)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    if tainted in ("result", "plan"):
        original = driver._plan
        def plan(inst, kind, cfg):
            result = original(inst, kind, cfg)
            target = result if tainted == "result" else result.plan
            target.provenance.stubs["synthetic.boundary"] = "test-only taint"
            return result
        monkeypatch.setattr(driver, "_plan", plan)
    elif tainted == "finalized_result":
        def finalize(inst, result, cfg):
            return result.model_copy(update={"provenance": Provenance(stubs={"synthetic.finalize": "test"})})
        monkeypatch.setattr(driver, "_finalize", finalize)
    elif tainted == "report":
        original = driver._check
        def check(inst, plan):
            report = original(inst, plan)
            report.provenance.stubs["synthetic.checker"] = "test-only taint"
            return report
        monkeypatch.setattr(driver, "_check", check)
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert not outcome["accepted"]
    assert "taint" in json.dumps(outcome).lower()
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_clean_typed_placement_evidence_is_not_artifact_provenance(driver):
    from stowage.model.vessel import HeatMapSource,PlacementMapEvidence
    evidence=HeatMapSource(id='synthetic-heat',provenance=PlacementMapEvidence(
        document='Synthetic design declaration',clause='SW1',scenario_id='unit-gold-evidence'))
    driver._require_clean(evidence)


def test_declared_ballast_source_text_is_valid_evidence_not_artifact_provenance(driver):
    from stowage.model.vessel import BallastSystem
    driver._require_clean(BallastSystem(ballast_pump_m3_h=300.,heel_transfer_m3_h=150.,
        max_cranes=2,provenance='Synthetic D74 design source'))


def test_clean_typed_metadata_cannot_hide_nested_stub_or_mixed_evidence(driver):
    from stowage.frozen import StubTaintError
    from stowage.model.vessel import HeatMapSource,PlacementMapEvidence,ImdgPlacementGeometry
    instance=tagged_instance()
    instance.vessel.imdg_placement_geometry=ImdgPlacementGeometry()
    instance.vessel.imdg_placement_geometry.heat_sources.append(HeatMapSource(id='synthetic-heat',
        provenance=PlacementMapEvidence(document='Synthetic source',clause='SW1',scenario_id='unit-gold-evidence')))
    instance.vessel.voyage_policy.provenance=Provenance(stubs={'synthetic.deep':'must refuse'})
    with pytest.raises(StubTaintError):driver._require_clean(instance)
    instance.vessel.voyage_policy.provenance=Provenance(input_versions={'checker':['stale']})
    with pytest.raises(ValueError,match='mixed|unclean'):driver._require_clean(instance)


def test_bad_typed_evidence_is_rejected_and_never_silently_skipped(driver):
    from stowage.model.vessel import HeatMapSource,PlacementMapEvidence
    from pydantic import ValidationError
    metadata=HeatMapSource(id='synthetic-heat',provenance=PlacementMapEvidence(
        document='Synthetic source',clause='SW1',scenario_id='unit-gold-evidence'))
    metadata.provenance.document=''
    with pytest.raises((TypeError,ValueError,ValidationError)):driver._require_clean(metadata)


def test_stub_in_serialized_nested_evidence_is_not_dropped(driver):
    from stowage.frozen import StubTaintError
    with pytest.raises(StubTaintError):
        driver._require_clean({'nested':{'provenance':Provenance(stubs={'synthetic.serialized':'must refuse'}).model_dump()}})


def test_current_checker_report_extensions_round_trip_without_dropping_evidence(driver):
    from stowage.checker.in_port_report import BallastProgramme,InPortCheckReport,InPortReport
    from stowage.checker.lashing import LashingReport
    programme=BallastProgramme(changeover_k=1,lifts_after_k=0,pumped_mass_t=0.,abs_change_t=0.,
        pump_time_required_h=0.,pump_time_available_h=1.,pump_window_lifts=0,
        ballast_pump_m3_h=300.,t_lift_s=72.,cranes=2,gross_moves_per_hour=25.,
        heel_step_limit_t=3.,heel_max_ideal_step_t=0.)
    report=InPortCheckReport(passed=False,parts={
        'in_port':InPortReport(name='in_port',ballast_programme=programme),
        'condition:synthetic:lashing':LashingReport(name='lashing',notes=['synthetic design limitation'])})
    payload=report.model_dump(mode='json')
    parsed=driver.parse_check_report(payload)
    assert parsed.model_dump(mode='json')==payload
    assert parsed.parts['in_port'].ballast_programme==programme
    assert parsed.parts['condition:synthetic:lashing'].notes==['synthetic design limitation']
    parsed.provenance.stubs['synthetic.checker']='must refuse'
    from stowage.frozen import StubTaintError
    with pytest.raises(StubTaintError):driver._require_clean(parsed)
    payload['parts']['in_port']['unknown_evidence']='must refuse'
    with pytest.raises(ValueError):driver.parse_check_report(payload)


def test_resume_command_carries_explicit_instance_root(driver,tmp_path):
    import shlex
    root=tmp_path/'epoch instances with spaces'
    manifest=driver.write_manifest(tmp_path/'publication',instances=root)
    command=shlex.split(manifest['resume_command'].splitlines()[-1])
    assert command[command.index('--instances')+1]==str(root.resolve())
    assert '--max-workers' in command and command[command.index('--max-workers')+1]=='1'


def test_failed_full_checker_never_becomes_gold(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    monkeypatch.setattr(driver, "_check", lambda instance, plan: CheckReport(passed=False))
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert not outcome["accepted"]
    assert outcome["exact"]["checker_passed"] is False
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_checker_epoch_mismatch_fails_before_any_planner_phase(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    versions = {**driver.component_versions(), "checker": "synthetic-stale-checker"}
    monkeypatch.setattr(driver, "component_versions", lambda: versions)
    with pytest.raises(ValueError, match="checker epoch differs"):
        driver.process_one(record, config(driver, tmp_path))
    assert calls == []
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_real_mask_rejected_action_never_becomes_gold(driver, tmp_path, monkeypatch):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskedDecoder, MaskError
    from stowage.feasibility import make_feasibility_engine
    from stowage.model.cargo import SpecialStowage

    instance = tagged_instance()
    # A hull-free geometry fixture isolates the real static mask. Production
    # defaults are used: no global-screen override or fake legality oracle.
    instance.vessel.hull = None
    instance.containers[0].special_stowage = [SpecialStowage.UNDER_DECK]
    forbidden = Place(instance.load_list[0], "0010182")
    space = get_action_space()
    engine = make_feasibility_engine(instance, space)
    assert not engine.is_legal(forbidden)
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    decoder = MaskedDecoder(engine, codec)
    with pytest.raises(MaskError):
        for token in codec.encode_action(forbidden):
            decoder.advance(token)

    root = tmp_path / "inputs"
    write_manifest(root, instance=instance)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch, mock_mask=False)
    # The supplied planner lies about mask verification. Admission must replay
    # independently even when a planner and checker both report success.
    # Hull-free replay is explicitly diagnostic; the production decoder itself
    # remains unmocked and must reject this static-rule violation.
    outcome = driver.run_one(record, replace(config(driver, tmp_path), c2_off=True))
    assert not outcome["accepted"], "gold admission trusted a mask-rejected action"
    assert outcome["exact"]["report_binding_valid"]
    assert outcome["exact"]["shippable"]
    assert not outcome["exact"]["mask_verified"]
    assert outcome["exact"]["mask_replay"]["error_type"] == "MaskError"
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_actual_full_checker_physics_failure_never_becomes_gold(driver, tmp_path, monkeypatch):
    from stowage.checker import check
    from stowage.model import ShipState, VoyageConditionIdentity, VoyageSpec
    from stowage.vessel.fleet import load_vessel
    from stowage.ports.profiles import load_port

    instance = tagged_instance()
    # Unmodified production hull, tanks, operating limits and rule catalogue.
    # One 12 t box on an otherwise light feeder fails real loading physics.
    # This is rejection evidence, not a checker-approved gold witness.
    instance.vessel = load_vessel("feeder_500")
    # Declare the production port's D74 timing inputs for this synthetic
    # rejection fixture, retaining its original yard, crane count and limits.
    # The generic fixture otherwise has unknown crane/pumping windows, which
    # correctly refuses in-port execution when the rule switch is on.
    declared_crane = load_port("laem_chabang_like").quay_crane
    instance.port.quay_crane = instance.port.quay_crane.model_copy(update={
        name: getattr(declared_crane, name) for name in (
            "gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h")})
    instance.onboard = ShipState(vessel_id=instance.vessel.id)
    # Declared synthetic seawater leg; actual production policy derives arrival
    # from the empty consumable tanks, with all three voyage states checked.
    evidence = Provenance(created_by="test_exact_gold.synthetic_voyage",
        parents=["DECLARATION: synthetic next-call environment and seawater density"])
    instance.voyage = VoyageSpec(**{
        phase: VoyageConditionIdentity(
            id=f"exact-gold.{phase}", phase=phase, call_index=index,
            locode=instance.rotation.calls[index].locode,
            environment_id=f"synthetic-gold.{phase}", density_id="synthetic-seawater-1025",
            water_density_kg_m3=instance.port.water_density_kg_m3,
            provenance=evidence)
        for index, phase in enumerate(("departure", "arrival"))}, provenance=evidence)
    root = tmp_path / "inputs"
    write_manifest(root, instance=instance)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch, mock_mask=False)
    from time import monotonic
    from stowage.solver.exact_deadline import run_with_deadline
    def bounded_check(instance, plan):
        outcome = run_with_deadline(check, (instance, plan), deadline=monotonic() + 10.)
        assert not outcome.timed_out, "full checker exceeded bounded integration budget"
        return outcome.value
    monkeypatch.setattr(driver, "_check", bounded_check)
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert not outcome["accepted"]
    assert "exact" in outcome, outcome
    report = driver.parse_check_report(outcome["exact"]["report"])
    assert not report.passed
    conditions = (
        (instance.voyage.departure.id, "departure"),
        (f"{instance.voyage.departure.id}:mid_voyage", "mid_voyage"),
        (instance.voyage.arrival.id, "arrival"),
    )
    expected_conditions = {
        f"condition:{identity}:{name}" for identity, _ in conditions
        for name in ("stability", "strength", "visibility", "lashing", "constraints")}
    assert {name for name in report.parts if name.startswith("condition:")} == expected_conditions
    lashing_names = {"lashing"} | {
        f"condition:{identity}:lashing" for identity, _ in conditions}
    assert all(part.ran for name, part in report.parts.items() if name not in lashing_names), {
        name: part.error for name, part in report.parts.items() if not part.ran}
    # This light condition also exceeds D56's production lashing-design GM.
    # D57 must preserve that hard refusal at each sample and in the aggregate.
    for name in lashing_names:
        lashing = report.parts[name]
        assert not lashing.ran
        assert "GM above lashing design GM" in lashing.error
        assert any(violation.severity == "hard" for violation in lashing.violations)
    for identity, phase in conditions:
        lashing = report.parts[f"condition:{identity}:lashing"]
        assert f"[{identity} {phase}]" in lashing.error
        assert all(f"[{identity} {phase}]" in violation.message for violation in lashing.violations)
    assert any(violation.rule_id.startswith("iscode.") for violation in report.violations)
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_diagnostic_c2_off_result_is_not_gold(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    original = driver._plan
    def plan(inst, kind, cfg):
        result = original(inst, kind, cfg)
        if kind == "scalable":
            # A healthy scalable fallback may legitimately publish. This case
            # isolates rejection of exact's diagnostic candidate without one.
            return result.model_copy(update={"status": "timeout", "plan": None})
        result.metrics["global_screen"] = "off: diagnostic"
        return result
    monkeypatch.setattr(driver, "_plan", plan)
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert not outcome["accepted"]
    assert "C2" in outcome["exact"]["message"]
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_accepted_shard_resume_is_idempotent_and_detects_corrupt_output(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    cfg = config(driver, tmp_path)
    first = driver.run_one(record, cfg)
    assert first["accepted"]
    gold_file = Path(first["gold_file"])
    original_bytes = gold_file.read_bytes()
    second = driver.run_one(record, cfg)
    assert second["resumed"] and second["accepted"]
    assert calls == ["exact", "scalable"] and gold_file.read_bytes() == original_bytes
    gold_file.write_text('{"corrupted": true}')
    third = driver.run_one(record, cfg)
    assert third["accepted"] and not third.get("resumed", False)
    assert calls == ["exact", "scalable", "exact", "scalable"]
    assert json.loads(gold_file.read_text())["plan"] == json.loads(original_bytes)["plan"]
    assert third["gold_sha256"] == sha256(gold_file.read_bytes()).hexdigest()


def test_failed_attempt_resume_does_not_repeat_without_explicit_retry(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    monkeypatch.setattr(driver, "_check", lambda inst, plan: CheckReport(passed=False))
    cfg = config(driver, tmp_path)
    first = driver.run_one(record, cfg)
    assert not first["accepted"]
    second = driver.run_one(record, cfg)
    assert second["resumed"] and not second["accepted"]
    assert calls == ["exact", "scalable"]
    third = driver.run_one(record, replace(cfg, retry_failed=True))
    assert not third["accepted"]
    assert calls == ["exact", "scalable", "exact", "scalable"]


def test_phase_budget_changes_get_distinct_resume_identity(driver, tmp_path, monkeypatch):
    install_good_phases(driver, monkeypatch)
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    cfg = config(driver, tmp_path)
    baseline = driver.record_key(record, cfg)
    assert driver.record_key(record, replace(cfg, finalize_s=cfg.finalize_s + 1)) != baseline
    assert driver.record_key(record, replace(cfg, checker_s=cfg.checker_s + 1)) != baseline


def test_atomic_json_never_exposes_partial_replacement(driver, tmp_path, monkeypatch):
    target = tmp_path / "gold.json"
    driver.atomic_json(target, {"previous": True})
    original = target.read_bytes()
    replace_path = Path.replace
    def fail_replace(self, destination):
        raise OSError("synthetic interruption before commit")
    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="before commit"):
        driver.atomic_json(target, {"new": True})
    assert target.read_bytes() == original
    monkeypatch.setattr(Path, "replace", replace_path)
    driver.atomic_json(target, {"new": True})
    assert json.loads(target.read_text()) == {"new": True}
    assert not list(tmp_path.glob("*.partial"))


@pytest.mark.parametrize("boundary", ["stop", "hard_stop"])
def test_worst_phase_budget_must_fit_before_admitting_a_shard(driver, tmp_path, monkeypatch, boundary):
    from datetime import datetime, timezone

    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    cfg = config(driver, tmp_path)
    cutoff = datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    # Each search, both finalizers/checkers, and the lower-bound/cleanup reserve
    # must fit; merely being before the advertised clock stop is insufficient.
    phase_minimum = cfg.search_s + cfg.scalable_s + 2 * (cfg.finalize_s + cfg.checker_s)
    assert driver.case_budget(cfg) >= phase_minimum
    cfg = replace(cfg, stop_at="2030-01-01T00:00:00+00:00" if boundary == "stop" else "2030-01-01T01:00:00+00:00",
                  hard_stop_at="2030-01-01T00:00:00+00:00" if boundary == "hard_stop" else "2030-01-01T01:00:00+00:00")
    monkeypatch.setattr(driver, "_now", lambda: cutoff - phase_minimum + .5)
    monkeypatch.setattr(driver, "process_one", lambda *args: pytest.fail("unsafe shard was admitted"))
    outcome = driver.run_one(record, cfg)
    assert outcome["status"] == "stop_boundary" and not outcome["accepted"]
    assert not (cfg.output / "state").exists()


def test_source_hash_change_starts_a_new_resume_epoch(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    epoch = ["source-epoch-a"]
    monkeypatch.setattr(driver, "source_fingerprint", lambda: {"sha256": epoch[0], "sources": {}})
    cfg = config(driver, tmp_path)
    first_key = driver.record_key(record, cfg)
    assert driver.run_one(record, cfg)["accepted"]
    epoch[0] = "source-epoch-b"
    assert driver.record_key(record, cfg) != first_key
    outcome = driver.run_one(record, cfg)
    assert outcome["accepted"] and not outcome.get("resumed", False)
    assert calls == ["exact", "scalable", "exact", "scalable"]


def test_source_change_during_full_check_rejects_gold_and_persists_failed_attempt(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    original_phase = driver._phase
    def changed_phase(function, args, limit_s):
        value, metadata = original_phase(function, args, limit_s)
        if function is driver._check:
            metadata["sources_after"] = {"sha256": "changed-during-check", "sources": {}}
        return value, metadata
    monkeypatch.setattr(driver, "_phase", changed_phase)
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert not outcome["accepted"]
    assert "source" in json.dumps(outcome).lower()
    states = list((tmp_path / "output" / "state").glob("*.json"))
    assert len(states) == 1 and json.loads(states[0].read_text())["accepted"] is False
    assert not list((tmp_path / "output" / "plans").glob("*.json"))


def test_each_worker_is_recycled_after_one_shard(driver, tmp_path, monkeypatch):
    install_good_phases(driver, monkeypatch)
    observed = {}
    class EmptyPool:
        def __init__(self, **kwargs):
            observed.update(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(driver, "ProcessPoolExecutor", EmptyPool)
    monkeypatch.setattr(sys, "argv", ["driver", "--instances", str(tmp_path / "inputs"),
                                      "--output", str(tmp_path), "--limit", "0", "--once",
                                      "--stop-at", "2030-01-01T01:00:00+00:00",
                                      "--hard-stop-at", "2030-01-01T02:00:00+00:00"])
    driver.main()
    assert observed["max_tasks_per_child"] == 1
    assert observed["mp_context"].get_start_method() == "spawn"


def test_watcher_submits_newly_published_train_tiny_records_once(driver, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    root = tmp_path / "inputs"
    write_manifest(root)
    first = driver.inspect_manifest(root)[0]
    spec = dict(frozen_train_specs()[1])
    second = replace(first, spec=spec, spec_key=spec_key(spec), instance="published-later.json")
    install_good_phases(driver, monkeypatch)
    scans, submitted = [], []
    def inspect(path, split_manifest=None, *, allow_diagnostic=False):
        assert not allow_diagnostic
        scans.append(path)
        return ([first] if len(scans) == 1 else [first, second]), None
    class Future:
        def result(self):
            return {"accepted": False, "status": "synthetic_failure"}
    class SynchronousPool:
        def __init__(self, **kwargs):
            assert kwargs["max_tasks_per_child"] == 1
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def submit(self, function, record, cfg):
            assert function is driver.run_one
            submitted.append(record.spec_key)
            return Future()
    # Watcher scheduling consumes already validated frozen-epoch records;
    # the synthetic policy fixture is deliberately not a production freeze.
    monkeypatch.setattr(driver, "inspect_epoch_manifest", inspect)
    monkeypatch.setattr(driver, "ProcessPoolExecutor", SynchronousPool)
    monkeypatch.setattr(driver, "wait", lambda active, **kwargs: (set(active), set()))
    monkeypatch.setattr(driver, "_now", lambda: datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(sys, "argv", ["driver", "--instances", str(root), "--output", str(tmp_path / "watcher"),
        "--limit", "2", "--poll-s", "0", "--stop-at", "2030-01-01T01:00:00+00:00",
        "--hard-stop-at", "2030-01-01T02:00:00+00:00"])
    driver.main()
    assert submitted == [first.spec_key, second.spec_key]
    assert len(scans) == 2
    state = json.loads((tmp_path / "watcher" / "watcher-state.json").read_text())
    assert state["completed"] == state["submitted"] == 2


def test_timeout_incumbent_can_be_checked_gold_without_claiming_optimality(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    original = driver._plan
    def plan(inst, kind, cfg):
        result = original(inst, kind, cfg)
        if kind == "exact":
            result.status = "timeout"
            result.metrics["optimality_status"] = "TIME_LIMIT"
        return result
    def finalize(inst, result, cfg):
        assert result.status == "ok"  # Required finisher boundary normalization.
        return result
    monkeypatch.setattr(driver, "_plan", plan)
    monkeypatch.setattr(driver, "_finalize", finalize)
    outcome = driver.run_one(record, config(driver, tmp_path))
    assert outcome["accepted"] and outcome["exact"]["status"] == "timeout"
    gold = json.loads(Path(outcome["gold_file"]).read_text())
    assert gold["gold_status"] == "feasible"
    assert gold["check_report"]["passed"]


@pytest.mark.parametrize('typed_report',[False,True])
def test_gold_manifest_publishes_only_committed_checked_shards(driver, tmp_path, monkeypatch,typed_report):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    if typed_report:
        from stowage.checker.in_port_report import BallastProgramme,InPortCheckReport,InPortReport
        from stowage.checker.lashing import LashingReport
        base_check=driver._check
        def current_report(instance,plan):
            # Synthetic passing evidence tests writer policy, not physics.
            report=InPortCheckReport.model_validate(base_check(instance,plan).model_dump())
            report.parts['in_port']=InPortReport(name='in_port',ballast_programme=BallastProgramme(
                changeover_k=1,lifts_after_k=0,pumped_mass_t=0.,abs_change_t=0.,
                pump_time_required_h=0.,pump_time_available_h=1.,pump_window_lifts=0,
                ballast_pump_m3_h=300.,t_lift_s=72.,cranes=2,gross_moves_per_hour=25.,
                heel_step_limit_t=3.,heel_max_ideal_step_t=0.))
            report.parts['lashing']=LashingReport(name='lashing',notes=['synthetic design evidence'])
            return report
        monkeypatch.setattr(driver,'_check',current_report)
    cfg = config(driver, tmp_path)
    outcome = driver.run_one(record, cfg)
    assert outcome["accepted"]
    (cfg.output / "plans" / "unfinished.json.partial").write_text('{"not_complete":')
    (cfg.output / "plans" / "broken.json").write_text('{"invalid":')
    driver.atomic_json(cfg.output / "state" / "failed-synthetic.json", {"accepted": False, "status": "error"})
    manifest = driver.write_manifest(cfg.output)
    assert manifest["format"] == "tiny-gold-manifest@1.0"
    assert manifest["accepted"] == 1 and manifest["failed_attempts"] == 1
    assert len(manifest["shards"]) == 1
    shard = manifest["shards"][0]
    assert shard["sha256"] == outcome["gold_sha256"]
    assert shard["split"] == "train" and shard["checker_passed"] is True
    assert "unfinished" not in json.dumps(manifest["shards"])
    assert any(item["path"].endswith("broken.json") for item in manifest["excluded"])
    buckets={'crossing_used','direct_delivery_present','kg_tight_stacks','dg_segregation_binding'}
    assert set(outcome['rule_binding'])==buckets
    assert set(manifest['rule_binding_yield'])==buckets
    assert all(row['attempted']==2 for row in manifest['rule_binding_yield'].values())


def test_valid_json_tampering_cannot_republish_an_unchecked_gold_hash(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    cfg = config(driver, tmp_path)
    outcome = driver.run_one(record, cfg)
    gold_path = Path(outcome["gold_file"])
    payload = json.loads(gold_path.read_text())
    payload["objective"] += 100
    gold_path.write_text(json.dumps(payload))
    manifest = driver.write_manifest(cfg.output)
    assert manifest["accepted"] == 0 and manifest["shards"] == []
    assert any(item["path"].endswith(gold_path.name) for item in manifest["excluded"])
    assert all(row['published']==0 and row['published_unknown']==0 for row in manifest['rule_binding_yield'].values())


def test_accepted_resume_rechecks_input_sha_before_reusing_gold(driver, tmp_path, monkeypatch):
    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    calls = install_good_phases(driver, monkeypatch)
    cfg = config(driver, tmp_path)
    assert driver.run_one(record, cfg)["accepted"]
    input_path = root / record.group / record.instance
    input_path.write_bytes(input_path.read_bytes() + b"\n ")  # Still valid JSON; identity changed.
    outcome = driver.run_one(record, cfg)
    assert not outcome["accepted"] and not outcome.get("resumed", False)
    assert "checksum mismatch" in outcome["error"]
    assert calls == ["exact", "scalable"]
    published = json.loads((cfg.output / "manifest.json").read_text())
    assert published["accepted"] == 0


@pytest.mark.parametrize("requested,headroom,expected", [(60., 130., 60.), (60., 10., 8.),
                                                        (60., 2., 0.), (60., -1., 0.), (0., 130., 0.)])
def test_phase_limit_reserves_absolute_hard_stop_cleanup(driver, tmp_path, monkeypatch, requested, headroom, expected):
    from datetime import datetime

    cfg = config(driver, tmp_path)
    cutoff = datetime.fromisoformat(cfg.hard_stop_at).timestamp()
    monkeypatch.setattr(driver, "_now", lambda: cutoff - headroom)
    assert driver.remaining_phase_s(cfg, requested) == expected


def test_expired_active_case_starts_no_planner_or_phase(driver, tmp_path, monkeypatch):
    from datetime import datetime

    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    cfg = config(driver, tmp_path)
    cutoff = datetime.fromisoformat(cfg.hard_stop_at).timestamp()
    monkeypatch.setattr(driver, "_now", lambda: cutoff - 1.)
    monkeypatch.setattr(driver, "_plan", lambda *a: pytest.fail("planner started past absolute cutoff"))
    monkeypatch.setattr(driver, "_phase", lambda *a: pytest.fail("phase started past absolute cutoff"))
    outcome = driver.process_one(record, cfg)
    assert not outcome["accepted"]
    assert not list((cfg.output / "plans").glob("*.json"))


def test_each_active_phase_is_recapped_and_comparison_stops_at_absolute_cutoff(driver, tmp_path, monkeypatch):
    from datetime import datetime

    root = tmp_path / "inputs"
    write_manifest(root)
    record = driver.inspect_manifest(root)[0]
    install_good_phases(driver, monkeypatch)
    cfg = config(driver, tmp_path)
    cutoff = datetime.fromisoformat(cfg.hard_stop_at).timestamp()
    clock = [cutoff - 100.]
    events = []
    monkeypatch.setattr(driver, "_now", lambda: clock[0])
    original_plan, original_phase = driver._plan, driver._phase
    def plan(inst, kind, capped_config):
        assert kind == "exact", "comparison started after the checker consumed the remaining budget"
        assert 0 < capped_config.search_s <= cutoff - clock[0] - 2.
        events.append(("exact", capped_config.search_s))
        value = original_plan(inst, kind, capped_config)
        clock[0] += capped_config.search_s
        return value
    def phase(function, args, limit_s):
        assert 0 < limit_s <= cutoff - clock[0] - 2.
        assert function in (driver._finalize, driver._check), "new phase started after cutoff"
        label = "finalize" if function is driver._finalize else "checker"
        if label == "finalize":
            assert args[-1].finalize_s == limit_s
        events.append((label, limit_s))
        value, metadata = original_phase(function, args, limit_s)
        clock[0] += limit_s
        return value, metadata
    monkeypatch.setattr(driver, "_plan", plan)
    monkeypatch.setattr(driver, "_phase", phase)
    outcome = driver.process_one(record, cfg)
    assert not outcome["accepted"]
    assert outcome["exact"]["checker_passed"]
    assert outcome["exact"]["report_binding_valid"]
    assert not outcome["exact"]["mask_verified"]
    assert "production mask replay" in outcome["exact"]["message"]
    assert events == [("exact", 60.), ("finalize", 30.), ("checker", 8.)]
    assert clock[0] == cutoff - 2.
    assert not outcome["scalable"]["accepted"]
    assert outcome["lower_bound"] is None


def test_preexisting_stop_request_prevents_manifest_scan_and_submission(driver, tmp_path, monkeypatch):
    install_good_phases(driver, monkeypatch)
    stop_file = tmp_path / "STOP_REQUESTED"
    stop_file.write_text("synthetic stop request\n")
    class IdlePool:
        def __init__(self, **kwargs):
            assert kwargs["max_tasks_per_child"] == 1
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def submit(self, *args):
            pytest.fail("stopped watcher submitted new work")
    monkeypatch.setattr(driver, "ProcessPoolExecutor", IdlePool)
    monkeypatch.setattr(driver, "inspect_epoch_manifest", lambda *a, **kw: pytest.fail("stopped watcher scanned inputs"))
    monkeypatch.setattr(sys, "argv", ["driver", "--instances", str(tmp_path / "inputs"),
                                      "--output", str(tmp_path), "--stop-at", "2030-01-01T01:00:00+00:00",
                                      "--hard-stop-at", "2030-01-01T02:00:00+00:00"])
    driver.main()
    state = json.loads((tmp_path / "watcher-state.json").read_text())
    assert state["stop_requested"] and state["submitted"] == state["completed"] == 0


def test_watcher_requires_explicit_frozen_instance_root(driver, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["driver", "--output", str(tmp_path / "output"), "--once"])
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 2
    assert not (tmp_path / "output").exists()
