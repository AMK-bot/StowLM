"""Fast filesystem/contract checks; no solver or cloud work."""
import hashlib
import importlib.util
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import tempfile
import threading

import pytest


def load_module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


driver = load_module("driver")
status = load_module("render_status")


def test_administrative_ownership_drift_is_gated_only_if_loaded(monkeypatch):
    monkeypatch.delitem(driver.sys.modules, "stowage.ownership", raising=False)
    assert not driver.relevant_source("src/stowage/ownership.py")
    monkeypatch.setitem(driver.sys.modules, "stowage.ownership", SimpleNamespace())
    assert driver.relevant_source("src/stowage/ownership.py")
    assert driver.relevant_source("src/stowage/checker/full.py")


def test_tiny_report_uses_tiny_runtime_when_small_updates_latest_view(tmp_path):
    driver.atomic_json(tmp_path / "tiny-v6.process.json", {"pid": 100})
    driver.atomic_json(tmp_path / "runtime/100.json", {"workers": 2, "status": "running"})
    driver.atomic_json(tmp_path / "runtime.json", {"workers": 5, "status": "running"})
    assert status.rung_runtime(tmp_path, "tiny")["workers"] == 2


def test_plan_manifest_records_old_and_new_securing_settings(tmp_path):
    for seed, screen in ((1, False), (2, True)):
        row = dict(completed=True, passed=True, case=f"train/small/ship/family/seed-{seed}",
                   split="train", rung="small", plan_sha256=f"plan-{seed}",
                   config={"scalable_config": {"securing_screen": screen}, "ballast_v4": True},
                   stages={"checker": {"passed": True}, "mask": {"passed": True}})
        driver.atomic_json(tmp_path / row["case"] / "result.json", row)
    manifest = status.plan_manifest(tmp_path)
    assert {r["securing_screen"] for r in manifest["plans"]} == {False, True}
    assert all(r["ballast_v4"] for r in manifest["plans"])


def test_exact_only_source_gate_excludes_unused_scalable_search():
    assert not driver.relevant_source("src/stowage/solver/scalable.py", used_scalable=False)
    assert not driver.relevant_source("src/stowage/solver/construct_v4.py", used_scalable=False)
    assert driver.relevant_source("src/stowage/solver/scalable.py", used_scalable=True)
    assert driver.relevant_source("src/stowage/solver/exact_engine.py", used_scalable=False)
    assert driver.relevant_source("src/stowage/checker.py", used_scalable=False)


def commit_case(root, seed="seed-1", accepted=True, split="train", instance_id=None):
    seed = int(str(seed).removeprefix("seed-"))
    config = dict(split=split, rung="tiny", vessel="feeder_500", generator="random_yard",
                  seed=seed, port="laem_chabang_like", noise=0.2, max_attempts=100,
                  n_boxes=None, dense_20ft=False, tiny_dg_enabled=True)
    path = root / f"{split}/tiny/feeder_500/random_yard/seed-{seed:09d}.json"
    for requested_split in ("train", "val", "test"):
        request = root / f"{requested_split}.requested.json"
        if not request.exists():
            driver.atomic_json(request, {"specs": [], "counts": {}, "split_policy": "mvp-v1-seed-disjoint-all-ships-all-rungs"})
    request = root / f"{split}.requested.json"
    inventory = json.loads(request.read_text())
    inventory["specs"] = [s for s in inventory["specs"] if s["seed"] != seed] + [config]
    inventory["counts"] = {"tiny": len(inventory["specs"])}
    driver.atomic_json(request, inventory)
    manifest = root / "showcase/showcase-manifest.json"
    if not manifest.exists():
        driver.atomic_json(manifest, {"records": [], "instances": 0, "rule_freeze_v1_enabled": False})
    driver.atomic_json(path, {"id": instance_id or f"synthetic-{seed}", "seed": seed, "synthetic": seed})
    driver.atomic_json(path.with_suffix(".audit.json"), {
        "accepted": accepted, "rule_freeze_v1_enabled": False,
        "instance_sha256": driver.sha256(path), "seed": seed, "config": config,
    })
    return path


def test_stream_discovers_only_atomic_accepted_audits(tmp_path):
    corpus = tmp_path / "corpus"
    first = commit_case(corpus)
    uncommitted = commit_case(corpus, "seed-2")
    audit_path = uncommitted.with_suffix(".audit.json")
    audit = json.loads(audit_path.read_text())
    audit_path.unlink()
    commit_case(corpus, "seed-3", accepted=False)
    assert driver.discover(corpus, ["tiny"], ["train"]) == [first]
    driver.atomic_json(audit_path, audit)
    assert driver.discover(corpus, ["tiny"], ["train"]) == [first, uncommitted]


def test_discovery_schedules_tampered_input_for_recorded_drop(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    payload = json.loads(path.read_text()) | {"synthetic": "tampered"}
    driver.atomic_json(path, payload)
    assert driver.discover(corpus, ["tiny"], ["train"]) == [path]
    row = driver.process_case(path, corpus, output, {})
    assert not row["passed"] and row["drop_reason"] == "input"
    assert "checksum" in row.get("error", "")


def cached_result(path, config):
    return {"input_sha256": driver.sha256(path), "config": config,
            "configuration_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            "completed": True, "passed": False, "drop_reason": "test failure"}


def test_resume_returns_completed_failure_without_solver_imports(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    path = commit_case(corpus)
    output = tmp_path / "gold"
    config = {"exact_s": 1}
    row = cached_result(path, config)
    directory = driver.result_dir(path, corpus, output)
    driver.atomic_json(directory / "result.json", row)
    monkeypatch.setattr(driver, "source_pin", lambda: pytest.fail("resume must skip processing"))
    assert driver.process_case(path, corpus, output, config) == row
    with pytest.raises(ValueError, match="Immutable gold settings"):
        driver.process_case(path, corpus, output, {"exact_s": 2})


def test_resume_rejects_missing_passing_plan(tmp_path):
    path = commit_case(tmp_path / "corpus")
    row = cached_result(path, {}) | {"passed": True}
    output = tmp_path / "gold"
    directory = driver.result_dir(path, tmp_path / "corpus", output)
    driver.atomic_json(directory / "result.json", row)
    with pytest.raises(ValueError, match="Published gold plan missing"):
        driver.process_case(path, tmp_path / "corpus", output, {})


@pytest.mark.parametrize("checker,mask,expected", [(True, True, True), (False, True, False), (True, False, False)])
def test_report_requires_both_gate_records(checker, mask, expected):
    row = {"passed": True, "stages": {"checker": {"passed": checker}, "mask": {"passed": mask}}}
    assert status.qualifies(row) is expected


def test_report_measured_projection_and_failed_denominator(tmp_path):
    good = {"completed": True, "rung": "tiny", "passed": True, "wall_s": 30,
            "stages": {"checker": {"passed": True, "wall_s": 2}, "mask": {"passed": True, "wall_s": 3}}}
    bad = {"completed": True, "rung": "tiny", "passed": False, "wall_s": 90,
           "drop_reason": "solve timeout", "stages": {"exact": {"wall_s": 60}}}
    driver.atomic_json(tmp_path / "train/tiny/a/result.json", good)
    driver.atomic_json(tmp_path / "val/tiny/b/result.json", bad)
    driver.atomic_json(tmp_path / "train/tiny/c/result.json", {"completed": False, "rung": "tiny"})
    content = status.render(tmp_path)
    assert "| tiny | 1/2 | 50.0%" in content
    assert "mean total wall 60.000 s" in content
    assert "16.800 h on one core" in content
    assert "tiny: solve timeout: 1" in content
    assert "medium: no completed gold-pipeline timing" in content
    status.write_reports(tmp_path)
    assert (tmp_path / "STATUS.md").read_text() == (tmp_path / "YIELD.md").read_text()


def test_resume_finished_config_migration_keeps_published_plan(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    directory = driver.result_dir(path, corpus, output)
    driver.atomic_json(directory / "plan.json", {"synthetic_plan": "retained"})
    plan_bytes = (directory / "plan.json").read_bytes()
    row = cached_result(path, {"exact_s": 10}) | {
        "passed": True, "plan_sha256": driver.sha256(directory / "plan.json")}
    driver.atomic_json(directory / "result.json", row)
    monkeypatch.setattr(driver, "source_pin", lambda: pytest.fail("finished case must not run solver"))
    result = driver.process_case(path, corpus, output, {"exact_s": 30}, resume_finished=True)
    assert result == row
    assert (directory / "plan.json").read_bytes() == plan_bytes
    assert json.loads((directory / "result.json").read_text()) == row


def test_resume_finished_never_adopts_incomplete_different_config(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    row = cached_result(path, {"exact_s": 10}) | {"completed": False}
    driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", row)
    with pytest.raises(ValueError, match="[Ii]mmutable|[Ii]dentity|[Ss]ettings|[Cc]onfig"):
        driver.process_case(path, corpus, output, {"exact_s": 30}, resume_finished=True)


def test_resume_finished_still_rejects_changed_input_or_plan(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    directory = driver.result_dir(path, corpus, output)
    driver.atomic_json(directory / "plan.json", {"original": True})
    row = cached_result(path, {}) | {"passed": True, "plan_sha256": driver.sha256(directory / "plan.json")}
    driver.atomic_json(directory / "result.json", row)
    (directory / "plan.json").write_text("{}")
    with pytest.raises(ValueError, match="[Pp]lan"):
        driver.process_case(path, corpus, output, {"exact_s": 30}, resume_finished=True)
    driver.atomic_json(directory / "result.json", row | {"passed": False})
    path.write_text("{}")
    rejected = driver.process_case(path, corpus, output, {"exact_s": 30}, resume_finished=True)
    assert not rejected["passed"] and rejected["drop_reason"] == "input"
    assert "Split admission" in rejected.get("error", "")


def ledger_text(other_cores, other_ram=20):
    return ("| executive/job | task | cores | RAM GB | GPU | est. wall | start (ICT) |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| other/task | 4.7 | {other_cores} | {other_ram} | no | 1h | now |\n"
            "| mvp-gold/current | 6.1 | 1 | 2 | no | 1h | now |\n")


def mock_live_capacity(monkeypatch, load=1, free=90):
    monkeypatch.setattr(driver.os, "getloadavg", lambda: (load, load, load))
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout=f"System-wide memory free percentage: {free}%", returncode=0))


@pytest.mark.parametrize("other_cores,expected", [(15, 1), (14, 2), (13, 3), (12, 3), (16, 0)])
def test_adaptive_admission_respects_remaining_cores(tmp_path, monkeypatch, other_cores, expected):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(other_cores))
    mock_live_capacity(monkeypatch)
    detail = driver.select_admission(ledger, 3, adaptive=True, ledger_owner="mvp-gold/current")
    assert detail["actual_workers"] == expected
    assert detail["admitted"] is bool(expected)


def test_adaptive_counts_other_gold_and_only_updates_own_row(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    other = "| mvp-gold/unrelated | 6.1 | 2 | 4 | no | 9h | earlier |"
    ledger.write_text(ledger_text(12) + other + "\n")
    mock_live_capacity(monkeypatch)
    detail = driver.select_admission(ledger, 3, adaptive=True, ledger_owner="mvp-gold/current")
    assert detail["actual_workers"] == 2
    driver.update_ledger_reservation(ledger, "mvp-gold/current", 2)
    assert other in ledger.read_text()
    own = next(line for line in ledger.read_text().splitlines() if "mvp-gold/current" in line)
    fields = [part.strip() for part in own.strip("|").split("|")]
    assert float(fields[2]) == 2 and float(fields[3]) == 4
    with pytest.raises(ValueError):
        driver.update_ledger_reservation(ledger, "mvp-gold/missing", 3)


@pytest.mark.parametrize("load,free,ram", [(17, 90, 20), (1, 10, 20), (1, 90, 112)])
def test_adaptive_queues_under_live_or_ram_pressure(tmp_path, monkeypatch, load, free, ram):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(1, ram))
    mock_live_capacity(monkeypatch, load, free)
    detail = driver.select_admission(ledger, 3, adaptive=True, ledger_owner="mvp-gold/current")
    assert detail["actual_workers"] == 0


def test_adaptive_does_not_admit_unknown_reservations(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text("unknown"))
    mock_live_capacity(monkeypatch)
    assert driver.select_admission(ledger, 3, adaptive=True,
                                   ledger_owner="mvp-gold/current")["actual_workers"] == 0


def test_fixed_worker_mode_does_not_silently_downsize(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(14))
    mock_live_capacity(monkeypatch)
    detail = driver.select_admission(ledger, 3, adaptive=False, ledger_owner="mvp-gold/current")
    assert detail["actual_workers"] == 0


@pytest.mark.parametrize("other_cores,ram,load,free,expected", [
    (15, 60, 12, 50, True), (16, 60, 12, 50, False),
    (10, 111, 12, 50, False), (10, 60, 18, 50, False),
    (10, 60, 12, 10, False),
])
def test_admission_checks_reservations_and_live_use(tmp_path, monkeypatch,
                                                  other_cores, ram, load, free, expected):
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| executive/job | task | cores | RAM GB | GPU | est. wall | start |\n"
                      "|---|---|---|---|---|---|---|\n"
                      f"| another/job | 5.4 | {other_cores} cores | ≤{ram} GB | no | 1h | now |\n"
                      "| mvp-gold/self | 6.1 | 1 | 2 | no | 1h | now |\n")
    monkeypatch.setattr(driver.os, "getloadavg", lambda: (load, load, load))
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(stdout=f"System-wide memory free percentage: {free}%"))
    evidence = driver.admission(ledger, 1, ledger_owner="mvp-gold/self")
    assert evidence["admitted"] is expected
    assert evidence["other_reserved_cores"] == other_cores
    assert evidence["other_reserved_ram_gb"] == ram


def test_campaign_manifest_allows_locked_settings_for_other_rungs(tmp_path, monkeypatch):
    output = tmp_path / "gold"
    monkeypatch.setattr(driver, "discover", lambda *args: [])
    for rung, budget in (("tiny", "10"), ("small", "30"), ("medium", "45")):
        monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                            "--rung", rung, "--solver-s", budget])
        driver.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert [c["solver_s"] for c in manifest["configurations"]] == [10, 30, 45]


def test_source_drift_preserves_child_solver_and_checker_dependencies():
    assert driver.relevant_source("src/stowage/solver/exact_engine.py")
    assert driver.relevant_source("src/stowage/checker/full.py")
    assert driver.relevant_source("params/vessels.toml")
    assert not driver.relevant_source("src/stowage/solver/twostep_construct.py")
    assert not driver.relevant_source("src/llm/stowlm/unloaded_trainer.py")


def test_supervisor_rechecks_admission_between_single_case_shards(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    paths = [commit_case(corpus, f"seed-{i}") for i in range(3)]
    calls = []
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--admit-ledger", "dummy.md"])
    monkeypatch.setattr(driver, "select_admission", lambda *args, **kwargs:
                        calls.append("admit") or {"admitted": True, "actual_workers": 1})
    class Worker:
        pid = 123
        def __init__(self, command, **kwargs):
            calls.append("launch")
            path = Path(command[command.index("--case")+1])
            config = json.loads(command[command.index("--config-json")+1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json",
                               cached_result(path, config))
        def wait(self):
            return 0
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: None)
    driver.main()
    assert calls == ["admit", "launch"] * 3
    assert all((driver.result_dir(path, corpus, output) / "result.json").exists() for path in paths)


def verify_split(path, corpus, output=None):
    return load_module("split_admission").verify_candidate(path, corpus, output)


def test_admission_valid_requested_config(tmp_path):
    path = commit_case(tmp_path, "seed-1")
    result = verify_split(path, tmp_path)
    assert result["seed"] == 1 and result["instance_id"] == "synthetic-1"


def test_relabelled_test_input_drops_before_solver(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    heldout = commit_case(corpus, "seed-8000011", split="test")
    train = corpus / "train" / heldout.relative_to(corpus / "test")
    train.parent.mkdir(parents=True)
    train.write_bytes(heldout.read_bytes())
    audit = json.loads(heldout.with_suffix(".audit.json").read_text())
    audit["config"]["split"] = "train"
    driver.atomic_json(train.with_suffix(".audit.json"), audit)
    monkeypatch.setattr(driver, "source_pin", lambda: {})
    from stowage.solver.exact import ExactPlanner
    from stowage.solver.scalable import ScalableSolver
    def solver_reached(*args, **kwargs):
        pytest.fail("relabelled heldout reached solver")
    monkeypatch.setattr(ExactPlanner, "plan", solver_reached)
    monkeypatch.setattr(ScalableSolver, "plan", solver_reached)
    row = driver.process_case(train, corpus, output, {})
    assert row["completed"] and not row["passed"]
    assert row["drop_reason"] == "input" and "Split admission" in row.get("error", "")
    assert "exact" not in row.get("stages", {}) and "scalable" not in row.get("stages", {})


def test_reserved_showcase_candidate_seed_rejected_after_rewriting_id_and_bytes(tmp_path):
    path = commit_case(tmp_path, "seed-123")
    driver.atomic_json(tmp_path / "showcase/selected.audit.json", {
        "seed_selection": [{"seed": 123}, {"seed": 456}], "config": {"seed": 456}})
    original = driver.sha256(path)
    payload = json.loads(path.read_text()) | {"id": "changed-id", "synthetic": "changed-content"}
    driver.atomic_json(path, payload)
    audit_path = path.with_suffix(".audit.json")
    audit = json.loads(audit_path.read_text()) | {"instance_sha256": driver.sha256(path)}
    driver.atomic_json(audit_path, audit)
    assert driver.sha256(path) != original
    with pytest.raises(ValueError):
        verify_split(path, tmp_path)


@pytest.mark.parametrize("field,value", [("noise", 0.9), ("port", "other_port"), ("tiny_dg_enabled", False)])
def test_requested_membership_requires_complete_audit_config(tmp_path, field, value):
    path = commit_case(tmp_path)
    audit_path = path.with_suffix(".audit.json")
    audit = json.loads(audit_path.read_text())
    audit["config"][field] = value
    driver.atomic_json(audit_path, audit)
    with pytest.raises(ValueError):
        verify_split(path, tmp_path)


@pytest.mark.parametrize("duplicate", ["id", "hash"])
def test_train_val_duplicate_identity_or_hash_rejected(tmp_path, duplicate):
    train = commit_case(tmp_path, "seed-1")
    val = commit_case(tmp_path, "seed-2", split="val", instance_id="synthetic-1" if duplicate == "id" else None)
    if duplicate == "hash":
        val.write_bytes(train.read_bytes())
        audit_path = val.with_suffix(".audit.json")
        driver.atomic_json(audit_path, json.loads(audit_path.read_text()) | {"instance_sha256": driver.sha256(val)})
    with pytest.raises(ValueError):
        verify_split(train, tmp_path)


def test_resume_rechecks_duplicate_identity_before_retaining_gold(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    train = commit_case(corpus, "seed-1")
    directory = driver.result_dir(train, corpus, output)
    driver.atomic_json(directory / "plan.json", {"plan": "previously accepted"})
    prior = cached_result(train, {}) | {"passed": True, "plan_sha256": driver.sha256(directory / "plan.json")}
    driver.atomic_json(directory / "result.json", prior)
    commit_case(corpus, "seed-2", split="val", instance_id="synthetic-1")
    row = driver.process_case(train, corpus, output, {}, resume_finished=True)
    assert not row["passed"] and row["drop_reason"] == "input"
    assert "Split admission" in row.get("error", "")


@pytest.mark.parametrize("duplicate", ["id", "hash"])
def test_resume_rechecks_existing_gold_duplicates_even_without_source_shard(tmp_path, duplicate):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    train = commit_case(corpus, "seed-1")
    directory = driver.result_dir(train, corpus, output)
    driver.atomic_json(directory / "result.json", cached_result(train, {}))
    other = {"instance_id": "synthetic-1" if duplicate == "id" else "other-id",
             "input_sha256": driver.sha256(train) if duplicate == "hash" else "other-hash",
             "completed": True, "passed": True}
    driver.atomic_json(output / "val/tiny/feeder_500/random_yard/seed-000000002/result.json", other)
    row = driver.process_case(train, corpus, output, {}, resume_finished=True)
    assert not row["passed"] and row["drop_reason"] == "input"
    assert "Split admission" in row.get("error", "")


def test_concurrent_report_writers_use_unique_temps(tmp_path, monkeypatch):
    start = threading.Barrier(2)
    created = []
    lock = threading.Lock()
    original = tempfile.mkstemp
    def tracked(*args, **kwargs):
        result = original(*args, **kwargs)
        with lock:
            created.append(result[1])
        return result
    monkeypatch.setattr(tempfile, "mkstemp", tracked)
    def writer():
        start.wait(timeout=5)
        status.write_reports(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert len(created) >= 4 and len(created) == len(set(created))
    assert (tmp_path / "YIELD.md").read_bytes() == (tmp_path / "STATUS.md").read_bytes()


def test_concurrent_manifests_keep_both_configurations(tmp_path):
    barrier = threading.Barrier(2)
    configs = [{"exact_s": 10}, {"exact_s": 30}]
    def publish(config):
        barrier.wait(timeout=5)
        driver.publish_manifest(tmp_path, tmp_path / "corpus", config)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, config) for config in configs]
        for future in futures:
            future.result(timeout=10)
    actual = json.loads((tmp_path / "manifest.json").read_text())["configurations"]
    assert sorted(c["exact_s"] for c in actual) == [10, 30]


def test_report_failures_retry_without_aborting_and_clear_after_success(tmp_path, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise driver.subprocess.CalledProcessError(1, "renderer")
    monkeypatch.setattr(driver.subprocess, "run", fail)
    driver.render_reports(tmp_path, tmp_path / "corpus")
    assert len(calls) == 2
    errors = list((tmp_path / "report-errors").glob("*.json"))
    assert len(errors) == 1 and json.loads(errors[0].read_text())["attempt"] == 2
    monkeypatch.setattr(driver.subprocess, "run", lambda *args, **kwargs: None)
    driver.render_reports(tmp_path, tmp_path / "corpus")
    assert not errors[0].exists()


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("load,admitted", [(16, True), (16.1, False)])
def test_default_max_load_stays_sixteen(tmp_path, monkeypatch, adaptive, load, admitted):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(12))
    mock_live_capacity(monkeypatch, load=load)
    result = driver.select_admission(ledger, 3, adaptive=adaptive, ledger_owner="mvp-gold/current")
    assert result["admitted"] is admitted
    assert result["actual_workers"] == (3 if admitted else 0)


@pytest.mark.parametrize("adaptive", [False, True])
@pytest.mark.parametrize("load,admitted", [(15, True), (15.1, False)])
def test_configured_max_load_applies_to_fixed_and_adaptive(tmp_path, monkeypatch, adaptive, load, admitted):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(12))
    mock_live_capacity(monkeypatch, load=load)
    result = driver.select_admission(ledger, 3, adaptive=adaptive,
                                     ledger_owner="mvp-gold/current", max_load=15)
    assert result["admitted"] is admitted
    assert result["actual_workers"] == (3 if admitted else 0)


@pytest.mark.parametrize("enabled", [False, True])
def test_ballast_v4_cli_records_explicit_config(tmp_path, monkeypatch, enabled):
    output = tmp_path / "gold"
    monkeypatch.setattr(driver, "discover", lambda *args: [])
    monkeypatch.setattr(driver, "render_reports", lambda *args: None)
    argv = ["driver.py", "--output", str(output)] + (["--ballast-v4"] if enabled else [])
    monkeypatch.setattr(driver.sys, "argv", argv)
    driver.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["configurations"][0]["ballast_v4"] is enabled


def test_input_settings_filter_requires_both_slack_and_mass(tmp_path):
    settings = {"booking_slack": 0.3, "cargo_mass_scale": 0.8}
    variants = [settings, {"booking_slack": 0.2, "cargo_mass_scale": 0.8},
                {"booking_slack": 0.3, "cargo_mass_scale": 1.0},
                {"booking_slack": 0.3}, {}]
    paths = []
    for index, extra in enumerate(variants):
        path = commit_case(tmp_path, f"seed-{index}")
        audit_path = path.with_suffix(".audit.json")
        audit = json.loads(audit_path.read_text())
        audit["config"].update(extra)
        driver.atomic_json(audit_path, audit)
        paths.append(path)
    assert driver.filter_input_settings(paths, settings) == [paths[0]]
    assert driver.filter_input_settings(paths, {}) == paths
    assert driver.filter_input_settings(paths, {"missing": None}) == []


def test_input_settings_cli_persists_object(tmp_path, monkeypatch):
    output = tmp_path / "gold"
    settings = {"booking_slack": 0.3, "cargo_mass_scale": 0.8}
    settings_path = tmp_path / "settings.json"
    driver.atomic_json(settings_path, settings)
    monkeypatch.setattr(driver, "discover", lambda *args: [])
    monkeypatch.setattr(driver, "render_reports", lambda *args: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--input-settings", str(settings_path)])
    driver.main()
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["configurations"][0]["input_settings"] == settings


@pytest.mark.parametrize("value", [[], 0, None, "not an object"])
def test_input_settings_cli_rejects_nonobjects(tmp_path, monkeypatch, value):
    settings_path = tmp_path / "settings.json"
    driver.atomic_json(settings_path, value)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(tmp_path / "gold"),
                        "--input-settings", str(settings_path)])
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 2


def test_input_settings_filter_excludes_old_cached_cases_before_resume(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    directory = driver.result_dir(path, corpus, output)
    driver.atomic_json(directory / "result.json", cached_result(path, {"old": "settings"}))
    settings_path = tmp_path / "settings.json"
    driver.atomic_json(settings_path, {"booking_slack": 0.3, "cargo_mass_scale": 0.8})
    monkeypatch.setattr(driver, "render_reports", lambda *args: None)
    monkeypatch.setattr(driver, "process_case", lambda *a, **k: pytest.fail("old settings reached resume"))
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--input-settings", str(settings_path)])
    driver.main()
    assert json.loads((directory / "result.json").read_text())["config"] == {"old": "settings"}


def val_records(output, rung, count):
    for index in range(count):
        driver.atomic_json(output / f"val/{rung}/feeder_500/random_yard/seed-{index:09d}/result.json",
                           {"split": "val", "rung": rung, "completed": True, "passed": True})


def test_policy_defaults_and_reloads_file(tmp_path):
    defaults = driver.load_policy()
    assert defaults["max_load"] == 16
    assert defaults["train_first"] is True
    assert defaults["val_gold_caps"]["tiny"] == 50
    assert defaults["val_gold_caps"]["small"] == 56
    path = tmp_path / "policy.json"
    driver.atomic_json(path, {"max_load": 15, "val_gold_caps": {"tiny": 40}})
    first = driver.load_policy(path)
    assert first["max_load"] == 15 and first["val_gold_caps"]["tiny"] == 40
    assert first["val_gold_caps"]["small"] == 56
    driver.atomic_json(path, {"max_load": 16})
    assert driver.load_policy(path)["max_load"] == 16
    assert first["max_load"] == 15


def test_policy_dispatches_train_before_val(tmp_path):
    corpus = tmp_path / "corpus"
    val = commit_case(corpus, "seed-2", split="val")
    train = commit_case(corpus, "seed-1")
    selected = driver.select_pending([val, train], tmp_path / "gold", corpus, 3, driver.load_policy())
    assert selected == [train]
    assert driver.select_pending([val], tmp_path / "gold", corpus, 3,
                                 driver.load_policy(), train_waiting=True) == []


def test_val_cap_counts_only_completed_passes_and_limits_batch(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    paths = [commit_case(corpus, f"seed-{100 + i}", split="val") for i in range(3)]
    val_records(output, "tiny", 49)
    driver.atomic_json(output / "val/tiny/fail/result.json", {
        "split": "val", "rung": "tiny", "completed": True, "passed": False})
    driver.atomic_json(output / "val/tiny/incomplete/result.json", {
        "split": "val", "rung": "tiny", "completed": False, "passed": True})
    policy = driver.load_policy()
    assert driver.select_pending(paths, output, corpus, 3, policy) == paths[:1]
    driver.atomic_json(output / "val/tiny/last/result.json", {
        "split": "val", "rung": "tiny", "completed": True, "passed": True})
    assert driver.select_pending(paths, output, corpus, 3, policy) == []


def test_small_validation_cap_defaults_to_fifty_six(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    paths = [corpus / f"val/small/feeder_500/random_yard/seed-{i:09d}.json" for i in range(2)]
    val_records(output, "small", 55)
    assert driver.select_pending(paths, output, corpus, 3, driver.load_policy()) == paths[:1]


def test_train_inventory_waits_for_unemitted_and_incomplete_inputs(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    assert driver.train_inventory_waiting(corpus, output, ["tiny"])
    driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", {"completed": False})
    assert driver.train_inventory_waiting(corpus, output, ["tiny"])
    driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", {"completed": True, "passed": False})
    assert not driver.train_inventory_waiting(corpus, output, ["tiny"])
    (driver.result_dir(path, corpus, output) / "result.json").unlink()
    path.unlink()
    path.with_suffix(".audit.json").unlink()
    assert driver.train_inventory_waiting(corpus, output, ["tiny"])


def test_policy_reload_changes_admission_without_case_config_drift(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for index in range(3):
        commit_case(corpus, f"seed-{index}")
    policy = tmp_path / "policy.json"
    driver.atomic_json(policy, {"max_load": 15, "max_workers": 1})
    configs, loads, workers = [], [], []
    def admit(*args, **kwargs):
        loads.append(kwargs["max_load"])
        workers.append(args[1])
        return {"admitted": True, "actual_workers": args[1]}
    monkeypatch.setattr(driver, "select_admission", admit)
    monkeypatch.setattr(driver, "render_reports", lambda *args: None)
    class Worker:
        pid = 123
        def __init__(self, command, **kwargs):
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            configs.append(config)
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
            driver.atomic_json(policy, {"max_load": 16, "max_workers": 2})
        def wait(self):
            return 0
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--admit-ledger", "dummy.md",
                        "--workers", "3", "--policy-file", str(policy)])
    driver.main()
    assert loads == [15, 16]
    assert workers == [1, 2]
    assert len(configs) == 3 and all(config == configs[0] for config in configs)
    assert not {"max_load", "max_workers", "policy_file"} & configs[0].keys()


def test_per_ship_input_settings_require_qualified_pairs_and_exclude_1700(tmp_path):
    settings = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
                "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45}}
    paths = []
    variants = [("feeder_500", .55), ("feeder_800", .45), ("feeder_500", .45),
                ("feeder_800", .55), ("feeder_1700", .55)]
    for index, (ship, mass) in enumerate(variants):
        path = tmp_path / f"{ship}-{index}.json"
        driver.atomic_json(path.with_suffix(".audit.json"), {"config": {
            "vessel": ship, "booking_slack": .25, "booking_mass_fraction": mass}})
        paths.append(path)
    assert driver.filter_input_settings(paths, settings) == paths[:2]
    assert driver.input_settings_for({"vessel": "feeder_1700"}, settings) is None


def test_expanded_per_ship_mapping_can_admit_1700_without_changing_other_ships(tmp_path):
    settings = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
                "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45},
                "feeder_1700": {"booking_slack": .25, "booking_mass_fraction": .40}}
    paths = []
    for ship, required in settings.items():
        path = tmp_path / f"{ship}.json"
        driver.atomic_json(path.with_suffix(".audit.json"), {"config": {"vessel": ship, **required}})
        paths.append(path)
    assert driver.filter_input_settings(paths, settings) == paths
    assert driver.input_settings_for({"vessel": "feeder_1700"}, settings) == settings["feeder_1700"]


def test_unqualified_1700_train_request_does_not_block_validation(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    template = json.loads((corpus / "train.requested.json").read_text())["specs"][0]
    settings = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
                "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45}}
    specs = [template | {"seed": seed, "vessel": ship, "booking_slack": .25, "booking_mass_fraction": mass}
             for seed, ship, mass in [(1, "feeder_500", .55), (2, "feeder_800", .45), (3, "feeder_1700", .55)]]
    driver.atomic_json(corpus / "train.requested.json", {"specs": specs, "counts": {"tiny": 3}})
    for spec in specs[:2]:
        driver.atomic_json(output / f"train/tiny/{spec['vessel']}/random_yard/seed-{spec['seed']:09d}/result.json",
                           {"completed": True, "passed": True})
    assert not driver.train_inventory_waiting(corpus, output, ["tiny"], settings)


@pytest.mark.parametrize("previous", [None, "0", "1", "external-value"])
@pytest.mark.parametrize("raises", [False, True])
def test_ballast_scope_restores_previous_environment_on_success_or_error(monkeypatch, previous, raises):
    if previous is None:
        monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    else:
        monkeypatch.setenv("STOWAGE_BALLAST_V4", previous)
    try:
        with driver.scoped_ballast_v4(True):
            assert driver.os.environ.get("STOWAGE_BALLAST_V4") == "1"
            if raises:
                raise RuntimeError("finisher failed")
    except RuntimeError:
        assert raises
    assert driver.os.environ.get("STOWAGE_BALLAST_V4") == previous


@pytest.mark.parametrize("fail_finalize", [False, True])
def test_ballast_v4_visible_only_during_finalize(tmp_path, monkeypatch, fail_finalize):
    monkeypatch.setenv("STOWAGE_BALLAST_V4", "external-value")
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    audit_path = path.with_suffix(".audit.json")
    driver.atomic_json(audit_path, json.loads(audit_path.read_text()) | {"requested_targets": 0})
    observed = []
    def observe(stage):
        observed.append((stage, driver.os.environ.get("STOWAGE_BALLAST_V4")))
    real_verify = driver.verify_candidate
    def verify(*args, **kwargs):
        observe("input")
        return real_verify(*args, **kwargs)
    monkeypatch.setattr(driver, "verify_candidate", verify)
    monkeypatch.setattr(driver, "source_pin", lambda: {})
    monkeypatch.setattr(importlib.import_module("stowage.frozen"), "require_clean", lambda *a, **k: None)
    monkeypatch.setattr(importlib.import_module("stowage.generate.background_acceptance"), "requires_background_gate", lambda *a, **k: False)
    monkeypatch.setattr(importlib.import_module("stowage.rules.catalogue"), "catalogue_snapshot", lambda: SimpleNamespace(content_sha256="digest"))
    monkeypatch.setattr(importlib.import_module("stowage.rules.catalogue"), "rule_freeze_v1_enabled", lambda: False)
    monkeypatch.setattr(importlib.import_module("stowage.versions"), "component_versions", lambda: {})
    instance = SimpleNamespace(id="synthetic-1", seed=1, size_class="tiny", load_list=[], voyage=object(), vessel=object())
    monkeypatch.setattr(importlib.import_module("stowage.io"), "load_instance", lambda *a: instance)
    monkeypatch.setattr(importlib.import_module("stowage.io"), "save_plan", lambda plan, filename: Path(filename).write_text("{}"))
    class Result:
        status = "ok"
        planner = "mock-exact"
        planner_version = "mock"
        metrics = {"optimality_label": "optimal"}
        message = "mock"
        plan = SimpleNamespace(ballast={})
        shippable = True
        def model_copy(self, update):
            return self
        def _record_full_check(self, *args):
            pass
    result = Result()
    class Exact:
        def __init__(self, **kwargs):
            pass
        def plan(self, *args, **kwargs):
            observe("solve")
            return result
    monkeypatch.setattr(importlib.import_module("stowage.solver.exact"), "ExactPlanner", Exact)
    def finalize(*args, **kwargs):
        observe("finalize")
        if fail_finalize:
            raise RuntimeError("finisher failed")
        return result
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize", finalize)
    count = SimpleNamespace(weighted_nonproductive=0, model_dump=lambda **k: {})
    def check(*args):
        observe("checker")
        return SimpleNamespace(passed=True, violations=[], count=count, model_dump_json=lambda: "{}")
    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check", check)
    def replay(instance, plan, details):
        observe("mask")
        details["passed"] = True
    monkeypatch.setattr(driver, "replay", replay)
    config = dict(exact_s=1, solver_s=1, ballast_s=1, checker_s=1, mask_s=1,
                  constructor_mode="default", scalable_config={}, ballast_v4=True)
    row = driver.process_case(path, corpus, output, config)
    assert row["passed"] is (not fail_finalize)
    assert ("finalize", "1") in observed
    assert all(value is None for stage, value in observed if stage != "finalize")
    assert {"input", "solve", "finalize"} <= {stage for stage, _ in observed}
    if not fail_finalize:
        assert {"checker", "mask"} <= {stage for stage, _ in observed}
    assert driver.os.environ.get("STOWAGE_BALLAST_V4") == "external-value"


def test_zero_worker_policy_is_valid_and_dispatches_nothing(tmp_path):
    path = tmp_path / "policy.json"
    driver.atomic_json(path, {"max_workers": 0})
    policy = driver.load_policy(path)
    assert policy["max_workers"] == 0
    candidate = tmp_path / "corpus/train/tiny/feeder_500/random_yard/seed-000000001.json"
    assert driver.select_pending([candidate], tmp_path / "gold", tmp_path / "corpus", 0, policy) == []


@pytest.mark.parametrize("workers", [-1, 13])
def test_worker_policy_rejects_out_of_range_caps(tmp_path, workers):
    path = tmp_path / "policy.json"
    driver.atomic_json(path, {"max_workers": workers})
    with pytest.raises(ValueError):
        driver.load_policy(path)


def test_zero_worker_policy_pauses_without_launches_or_reservations(tmp_path, monkeypatch):
    output, policy, stop = tmp_path / "gold", tmp_path / "policy.json", tmp_path / "stop"
    driver.atomic_json(policy, {"max_workers": 0})
    reservations, states = [], []
    monkeypatch.setattr(driver, "discover", lambda *a: pytest.fail("paused policy discovered work"))
    monkeypatch.setattr(driver.subprocess, "Popen", lambda *a, **k: pytest.fail("paused policy launched worker"))
    monkeypatch.setattr(driver, "update_ledger_reservation", lambda ledger, owner, workers: reservations.append(workers))
    monkeypatch.setattr(driver, "publish_runtime", lambda output, row: states.append(row))
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.time, "sleep", lambda seconds: stop.write_text("stop after one paused poll"))
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output), "--follow",
                        "--policy-file", str(policy), "--stop-file", str(stop),
                        "--admit-ledger", "unused-ledger", "--ledger-owner", "mvp-gold/test"])
    driver.main()
    paused = next(row for row in states if row["status"] == "policy_paused")
    assert paused["workers"] == 0 and paused["active_worker_pids"] == []
    assert reservations and set(reservations) == {0}
    assert states[-1]["status"] == "stopped"


def test_admission_and_reservation_share_lock_released_before_launch(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = commit_case(corpus)
    events = []
    def assert_locked():
        with (output / ".admission.lock").open("a") as handle:
            with pytest.raises(BlockingIOError):
                driver.fcntl.flock(handle, driver.fcntl.LOCK_EX | driver.fcntl.LOCK_NB)
    def admit(*args, **kwargs):
        assert_locked()
        events.append("admission")
        return {"admitted": True, "actual_workers": 1}
    def reserve(ledger, owner, workers):
        if workers:
            assert_locked()
            events.append("reserve")
    class Worker:
        pid = 123
        def __init__(self, command, **kwargs):
            with (output / ".admission.lock").open("a") as handle:
                driver.fcntl.flock(handle, driver.fcntl.LOCK_EX | driver.fcntl.LOCK_NB)
                driver.fcntl.flock(handle, driver.fcntl.LOCK_UN)
            events.append("launch")
            config = json.loads(command[command.index("--config-json") + 1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
        def wait(self):
            return 0
    monkeypatch.setattr(driver, "select_admission", admit)
    monkeypatch.setattr(driver, "update_ledger_reservation", reserve)
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--max-instances", "1",
                        "--admit-ledger", "unused-ledger", "--ledger-owner", "mvp-gold/test"])
    driver.main()
    assert events == ["admission", "reserve", "launch"]


def test_live_securing_screen_reload_changes_new_cases_and_retains_finished_bytes(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for seed in (1, 2):
        commit_case(corpus, f"seed-{seed}")
    settings_path = tmp_path / "scalable.json"
    driver.atomic_json(settings_path, {"securing_screen": False})
    configs, original = [], {}
    class Worker:
        pid = 123
        def __init__(self, command, **kwargs):
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            configs.append(config)
            directory = driver.result_dir(path, corpus, output)
            driver.atomic_json(directory / "plan.json", {"plan": "mock-finished", "seed": json.loads(path.read_text())["seed"]})
            row = cached_result(path, config) | {
                "passed": True, "instance_id": json.loads(path.read_text())["id"],
                "plan_sha256": driver.sha256(directory / "plan.json")}
            driver.atomic_json(directory / "result.json", row)
            if len(configs) == 1:
                original.update(directory=directory, result=(directory / "result.json").read_bytes(),
                                plan=(directory / "plan.json").read_bytes())
                driver.atomic_json(settings_path, {"securing_screen": True})
        def wait(self):
            return 0
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--max-instances", "2", "--resume-finished",
                        "--scalable-config", str(settings_path)])
    driver.main()
    assert [config["scalable_config"]["securing_screen"] for config in configs] == [False, True]
    directory = original["directory"]
    assert (directory / "result.json").read_bytes() == original["result"]
    assert (directory / "plan.json").read_bytes() == original["plan"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert {config["scalable_config"]["securing_screen"] for config in manifest["configurations"]} == {False, True}


def test_case_config_reload_returns_new_snapshot_for_expanded_ship_settings(tmp_path):
    scalable, inputs = tmp_path / "scalable.json", tmp_path / "inputs.json"
    original = {"scalable_config": {"securing_screen": False}, "input_settings": {"feeder_500": {"booking_slack": .25}}}
    expanded = {"feeder_500": {"booking_slack": .25, "booking_mass_fraction": .55},
                "feeder_800": {"booking_slack": .25, "booking_mass_fraction": .45},
                "feeder_1700": {"booking_slack": .25, "booking_mass_fraction": .40}}
    driver.atomic_json(scalable, {"securing_screen": True})
    driver.atomic_json(inputs, expanded)
    updated = driver.reload_case_config(original, scalable, inputs)
    assert updated is not original
    assert updated["scalable_config"]["securing_screen"] is True
    assert updated["input_settings"] == expanded
    assert original["scalable_config"]["securing_screen"] is False
    assert "feeder_1700" not in original["input_settings"]


def test_live_load_first_ignores_stale_reservations_and_limits_new_starts(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(16, 112))
    mock_live_capacity(monkeypatch, load=12.2, free=90)
    live = driver.select_admission(ledger, 10, adaptive=True, ledger_owner="mvp-gold/current",
                                   admission_mode="live_load_first")
    assert live["admitted"] and live["actual_workers"] == 3
    default = driver.select_admission(ledger, 10, adaptive=True, ledger_owner="mvp-gold/current")
    assert not default["admitted"] and default["actual_workers"] == 0
    ramped = driver.select_admission(ledger, 10, adaptive=True, ledger_owner="mvp-gold/current",
                                     admission_mode="live_load_first", recent_start_workers=3)
    assert ramped["actual_workers"] <= 1
    full = driver.select_admission(ledger, 10, adaptive=True, ledger_owner="mvp-gold/current",
                                   admission_mode="live_load_first", recent_start_workers=4)
    assert not full["admitted"] and full["actual_workers"] == 0


@pytest.mark.parametrize("load,free", [(16.1, 90), (12.2, 14)])
def test_live_load_first_queues_for_real_load_or_memory_pressure(tmp_path, monkeypatch, load, free):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(0))
    mock_live_capacity(monkeypatch, load=load, free=free)
    result = driver.select_admission(ledger, 3, adaptive=True, max_load=20,
                                     admission_mode="live_load_first")
    assert not result["admitted"] and result["actual_workers"] == 0


def test_live_start_leases_are_shared_expire_and_release_only_own_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(driver.os, "getpid", lambda: 111)
    driver.reserve_live_start(tmp_path, 3, now=1000)
    assert driver.recent_live_workers(tmp_path, now=1001) == 3
    monkeypatch.setattr(driver.os, "getpid", lambda: 222)
    driver.reserve_live_start(tmp_path, 1, now=1001)
    assert driver.recent_live_workers(tmp_path, now=1002) == 4
    monkeypatch.setattr(driver.os, "getpid", lambda: 111)
    driver.reserve_live_start(tmp_path, 0, now=1002)
    assert driver.recent_live_workers(tmp_path, now=1003) == 1
    assert driver.recent_live_workers(tmp_path, now=1062) == 0


def test_live_ledger_tracks_spawned_and_finished_pids_not_batch_size(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for seed in (1, 2):
        commit_case(corpus, f"seed-{seed}")
    policy = tmp_path / "policy.json"
    driver.atomic_json(policy, {"admission_mode": "live_load_first", "max_workers": 2})
    spawned, finished, reservations = set(), set(), []
    event_lock = threading.Lock()
    both_started, first_removed, first_reserved = threading.Barrier(2), threading.Event(), threading.Event()
    next_pid = [0]
    def reserve(ledger, owner, workers):
        with event_lock:
            assert workers == len(spawned - finished)
            reservations.append(workers)
            if workers == 1 and not finished:
                first_reserved.set()
            if 1 in finished and workers == 1:
                first_removed.set()
    def admit(*args, **kwargs):
        assert kwargs["admission_mode"] == "live_load_first"
        return {"admitted": True, "actual_workers": 2}
    class Worker:
        def __init__(self, command, **kwargs):
            with event_lock:
                next_pid[0] += 1
                self.pid = next_pid[0]
            if self.pid == 2:
                assert first_reserved.wait(timeout=5)
            with event_lock:
                spawned.add(self.pid)
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
        def wait(self):
            both_started.wait(timeout=5)
            if self.pid == 2:
                assert first_removed.wait(timeout=5)
            with event_lock:
                finished.add(self.pid)
            return 0
    monkeypatch.setattr(driver, "select_admission", admit)
    monkeypatch.setattr(driver, "update_ledger_reservation", reserve)
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--workers", "2", "--max-instances", "2",
                        "--poll-s", ".001", "--policy-file", str(policy), "--admit-ledger", "unused-ledger",
                        "--ledger-owner", "mvp-gold/test"])
    driver.main()
    assert 2 in reservations
    assert reservations[-1] == 0
    assert finished == spawned == {1, 2}


def test_continuous_live_scheduler_refills_before_long_running_case_finishes(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for seed in (1, 2, 3):
        commit_case(corpus, f"seed-{seed}")
    policy = tmp_path / "policy.json"
    driver.atomic_json(policy, {"admission_mode": "live_load_first", "max_workers": 2})
    both_started, third_started = threading.Barrier(2), threading.Event()
    lock = threading.Lock()
    spawned, finished, reservations, runtimes = [], set(), [], []
    class Worker:
        def __init__(self, command, **kwargs):
            with lock:
                self.pid = len(spawned) + 1
                spawned.append(self.pid)
                if self.pid == 3:
                    assert 1 not in finished
                    third_started.set()
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
        def wait(self):
            if self.pid in (1, 2):
                both_started.wait(timeout=5)
            if self.pid == 1:
                assert third_started.wait(timeout=5), "finished short case did not refill while first case was active"
            with lock:
                finished.add(self.pid)
            return 0
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "select_admission", lambda ledger, workers, *a, **k: {
        "admitted": True, "actual_workers": workers})
    monkeypatch.setattr(driver, "update_ledger_reservation", lambda ledger, owner, workers: reservations.append(workers))
    monkeypatch.setattr(driver, "publish_runtime", lambda output, row: runtimes.append(row))
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--workers", "2", "--max-instances", "3",
                        "--poll-s", ".001", "--policy-file", str(policy),
                        "--admit-ledger", "unused-ledger", "--ledger-owner", "mvp-gold/test"])
    driver.main()
    assert spawned == [1, 2, 3] and finished == {1, 2, 3}
    assert max(reservations) == 2 and reservations[-1] == 0
    assert all(row["active_workers"] == len(row["active_worker_pids"]) for row in runtimes)


def test_continuous_live_scheduler_stop_drains_without_starting_pending_case(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for seed in (1, 2, 3):
        commit_case(corpus, f"seed-{seed}")
    policy, stop = tmp_path / "policy.json", tmp_path / "stop"
    driver.atomic_json(policy, {"admission_mode": "live_load_first", "max_workers": 2})
    both_started, release_first = threading.Barrier(2), threading.Event()
    lock = threading.Lock()
    spawned, finished, reservations = [], set(), []
    class Worker:
        def __init__(self, command, **kwargs):
            with lock:
                self.pid = len(spawned) + 1
                spawned.append(self.pid)
                assert self.pid <= 2, "stop admitted a pending third case"
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
        def wait(self):
            both_started.wait(timeout=5)
            if self.pid == 2:
                stop.write_text("cooperative stop with first worker still active")
            else:
                assert release_first.wait(timeout=5)
                assert stop.exists()
            with lock:
                finished.add(self.pid)
            return 0
    def runtime(output, row):
        if stop.exists():
            release_first.set()
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "select_admission", lambda ledger, workers, *a, **k: {
        "admitted": True, "actual_workers": workers})
    monkeypatch.setattr(driver, "update_ledger_reservation", lambda ledger, owner, workers: reservations.append(workers))
    monkeypatch.setattr(driver, "publish_runtime", runtime)
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--workers", "2", "--follow", "--stop-file", str(stop),
                        "--poll-s", ".001", "--policy-file", str(policy),
                        "--admit-ledger", "unused-ledger", "--ledger-owner", "mvp-gold/test"])
    driver.main()
    assert spawned == [1, 2] and finished == {1, 2}
    assert reservations[-1] == 0


def test_continuous_live_scheduler_lowered_cap_blocks_refill_until_slot_is_free(tmp_path, monkeypatch):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    for seed in (1, 2, 3):
        commit_case(corpus, f"seed-{seed}")
    policy = tmp_path / "policy.json"
    driver.atomic_json(policy, {"admission_mode": "live_load_first", "max_workers": 2})
    both_started, release_first = threading.Barrier(2), threading.Event()
    lock = threading.Lock()
    spawned, finished, states = [], set(), []
    class Worker:
        def __init__(self, command, **kwargs):
            with lock:
                self.pid = len(spawned) + 1
                spawned.append(self.pid)
                if self.pid == 3:
                    assert 1 in finished, "lowered one-worker cap refilled while first worker remained active"
            path = Path(command[command.index("--case") + 1])
            config = json.loads(command[command.index("--config-json") + 1])
            driver.atomic_json(driver.result_dir(path, corpus, output) / "result.json", cached_result(path, config))
        def wait(self):
            if self.pid in (1, 2):
                both_started.wait(timeout=5)
            if self.pid == 2:
                driver.atomic_json(policy, {"admission_mode": "live_load_first", "max_workers": 1})
            if self.pid == 1:
                assert release_first.wait(timeout=5)
            with lock:
                finished.add(self.pid)
            return 0
    def runtime(output, row):
        states.append(row)
        if row["effective_policy"].get("max_workers") == 1 and row["active_workers"] == 1:
            with lock:
                if 1 not in finished:
                    assert spawned == [1, 2]
                    release_first.set()
    monkeypatch.setattr(driver.subprocess, "Popen", Worker)
    monkeypatch.setattr(driver, "select_admission", lambda ledger, workers, *a, **k: {
        "admitted": True, "actual_workers": workers})
    monkeypatch.setattr(driver, "update_ledger_reservation", lambda *a: None)
    monkeypatch.setattr(driver, "publish_runtime", runtime)
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output),
                        "--corpus-root", str(corpus), "--workers", "2", "--max-instances", "3",
                        "--poll-s", ".001", "--policy-file", str(policy),
                        "--admit-ledger", "unused-ledger", "--ledger-owner", "mvp-gold/test"])
    driver.main()
    assert spawned == [1, 2, 3] and finished == {1, 2, 3}
    assert all(row["active_workers"] <= 1 for row in states if row["effective_policy"].get("max_workers") == 1)


def make_small_fixture(corpus):
    tiny = commit_case(corpus)
    path = corpus / "train/small/feeder_500/random_yard" / tiny.name
    path.parent.mkdir(parents=True)
    tiny.replace(path)
    old_audit = tiny.with_suffix(".audit.json")
    audit = json.loads(old_audit.read_text())
    audit["config"]["rung"] = "small"
    driver.atomic_json(path.with_suffix(".audit.json"), audit)
    old_audit.unlink()
    driver.atomic_json(corpus / "train.requested.json", {"specs": [audit["config"]], "counts": {"small": 1}})
    return path


def test_small_constructor_policy_metadata_is_outside_case_configuration(tmp_path):
    assert driver.policy_metadata("small") == {"policy": "scalable-constructor-v1"}
    assert driver.policy_metadata("tiny") == {}
    assert driver.policy_metadata("medium") == {}
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = make_small_fixture(corpus)
    # A checksum mismatch exercises fresh-row publication without invoking a solver.
    driver.atomic_json(path, json.loads(path.read_text()) | {"synthetic": "changed"})
    config = {"solver_s": 1, "scalable_config": {}}
    row = driver.process_case(path, corpus, output, config)
    assert row["policy"] == "scalable-constructor-v1"
    assert row["drop_reason"] == "input" and not row["passed"]
    assert row["config"] == config and "policy" not in row["config"]


def test_finished_small_policy_metadata_is_not_retroactively_relabelled(tmp_path):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = make_small_fixture(corpus)
    directory = driver.result_dir(path, corpus, output)
    prior = cached_result(path, {"solver_s": 1})
    driver.atomic_json(directory / "result.json", prior)
    before = (directory / "result.json").read_bytes()
    result = driver.process_case(path, corpus, output, {"solver_s": 2}, resume_finished=True)
    assert result == prior and "policy" not in result
    assert (directory / "result.json").read_bytes() == before


def test_measured_cpu_admits_low_busy_cpu_despite_high_load_and_stale_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(16, 112))
    mock_live_capacity(monkeypatch, load=35, free=90)
    monkeypatch.setattr(driver, "measure_cpu_usage", lambda: {"cores": 4, "source": "ps", "errors": []})
    result = driver.select_admission(ledger, 10, adaptive=True, admission_mode="measured_cpu")
    assert result["admitted"] and result["actual_workers"] == 10
    default = driver.select_admission(ledger, 10, adaptive=True)
    assert not default["admitted"] and default["actual_workers"] == 0


@pytest.mark.parametrize("cpu,free", [(15, 90), (16, 90), (4, 14), (None, 14)])
def test_measured_cpu_queues_at_busy_cpu_ceiling_or_memory_pressure(tmp_path, monkeypatch, cpu, free):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(0))
    mock_live_capacity(monkeypatch, load=1, free=free)
    monkeypatch.setattr(driver, "measure_cpu_usage", lambda: {
        "cores": cpu, "source": "unavailable" if cpu is None else "ps", "errors": []})
    result = driver.select_admission(ledger, 8, adaptive=True, admission_mode="measured_cpu")
    assert not result["admitted"] and result["actual_workers"] == 0


def test_measured_cpu_counts_pending_starts_against_busy_cpu_limit(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(16))
    mock_live_capacity(monkeypatch, load=30, free=90)
    monkeypatch.setattr(driver, "measure_cpu_usage", lambda: {"cores": 12, "source": "ps", "errors": []})
    first = driver.select_admission(ledger, 8, adaptive=True, admission_mode="measured_cpu")
    assert first["actual_workers"] == 3
    repeated = driver.select_admission(ledger, 8, adaptive=True, admission_mode="measured_cpu",
                                       recent_start_workers=3)
    assert repeated["actual_workers"] == 0


@pytest.mark.parametrize("active,pending,expected", [(0, 0, 8), (5, 0, 3), (6, 2, 0), (8, 0, 0), (2, 3, 3)])
def test_unavailable_cpu_fallback_is_eight_total_workers(tmp_path, monkeypatch, active, pending, expected):
    ledger = tmp_path / "ledger.md"
    ledger.write_text(ledger_text(16, 112))
    mock_live_capacity(monkeypatch, load=40, free=90)
    monkeypatch.setattr(driver, "measure_cpu_usage", lambda: {
        "cores": None, "source": "unavailable", "errors": ["ps denied", "top denied"]})
    result = driver.select_admission(ledger, 10, adaptive=True, admission_mode="measured_cpu",
                                     active_gold_workers=active, recent_start_workers=pending)
    assert result["actual_workers"] == expected
    if expected:
        repeated = driver.select_admission(ledger, 10, adaptive=True, admission_mode="measured_cpu",
                                           active_gold_workers=active, recent_start_workers=pending + expected)
        assert repeated["actual_workers"] == 0


def test_shared_active_worker_count_unions_supervisor_pid_sets(tmp_path):
    driver.atomic_json(tmp_path / "runtime/111.json", {"pid": 111, "active_worker_pids": [101, 102]})
    driver.atomic_json(tmp_path / "runtime/222.json", {"pid": 222, "active_worker_pids": [102, 103]})
    driver.atomic_json(tmp_path / "runtime/333.json", {"pid": 333, "active_worker_pids": []})
    assert driver.shared_active_workers(tmp_path) == 3


def test_cpu_measurement_sums_ps_percentages_without_real_process_probe(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[0] == "ps"
        return SimpleNamespace(returncode=0, stdout="100.0\n250.0\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", run)
    measurement = driver.measure_cpu_usage()
    assert measurement["source"] == "ps" and measurement["cores"] == pytest.approx(3.5)
    assert len(calls) == 1


def test_cpu_measurement_falls_back_to_top_idle_on_eighteen_cores(monkeypatch):
    def run(command, **kwargs):
        if command[0] == "ps":
            return SimpleNamespace(returncode=1, stdout="", stderr="Operation not permitted")
        assert command[0] == "top"
        return SimpleNamespace(returncode=0, stdout="CPU usage: 10.0% user, 20.0% sys, 70.0% idle\n", stderr="")
    monkeypatch.setattr(driver.subprocess, "run", run)
    measurement = driver.measure_cpu_usage()
    assert measurement["source"] == "top" and measurement["cores"] == pytest.approx(5.4)


def test_cpu_measurement_reports_unavailable_when_both_probes_denied(monkeypatch):
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="Operation not permitted"))
    def denied():
        raise OSError("Mach CPU probe denied")
    monkeypatch.setattr(driver, "measure_mach_cpu_usage", denied)
    measurement = driver.measure_cpu_usage()
    assert measurement["source"] == "unavailable" and measurement["cores"] is None
    assert len(measurement["errors"]) == 3


def test_cpu_measurement_uses_mach_after_ps_and_top_denied(monkeypatch):
    monkeypatch.setattr(driver.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="Operation not permitted"))
    monkeypatch.setattr(driver, "measure_mach_cpu_usage", lambda: 11.18)
    measurement = driver.measure_cpu_usage()
    assert measurement["source"] == "mach-host-cpu-ticks"
    assert measurement["cores"] == 11.18
    assert len(measurement["errors"]) == 2


def test_mach_busy_cores_accounts_for_all_busy_categories_and_counter_wrap():
    before = (0xfffffff0, 100, 0xfffffff0, 20)
    after = (4, 110, 14, 25)
    assert driver._mach_busy_cores(before, after, 18) == pytest.approx(35/65*18)


@pytest.mark.parametrize("before,after,cpus", [
    ((1, 2, 3, 4), (1, 2, 3, 4), 18),
    ((1, 2, 3), (2, 3, 4), 18),
    ((1, 2, 3, -1), (2, 3, 4, 5), 18),
    ((1, 2, 3, 4), (2, 3, 4, 5), None),
    ((1, 2, 3, 4), (2, 3, 4, 5), 19),
])
def test_mach_busy_cores_rejects_invalid_or_nonadvancing_samples(before, after, cpus):
    with pytest.raises(ValueError):
        driver._mach_busy_cores(before, after, cpus)


def test_mach_measurement_samples_twice_with_requested_interval(monkeypatch):
    monkeypatch.setattr(driver.sys, "platform", "darwin")
    monkeypatch.setattr(driver.os, "cpu_count", lambda: 18)
    samples = iter([(10, 20, 30, 40), (12, 23, 40, 45)])
    monkeypatch.setattr(driver, "_mach_cpu_ticks", lambda: next(samples))
    waits = []
    monkeypatch.setattr(driver.time, "sleep", waits.append)
    assert driver.measure_mach_cpu_usage(.5) == pytest.approx(9)
    assert waits == [.5]


def test_mach_measurement_rejects_other_operating_system(monkeypatch):
    monkeypatch.setattr(driver.sys, "platform", "linux")
    with pytest.raises(OSError, match="requires Darwin"):
        driver.measure_mach_cpu_usage()


@pytest.mark.parametrize("status,count", [(5, 4), (0, 3)])
def test_mach_tick_api_rejects_kernel_error_and_short_result(monkeypatch, status, count):
    import ctypes
    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    def statistics(host, flavor, ticks, result_count):
        assert host == 123 and flavor == 3
        result_count._obj.value = count
        return status
    library = SimpleNamespace(mach_host_self=Function(lambda: 123),
                              host_statistics=Function(statistics))
    monkeypatch.setattr(ctypes, "CDLL", lambda path: library)
    with pytest.raises(ValueError, match="host_statistics failed"):
        driver._mach_cpu_ticks()


def test_runtime_snapshot_imports_pins_and_epoch_metadata_in_clean_child(tmp_path):
    snapshot, corpus, output = tmp_path / "epoch", tmp_path / "corpus", tmp_path / "gold"
    files = {
        "src/stowage/__init__.py": "ORIGIN = 'immutable-epoch'\n",
        "src/llm/__init__.py": "",
        "src/llm/stowlm/__init__.py": "ORIGIN = 'immutable-epoch'\n",
        "params/fixture.toml": "value = 1\n",
        "pyproject.toml": "[project]\nname = 'epoch-fixture'\nversion = '0.0.0'\n",
    }
    for name, content in files.items():
        destination = snapshot / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    pins = {name: driver.sha256(snapshot / name) for name in files}
    digest = hashlib.sha256(json.dumps(pins, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    driver.atomic_json(snapshot / "MANIFEST.json", {"files": pins, "epoch_sha256": digest})
    path = commit_case(corpus)
    driver.atomic_json(path, json.loads(path.read_text()) | {"synthetic": "checksum mismatch"})
    live = tmp_path / "live/src/stowage/live.py"
    live.parent.mkdir(parents=True)
    live.write_text("LIVE = 1\n")
    script = r'''
import importlib.util, json, os, pathlib, sys, types
driver_path, epoch, digest, candidate, corpus, output, live = sys.argv[1:]
spec = importlib.util.spec_from_file_location("isolated_gold_driver", driver_path)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
try:
    d.configure_runtime(epoch, "0" * 64)
except ValueError:
    pass
else:
    raise AssertionError("wrong epoch digest accepted")
metadata = d.configure_runtime(epoch, digest)
import stowage, llm.stowlm, math
assert stowage.ORIGIN == llm.stowlm.ORIGIN == "immutable-epoch"
assert d.ROOT == pathlib.Path(epoch)
assert sys.path[0] == str(pathlib.Path(epoch) / "src")
assert os.environ["PYTHONPATH"] == str(pathlib.Path(epoch) / "src")
assert os.environ["PYTHONNOUSERSITE"] == "1"
origins = d.assert_snapshot_imports()
assert {"stowage", "llm", "llm.stowlm"} <= origins.keys()
assert all(pathlib.Path(p).is_relative_to(pathlib.Path(epoch) / "src") for p in origins.values())
for name, module in [("stowage.bad", types.SimpleNamespace(__file__=live)),
                     ("llm.bad_namespace", types.SimpleNamespace(__path__=[str(pathlib.Path(live).parent)]))]:
    sys.modules[name] = module
    try:
        d.assert_snapshot_imports()
    except RuntimeError:
        pass
    else:
        raise AssertionError("live project module escaped epoch origin guard")
    finally:
        del sys.modules[name]
row = d.process_case(candidate, corpus, output, metadata)
assert row["drop_reason"] == "input" and not row["passed"]
assert row["runtime_epoch_sha256"] == digest
assert row["runtime_snapshot"] == epoch
d.publish_manifest(pathlib.Path(output), pathlib.Path(corpus), metadata)
manifest = json.loads((pathlib.Path(output) / "manifest.json").read_text())
assert manifest["configurations"][0]["runtime_epoch_sha256"] == digest
before = d.source_pin()
pathlib.Path(live).write_text("LIVE = 2\n")
assert d.source_pin() == before
changed = pathlib.Path(epoch) / "params/fixture.toml"
changed.write_text("value = 2\n")
assert d.source_pin()["params/fixture.toml"] != before["params/fixture.toml"]
try:
    d.configure_runtime(epoch, digest)
except ValueError:
    pass
else:
    raise AssertionError("mutated runtime epoch accepted")
print(json.dumps({"epoch": digest, "project_modules": len(origins)}))
'''
    result = driver.subprocess.run([driver.sys.executable, "-c", script, str(Path(driver.__file__).resolve()),
                                   str(snapshot), digest, str(path), str(corpus), str(output), str(live)],
                                  capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout.splitlines()[-1])["epoch"] == digest


def test_securing_replay_uses_production_d95_decoder_factory_and_c2_off(monkeypatch):
    instance = SimpleNamespace(containers=[])
    actions = importlib.import_module("stowage.actions")
    feasibility = importlib.import_module("stowage.feasibility")
    mask = importlib.import_module("llm.stowlm.mask")
    formatting = importlib.import_module("llm.stowlm.format")
    securing = importlib.import_module("llm.securing")
    called = []
    codec = SimpleNamespace(end_plan_tokens=[99])
    decoder = SimpleNamespace(codec=codec, status="active", actions=[], tokens=[])
    def advance(token):
        decoder.tokens.append(token)
        decoder.status = "complete"
    decoder.advance = advance
    engine = SimpleNamespace(instance=instance, diagnostics={})
    monkeypatch.setattr(actions, "get_action_space", lambda: SimpleNamespace(from_plan=lambda *a: []))
    def make_engine(current, space, config):
        assert current is instance and config.extra["global_screen"] == 0
        called.append("c2-off")
        return engine
    monkeypatch.setattr(feasibility, "make_feasibility_engine", make_engine)
    monkeypatch.setattr(formatting, "load_tokenizer", lambda: object())
    monkeypatch.setattr(formatting, "ActionCodec", lambda *a: codec)
    monkeypatch.setattr(mask, "MaskedDecoder", lambda *a: pytest.fail("securing replay bypassed D95 factory"))
    def factory_builder():
        called.append("factory-built")
        def factory(current, current_engine, current_codec, config, n):
            assert current is instance and current_engine is engine and current_codec is codec and n == 1
            called.append("factory-called")
            return [decoder]
        factory.name = "irs-securing-necessary-envelope"
        return factory
    monkeypatch.setattr(securing, "securing_decoder_factory", factory_builder)
    details = {}
    driver.replay(instance, object(), details, securing=True)
    assert called == ["c2-off", "factory-built", "factory-called"]
    assert details["passed"] and details["actions_equal"]


@pytest.mark.parametrize("budget", [None, 240])
@pytest.mark.parametrize("checked_objective,passed", [(8, True), (10, True), (11, False)])
def test_warmstart_quality_gate_uses_independent_checker_objective(tmp_path, monkeypatch, checked_objective, passed, budget):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = make_small_fixture(corpus)
    audit_path = path.with_suffix(".audit.json")
    driver.atomic_json(audit_path, json.loads(audit_path.read_text()) | {"requested_targets": 0})
    monkeypatch.setattr(driver, "source_pin", lambda: {})
    monkeypatch.setattr(importlib.import_module("stowage.frozen"), "require_clean", lambda *a, **k: None)
    monkeypatch.setattr(importlib.import_module("stowage.generate.background_acceptance"), "requires_background_gate", lambda *a, **k: False)
    catalogue = importlib.import_module("stowage.rules.catalogue")
    monkeypatch.setattr(catalogue, "catalogue_snapshot", lambda: SimpleNamespace(content_sha256="digest"))
    monkeypatch.setattr(catalogue, "rule_freeze_v1_enabled", lambda: False)
    monkeypatch.setattr(importlib.import_module("stowage.versions"), "component_versions", lambda: {})
    instance = SimpleNamespace(id="synthetic-1", seed=1, size_class="small", load_list=[], voyage=object(), vessel=object())
    io = importlib.import_module("stowage.io")
    monkeypatch.setattr(io, "load_instance", lambda *a: instance)
    monkeypatch.setattr(io, "save_plan", lambda plan, filename: Path(filename).write_text("{}"))
    class Result:
        status, planner, planner_version, message = "ok", "gold-warmstart", "mock", "mock"
        metrics = {"source": "two_step_fallback" if checked_objective == 10 else "fixed_stow_yard_lns",
                   "objective_before": 10, "objective_after": 10 if checked_objective == 10 else 8,
                   "lns_elapsed_s": .01, "baseline_checker_passed": True, "baseline_mask": {"passed": True}}
        plan, shippable = SimpleNamespace(ballast={}), True
        def _record_full_check(self, *args):
            pass
    result = Result()
    calls = []
    def solve(instance, seed, lns_s=15, ballast_s=90):
        assert ballast_s == (budget or 90)
        assert lns_s == 15 and driver.os.environ.get("STOWAGE_BALLAST_V4") is None
        calls.append("solve-gold")
        return result, {"two_step_weighted_moves": 10, "gold_weighted_moves": 8,
                        "lns_elapsed_s": .01, "baseline_producer_version": "4.3.0", "baseline_producer_sha256": {}}
    monkeypatch.setattr(driver, "solve_gold_candidate", solve)
    def finalize(*args, **kwargs):
        assert kwargs["time_limit_s"] == (budget or 120)
        assert driver.os.environ.get("STOWAGE_BALLAST_V4") == "1"
        calls.append("scoped-finalize")
        return result
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize", finalize)
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "ScalableSolver", lambda *a: pytest.fail("warmstart used constructor solver"))
    def check(*args):
        calls.append("checker")
        count = SimpleNamespace(weighted_nonproductive=checked_objective, model_dump=lambda **k: {})
        return SimpleNamespace(passed=True, violations=[], count=count, model_dump_json=lambda: "{}")
    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check", check)
    def replay(instance, plan, details, *, securing=False):
        assert securing, "warmstart skipped D95 mask layer"
        calls.append("d95-mask")
        details["passed"] = True
    monkeypatch.setattr(driver, "replay", replay)
    config = dict(exact_s=1, solver_s=1, ballast_s=1, checker_s=1, mask_s=1,
                  constructor_mode="default", scalable_config={}, ballast_v4=True, gold_warmstart_v1=True,
                  gold_constructor_fallback_v1=True)
    if budget is not None:
        config["small_ballast_s"] = budget
    row = driver.process_case(path, corpus, output, config)
    assert row["ballast_budgets_s"] == {"internal": budget or 90, "external": budget or 120}
    assert row["stages"]["ballast"]["limit_s"] == (budget or 120)+5
    assert row["stages"]["gold_warmstart"]["limit_s"] == 120+(budget or 90)+15+1+1+5
    assert row["passed"] is passed, row.get("error")
    assert calls[:3] == ["solve-gold", "scoped-finalize", "checker"]
    assert row["gold_weighted_moves"] == checked_objective
    assert row["quality_delta"] == checked_objective - 10
    if passed:
        assert "d95-mask" in calls and row["stages"]["checker"]["passed"] and row["stages"]["mask"]["passed"]
        assert row["policy"] == "gold-warmstart-v2"
        assert row["solver_metrics"]["source"] == result.metrics["source"]
        assert (driver.result_dir(path, corpus, output) / "plan.json").exists()
    else:
        assert row["drop_reason"] == "quality"
        assert not (driver.result_dir(path, corpus, output) / "plan.json").exists()


def test_gold_return_adapter_preserves_result_and_measured_comparator(tmp_path, monkeypatch):
    twostep = importlib.import_module("stowage.solver.twostep")
    gold = importlib.import_module("stowage.solver.gold_warmstart")
    monkeypatch.setattr(twostep, "VERSION", "4.3.0")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    pins = {}
    for name in ("twostep", "twostep_adapter", "twostep_terminal", "twostep_construct"):
        relative = f"src/stowage/solver/{name}.py"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# frozen {name}\n")
        pins[relative] = driver.sha256(path)
    result = SimpleNamespace(metrics={"objective_before": 10, "objective_after": 8, "lns_elapsed_s": .3})
    instance = object()
    def solve(current, *, seed, lns_time_limit_s):
        assert current is instance and seed == 7 and lns_time_limit_s == 15
        return result
    monkeypatch.setattr(gold, "solve_gold", solve)
    returned, metadata = driver.solve_gold_candidate(instance, 7)
    assert returned is result
    assert metadata["two_step_weighted_moves"] == 10 and metadata["gold_weighted_moves"] == 8
    assert metadata["lns_elapsed_s"] == .3
    assert metadata["baseline_producer_version"] == "4.3.0"
    assert metadata["baseline_producer_sha256"] == pins


def test_gold_return_adapter_rejects_other_baseline_version_before_solving(monkeypatch):
    monkeypatch.setattr(importlib.import_module("stowage.solver.twostep"), "VERSION", "4.4.0")
    monkeypatch.setattr(importlib.import_module("stowage.solver.gold_warmstart"), "solve_gold",
                        lambda *a, **k: pytest.fail("unapproved baseline producer reached gold solver"))
    with pytest.raises(RuntimeError, match="4.3.0"):
        driver.solve_gold_candidate(object(), 7)


def mock_legacy_candidate(tmp_path, monkeypatch, *, old_value=7, fresh_value=8,
                          baseline=10, checker_passed=True, mask_passed=True):
    directory = tmp_path / "legacy"
    driver.atomic_json(directory / "plan.json", {"untouched": "archived-plan"})
    input_sha = "a" * 64
    driver.atomic_json(directory / "result.json", {"passed": True, "input_sha256": input_sha,
                       "plan_sha256": driver.sha256(directory / "plan.json")})
    legacy, fresh, instance = object(), object(), object()
    warm = SimpleNamespace(status="ok", plan=fresh, metrics={"objective_before": baseline,
                           "baseline_checker_passed": True, "baseline_mask": {"passed": True}})
    calls = []
    def load(path):
        calls.append("load")
        return legacy
    monkeypatch.setattr(importlib.import_module("stowage.io"), "load_plan", load)
    monkeypatch.setattr(importlib.import_module("stowage.frozen"), "require_clean", lambda *a, **k: None)
    def apply(current, plan):
        assert current is instance
        calls.append("apply-old" if plan is legacy else "apply-fresh")
        return SimpleNamespace(legal=True, count=SimpleNamespace(
            weighted_nonproductive=old_value if plan is legacy else fresh_value))
    monkeypatch.setattr(importlib.import_module("stowage.moves"), "apply_plan", apply)
    def check(current, plan):
        assert current is instance and plan is legacy
        calls.append("checker")
        return SimpleNamespace(passed=checker_passed,
                               model_dump_json=lambda: json.dumps({"passed": checker_passed}))
    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check", check)
    def replay(current, plan, details, *, securing=False):
        assert current is instance and plan is legacy and securing
        calls.append("d95-mask")
        details["passed"] = mask_passed
    monkeypatch.setattr(driver, "replay", replay)
    return instance, directory, input_sha, warm, legacy, calls


def test_legacy_worse_than_baseline_is_dropped_before_checker_and_mask(tmp_path, monkeypatch):
    instance, directory, digest, warm, legacy, calls = mock_legacy_candidate(
        tmp_path, monkeypatch, old_value=11)
    before = {name: (directory / name).read_bytes() for name in ("result.json", "plan.json")}
    selected, record = driver.assess_legacy_candidate(instance, directory, digest, warm)
    assert selected is None and record["drop_reason"] == "legacy_worse_than_two_step"
    assert calls == ["load", "apply-old", "apply-fresh"]
    assert {name: (directory / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("old_value,selected,decision", [(7, True, "selected_legacy"),
                                                        (8, True, "selected_legacy"),
                                                        (9, False, "superseded_by_better_gold")])
def test_legacy_selection_requires_fresh_gates_and_no_worse_than_new_gold(tmp_path, monkeypatch, old_value, selected, decision):
    instance, directory, digest, warm, legacy, calls = mock_legacy_candidate(
        tmp_path, monkeypatch, old_value=old_value)
    candidate, record = driver.assess_legacy_candidate(instance, directory, digest, warm)
    assert (candidate is legacy) is selected
    assert record["selected"] is selected and record["decision"] == decision
    assert record["retain_legacy"] is True
    assert calls == ["load", "apply-old", "apply-fresh", "checker", "d95-mask"]
    assert record["legacy_objective"] == old_value and record["fresh_gold_objective"] == 8


@pytest.mark.parametrize("failed", ["checker", "mask", "baseline-checker", "baseline-mask"])
def test_legacy_candidate_cannot_reuse_historical_flags_for_fresh_gates(tmp_path, monkeypatch, failed):
    instance, directory, digest, warm, legacy, calls = mock_legacy_candidate(
        tmp_path, monkeypatch, checker_passed=failed != "checker", mask_passed=failed != "mask")
    if failed == "baseline-checker":
        warm.metrics["baseline_checker_passed"] = False
    if failed == "baseline-mask":
        warm.metrics["baseline_mask"] = {"passed": False}
    candidate, record = driver.assess_legacy_candidate(instance, directory, digest, warm)
    assert candidate is None and not record["retain_legacy"]
    assert record["drop_reason"].startswith("legacy_quality_")
    assert "checker" in calls
    assert ("d95-mask" in calls) is (failed != "checker")


@pytest.mark.parametrize("mismatch", ["input", "plan"])
def test_legacy_artifact_pin_mismatch_drops_before_loading_plan(tmp_path, monkeypatch, mismatch):
    instance, directory, digest, warm, legacy, calls = mock_legacy_candidate(tmp_path, monkeypatch)
    if mismatch == "input":
        digest = "b" * 64
    else:
        (directory / "plan.json").write_text("{}")
    candidate, record = driver.assess_legacy_candidate(instance, directory, digest, warm)
    assert candidate is None and not record["selected"]
    assert record["drop_reason"] == ("legacy_quality_input_mismatch" if mismatch == "input" else "legacy_quality_plan_pin_mismatch")
    assert calls == []


def test_legacy_missing_measured_baseline_remains_unverified(tmp_path, monkeypatch):
    instance, directory, digest, warm, legacy, calls = mock_legacy_candidate(tmp_path, monkeypatch, baseline=None)
    candidate, record = driver.assess_legacy_candidate(instance, directory, digest, warm)
    assert candidate is None and record["drop_reason"] == "legacy_quality_unverified"
    assert not record["retain_legacy"] and calls == []


def test_concurrent_legacy_decision_index_is_atomic_and_linked_from_manifest(tmp_path):
    driver.publish_manifest(tmp_path, tmp_path / "corpus", {"legacy_small_root": str(tmp_path / "legacy")})
    barrier = threading.Barrier(2)
    records = {"train/small/case-1": {"decision": "selected_legacy"},
               "train/small/case-2": {"decision": "superseded_by_better_gold"}}
    def publish(case):
        barrier.wait(timeout=5)
        driver.publish_legacy_decision(tmp_path, case, records[case])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(publish, case) for case in records]
        for future in futures:
            future.result(timeout=10)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    index = json.loads((tmp_path / manifest["legacy_quality_manifest"]).read_text())
    assert index["cases"] == records


@pytest.mark.parametrize("raises", [False, True])
def test_gold_internal_ballast_scope_does_not_reach_planning_or_restore_wrongly(tmp_path, monkeypatch, raises):
    twostep = importlib.import_module("stowage.solver.twostep")
    gold = importlib.import_module("stowage.solver.gold_warmstart")
    dispatch = importlib.import_module("stowage.solver.ballast_v4_dispatch")
    monkeypatch.setattr(twostep, "VERSION", "4.3.0")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    for name in ("twostep", "twostep_adapter", "twostep_terminal", "twostep_construct"):
        path = tmp_path / f"src/stowage/solver/{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {name}\n")
    monkeypatch.setenv("STOWAGE_BALLAST_V4", "external-value")
    observed = []
    def finish(*args, **kwargs):
        observed.append(("finish", driver.os.environ.get("STOWAGE_BALLAST_V4")))
        if raises:
            raise RuntimeError("internal ballast failed")
        return SimpleNamespace(status="ok", plan=object())
    monkeypatch.setattr(dispatch, "finish", finish)
    def solve(*args, **kwargs):
        observed.append(("planning", driver.os.environ.get("STOWAGE_BALLAST_V4")))
        dispatch.finish(object(), object(), time_limit_s=90, seed=7)
        observed.append(("qualification", driver.os.environ.get("STOWAGE_BALLAST_V4")))
        return SimpleNamespace(metrics={"objective_before": 10, "objective_after": 8, "lns_elapsed_s": .1})
    monkeypatch.setattr(gold, "solve_gold", solve)
    with driver.scoped_ballast_v4(False):
        if raises:
            with pytest.raises(RuntimeError, match="internal ballast failed"):
                driver.solve_gold_candidate(object(), 7)
        else:
            _, metadata = driver.solve_gold_candidate(object(), 7)
            assert len(metadata["internal_ballast_calls"]) == 1
            call = metadata["internal_ballast_calls"][0]
            assert call["time_limit_s"] == 90
            assert call["environment_before"] is None and call["environment_during"] == "1"
            assert call["environment_after"] is None and call["backend_already_direct_v4"] is True
        assert driver.os.environ.get("STOWAGE_BALLAST_V4") is None
    assert observed[:2] == [("planning", None), ("finish", "1")]
    if not raises:
        assert observed[-1] == ("qualification", None)
    assert dispatch.finish is finish
    assert driver.os.environ.get("STOWAGE_BALLAST_V4") == "external-value"


@pytest.mark.parametrize("failure", ["dead_end", "ballast_not_found", "unchecked_baseline"])
@pytest.mark.parametrize("veto", [None, "checker", "mask"])
def test_failed_two_step_baseline_uses_checked_constructor_fallback_without_fake_objective(tmp_path, monkeypatch, failure, veto):
    corpus, output = tmp_path / "corpus", tmp_path / "gold"
    path = make_small_fixture(corpus)
    audit_path = path.with_suffix(".audit.json")
    driver.atomic_json(audit_path, json.loads(audit_path.read_text()) | {"requested_targets": 0})
    monkeypatch.setattr(driver, "source_pin", lambda: {})
    monkeypatch.setattr(importlib.import_module("stowage.frozen"), "require_clean", lambda *a, **k: None)
    monkeypatch.setattr(importlib.import_module("stowage.generate.background_acceptance"), "requires_background_gate", lambda *a, **k: False)
    catalogue = importlib.import_module("stowage.rules.catalogue")
    monkeypatch.setattr(catalogue, "catalogue_snapshot", lambda: SimpleNamespace(content_sha256="digest"))
    monkeypatch.setattr(catalogue, "rule_freeze_v1_enabled", lambda: False)
    monkeypatch.setattr(importlib.import_module("stowage.versions"), "component_versions", lambda: {})
    instance = SimpleNamespace(id="synthetic-1", seed=1, size_class="small", load_list=[], voyage=object(), vessel=object())
    io = importlib.import_module("stowage.io")
    monkeypatch.setattr(io, "load_instance", lambda *a: instance)
    monkeypatch.setattr(io, "save_plan", lambda plan, filename: Path(filename).write_text("{}"))
    failed = SimpleNamespace(status="ok" if failure == "unchecked_baseline" else "error",
        plan=SimpleNamespace(ballast={}) if failure == "unchecked_baseline" else None,
        metrics={"baseline_checker_passed": False, "baseline_mask": {"passed": False}, "failure": failure},
        planner="mock-two-step", planner_version="4.3.0", message=failure)
    class Result:
        status, planner, planner_version, message = "ok", "mock-constructor", "mock", "mock"
        metrics = {}
        plan, shippable = SimpleNamespace(ballast={}), True
        def model_copy(self, update):
            self.__dict__.update(update)
            return self
        def _record_full_check(self, instance, report):
            self.shippable = report.passed
    result, calls = Result(), []
    monkeypatch.setattr(driver, "solve_gold_candidate", lambda *a, **k: (failed, {
        "two_step_weighted_moves": None, "gold_weighted_moves": None, "baseline_producer_version": "4.3.0"}))
    class Constructor:
        def __init__(self, config):
            assert config.securing_screen and config.heavy_first_ordering
            assert config.feasibility_config.extra["global_screen"] == 0
        def plan(self, current, *, seed, time_limit_s):
            assert current is instance and seed == 1 and time_limit_s == 120
            assert driver.os.environ.get("STOWAGE_BALLAST_V4") is None
            calls.append("constructor")
            return result
    monkeypatch.setattr(importlib.import_module("stowage.solver.scalable"), "ScalableSolver", Constructor)
    def finalize(current, candidate, *, time_limit_s):
        assert current is instance and candidate is result and time_limit_s == 120
        assert driver.os.environ.get("STOWAGE_BALLAST_V4") == "1"
        calls.append("finalize")
        return result
    monkeypatch.setattr(importlib.import_module("stowage.planner"), "finalize", finalize)
    def check(*args):
        assert driver.os.environ.get("STOWAGE_BALLAST_V4") is None
        calls.append("checker")
        return SimpleNamespace(passed=veto != "checker", violations=[], model_dump_json=lambda: "{}",
            count=SimpleNamespace(weighted_nonproductive=17, model_dump=lambda **k: {}))
    monkeypatch.setattr(importlib.import_module("stowage.checker"), "check", check)
    def replay(current, plan, details, *, securing=False):
        assert securing and driver.os.environ.get("STOWAGE_BALLAST_V4") is None
        calls.append("D95-mask")
        details["passed"] = veto != "mask"
        if veto == "mask":
            raise RuntimeError("D95 mask rejected fallback")
    monkeypatch.setattr(driver, "replay", replay)
    config = dict(exact_s=1, solver_s=1, ballast_s=1, checker_s=1, mask_s=1,
                  constructor_mode="default", scalable_config={}, ballast_v4=True,
                  gold_warmstart_v1=True, gold_constructor_fallback_v1=True)
    row = driver.process_case(path, corpus, output, config)
    assert row["passed"] is (veto is None), row.get("error")
    assert calls == ["constructor", "finalize", "checker"] + ([] if veto == "checker" else ["D95-mask"])
    assert row["two_step_weighted_moves"] is None
    assert row["source"] == "constructor-fallback (two-step failed)"
    assert row["policy"] == "constructor-fallback-two-step-failed-v1"
    assert row.get("quality_delta") is None
    if veto == "checker":
        assert row["drop_reason"] == "checker"
    else:
        assert row["objective"] == 17
    if veto:
        assert row["drop_reason"] == veto
        assert not (driver.result_dir(path, corpus, output) / "plan.json").exists()


def test_priority_rerun_order_is_live_and_preserves_unlisted_order(tmp_path):
    corpus = tmp_path / "corpus"
    paths = [commit_case(corpus, f"seed-{seed}") for seed in (1, 2, 3, 4)]
    priority = tmp_path / "priority.json"
    def case(path):
        return str(path.relative_to(corpus).with_suffix(""))
    driver.atomic_json(priority, {"requested_rerun": [{"case": case(paths[2])}, {"case": case(paths[0])}]})
    assert driver.prioritize_pending(paths, corpus, priority) == [paths[2], paths[0], paths[1], paths[3]]
    assert driver.prioritize_pending(paths, corpus) == paths
    driver.atomic_json(priority, {"requested_rerun": [{"case": case(paths[3])}]})
    assert driver.prioritize_pending(paths, corpus, priority) == [paths[3], paths[0], paths[1], paths[2]]


def test_priority_file_does_not_change_case_configuration(tmp_path, monkeypatch):
    priority = tmp_path / "priority.json"
    driver.atomic_json(priority, {"requested_rerun": []})
    monkeypatch.setattr(driver, "discover", lambda *a: [])
    monkeypatch.setattr(driver, "render_reports", lambda *a: None)
    configs = []
    for label, extra in [("default", []), ("priority", ["--priority-file", str(priority)])]:
        output = tmp_path / label
        monkeypatch.setattr(driver.sys, "argv", ["driver.py", "--output", str(output), *extra])
        driver.main()
        configs.append(json.loads((output / "manifest.json").read_text())["configurations"])
    assert configs[0] == configs[1]
    assert "priority_file" not in configs[1][0]


def test_d100_policy_live_ballast_budget_is_immutable_per_attempt(tmp_path):
    path = tmp_path / "policy.json"
    original = {"ballast_s": 45, "gold_warmstart_v1": True}
    assert driver.small_ballast_budgets(original) == (90, 120)
    driver.atomic_json(path, {"small_ballast_s": 240})
    active_policy = driver.load_policy(path)
    updated = driver.reload_case_config(original, policy=active_policy)
    assert driver.small_ballast_budgets(updated) == (240, 240)
    assert original == {"ballast_s": 45, "gold_warmstart_v1": True}
    driver.atomic_json(path, {})
    restored = driver.reload_case_config(updated, policy=driver.load_policy(path))
    assert driver.small_ballast_budgets(restored) == (90, 120)
    assert driver.small_ballast_budgets(updated) == (240, 240)


@pytest.mark.parametrize("value", [0, -1, True, "240", float("nan"), float("inf")])
def test_d100_rejects_invalid_policy_ballast_limits(tmp_path, value):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"small_ballast_s": value}))
    with pytest.raises(ValueError, match="small_ballast_s"):
        driver.load_policy(path)


def test_d100_adapter_passes_internal_budget_without_broadening_v4_scope(tmp_path, monkeypatch):
    twostep = importlib.import_module("stowage.solver.twostep")
    gold = importlib.import_module("stowage.solver.gold_warmstart")
    monkeypatch.setattr(twostep, "VERSION", "4.3.0")
    monkeypatch.setattr(driver, "ROOT", tmp_path)
    for name in ("twostep", "twostep_adapter", "twostep_terminal", "twostep_construct"):
        path = tmp_path / f"src/stowage/solver/{name}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# frozen\n")
    monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    def solve(instance, *, seed, lns_time_limit_s, ballast_time_limit_s):
        assert seed == 7 and lns_time_limit_s == 15 and ballast_time_limit_s == 240
        assert driver.os.environ.get("STOWAGE_BALLAST_V4") is None
        return SimpleNamespace(metrics={})
    monkeypatch.setattr(gold, "solve_gold", solve)
    _, metadata = driver.solve_gold_candidate(object(), 7, ballast_s=240)
    assert metadata["internal_ballast_budget_s"] == 240
    assert driver.os.environ.get("STOWAGE_BALLAST_V4") is None
