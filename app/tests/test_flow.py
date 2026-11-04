"""Demo jobs use the shared planner pipeline without blocking UI requests."""

from threading import Event
from types import SimpleNamespace
from time import monotonic, sleep

import pytest

from stowage.model import PlannerResult
from app.tests.report_helpers import bind_report


def wait_done(service, job_id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        job = service.job(job_id)
        if job["status"] in {"done", "error"}:
            return job
        sleep(.01)
    pytest.fail("Mock planner job failed to finish within five seconds")


def test_live_job_returns_before_pipeline_and_keeps_fresh_checker_verdict(replay_case, monkeypatch, tmp_path):
    from app.flow import DemoService, ConflictError

    instance, plan = replay_case
    entered, release = Event(), Event()
    calls = []

    def pipeline(planner, received, **kwargs):
        calls.append((planner, received, kwargs))
        entered.set()
        assert release.wait(3)
        return PlannerResult(status="ok", plan=plan, planner="solver.scalable",
                             planner_version="test", seed=0, wall_time_s=0.), None

    monkeypatch.setattr("app.flow.run_planner", pipeline)
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    try:
        job = service.start_solve(instance.id, planner="solver.scalable")
        assert job["status"] in {"queued", "running"}
        assert entered.wait(1)
        assert service.job(job["id"])["status"] == "running"
        # Reading catalog and current replays stays available during planning.
        assert service.catalog()
        with pytest.raises(ConflictError):
            service.start_solve(instance.id)
    finally:
        release.set()
    completed = wait_done(service, job["id"])
    assert completed["status"] == "done"
    assert len(calls) == 1
    assert calls[0][1] == instance
    assert calls[0][2]["time_limit_s"] > 0
    replay = service.replay(completed["plan_ids"][0])
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"


def test_live_job_exception_is_pollable_and_releases_active_slot(replay_case, monkeypatch, tmp_path):
    from app.flow import DemoService

    instance, plan = replay_case

    def broken(*args, **kwargs):
        raise RuntimeError("Synthetic planner failure")

    monkeypatch.setattr("app.flow.run_planner", broken)
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    first = service.start_solve(instance.id)
    failed = wait_done(service, first["id"])
    assert failed["status"] == "error"
    assert "Synthetic planner failure" in failed["error"]
    second = service.start_solve(instance.id)
    assert second["id"] != first["id"]
    assert wait_done(service, second["id"])["status"] == "error"


@pytest.mark.parametrize("kwargs", [
    {"planner": "stowlm"}, {"planner": "spark.zeroshot"},
    {"time_limit_s": 0}, {"time_limit_s": -1}, {"time_limit_s": float("nan")},
    {"time_limit_s": 1000000},
])
def test_live_solve_rejects_unavailable_planners_and_unbounded_inputs(replay_case, tmp_path, kwargs):
    from app.flow import DemoService

    instance, plan = replay_case
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    with pytest.raises(ValueError):
        service.start_solve(instance.id, **kwargs)


def test_live_solve_rejects_unknown_instance(replay_case, tmp_path):
    from app.flow import DemoService

    instance, plan = replay_case
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    with pytest.raises(KeyError):
        service.start_solve("unknown.instance")
    with pytest.raises(KeyError):
        service.job("missing-job")


def test_comparison_job_runs_both_planners_serially(replay_case, monkeypatch, tmp_path):
    from app.flow import DemoService

    instance, plan = replay_case
    calls = []
    monkeypatch.setattr("app.flow._make_planner", lambda identity: SimpleNamespace(name=identity))

    def pipeline(planner, received, **kwargs):
        calls.append(planner.name)
        return PlannerResult(status="ok", plan=plan, planner=planner.name,
                             planner_version="test", seed=0, wall_time_s=0.), None

    monkeypatch.setattr("app.flow.run_planner", pipeline)
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    completed = wait_done(service, service.start_solve(instance.id, compare=True)["id"])
    assert completed["status"] == "done"
    assert calls == ["solver.scalable", "baseline.twostep"]
    assert len(completed["plan_ids"]) == 2


@pytest.mark.parametrize("size", ["medium", "large", "full"])
def test_live_solve_large_inputs_are_import_only(replay_case, tmp_path, size):
    from app.flow import DemoService

    instance, plan = replay_case
    instance = instance.model_copy(update={"size_class": size})
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    with pytest.raises(ValueError, match="tiny|small"):
        service.start_solve(instance.id)


def test_imported_metadata_never_overwrites_current_checker_verdict(replay_case, tmp_path):
    from app.flow import DemoService

    instance, plan = replay_case
    service = DemoService(instance, plan, source_metadata={
        "planner": "stowlm", "departure_approved": True, "checker_status": "passed",
        "physical_replay_legal": False, "notice": "Old approval",
    }, instance_root=tmp_path, run_root=tmp_path)
    replay = service.replay(service.active_plan_id)
    assert replay["metadata"]["planner"] == "stowlm"
    assert replay["metadata"]["departure_approved"] is False
    assert replay["metadata"]["checker_status"] == "NOT VALIDATED"
    assert replay["metadata"]["physical_replay_legal"] is True
    assert replay["metadata"]["notice"] != "Old approval"


def test_register_refuses_changed_instance_using_same_id(replay_case, tmp_path):
    from app.flow import DemoService
    from app.inputs import LoadedInputs

    instance, plan = replay_case
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    changed = instance.model_copy(update={"seed": (instance.seed or 0) + 1})
    with pytest.raises(ValueError, match="same id"):
        service.register(LoadedInputs(changed, plan))


@pytest.mark.parametrize("mode", ["result_taint", "timeout", "later_plans"])
def test_live_result_shipping_restrictions_survive_fresh_replay(replay_case, monkeypatch, tmp_path, mode):
    from app.flow import DemoService
    from stowage.model import CheckReport, MoveCount, PartReport, Provenance

    instance, plan = replay_case
    candidate = PlannerResult(status="timeout" if mode == "timeout" else "ok", plan=plan,
                              planner="solver.scalable", planner_version="synthetic-test")
    if mode == "result_taint":
        candidate.provenance = Provenance(stubs={"solver.foo": "live result stub"})
    elif mode == "later_plans":
        candidate.later_plans = [plan.model_copy(update={"id": "later", "call_index": 1, "moves": []})]
    # Synthetic all-part passing evidence isolates result forwarding. It never
    # claims the replay_case's physical condition is a real approved departure.
    report = CheckReport(passed=True, count=MoveCount(), parts={
        name: PartReport(name=name) for name in
        ("moves", "rules", "stability", "strength", "visibility", "lashing", "constraints")})
    bind_report(report, instance, plan)
    monkeypatch.setattr("app.flow.run_planner", lambda *args, **kwargs: (candidate, report))
    monkeypatch.setattr("app.backend.build_checker_payload", lambda *args, **kwargs: {
        "passed": True, "report": report.model_dump(mode="json"), "violations": []})
    service = DemoService(instance, plan, instance_root=tmp_path, run_root=tmp_path)
    completed = wait_done(service, service.start_solve(instance.id)["id"])
    assert completed["status"] == "done"
    assert completed["results"][0]["passed"] is (mode != "result_taint")
    replay = service.replay(completed["plan_ids"][0])
    assert replay["checker"]["report"]["passed"] is True
    assert replay["checker"]["passed"] is (mode != "result_taint")
    assert replay["metadata"]["validation_status"] == ("NOT VALIDATED" if mode == "result_taint" else "PASS")
    assert replay["metadata"]["departure_approved"] is False
    assert completed["results"][0]["shippable"] is False
    assert replay["metadata"]["shipping_blockers"]
    assert replay["planner_result"]["status"] == candidate.status
    if mode == "result_taint":
        assert replay["metadata"]["provenance"]["stubs"] == {"solver.foo": "live result stub"}
        assert replay["metadata"]["stub_tainted"] is True
    elif mode == "later_plans":
        assert replay["planner_result"]["later_plans"] == [p.model_dump(mode="json") for p in candidate.later_plans]


def _write_instance(path, instance):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(instance.model_dump_json())


def _empty_pipeline(calls):
    def run(planner, instance, **kwargs):
        calls.append(instance.model_copy(deep=True))
        return PlannerResult(status='timeout', plan=None, planner='solver.scalable', planner_version='synthetic-test'), None
    return run


def test_discovery_marks_same_id_different_world_unavailable(replay_case, tmp_path, monkeypatch):
    from app.flow import DemoService
    instance, plan = replay_case
    conflicting = instance.model_copy(deep=True)
    conflicting.seed = (instance.seed or 0) + 1
    _write_instance(tmp_path / 'corpus/family/tiny/seed-00000001.json', conflicting)
    calls=[]
    monkeypatch.setattr('app.flow.run_planner', _empty_pipeline(calls))
    service=DemoService(instance,plan,instance_root=tmp_path/'corpus',run_root=tmp_path/'runs')
    info=next(i for i in service.catalog()['instances'] if i['id']==instance.id)
    assert info.get('conflict') is True
    assert info['live'] is False
    with pytest.raises(ValueError,match='conflict|contents|changed'):
        service.start_solve(instance.id)
    assert calls==[]


def test_lazy_instance_source_change_is_rejected_before_planning(replay_case,tmp_path,monkeypatch):
    from app.flow import DemoService
    instance,plan=replay_case
    lazy=instance.model_copy(deep=True)
    lazy.id='lazy.synthetic.instance'
    path=tmp_path/'corpus/family/tiny/seed-00000001.json'
    _write_instance(path,lazy)
    calls=[]
    monkeypatch.setattr('app.flow.run_planner',_empty_pipeline(calls))
    service=DemoService(instance,plan,instance_root=tmp_path/'corpus',run_root=tmp_path/'runs')
    lazy.seed=(lazy.seed or 0)+1
    _write_instance(path,lazy)
    with pytest.raises(ValueError,match='changed|digest'):
        service.start_solve(lazy.id)
    assert calls==[]


def test_discovered_artifacts_with_conflicting_embedded_worlds_block_live_solve(replay_case,tmp_path,monkeypatch):
    import json
    from app.flow import DemoService
    instance,plan=replay_case
    for index in [1,2]:
        world=instance.model_copy(deep=True)
        world.id='imported.same.id'
        world.seed=index
        candidate=plan.model_copy(update={'id':f'plan-{index}','instance_id':world.id})
        path=tmp_path/f'runs/run/result-{index}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'instance':world.model_dump(mode='json'),
            'result':PlannerResult(status='ok',plan=candidate,planner='solver.scalable',
                planner_version='synthetic-test').model_dump(mode='json')}))
    calls=[]
    monkeypatch.setattr('app.flow.run_planner',_empty_pipeline(calls))
    service=DemoService(instance,plan,instance_root=tmp_path/'corpus',run_root=tmp_path/'runs')
    info=next(i for i in service.catalog()['instances'] if i['id']=='imported.same.id')
    assert info.get('conflict') is True
    with pytest.raises(ValueError,match='conflict|contents|changed'):
        service.start_solve('imported.same.id')
    assert calls==[]


def test_mutation_of_cached_world_is_rejected_before_planning(replay_case,tmp_path,monkeypatch):
    from app.flow import DemoService
    instance,plan=replay_case
    calls=[]
    monkeypatch.setattr('app.flow.run_planner',_empty_pipeline(calls))
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    service._instances[instance.id].seed=(instance.seed or 0)+1
    with pytest.raises(ValueError,match='changed|digest|contents'):
        service.start_solve(instance.id)
    assert calls==[]


def test_registered_world_is_snapshot_of_callers_mutable_instance(replay_case,tmp_path,monkeypatch):
    from app.flow import DemoService
    instance,plan=replay_case
    original=instance.model_copy(deep=True)
    calls=[]
    monkeypatch.setattr('app.flow.run_planner',_empty_pipeline(calls))
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    instance.seed=(instance.seed or 0)+1
    completed=wait_done(service,service.start_solve(instance.id)['id'])
    assert completed['status']=='done'
    assert calls==[original]


def test_live_planner_catalog_uses_frozen_public_ids(replay_case,tmp_path):
    from app.flow import DemoService,_make_planner
    instance,plan=replay_case
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    ids={p['id'] for p in service.catalog()['planners'] if p['live']}
    assert ids=={'solver.scalable','solver.exact','baseline.twostep'}
    assert type(_make_planner('solver.scalable')).__name__=='ScalableSolver'
    assert type(_make_planner('baseline.twostep')).__name__=='TwoStepPlanner'


def test_default_discovery_checks_content_across_all_current_corpus_roots(replay_case,tmp_path,monkeypatch):
    from app.flow import DemoService
    instance,plan=replay_case
    roots=[tmp_path/'v2',tmp_path/'v3',tmp_path/'fix-wave']
    for index,root in enumerate(roots):
        world=instance.model_copy(deep=True)
        world.id='colliding.corpus.instance'
        world.seed=index
        _write_instance(root/'family/tiny/seed-00000001.json',world)
    monkeypatch.setattr('app.flow.instance_roots',lambda explicit:tuple(roots) if explicit is None else (explicit,))
    service=DemoService(instance,plan,run_root=tmp_path/'runs')
    info=next(i for i in service.catalog()['instances'] if i['id']=='colliding.corpus.instance')
    assert info['conflict'] is True
    assert info['live'] is False
    with pytest.raises(ValueError,match='Conflicting'):
        service.start_solve(info['id'])


def test_selected_artifact_source_change_is_rejected_before_loading(replay_case,tmp_path):
    import json
    from app.flow import DemoService
    instance,plan=replay_case
    world=instance.model_copy(deep=True)
    world.id='artifact.synthetic.world'
    candidate=plan.model_copy(update={'instance_id':world.id})
    path=tmp_path/'runs/run/result.json'
    path.parent.mkdir(parents=True)
    data={'instance':world.model_dump(mode='json'),'result':PlannerResult(status='ok',plan=candidate,
        planner='solver.scalable',planner_version='synthetic-test').model_dump(mode='json')}
    path.write_text(json.dumps(data))
    service=DemoService(instance,plan,instance_root=tmp_path/'corpus',run_root=tmp_path/'runs')
    artifact=next(p for p in service.catalog()['plans'] if p['id'].startswith('artifact-'))
    data['instance']['seed']=(world.seed or 0)+1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='changed'):
        service.replay(artifact['id'])


def test_catalog_content_digests_cannot_be_mutated_by_caller(replay_case,tmp_path):
    from app.flow import DemoService
    from app.identity import instance_content_digest
    instance,plan=replay_case
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    first=service.catalog()
    assert first['instances'][0]['content_digest']==instance_content_digest(instance)
    assert first['plans'][0]['content_digest']==instance_content_digest(instance)
    first['instances'][0]['content_digest']='tampered'
    first['planners'][0]['id']='tampered'
    second=service.catalog()
    assert second['instances'][0]['content_digest']==instance_content_digest(instance)
    assert second['planners'][0]['id']=='solver.scalable'


def test_picker_discovers_current_flat_and_nested_nonseed_instance_names(replay_case,tmp_path):
    from app.flow import DemoService
    instance,plan=replay_case
    for relative,identity in [('small-seed-1.json','current.flat.small'),
                              ('d48-d49/feeder_500-small-seed-1.json','current.nested.small'),
                              ('d50/feeder_500-tiny.json','current.nested.tiny')]:
        world=instance.model_copy(deep=True)
        world.id=identity
        _write_instance(tmp_path/'corpus'/relative,world)
    # Audit/plan/report files can contain complete embedded model-shaped data;
    # their filenames still identify evidence, not picker input artifacts.
    for suffix in ['audit','plan','report']:
        world=instance.model_copy(deep=True)
        world.id=f'not-an-instance.{suffix}'
        _write_instance(tmp_path/'corpus'/f'fake.{suffix}.json',world)
    service=DemoService(instance,plan,instance_root=tmp_path/'corpus',run_root=tmp_path/'runs')
    ids={info['id'] for info in service.catalog()['instances']}
    assert {'current.flat.small','current.nested.small','current.nested.tiny'} <= ids
    assert not any(identity.startswith('not-an-instance.') for identity in ids)


def test_no_plan_job_preserves_planner_error_and_reports_no_replay(replay_case,tmp_path,monkeypatch):
    from app.flow import DemoService
    instance,plan=replay_case
    monkeypatch.setattr('app.flow.run_planner',lambda planner,*_,**__: (
        PlannerResult(status='error',plan=None,planner=planner.name,planner_version='synthetic-test',
            message='Global stability model unavailable: production linear policy epoch is stale; requalify'),None))
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    completed=wait_done(service,service.start_solve(instance.id,compare=True)['id'])
    assert completed['status']=='done'
    assert completed['stage']=='Complete · no replayable output'
    assert completed['plan_ids']==[]
    assert [r['planner'] for r in completed['results']]==['solver.scalable','baseline.twostep']
    assert all(r['status']=='error' and not r['passed'] and not r['shippable'] for r in completed['results'])
    assert all('epoch is stale; requalify' in r['message'] for r in completed['results'])


def test_register_rejects_plan_for_different_world_or_call(replay_case,tmp_path):
    from app.flow import DemoService
    from app.inputs import LoadedInputs
    instance,plan=replay_case
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    for update in [{'instance_id':'other.world'},{'call_index':plan.call_index+1}]:
        with pytest.raises(ValueError,match='instance_id|call_index'):
            service.register(LoadedInputs(instance,plan.model_copy(update=update)))


def test_replay_response_mutation_cannot_change_cached_world_or_approval(replay_case,tmp_path):
    from copy import deepcopy
    from app.flow import DemoService
    instance,plan=replay_case
    service=DemoService(instance,plan,instance_root=tmp_path,run_root=tmp_path)
    original=service.replay(service.active_plan_id)
    expected=deepcopy(original)
    expected_comparison=service.comparison(service.active_plan_id,service.active_plan_id)
    original['instance']['seed']=(instance.seed or 0)+1
    original['plan']['instance_id']='changed.displayed.world'
    original['metadata']['departure_approved']=True
    original['metadata']['shipping_blockers']=[]
    if 'checker' in original:
        original['checker']['passed']=True
    assert service.replay(service.active_plan_id)==expected
    assert service.comparison(service.active_plan_id,service.active_plan_id)==expected_comparison


def test_discovery_can_be_disabled_for_explicit_selected_world(replay_case,tmp_path):
    from app.flow import DemoService
    from app.inputs import LoadedInputs
    instance,plan=replay_case
    conflicting=instance.model_copy(deep=True)
    conflicting.seed=(instance.seed or 0)+1
    _write_instance(tmp_path/'corpus/family/tiny/seed-00000001.json',conflicting)
    extra=instance.model_copy(deep=True)
    extra.id='undiscovered.extra.world'
    _write_instance(tmp_path/'runs/extra.json',extra)
    comparison=LoadedInputs(instance,plan.model_copy(update={'id':'selected.comparison.plan'}))
    service=DemoService(instance,plan,comparison_inputs=[comparison],instance_root=tmp_path/'corpus',
                        run_root=tmp_path/'runs',discover=False)
    catalog=service.catalog()
    assert len(catalog['instances'])==1
    assert catalog['instances'][0]['id']==instance.id
    assert catalog['instances'][0]['live'] is True
    assert not catalog['instances'][0].get('conflict')
    assert len(catalog['plans'])==2
    assert {p['label'] for p in catalog['plans']}=={plan.id,'selected.comparison.plan'}
