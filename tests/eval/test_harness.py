"""Evaluation isolation, resumability and official provenance boundaries."""
from hashlib import sha256
import json

import pytest

from eval import harness
from eval.splits import SPLIT_POLICY, SPLITS_VERSION, InstanceSpec, canonical_spec, spec_key, split_of
from stowage.frozen import StubTaintError
from stowage.frozen import current_provenance
from stowage.io import save_instance
from stowage.model import CheckReport, MoveCount, PlannerResult, Provenance


class Diagnostic:
    name = "test-diagnostic"
    version = "1"
    deterministic = True
    sets_ballast = False

    def plan(self, instance, *, seed=0, time_limit_s=None):
        raise AssertionError("test supplies pipeline")


def campaign(tmp_path, empty_ship_instance):
    root = tmp_path / "campaign"
    shards = []
    for split in ("train", "val", "test"):
        from eval.splits import load_inventory
        entry = next(entry for entry in load_inventory().manifest["entries"]
                     if entry["split"] == split and entry["spec"]["rung"] == "tiny"
                     and entry["spec"]["generator"] == "random_yard")
        spec = InstanceSpec(**entry["spec"])
        seed = spec.seed
        instance = empty_ship_instance.model_copy(deep=True, update={"seed": seed, "size_class": "tiny",
            "id": f"test-{split}", "family": {"size": "tiny", "seed": str(seed), "vessel": spec.vessel,
            "port": spec.port, "blocking_ratio": str(spec.blocking_ratio),
            "cargo_profile": spec.cargo_profile, "requested_n_boxes": "auto" if spec.n_boxes is None else str(spec.n_boxes),
            "dense_20ft": str(spec.dense_20ft).lower(), "tiny_dg_enabled": str(spec.tiny_dg_enabled).lower(),
            "without_dg": str(spec.without_dg).lower(), "equipment_profile": spec.equipment_profile},
            "provenance": Provenance(created_by="generate.random_yard")})
        path = root / "random_yard/tiny" / f"{split}.json"
        save_instance(instance, path)
        shards.append({"instance": path.name, "sha256": sha256(path.read_bytes()).hexdigest(),
                       "split": split, "spec_key": spec_key(spec), "split_spec": canonical_spec(spec),
                       "split_policy": SPLIT_POLICY, "split_version": SPLITS_VERSION})
    (root / "manifest.json").write_text(json.dumps({"groups": {"random_yard/tiny": {"shards": shards}}}))
    return root


def test_selection_never_opens_train_and_checks_labels(tmp_path, empty_ship_instance):
    root = campaign(tmp_path, empty_ship_instance)
    (root / "random_yard/tiny/train.json").unlink()
    rows = harness.select_instances(root, split="test", rungs=["tiny"])
    assert len(rows) == 1 and rows[0]["split"] == "test"
    with pytest.raises(ValueError, match="only 1 available"):
        harness.select_instances(root, split="val", rungs=["tiny"], limits={"tiny": 2})
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    data["groups"]["random_yard/tiny"]["shards"][0]["split"] = "test"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="label|inconsistent"):
        harness.select_instances(root, split="test", rungs=["tiny"])


def test_resume_is_pinned_and_preserves_failed_denominator(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    calls = []

    def pipeline(planner, instance, **kwargs):
        calls.append(instance.id)
        return (PlannerResult(status="error", planner=planner.name, planner_version=planner.version),
                CheckReport(passed=False, count=MoveCount(relocations=2, weighted_nonproductive=2),
                            provenance=current_provenance()))

    monkeypatch.setattr(harness, "run_planner", pipeline)
    kwargs = dict(instances_dir=root, run_dir=tmp_path / "run", split="val", rungs=["tiny"])
    first = harness.run_evaluation(Diagnostic(), **kwargs)
    assert len(calls) == 1
    assert (tmp_path / "run/ROW.md").exists()
    assert harness.run_evaluation(Diagnostic(), **kwargs) == first
    assert len(calls) == 1
    records = list((tmp_path / "run/instances").glob("*.json"))
    record = json.loads(records[0].read_text())
    assert not record["shippable"] and record["fallback_needed"]
    assert record["report"]["count"]["relocations"] == 2
    with pytest.raises(ValueError, match="configuration changed"):
        harness.run_evaluation(Diagnostic(), seed=1, **kwargs)
    source = root / "random_yard/tiny/val.json"
    source.write_text(source.read_text() + " ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        harness.run_evaluation(Diagnostic(), **kwargs)


def test_official_taint_refused_and_smoke_marked(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    taint = Provenance(stubs={"checker.check": "test diagnostic"})

    def pipeline(planner, instance, **kwargs):
        return PlannerResult(status="error", planner=planner.name, planner_version=planner.version,
                             provenance=taint), CheckReport(passed=False, provenance=taint)

    monkeypatch.setattr(harness, "run_planner", pipeline)
    kwargs = dict(instances_dir=root, split="val", rungs=["tiny"])
    with pytest.raises(StubTaintError):
        harness.run_evaluation(Diagnostic(), run_dir=tmp_path / "official", **kwargs)
    summary = harness.run_evaluation(Diagnostic(), run_dir=tmp_path / "smoke", tiny_run=True, **kwargs)
    assert not summary["official"] and summary["tiny_run"]
    assert "tiny" in (tmp_path / "smoke/ROW.md").read_text().lower()


def test_worker_limits_rejected_before_work(tmp_path):
    planner = Diagnostic()
    for workers in (0, 7):
        with pytest.raises(ValueError, match="workers"):
            harness.run_evaluation(planner, workers=workers, run_dir=tmp_path)
    planner.num_workers = 4
    with pytest.raises(ValueError, match="num_workers"):
        harness.run_evaluation(planner, workers=2, run_dir=tmp_path)


def test_nested_parameters_pin_resume(tmp_path, empty_ship_instance, monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class Config:
        width: int = 2

    planner = Diagnostic()
    planner.config = Config()
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, "run_planner", lambda planner, instance, **kwargs: (
        PlannerResult(status="error", planner=planner.name, planner_version=planner.version), None))
    kwargs = dict(instances_dir=root, run_dir=tmp_path / "nested", rungs=["tiny"])
    harness.run_evaluation(planner, **kwargs)
    planner.config.width = 3
    with pytest.raises(ValueError, match="configuration changed"):
        harness.run_evaluation(planner, **kwargs)


def test_optional_batch_still_uses_pipeline(tmp_path, empty_ship_instance, monkeypatch):
    calls = []

    class BatchDiagnostic(Diagnostic):
        def plan_batch(self, instances, *, seed=0, time_limit_s=None):
            calls.append(("batch", len(instances)))
            return [PlannerResult(status="error", planner=self.name, planner_version=self.version,
                                  seed=seed, wall_time_s=0.1) for instance in instances]

    def pipeline(planner, instance, **kwargs):
        calls.append(("pipeline", instance.id))
        return planner.plan(instance, **kwargs), CheckReport(passed=False)

    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, "run_planner", pipeline)
    harness.run_evaluation(BatchDiagnostic(), instances_dir=root, run_dir=tmp_path / "batch",
                           rungs=["tiny"], batch_size=2)
    assert calls[0] == ("batch", 1) and calls[1][0] == "pipeline"
    record = json.loads(next((tmp_path / "batch/instances").glob("*.json")).read_text())
    assert "batch_generation_share_s" in record["timing"]


def test_unbounded_checker_diagnostics_are_standard_json_and_resume(tmp_path):
    from stowage.model import PartReport
    report = CheckReport(passed=False, parts={'stability': PartReport(
        name='stability', metrics={'gm_m': float('-inf')}, margins={'unbounded': float('inf')})})
    path = tmp_path / 'report.json'
    harness._write(path, report.model_dump(mode='json'))
    data = json.loads(path.read_text(), parse_constant=lambda value: pytest.fail(value))
    assert data['parts']['stability']['metrics']['gm_m'] == '-Infinity'
    assert harness._report(data).parts['stability'].metrics['gm_m'] == float('-inf')


def test_paired_runner_uses_same_frozen_selection(tmp_path, empty_ship_instance, monkeypatch):
    root = campaign(tmp_path, empty_ship_instance)
    observed = []
    def pipeline(planner, instance, **kwargs):
        observed.append((instance.id, kwargs['seed']))
        return PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None
    monkeypatch.setattr(harness, 'run_planner', pipeline)
    comparison = harness.run_paired_evaluation(
        Diagnostic(), Diagnostic(), run_dir=tmp_path / 'paired', instances_dir=root,
        rungs=['tiny'], split='val', seed=9, metric='pass_rate', n_bootstrap=10,
    )
    assert len(observed) == 2 and observed[0] == observed[1]
    allocations = json.loads((tmp_path / 'paired/first/allocations.json').read_text())
    assert comparison['n_pairs'] == len(allocations)
    assert comparison['confidence_interval'] == [0, 0]
    assert (tmp_path / 'paired/paired.json').exists()
