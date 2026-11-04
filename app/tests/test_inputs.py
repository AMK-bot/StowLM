"""Planner artifacts stay typed and never import an old checker approval."""

import pytest
import json

from app.inputs import load_inputs
from stowage.io import save_instance, save_plan, save_result
from stowage.model import PlannerResult


def test_load_explicit_moveplan(replay_case, tmp_path):
    instance, plan = replay_case
    instance_path = save_instance(instance, tmp_path / "instance.json")
    plan_path = save_plan(plan, tmp_path / "plan.json.gz")
    loaded = load_inputs(instance_path=instance_path, plan_path=plan_path)
    assert loaded.instance == instance
    assert loaded.plan == plan


def test_load_planner_result_and_run_folder(replay_case, tmp_path):
    instance, plan = replay_case
    save_instance(instance, tmp_path / "instance.json.gz")
    result = PlannerResult(status="ok", plan=plan, planner="test.any-planner",
                           planner_version="1", seed=1, wall_time_s=0.)
    save_result(result, tmp_path / "result.json")
    # An eval run can contain unrelated diagnostics, including stale approvals.
    (tmp_path / "checker.json").write_text('{"passed": true, "parts": {}}')
    loaded = load_inputs(run_folder=tmp_path, instance_path=tmp_path / "instance.json.gz")
    assert loaded.instance == instance
    assert loaded.plan == plan
    assert loaded.metadata["planner"] == result.planner


def test_run_folder_requires_choice_for_multiple_plans(replay_case, tmp_path):
    instance, plan = replay_case
    save_instance(instance, tmp_path / "instance.json")
    save_plan(plan, tmp_path / "first.json")
    other = plan.model_copy(update={"id": "another-plan"})
    save_plan(other, tmp_path / "second.json")
    with pytest.raises(ValueError, match="(?i)(multiple|entry|choose|ambiguous)"):
        load_inputs(run_folder=tmp_path)
    loaded = load_inputs(run_folder=tmp_path, entry="second.json", instance_path=tmp_path / "instance.json")
    assert loaded.plan == other


def test_planner_result_without_plan_fails_clearly(replay_case, tmp_path):
    instance, _ = replay_case
    path = save_instance(instance, tmp_path / "instance.json")
    result = PlannerResult(status="infeasible", planner="test.any-planner",
                           planner_version="1", seed=1, wall_time_s=0.)
    result_path = save_result(result, tmp_path / "result.json")
    with pytest.raises(ValueError, match="(?i)(no plan|without.*plan|plan.*none|does not contain)"):
        load_inputs(instance_path=path, plan_path=result_path)


def test_imported_plan_must_match_instance(replay_case, tmp_path):
    instance, plan = replay_case
    path = save_instance(instance, tmp_path / "instance.json")
    plan_path = save_plan(plan.model_copy(update={"instance_id": "wrong-instance"}),
                          tmp_path / "plan.json")
    with pytest.raises(ValueError, match="(?i)(match|instance)"):
        load_inputs(instance_path=path, plan_path=plan_path)


def test_run_record_supports_embedded_typed_artifacts(replay_case, tmp_path):
    instance, plan = replay_case
    result = PlannerResult(status="timeout", plan=plan, planner="custom.partial",
                           planner_version="3", seed=7, wall_time_s=0.)
    (tmp_path / "record.json").write_text(json.dumps({
        "result": result.model_dump(mode="json"),
        "instance": instance.model_dump(mode="json"),
        "check_report": {"passed": True},
    }))
    loaded = load_inputs(run_folder=tmp_path)
    assert loaded.instance == instance
    assert loaded.plan == plan
    assert loaded.metadata["planner_status"] == "timeout"
    assert "passed" not in loaded.metadata


def test_run_entry_stays_in_selected_folder(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        load_inputs(run_folder=tmp_path, entry="../outside.json")


def test_real_harness_record_resolves_summary_instance_via_catalog(replay_case, tmp_path, monkeypatch):
    """The evaluation harness saves an instance summary, not a full Instance."""
    instance, plan = replay_case
    instance_root = tmp_path / "instances-v2"
    instance_path = instance_root / "random_yard" / "small" / "seed-001.json"
    instance_path.parent.mkdir(parents=True)
    save_instance(instance, instance_path)
    monkeypatch.setattr("app.inputs.INSTANCE_ROOT", instance_root)
    run = tmp_path / "eval-runs" / "scalable"
    records = run / "instances"
    records.mkdir(parents=True)
    result = PlannerResult(status="ok", plan=plan, planner="solver.scalable",
                           planner_version="1", seed=1, wall_time_s=0.)
    import hashlib
    record = {"instance_id": instance.id, "spec_key": "small.seed-001", "rung": "small",
              "input_sha256": hashlib.sha256(instance_path.read_bytes()).hexdigest(),
              "instance": {"vessel_id": instance.vessel.id, "boxes_to_load": len(instance.load_list)},
              "result": result.model_dump(mode="json"),
              "report": {"passed": True}}
    (records / "small.seed-001.json").write_text(json.dumps(record))
    (run / "summary.json").write_text('{"method": "solver.scalable", "passed": true}')
    loaded = load_inputs(run_folder=run)
    assert loaded.instance == instance
    assert loaded.plan == plan
    assert loaded.metadata["planner"] == "solver.scalable"
    assert "passed" not in loaded.metadata


def test_result_only_taint_survives_import_with_clean_embedded_plan(replay_case, tmp_path):
    from stowage.model import Provenance

    instance, plan = replay_case
    result = PlannerResult(status="ok", plan=plan, planner="tainted.solver",
                           planner_version="1", provenance=Provenance(
                               stubs={"solver.foo": "stub"}, parents=["original-input"],
                               versions={"adapter": "test-v1"}))
    path = tmp_path / "result.json"
    # Explicit untrusted input: clean-save helpers correctly refuse stub taint.
    path.write_text(json.dumps({"schema_version": plan.schema_version,
                                **result.model_dump(mode="json")}))
    loaded = load_inputs(plan_path=path, selected_instance=instance)
    assert loaded.plan.provenance.stubs == {"solver.foo": "stub"}
    preserved = PlannerResult.model_validate(loaded.metadata["planner_result"])
    assert preserved.provenance == result.provenance
    assert "original-input" in loaded.plan.provenance.parents
    assert loaded.metadata["provenance"]["versions"]["adapter"] == "test-v1"


def test_import_preserves_later_plans_and_status(replay_case, tmp_path):
    instance, plan = replay_case
    result = PlannerResult(status="timeout", plan=plan, later_plans=[plan.model_copy(
        update={"id": "later", "call_index": 1, "moves": []})], planner="partial", planner_version="1")
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"schema_version": plan.schema_version,
                                **result.model_dump(mode="json")}))
    loaded = load_inputs(plan_path=path, selected_instance=instance)
    preserved = PlannerResult.model_validate(loaded.metadata["planner_result"])
    assert preserved.status == "timeout"
    assert preserved.later_plans == result.later_plans


def _write_record(path, instance, plan, *, binding=False, checksum=None):
    import hashlib
    record = {'instance': instance.model_dump(mode='json'),
              'result': PlannerResult(status='ok', plan=plan, planner='test', planner_version='1').model_dump(mode='json')}
    if binding:
        from stowage.checker.full import _input_bindings
        record['report'] = {'provenance': {'parents': list(_input_bindings(instance, plan))}}
    if checksum:
        record['input_sha256'] = checksum
    path.write_text(json.dumps(record))


@pytest.mark.parametrize('entry', ['a.json', 'z.json'])
def test_conflicting_same_id_embedded_worlds_never_pick_first(replay_case, tmp_path, entry):
    instance, plan = replay_case
    changed = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    _write_record(tmp_path / 'a.json', instance, plan)
    _write_record(tmp_path / 'z.json', changed, plan)
    with pytest.raises(ValueError, match='(?i)(conflict|different|ambiguous)'):
        load_inputs(run_folder=tmp_path, entry=entry)


@pytest.mark.parametrize('entry', ['a.json', 'z.json'])
def test_selected_bound_embedded_world_resolves_collision_in_both_orders(replay_case, tmp_path, entry):
    instance, plan = replay_case
    changed = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    _write_record(tmp_path / 'a.json', instance, plan, binding=True)
    _write_record(tmp_path / 'z.json', changed, plan, binding=True)
    loaded = load_inputs(run_folder=tmp_path, entry=entry)
    assert loaded.instance == (instance if entry == 'a.json' else changed)


@pytest.mark.parametrize('explicit', ['path', 'selected_instance'])
def test_explicit_world_cannot_override_selected_embedded_world(replay_case, tmp_path, explicit):
    instance, plan = replay_case
    changed = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    record = tmp_path / 'record.json'
    _write_record(record, instance, plan, binding=True)
    kwargs = {'selected_instance': changed}
    if explicit == 'path':
        kwargs = {'instance_path': save_instance(changed, tmp_path / 'explicit.json')}
    with pytest.raises(ValueError, match='(?i)(conflict|bound|different)'):
        load_inputs(plan_path=record, **kwargs)


def test_summary_only_current_v3_resolves_by_original_file_checksum(replay_case, tmp_path, monkeypatch):
    import hashlib
    instance, plan = replay_case
    experiments = tmp_path / 'experiments'
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', experiments / 'instances-v2')
    source = experiments / 'instances-v3' / 'simulated_yard' / 'small' / 'seed-00000001.json'
    source.parent.mkdir(parents=True)
    save_instance(instance, source)
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'instance_id': instance.id,
        'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'instance': {'family': {'seed': '1'}}, 'result': PlannerResult(status='ok', plan=plan,
            planner='test', planner_version='1').model_dump(mode='json')}))
    loaded = load_inputs(plan_path=record)
    assert loaded.instance == instance


def test_unbound_summary_refuses_unique_name_only_world(replay_case, tmp_path, monkeypatch):
    instance, plan = replay_case
    root = tmp_path / 'instances-v2'
    root.mkdir()
    save_instance(instance, root / 'instance.json')
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', root)
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'instance_id': instance.id, 'result': PlannerResult(
        status='ok', plan=plan, planner='test', planner_version='1').model_dump(mode='json')}))
    with pytest.raises(ValueError, match='(?i)(binding|--instance|explicit)'):
        load_inputs(plan_path=record)


def test_same_id_corpus_conflict_is_resolved_only_by_content_binding(replay_case, tmp_path, monkeypatch):
    from stowage.checker.full import _input_bindings
    instance, plan = replay_case
    experiments = tmp_path / 'experiments'
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', experiments / 'instances-v2')
    for name, world in [('instances-v2', instance), ('instances-v3', instance.model_copy(
            update={'seed': (instance.seed or 0) + 1}))]:
        root = experiments / name
        root.mkdir(parents=True)
        save_instance(world, root / 'world.json')
    expected = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'instance_id': instance.id, 'result': PlannerResult(
        status='ok', plan=plan, planner='test', planner_version='1').model_dump(mode='json'),
        'report': {'provenance': {'parents': list(_input_bindings(expected, plan))}}}))
    assert load_inputs(plan_path=record).instance == expected


def test_explicit_file_must_match_record_original_checksum(replay_case, tmp_path):
    instance, plan = replay_case
    source = save_instance(instance, tmp_path / 'instance.json')
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'input_sha256': 'f' * 64, 'result': PlannerResult(
        status='ok', plan=plan, planner='test', planner_version='1').model_dump(mode='json')}))
    with pytest.raises(ValueError, match='(?i)(checksum|sha256|binding|bound)'):
        load_inputs(instance_path=source, plan_path=record)


@pytest.mark.parametrize('corpus', ['instances-sim/fix-wave/d50', 'instances-sim/fix-wave/d48d49'])
def test_fix_wave_summary_lookup_checks_original_bytes_before_model_defaults(replay_case, tmp_path, monkeypatch, corpus):
    import hashlib
    instance, plan = replay_case
    experiments = tmp_path / 'experiments'
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', experiments / 'instances-v2')
    source = experiments / corpus / 'historical.json'
    source.parent.mkdir(parents=True)
    historical = instance.model_dump(mode='json')
    historical['schema_version'] = '1.3'
    historical.pop('voyage', None)
    historical['vessel'].pop('voyage_policy', None)
    source.write_text(json.dumps(historical, indent=3))
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'instance_id': instance.id,
        'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'result': PlannerResult(status='ok', plan=plan, planner='test',
                                planner_version='1').model_dump(mode='json')}))
    loaded = load_inputs(plan_path=record)
    assert loaded.instance.schema_version == '1.3'
    assert loaded.instance.voyage is None
    assert loaded.instance.vessel.voyage_policy is None


def test_file_checksum_and_canonical_binding_must_both_match(replay_case, tmp_path, monkeypatch):
    import hashlib
    from stowage.checker.full import _input_bindings
    instance, plan = replay_case
    changed = instance.model_copy(update={'seed': (instance.seed or 0) + 1})
    root = tmp_path / 'instances-v2'
    root.mkdir()
    source = save_instance(instance, root / 'world.json')
    monkeypatch.setattr('app.inputs.INSTANCE_ROOT', root)
    record = tmp_path / 'record.json'
    record.write_text(json.dumps({'input_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'report': {'provenance': {'parents': list(_input_bindings(changed, plan))}},
        'result': PlannerResult(status='ok', plan=plan, planner='test',
                                planner_version='1').model_dump(mode='json')}))
    with pytest.raises(ValueError, match='(?i)(checksum|binding|content)'):
        load_inputs(plan_path=record)


def test_run_scan_skips_numerical_cache_mirrors(tmp_path):
    """Start-up fix: run-local cache mirrors (81k JSON in showcase-v2-twostep) are never parsed as artifacts."""
    from app.inputs import _files
    (tmp_path / "instances/a").mkdir(parents=True)
    (tmp_path / "instances/a/result.json").write_text("{}")
    (tmp_path / "plan.json.gz").write_bytes(b"")
    for skipped in ("physics-numerical-cache/cache/linear/vessel", "timeline-cache", "instances/a/__pycache__"):
        (tmp_path / skipped).mkdir(parents=True, exist_ok=True)
        (tmp_path / skipped / "x.json").write_text("{}")
    assert [p.relative_to(tmp_path).as_posix() for p in _files(tmp_path)] == ["instances/a/result.json", "plan.json.gz"]
