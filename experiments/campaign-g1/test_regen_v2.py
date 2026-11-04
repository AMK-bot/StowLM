"""The v2 allocation is immutable; admission and resumes fail closed."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def regen():
    spec = importlib.util.spec_from_file_location("g1_test_regen_v2", HERE / "regen.py")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HERE))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(HERE))
    return module


def warm_report():
    from stowage.cache_warm import PRODUCTION_SHIPS
    from stowage.vessel import SLICE_PRESETS
    return {"complete": True, "ships": [dict(ship=ship, complete=True, errors=[], warnings=[], caches=[
        dict(cache="linear", geometry=geometry, status="reused", report=dict(
            margin_policy_status="empirical_measured", margin_policy_sha256="a"*64, mandatory_exact_checker=True))
        for geometry in (*SLICE_PRESETS, "tiny_production")]) for ship in PRODUCTION_SHIPS]}


def test_v2_release_raw_allocation_and_alias_are_preserved(regen, tmp_path):
    from eval.splits import entries_sha256
    from stowage.conventions import canonical_vessel_id
    release = regen.build_release("diagnostic-v2", True, 2)
    before = copy.deepcopy(release)
    jobs = regen.make_jobs(release, tmp_path, True, 2, 200, "diagnostic-v2")
    assert release == before
    assert release["version"] == "v2"
    assert entries_sha256(release["entries"]) == release["entries_sha256"]
    assert len(jobs) == 4
    assert {job["allocated"]["spec"]["generator"] for job in jobs} == {"random_yard", "simulated"}
    assert any(job["allocated"]["spec"]["vessel"] == "feeder_1000" for job in jobs)
    for job in jobs:
        assert Path(job["root"]).name == "instances-ediagnostic-v2"
        assert job["config"]["vessel"] == canonical_vessel_id(job["allocated"]["spec"]["vessel"])
        assert job["allocated"] in release["entries"]


def test_preflight_accepts_actual_central_warmup_schema(regen, tmp_path):
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(warm_report()))
    result = regen.qualification_preflight(path)
    assert result["complete"] is True
    assert result["sha256"] == regen.sha256_file(path)


@pytest.mark.parametrize("level", ["top", "ship", "cache", "report"])
@pytest.mark.parametrize("marker,value", [
    ("diagnostic", True), ("evidence_eligible", False), ("allow_drift", True),
])
def test_production_qualification_rejects_excluded_lineage(regen, tmp_path, level, marker, value):
    report = warm_report()
    node = {"top": report, "ship": report["ships"][0],
            "cache": report["ships"][0]["caches"][0],
            "report": report["ships"][0]["caches"][0]["report"]}[level]
    node[marker] = value
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="qualification.*(diagnostic|ineligible|excluded)"):
        regen.qualification_preflight(path, {"epoch_id": "production-e", "source_sha256": "source"})


def test_diagnostic_qualification_keeps_original_lineage(regen, tmp_path):
    report = warm_report()
    report.update(diagnostic=True, evidence_eligible=False, allow_drift=True)
    report["ships"][0]["caches"][0]["report"]["diagnostic"] = True
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(report))
    result = regen.qualification_preflight(path, {"diagnostic": True})
    assert result["diagnostic"] is True
    assert result["evidence_eligible"] is False
    assert result["allow_drift"] is True
    assert result["report"] == report


def test_qualification_hashes_the_exact_validated_bytes(regen, tmp_path, monkeypatch):
    path = tmp_path / "warm.json"
    original = json.dumps(warm_report()).encode()
    path.write_bytes(original)
    def replaced_between_validation_and_hash(file):
        replacement = {**warm_report(), "diagnostic": True, "evidence_eligible": False}
        Path(file).write_text(json.dumps(replacement))
        return regen.hashlib.sha256(Path(file).read_bytes()).hexdigest()
    monkeypatch.setattr(regen, "sha256_file", replaced_between_validation_and_hash)
    qualification = regen.qualification_preflight(path)
    assert qualification["sha256"] == regen.hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("fault", ["global", "ship", "geometry", "policy", "checker", "sha"])
def test_preflight_rejects_partial_or_unqualified_cache(regen, tmp_path, fault):
    report = warm_report()
    row = report["ships"][0]
    linear = row["caches"][0]
    if fault == "global":
        report["complete"] = False
    elif fault == "ship":
        row["complete"] = False
    elif fault == "geometry":
        row["caches"].pop()
    elif fault == "policy":
        linear["report"]["margin_policy_status"] = "unqualified"
    elif fault == "checker":
        linear["report"]["mandatory_exact_checker"] = False
    else:
        linear["report"]["margin_policy_sha256"] = "x"*64
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="qualification preflight incomplete"):
        regen.qualification_preflight(path)


def test_preflight_binds_explicit_upstream_epoch(regen, tmp_path):
    path = tmp_path / "warm.json"
    path.write_text(json.dumps({**warm_report(), "source_sha256": "wrong"}))
    with pytest.raises(ValueError, match="source_sha256 differs"):
        regen.qualification_preflight(path, {"source_sha256": "right", "epoch_id": "e"})


def test_diagnostic_allow_drift_never_waives_preflight(regen, tmp_path, monkeypatch):
    monkeypatch.setattr(regen, "check_manifest", lambda *args: {"epoch_id": "diagnostic-e", "diagnostic": True})
    path = tmp_path / "warm.json"
    path.write_text(json.dumps({"complete": False}))
    def forbidden(*args, **kwargs):
        pytest.fail("generation must not start before qualification completes")
    monkeypatch.setattr(regen, "make_jobs", forbidden)
    monkeypatch.setattr(regen, "_worker", forbidden)
    assert regen.main(["--diagnostic", "--allow-drift", "--cache-warm-report", str(path)]) == 1


def test_calibration_keeps_unmeasured_rungs_unknown(regen, tmp_path):
    records = [{"job": {"config": {"generator": "random_yard", "size": "tiny", "vessel": "ship"}},
                "elapsed_s": 2., "peak_rss_gb": .2}]
    result = regen._calibration(records, tmp_path / "calibration.json", "e", True)
    assert list(result["measured"]) == ["random_yard/tiny/ship"]
    assert result["measured"]["random_yard/tiny/ship"]["mean_wall_s"] == 2.
    assert "medium" not in result["measured"]


def test_calibration_retains_separate_rung_invocations_without_double_counting(regen, tmp_path):
    path = tmp_path / "calibration.json"
    tiny = {"job": {"id": "one", "config": {"generator": "random_yard", "size": "tiny", "vessel": "ship"}},
            "elapsed_s": 2., "peak_rss_gb": .2}
    small = {"job": {"id": "two", "config": {"generator": "random_yard", "size": "small", "vessel": "ship"}},
             "elapsed_s": 5., "peak_rss_gb": .4}
    regen._calibration([tiny], path, "e", True)
    regen._calibration([small], path, "e", True)
    result = regen._calibration([tiny, small], path, "e", True)
    assert set(result["measured"]) == {"random_yard/tiny/ship", "random_yard/small/ship"}
    assert result["measured"]["random_yard/tiny/ship"]["samples"] == 1
    assert result["measured"]["random_yard/small/ship"]["mean_wall_s"] == 5.


def test_calibration_does_not_mix_source_epochs(regen, tmp_path):
    path = tmp_path / "calibration.json"
    regen._calibration([], path, "first", True)
    with pytest.raises(ValueError, match="different source epoch"):
        regen._calibration([], path, "second", True)


def test_bound_alias_copy_keeps_generator_resume_bytes_and_pins_both(regen, tmp_path, monkeypatch):
    """An interruption after decoration cannot corrupt inner producer resume."""
    from types import SimpleNamespace
    import stowage.io
    release = regen.build_release("diagnostic-v2", True, 2)
    job = next(job for job in regen.make_jobs(release, tmp_path, True, 2, 200, "diagnostic-v2")
               if job["allocated"]["spec"]["vessel"] == "feeder_1000")
    source = Path(job["root"]) / ".producer" / job["config"]["generator"] / "tiny" / "seed.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"schema_version": "1.0", "family": {"vessel": job["config"]["vessel"]}}))
    source_audit = source.with_suffix(".audit.json")
    source_audit.write_text(json.dumps({"status": "accepted", "instance_sha256": regen.sha256_file(source)}))
    original, original_audit = source.read_bytes(), source_audit.read_bytes()
    class FakeInstance:
        def __init__(self, family):
            self.family = family
        def model_copy(self, *, update):
            return FakeInstance(update["family"])
    from stowage.model import Instance
    monkeypatch.setattr(Instance, "model_validate", lambda payload: FakeInstance(payload["family"]))
    monkeypatch.setattr(stowage.io, "save_instance", lambda instance, path: Path(path).write_text(json.dumps({"family": instance.family})))
    producer = dict(path=str(source), audit=json.loads(source_audit.read_text()), status="generated")
    result, raw_outputs = regen._publish_bound(job, producer)
    assert source.read_bytes() == original
    assert source_audit.read_bytes() == original_audit
    published = json.loads(Path(result["path"]).read_text())
    assert published["family"] == {"vessel": job["config"]["vessel"], "split_vessel": "feeder_1000"}
    # Replaying after an interruption reuses untouched producer bytes and yields
    # identical consumer bytes, before the outer marker has been committed.
    repeated, repeated_raw = regen._publish_bound(job, producer)
    assert repeated == result and repeated_raw == raw_outputs
    marker = tmp_path / "done.json"
    regen._commit(dict(job=job, result=result, producer_outputs=raw_outputs), marker, "diagnostic-v2", True)
    assert regen.marker_valid(marker, job, "diagnostic-v2", True)
    source.write_text("edited producer")
    with pytest.raises(ValueError, match="done-marker output missing or edited"):
        regen.marker_valid(marker, job, "diagnostic-v2", True)


def test_tranche_job_identity_stays_stable_when_target_extends(regen, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    from tranches import select_entries
    release = regen.build_release("e")
    first = select_entries(release, {"tiny": 2}, 0, True)
    second = select_entries(release, {"tiny": 3, "small": 1}, 0, True)
    a = regen.make_jobs(release, tmp_path, True, 2, 1, "e", entries=first)
    b = regen.make_jobs(release, tmp_path, True, 2, 1, "e", entries=second)
    assert {row["id"] for row in a} <= {row["id"] for row in b}
    original = regen.bind_tranche(tmp_path, "first", a, train_per_rung={"tiny": 2})
    assert regen.bind_tranche(tmp_path, "first", a, train_per_rung={"tiny": 2}) == original
    with pytest.raises(ValueError, match="configuration differs"):
        regen.bind_tranche(tmp_path, "first", b, train_per_rung={"tiny": 3})


def test_cli_resumes_and_extends_epoch_without_retrying_rejections(regen, tmp_path, monkeypatch, capsys):
    """No solver jobs: deterministic rejection stubs exercise orchestration only."""
    monkeypatch.syspath_prepend(str(HERE))
    monkeypatch.setattr(regen, "HERE", tmp_path)
    frozen = dict(epoch_id="diagnostic-tranche", diagnostic=True, label="diagnostic, not evidence",
                  source_sha256="source")
    monkeypatch.setattr(regen, "check_manifest", lambda *args: frozen)
    warm = tmp_path / "warm.json"
    warm.write_text(json.dumps(warm_report()))
    called = []
    def worker(job):
        called.append(job["id"])
        path = Path(job["root"]) / job["config"]["generator"] / job["config"]["size"] / f"{job['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "rejected"}))
        return dict(job=job, result=dict(path=str(path), audit={"status": "rejected"}), elapsed_s=2., peak_rss_gb=.1)
    monkeypatch.setattr(regen, "_worker", worker)
    args = ["--diagnostic", "--allow-drift", "--cache-warm-report", str(warm),
            "--diagnostic-eval-per-rung", "0", "--max-attempts", "1"]
    first = [*args, "--tranche", "first", "--train-per-rung", "tiny=2,small=2,medium=1,full=0"]
    assert regen.main(first) == 0
    assert len(called) == 5
    output = tmp_path / frozen["epoch_id"] / "regen"
    index_path = Path(json.loads((output / "plan.json").read_text())["instance_root"]) / "manifest.json"
    initial = json.loads(index_path.read_text())
    assert initial["tranches"]["first"]["complete"] is True
    assert initial["rejected"] == 5 and initial["count"] == 0
    assert len(initial["not_generated_spec_keys"]) == 9400-5
    assert initial["pending_spec_keys"] == []
    # Workers are operational, not epoch identity; no pending job means no pool.
    assert regen.main([*first, "--workers", "2"]) == 0
    assert len(called) == 5
    assert regen.main([*args, "--tranche", "second", "--train-per-rung", "tiny=3,small=3,medium=1,full=0"]) == 0
    assert len(called) == 7 and len(set(called)) == 7
    later = json.loads(index_path.read_text())
    assert later["tranches"]["first"]["rejected"] == 5
    assert later["tranches"]["second"]["rejected"] == 7
    assert later["rejected"] == 7
    assert later["allocation_complete"] is False
    assert len(later["requested_spec_keys"]) == 7
    # A tranche is allowed to change capacity, never the frozen producer policy.
    assert regen.main([*args, "--tranche", "third", "--train-per-rung", "tiny=4", "--max-attempts", "2"]) == 1
    assert len(called) == 7
    assert "MEASURED diagnostic, not evidence" in capsys.readouterr().out


def test_tranche_manifest_marks_only_attempted_specs_rejected(regen, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    from tranches import select_entries
    release = regen.build_release("e")
    selected = select_entries(release, {"tiny": 2}, 0, True)
    jobs = regen.make_jobs(release, tmp_path, True, 2, 1, "e", entries=selected)
    tranches = regen.bind_tranche(tmp_path / "control", "first", jobs, train_per_rung={"tiny": 2})
    index = regen._write_indices([], release, tmp_path, "e", True, tranches=tranches)
    assert index["rejected"] == 0 and index["rejected_bindings"] == []
    assert index["tranches"]["first"]["pending"] == 2
    assert len(index["pending_spec_keys"]) == 2
    assert len(index["not_generated_spec_keys"]) == 9400


def test_completed_loader_validates_the_same_checkpoint_row_it_returns(regen, tmp_path, monkeypatch):
    """Replacing a marker between reads cannot bless an older incompatible row."""
    marker = tmp_path / "done/release-one.json"
    marker.parent.mkdir()
    good = dict(epoch_id="e", diagnostic=True, job={"id": "one"}, outputs={})
    bad = {**good, "epoch_id": "other-epoch"}
    marker.write_text(json.dumps(good))
    read_text, read_bytes = Path.read_text, Path.read_bytes
    reads = []
    def replacement(path, binary):
        reads.append(path)
        value = bad if len(reads) == 1 else good
        raw = json.dumps(value)
        return raw.encode() if binary else raw
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: replacement(path, False) if path == marker else read_text(path, *a, **kw))
    monkeypatch.setattr(Path, "read_bytes", lambda path, *a, **kw: replacement(path, True) if path == marker else read_bytes(path, *a, **kw))
    with pytest.raises(ValueError, match="incompatible done marker"):
        regen.load_completed(tmp_path, "e", True)


def test_instance_index_hashes_the_exact_spec_validated_bytes(regen, tmp_path, monkeypatch):
    """A replacement during model validation never receives the old spec claims."""
    from eval.snapshot_io import FileSnapshot
    from eval import splits
    import stowage.io
    release = regen.build_release("e")
    job = regen.make_jobs(release, tmp_path, True, 2, 1, "e")[0]
    path = Path(job["root"]) / job["config"]["generator"] / "tiny/accepted.json"
    path.parent.mkdir(parents=True)
    original = b'{"schema_version":"1.0","content":"validated"}'
    path.write_bytes(original)
    def parse_then_replace(*args, **kwargs):
        path.write_bytes(b'{"schema_version":"1.0","content":"replacement"}')
        from types import SimpleNamespace
        return SimpleNamespace(containers=[], load_list=[])
    monkeypatch.setattr(FileSnapshot, "model", parse_then_replace)
    monkeypatch.setattr(stowage.io, "load_instance", parse_then_replace)
    monkeypatch.setattr(splits, "spec_from_instance", lambda _: job["allocated"]["spec"])
    result = regen._write_indices([dict(job=job, result=dict(path=str(path), audit={"status": "accepted"}))],
        release, tmp_path, "e", True)
    binding = next(iter(result["bindings"].values()))
    assert binding["sha256"] == regen.hashlib.sha256(original).hexdigest()
    assert binding["sha256"] != regen.sha256_file(path)
