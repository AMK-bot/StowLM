"""D157 tests use synthetic instances/results only; never read a sealed run."""
import importlib.util
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace as NS

import pytest

SPEC = importlib.util.spec_from_file_location("twostep_polish91", Path(__file__).with_name("twostep_polish91.py"))
pv = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pv)


@pytest.mark.parametrize("has_plan,final_status,passes,timeout", [
    (False, None, False, False), (True, "error", False, False),
    (True, "error", False, True), (True, "ok", False, False), (True, "ok", True, False),
])
def test_exact_pipeline_matches_original_synthetic(monkeypatch, tmp_path, capfd, has_plan, final_status, passes, timeout):
    import llm.stowlm.sft_data
    import stowage.checker
    import stowage.io
    import stowage.planner
    import stowage.solver.twostep

    clock, calls = NS(value=0.), []
    monkeypatch.setattr(pv.time, "perf_counter", lambda: clock.value)
    monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    instance = NS(id="synthetic", size_class="tiny")
    monkeypatch.setattr(stowage.io, "load_instance", lambda path: instance)
    monkeypatch.setattr(llm.stowlm.sft_data, "d79_engine_config", lambda: "CPU-D79")

    class Result:
        def __init__(self, status, plan):
            self.status, self.plan = status, plan
        def model_dump(self, **kwargs):
            return {"status": self.status, "plan": {} if self.plan else None}
    class Planner:
        def __init__(self, feasibility_config):
            calls.append(("config", feasibility_config))
        def plan(self, inst, **kwargs):
            calls.append(("plan", kwargs, pv.os.environ.get("STOWAGE_BALLAST_V4")))
            clock.value += .1
            return Result("ok" if has_plan else "dead_end", {} if has_plan else None)
    # A nonempty object is needed because the original explicitly checks None.
    monkeypatch.setattr(stowage.solver.twostep, "TwoStepPlanner", Planner)
    def finalize(inst, result, **kwargs):
        calls.append(("finalize", kwargs, pv.os.environ.get("STOWAGE_BALLAST_V4")))
        clock.value += 120. if timeout else .1
        return Result(final_status, {})
    monkeypatch.setattr(stowage.planner, "finalize", finalize)
    def check(inst, plan):
        calls.append(("check", pv.os.environ.get("STOWAGE_BALLAST_V4")))
        clock.value += .1
        return NS(passed=passes, count=NS(weighted_nonproductive=987.))
    monkeypatch.setattr(stowage.checker, "check", check)
    inputs = tmp_path / "inputs.txt"
    inputs.write_text(str(tmp_path / "synthetic.json"))
    original_out = tmp_path / "original"
    monkeypatch.setattr(sys, "argv", ["twostep_val.py", str(inputs), str(original_out), "120"])
    with pv.sealed_output(tmp_path / "original.log"):
        runpy.run_path(str(pv.HERE.parent / "ablation/twostep_val.py"), run_name="__main__")
    original = pv.read_json(original_out / "synthetic.json")
    original_calls = list(calls)
    calls.clear()
    clock.value = 0.
    job = dict(log=str(tmp_path / "worker.log"), input=str(tmp_path / "synthetic.json"),
        instance_id="synthetic", twostep_py_sha256="fake", reference=str(original_out / "synthetic.json"),
        reference_sha256="fake-reference", fingerprint="fake-job", rerun_output=str(tmp_path / "saved.json"))
    safe = pv.rerun_one(job)
    saved = pv.read_json(tmp_path / "saved.json")
    assert safe["status"] == "completed", saved
    assert saved["matched"] and pv.same_outcome(original, saved["row"])
    assert calls == original_calls
    assert ("plan", {"seed": 0, "time_limit_s": 120}, None) in calls
    assert all(call[-1] == "1" for call in calls if call[0] == "finalize")
    assert all(call[-1] is None for call in calls if call[0] == "check")
    assert "weighted_nonproductive" not in safe
    captured = capfd.readouterr()
    assert not captured.out and not captured.err


def supervisor_fixture(monkeypatch, tmp_path):
    import stowage.io
    out = tmp_path / "sealed-synthetic"
    out.mkdir()
    paths = [tmp_path / "tiny-a.json", tmp_path / "small-b.json"]
    for p in paths:
        p.write_text("synthetic-instance")
        pv.write_json(out / "twostep" / f"{p.stem}.json", dict(instance_id=p.stem, outcome="pass", passed=True, weighted_nonproductive=987.))
    (out / "inputs.txt").write_text("\n".join(str(p) for p in paths))
    monkeypatch.setattr(stowage.io, "load_instance", lambda path: NS(id=Path(path).stem, size_class=Path(path).stem.split("-")[0]))
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| unrelated | preserve |\n| test91-polish/cpu | reserved <=6 CPU |\n")
    args = NS(out=out, status=tmp_path / "safe.json", expected_maps=2, workers=2, seconds=15.,
        ledger=ledger, history=tmp_path / "history.md", ledger_key="test91-polish/cpu")
    return args


def test_global_mismatch_blocks_every_polish(monkeypatch, tmp_path, capfd):
    args = supervisor_fixture(monkeypatch, tmp_path)
    stages = []
    def pool(stage, jobs, *unused):
        stages.append(stage)
        assert stage == "rerun"
        for n, job in enumerate(jobs):
            row = pv.read_json(job["reference"])
            if n == 0:
                row["weighted_nonproductive"] = 999.
            pv.write_json(job["rerun_output"], dict(status="completed", fingerprint=job["fingerprint"], matched=n != 0, row=row))
    monkeypatch.setattr(pv, "run_pool", pool)
    assert pv.supervise(args) == 2
    guard = pv.read_json(args.out / "twostep-polish/GUARD.json")
    safe = pv.read_json(args.status)
    assert not guard["valid"] and guard["n_mismatches"] == 1
    assert safe["stage"] == "nondeterministic_do_not_use" and safe["mismatch_ids"] == ["tiny-a"]
    assert stages == ["rerun"]
    assert set(safe) <= {"protocol", "stage", "supervisor_pid", "worker_pids", "n_expected", "n_matches", "n_mismatches", "mismatch_ids", "n_errors", "wall_s"}
    assert "passed" not in args.status.read_text() and "improved" not in args.status.read_text()
    assert args.ledger.read_text() == "| unrelated | preserve |\n"
    assert "test91-polish/cpu" in args.history.read_text()
    captured = capfd.readouterr()
    assert not captured.out and not captured.err


def test_source_change_blocks_polish_before_submission(monkeypatch, tmp_path):
    args = supervisor_fixture(monkeypatch, tmp_path)
    stages = []
    def pool(stage, jobs, *unused):
        stages.append(stage)
        assert stage == "rerun"
        for job in jobs:
            pv.write_json(job["rerun_output"], dict(status="completed", fingerprint=job["fingerprint"], matched=True, row=pv.read_json(job["reference"])))
        Path(jobs[0]["input"]).write_text("changed")
    monkeypatch.setattr(pv, "run_pool", pool)
    assert pv.supervise(args) == 2
    guard = pv.read_json(args.out / "twostep-polish/GUARD.json")
    assert not guard["valid"] and guard["stage"] == "sources_changed"
    assert stages == ["rerun"]


def test_cpu_guard_rejects_mlx(monkeypatch):
    monkeypatch.setitem(sys.modules, "mlx", NS())
    with pytest.raises(RuntimeError, match="CPU-only"):
        pv.cpu_only()


@pytest.mark.parametrize("objective,passed,slot,ballast,expected", [(3., True, "A", "own", 3.), (7., True, "A", "own", 5.), (3., False, "A", "own", 5.), (3., True, "B", "own", 5.), (3., True, "A", "different", 5.)])
def test_nonworse_fixed_stow_fallback(objective, passed, slot, ballast, expected):
    own = NS(plan=NS(objective=5., slot="A", ballast="own"))
    candidate = NS(plan=NS(objective=objective, slot=slot, ballast=ballast))
    def check(instance, plan):
        return NS(passed=passed, count=NS(weighted_nonproductive=plan.objective))
    def replay(instance, plan):
        return NS(legal=True, final_state=NS(ship=NS(placements=[NS(container_id="box", slot=plan.slot)])))
    chosen, after, kept, _ = pv.select_polished(None, own, candidate, 5., check, replay)
    assert after == expected and chosen.plan.objective == expected
    assert kept == (expected == 5.)


def test_all_matching_guard_binds_only_passing_polish_hashes(monkeypatch, tmp_path):
    args = supervisor_fixture(monkeypatch, tmp_path)
    reference = args.out / "twostep/small-b.json"
    pv.write_json(reference, dict(instance_id="small-b", outcome="dead end", passed=False))
    stages = []
    def pool(stage, jobs, *unused):
        stages.append(stage)
        assert len(jobs) == 2  # Includes skip tasks; safe progress cannot expose passes.
        for job in jobs:
            original = pv.read_json(job["reference"])
            if stage == "rerun":
                pv.write_json(job["rerun_output"], dict(status="completed", fingerprint=job["fingerprint"], matched=True, row=original))
            else:
                pv.write_json(job["polish_output"], dict(status="completed" if original.get("passed") else "skipped_not_passing",
                    fingerprint=job["fingerprint"], run_fingerprint=job["run_fingerprint"]))
    monkeypatch.setattr(pv, "run_pool", pool)
    assert pv.supervise(args) == 0
    guard = pv.read_json(args.out / "twostep-polish/GUARD.json")
    assert stages == ["rerun", "polish"]
    assert guard["valid"] and guard["all_matches"] and guard["stage"] == "complete"
    assert guard["n_passing"] == guard["n_polished"] == 1
    assert set(guard["polish_sha256"]) == set(guard["rows"]) == {"tiny-a"}
    assert set(guard["reference_sha256"]) == {"tiny-a", "small-b"}
    assert guard["polish_sha256"]["tiny-a"] == pv.sha(guard["rows"]["tiny-a"])


def test_production_rejects_non_test_input_before_loading(monkeypatch, tmp_path):
    import stowage.io
    args = supervisor_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(pv, "DEFAULT_OUT", args.out)
    def forbidden_load(path):
        pytest.fail("non-test path must be rejected before loading")
    monkeypatch.setattr(stowage.io, "load_instance", forbidden_load)
    assert pv.supervise(args) == 2
    assert not pv.read_json(args.out / "twostep-polish/GUARD.json")["valid"]
