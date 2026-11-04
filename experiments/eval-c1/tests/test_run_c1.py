"""Driver fixes from code review w2-rev-c1 and logic review logic-c1 (CPU only, no model):
reference identity, flag refusals, harness worker cap, default time limit, stratified full selection, method label,
adapter lineage in the run manifest.

    uv run pytest -q -p no:cacheprovider experiments/eval-c1/tests/test_run_c1.py -o addopts=""
"""

import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import run_c1  # noqa: E402

TINY = HERE.parents[1] / "experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/tiny/seed-110280000.json"


class Sentinel(Exception):
    pass


def run_main(monkeypatch, argv):
    """main() up to planner construction; returns the parsed arguments it would plan with."""
    captured = {}

    def make_planner(a):
        captured["a"] = a
        raise Sentinel

    monkeypatch.setattr(run_c1, "make_planner", make_planner)
    monkeypatch.setattr(sys, "argv", ["run_c1.py", "--run-dir", "/unused", *argv])
    try:
        run_c1.main()
    except Sentinel:
        pass
    return captured["a"]


def test_loose_with_no_stub_is_refused_before_planning(monkeypatch):
    with pytest.raises(SystemExit, match="harness mode only"):
        run_main(monkeypatch, ["--loose", "missing.json", "--c2", "--no-stub"])


def test_official_defaults_fit_the_harness_and_bound_each_instance(monkeypatch):
    a = run_main(monkeypatch, ["--instances", "x", "--c2", "--no-stub"])
    assert a.time_limit == run_c1.OFFICIAL_TIME_LIMIT_S
    assert a.mask_workers == run_c1.OFFICIAL_MASK_WORKERS <= run_c1.HARNESS_WORKER_CAP
    assert run_main(monkeypatch, ["--instances", "x", "--c2", "--no-stub", "--time-limit", "120"]).time_limit == 120
    with pytest.raises(SystemExit, match="harness cap"):
        run_main(monkeypatch, ["--instances", "x", "--c2", "--no-stub", "--mask-workers", "8"])


def test_documented_official_worker_count_passes_the_real_harness_cap():
    """Code review Medium (README:32): eight workers were rejected by run_evaluation's cap before any input was
    read; the official default must get past that admission check (it then fails on the fake input root)."""
    from eval.harness import run_evaluation

    planner = SimpleNamespace(num_workers=run_c1.OFFICIAL_MASK_WORKERS, name="x", version="x")
    with pytest.raises(Exception) as err:
        run_evaluation(planner, run_dir="/nonexistent-run", instances_dir="/nonexistent-root", split="test",
                       rungs=("tiny",), limits={"tiny": 1}, workers=1, tiny_run=True)
    assert "must not exceed 6" not in str(err.value)
    eight = SimpleNamespace(num_workers=8, name="x", version="x")
    with pytest.raises(ValueError, match="must not exceed 6"):
        run_evaluation(eight, run_dir="/nonexistent-run", instances_dir="/nonexistent-root", split="test",
                       rungs=("tiny",), limits={"tiny": 1}, workers=1, tiny_run=True)


def test_loose_cache_identity_includes_references(tmp_path):
    """Code review Medium (run_c1:117): changing the reference records changes the run identity, so a resume
    refuses instead of reusing stale gap values."""
    from llm.stowlm.planner import StowLMPlanner

    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"w")
    planner = StowLMPlanner(model_path=model)
    a = SimpleNamespace(seed=0, time_limit=None, references="refs", adapter=None, n_samples=8, temperature=1.0)
    sha = run_c1._sha(TINY)
    one = run_c1.loose_config(a, planner, [TINY], {sha: {"exact": {"objective": 1}}})
    ninety_nine = run_c1.loose_config(a, planner, [TINY], {sha: {"exact": {"objective": 99}}})
    unrelated = run_c1.loose_config(a, planner, [TINY], {sha: {"exact": {"objective": 1}}, "other": {"x": 1}})
    assert run_c1._digest(one) != run_c1._digest(ninety_nine)
    assert run_c1._digest(one) == run_c1._digest(unrelated)  # only references of the evaluated inputs count


def rows_for(sizes):
    rows, n = [], 0
    for (ship, gen), size in sizes.items():
        for _ in range(size):
            rows.append({"spec_key": f"{n:04d}", "spec": {"vessel": ship, "generator": gen}})
            n += 1
    return rows


def test_full_rung_selection_is_stratified_by_ship_and_generator():
    """Logic review Medium: the 20 full specs cover every ship x generator stratum in proportion (the v2 test
    allocation sizes), deterministically, first by spec_key within a stratum."""
    sizes = {("feeder_500", "simulated"): 16, ("feeder_1000", "simulated"): 16, ("feeder_1000", "random_yard"): 14,
             ("feeder_500", "random_yard"): 12, ("feeder_1700", "simulated"): 11, ("feeder_1700", "random_yard"): 6}
    rows = rows_for(sizes)
    picked = run_c1.stratified_selection(rows, 20)
    counts = Counter((r["spec"]["vessel"], r["spec"]["generator"]) for r in picked)
    assert len(picked) == 20 and set(counts) == set(sizes)
    assert counts == {("feeder_500", "simulated"): 4, ("feeder_1000", "simulated"): 4,
                      ("feeder_1000", "random_yard"): 4, ("feeder_500", "random_yard"): 3,
                      ("feeder_1700", "simulated"): 3, ("feeder_1700", "random_yard"): 2}
    assert picked == run_c1.stratified_selection(list(reversed(rows)), 20)
    for stratum, group in Counter(tuple(r["spec"].values()) for r in rows).items():
        firsts = sorted(r["spec_key"] for r in rows if tuple(r["spec"].values()) == stratum)
        chosen = sorted(r["spec_key"] for r in picked if tuple(r["spec"].values()) == stratum)
        assert chosen == firsts[:len(chosen)]
    small = rows_for({("a", "x"): 1, ("b", "x"): 30})
    assert Counter(r["spec"]["vessel"] for r in run_c1.stratified_selection(small, 5)) == {"a": 1, "b": 4}


def test_stratify_passes_explicit_spec_keys_to_the_harness(monkeypatch, tmp_path):
    """The spec_keys= hook (w2-eval-honesty) replaces per-rung prefixes: stratified full specs plus first-N keys
    for the other rungs, labelled partial."""
    import eval.harness as harness

    tiny = [{"spec_key": f"t{i}", "spec": {"vessel": "s", "generator": "g"}} for i in range(5)]
    full = rows_for({("a", "x"): 6, ("b", "x"): 6})
    monkeypatch.setattr(harness, "select_instances",
                        lambda root, limits=None, rungs=(), **kw: (tiny[:limits["tiny"]] if rungs == ("tiny",)
                                                                   else full))
    seen = {}

    def fake_run(planner, *, spec_keys=None, **kw):
        seen.update(kw, spec_keys=spec_keys)
        raise Sentinel
    monkeypatch.setattr(harness, "run_evaluation", fake_run)
    a = SimpleNamespace(limits=["tiny=3", "full=4"], run_dir=str(tmp_path), instances="root", split="test",
                        no_stub=True, seed=0, time_limit=3600, split_manifest=None, stratify=["full=4"], subset=None)
    with pytest.raises(Sentinel):
        run_c1.run_harness(a, SimpleNamespace(num_workers=1))
    assert seen["limits"] is None and seen["partial"] is True and seen["rungs"] == ("tiny", "full")
    assert seen["spec_keys"][:3] == ["t0", "t1", "t2"] and len(seen["spec_keys"]) == 7
    assert json.loads((tmp_path / "selection.json").read_text())["full"] == seen["spec_keys"][3:]


def test_method_label_spells_out_checker_selected_best_of_k():
    assert run_c1.method_label("adapter", 8, 1.0) == ("StowLM (LoRA on 4-bit Muse Glimmer 30B), "
                                                      "checker-selected best of 8, T = 1")
    assert run_c1.method_label(None, 8, 1.0).startswith("4-bit base Muse Glimmer 30B + feasibility mask")


@pytest.mark.parametrize("kind, stubs, clean", [("pipeline_test", {"stowlm.train.dataset": "pipeline_test"}, False),
                                                ("production_gold", {}, True)])
def test_manifest_carries_adapter_data_mode_and_taint(tmp_path, kind, stubs, clean):
    from run_folder import adapter_lineage

    sidecar = tmp_path / "adapters.safetensors.provenance.json"
    sidecar.write_text(json.dumps({"provenance": {"stubs": stubs}, "training": {"data_identity": {
        "dataset_kind": "production_gold" if clean else "diagnostic", "claimed_kind": kind}}}))
    lineage = adapter_lineage(sidecar)
    assert lineage == {"data_mode": kind, "stubs": stubs, "clean": clean}
    sidecar.write_text("{broken")
    assert adapter_lineage(sidecar)["clean"] is False
    assert adapter_lineage(None)["clean"] is None


def test_paired_command_uses_one_selection_and_own_plans(monkeypatch):
    """Logic review Low: the README's paired step runs both methods through run_paired_evaluation on identical
    kwargs, own-plan population (D68), with the official defaults."""
    import eval.harness as harness
    import paired

    seen = {}
    monkeypatch.setattr(harness, "run_paired_evaluation",
                        lambda first, second, **kw: seen.update(first=first, second=second, **kw) or {})
    monkeypatch.setattr(sys, "argv", ["paired.py", "--first", "twostep", "--second", "scalable", "--run-dir", "/r",
                                      "--instances", "root", "--limits", "tiny=3", "full=2", "--c2", "--no-stub"])
    paired.main()
    assert seen["population"] == "own" and seen["limits"] == {"tiny": 3, "full": 2} and seen["tiny_run"] is False
    assert seen["time_limit_s"] == run_c1.OFFICIAL_TIME_LIMIT_S and seen["rungs"] == ("tiny", "full")
    monkeypatch.setattr(sys, "argv", ["paired.py", "--first", "stowlm", "--second", "base", "--run-dir", "/r",
                                      "--instances", "root", "--limits", "tiny=3"])
    with pytest.raises(SystemExit, match="needs --adapter"):
        paired.main()


def test_full_subset_for_6_2_is_a_stratified_ten_nested_in_c1s_twenty():
    """Leader decision (docs/compute.md): 6.2 uses all tiny/small/medium plus a stratified 10 of the 20 full specs;
    the 10 must be a deterministic subset of C1's 20 so the two runs pair on them."""
    sizes = {("feeder_500", "simulated"): 16, ("feeder_1000", "simulated"): 16, ("feeder_1000", "random_yard"): 14,
             ("feeder_500", "random_yard"): 12, ("feeder_1700", "simulated"): 11, ("feeder_1700", "random_yard"): 6}
    rows = rows_for(sizes)
    twenty = {r["spec_key"] for r in run_c1.nested_subset(rows, 20, None)}
    ten = run_c1.nested_subset(rows, 20, 10)
    assert len(ten) == 10 and {r["spec_key"] for r in ten} <= twenty
    assert set(Counter((r["spec"]["vessel"], r["spec"]["generator"]) for r in ten)) == set(sizes)
    assert ten == run_c1.nested_subset(list(reversed(rows)), 20, 10)


def test_subset_option_reaches_the_stratified_selection(monkeypatch, tmp_path):
    import eval.harness as harness

    rows = rows_for({("a", "x"): 8, ("b", "x"): 8})
    monkeypatch.setattr(harness, "select_instances", lambda *args, **kw: rows)
    seen = {}

    def fake_run(planner, *, spec_keys=None, **kw):
        seen.update(kw, spec_keys=spec_keys)
        raise Sentinel
    monkeypatch.setattr(harness, "run_evaluation", fake_run)
    a = SimpleNamespace(limits=["full=8"], run_dir=str(tmp_path), instances="root", split="test", no_stub=False,
                        seed=0, time_limit=None, split_manifest=None, stratify=["full=8"], subset=["full=4"])
    with pytest.raises(Sentinel):
        run_c1.run_harness(a, SimpleNamespace(num_workers=1))
    expected = [r["spec_key"] for r in run_c1.nested_subset(rows, 8, 4)]
    assert seen["spec_keys"] == expected and set(expected) <= {r["spec_key"] for r in run_c1.nested_subset(rows, 8, None)}


MEDIUM_SIZES = {("feeder_500", "simulated"): 15, ("feeder_800", "simulated"): 14, ("feeder_800", "random_yard"): 13,
                ("feeder_500", "random_yard"): 12, ("feeder_1700", "simulated"): 11, ("feeder_1700", "random_yard"): 10}


def test_presets_set_the_leader_selections(monkeypatch):
    a = run_main(monkeypatch, ["--instances", "root", "--preset", "6.2-b"])
    assert (a.limits, a.stratify, a.subset, a.n_samples) == (["medium=75"], ["medium=75"], ["medium=40"], 4)
    a = run_main(monkeypatch, ["--instances", "root", "--preset", "6.2-a"])
    assert (a.stratify, a.subset, a.n_samples) == (["full=20"], ["full=10"], 8)
    assert run_main(monkeypatch, ["--instances", "root", "--preset", "c1"]).n_samples == 8
    with pytest.raises(SystemExit, match="don't pass it as well"):
        run_main(monkeypatch, ["--instances", "root", "--preset", "c1", "--n-samples", "4"])
    with pytest.raises(SystemExit, match="needs --instances"):
        run_main(monkeypatch, ["--loose", "x.json", "--preset", "c1"])


def test_6_2_medium_40_is_stratified_and_nested_in_c1s_75(monkeypatch, tmp_path):
    """Leader decision: 6.2 runs a stratified paired 40 of the 75 medium (best-of-4); C1 runs all 75; the 40 are the
    same instances in both, recorded in selection.json."""
    import eval.harness as harness

    rows = rows_for(MEDIUM_SIZES)
    monkeypatch.setattr(harness, "select_instances", lambda *args, **kw: rows)
    seen = {}

    def fake_run(planner, *, spec_keys=None, **kw):
        seen.update(kw, spec_keys=spec_keys)
        raise Sentinel
    monkeypatch.setattr(harness, "run_evaluation", fake_run)
    a = SimpleNamespace(run_dir=str(tmp_path), instances="root", split="test", no_stub=True, seed=0, time_limit=3600,
                        split_manifest=None, **{k: v for k, v in run_c1.PRESETS["6.2-b"].items() if k != "n_samples"})
    with pytest.raises(Sentinel):
        run_c1.run_harness(a, SimpleNamespace(num_workers=1))
    c1_75 = {r["spec_key"] for r in run_c1.nested_subset(rows, 75, None)}
    forty = seen["spec_keys"]
    assert len(forty) == 40 and set(forty) <= c1_75 and c1_75 == {r["spec_key"] for r in rows}
    picked = [r for r in rows if r["spec_key"] in set(forty)]
    assert set(Counter((r["spec"]["vessel"], r["spec"]["generator"]) for r in picked)) == set(MEDIUM_SIZES)
    assert json.loads((tmp_path / "selection.json").read_text())["medium"] == forty
    assert forty == [r["spec_key"] for r in run_c1.nested_subset(list(reversed(rows)), 75, 40)]


def test_method_label_states_k_per_preset():
    assert "best of 4" in run_c1.method_label(None, run_c1.PRESETS["6.2-b"]["n_samples"], 1.0)
    assert "best of 8" in run_c1.method_label("a", run_c1.PRESETS["c1"]["n_samples"], 1.0)


def test_no_repair_is_refused_in_harness_mode(monkeypatch):  # rev-v4-a P1: never silently ignored
    with pytest.raises(SystemExit, match="loose mode only"):
        run_main(monkeypatch, ["--instances", "x", "--d79", "--no-repair"])
    assert run_main(monkeypatch, ["--loose", "missing.json", "--d79", "--no-repair"]).no_repair


def test_c2_and_d79_conflict_and_d79_is_enough_for_official(monkeypatch):  # rev-v4-a P2
    with pytest.raises(SystemExit, match="conflict"):
        run_main(monkeypatch, ["--instances", "x", "--c2", "--d79", "--no-stub"])
    assert run_main(monkeypatch, ["--instances", "x", "--d79", "--no-stub"]).d79


def test_harness_manifest_labels_d79(monkeypatch, tmp_path):  # rev-v4-a P2: same label as loose mode
    import json as _json
    from types import SimpleNamespace

    import eval.harness as harness
    import run_folder

    seen = {}

    def fake_eval(planner, **kw):
        (tmp_path / "config.json").write_text(_json.dumps({"regen_manifest_sha256": "hash"}))
        return {}

    monkeypatch.setattr(harness, "run_evaluation", fake_eval)
    monkeypatch.setattr(run_folder, "build_manifest", lambda planner, **kw: seen.update(kw) or {})
    monkeypatch.setattr(run_folder, "write_manifest", lambda *a: None)
    monkeypatch.setattr(run_folder, "export_harness_records", lambda *a: 0)
    a = SimpleNamespace(limits=["tiny=1"], stratify=None, run_dir=str(tmp_path), instances="unused", split="test",
                        no_stub=False, seed=0, time_limit=10, split_manifest=None, d79=True, c2=False,
                        temperature=1, n_samples=1, mask_workers=0, adapter=None)
    run_c1.run_harness(a, SimpleNamespace(num_workers=1))
    assert seen["c2"] == run_c1.D79_LABEL


def test_securing_needs_in_thread_and_loose(monkeypatch):
    with pytest.raises(SystemExit, match="in-thread"):
        run_main(monkeypatch, ["--loose", "m.json", "--d79", "--securing", "--mask-workers", "2"])
    with pytest.raises(SystemExit, match="loose mode only"):
        run_main(monkeypatch, ["--instances", "x", "--d79", "--securing", "--mask-workers", "0"])
    assert run_main(monkeypatch, ["--loose", "m.json", "--d79", "--securing", "--mask-workers", "0"]).securing


def test_ballast_v4_is_scoped_to_finalize(monkeypatch):
    import os

    import stowage.planner as sp

    seen = []
    monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    monkeypatch.setattr(sp, "finalize", lambda *a, **k: seen.append(os.environ.get("STOWAGE_BALLAST_V4")) or "r")
    with run_c1.ballast_v4_finalize(True):
        assert os.environ.get("STOWAGE_BALLAST_V4") is None
        from stowage.planner import finalize  # how StowLMPlanner imports it at call time
        assert finalize("i", "r") == "r"
        assert os.environ.get("STOWAGE_BALLAST_V4") is None
    assert seen == ["1"]


def _selected_result(**metrics):
    from types import SimpleNamespace

    class Result(SimpleNamespace):
        def model_copy(self, update):
            return Result(**{**vars(self), **update})
    return Result(status="ok", plan=SimpleNamespace(ballast=None), message=None, metrics=metrics)


def test_a_candidate_gets_one_finisher_attempt(monkeypatch):  # D139: no second finalize after selection tried it
    import stowage.planner as sp

    calls = []
    monkeypatch.delenv("STOWAGE_BALLAST_V4", raising=False)
    monkeypatch.setattr(sp, "finalize", lambda i, r, **k: calls.append(r) or r)
    timed_out = _selected_result(n_candidates_checked=1, n_checker_feasible=0, finisher_budget_timeouts=[0])
    rejected = _selected_result(n_candidates_checked=1, n_checker_feasible=0, finisher_budget_timeouts=[])
    with run_c1.ballast_v4_finalize(True):
        for result in (timed_out, rejected):
            out = sp.finalize("i", result, time_limit_s=480)
            assert out.status == "error" and out.metrics["second_finalize_refused"]
        assert calls == []  # the finisher never ran again
        fresh = _selected_result()  # selection's own first attempt carries no metrics
        assert sp.finalize("i", fresh, time_limit_s=120) is fresh and calls == [fresh]


def test_refused_candidates_keep_their_outcome_categories():
    from run_folder import FINISHER_TIME_LIMIT, instance_outcome

    base = {"n_samples": 1, "n_complete": 1, "n_candidates_checked": 1, "n_checker_feasible": 0,
            "second_finalize_refused": True}
    timed = {"status": "error", "repaired": False, "metrics": {**base, "finisher_budget_timeouts": [0],
                                                                "candidate_failures": {"0": "Ballast not found"}}}
    assert instance_outcome(timed, {"passed": False})["outcome"] == FINISHER_TIME_LIMIT
    rejected = {"status": "error", "repaired": False, "metrics": {**base, "finisher_budget_timeouts": [],
                                                                   "candidate_failures": {"0": "full checker rejected"}}}
    assert instance_outcome(rejected, {"passed": False})["outcome"] == "checker fail"


def test_candidate_cap_limits_every_finalize_call(monkeypatch):
    import stowage.planner as sp

    seen = []
    monkeypatch.setattr(sp, "finalize", lambda instance, result, **kw: seen.append(kw["time_limit_s"]))
    with run_c1.ballast_v4_finalize(True, 600.):
        sp.finalize(None, SimpleNamespace(), time_limit_s=7000.)
        sp.finalize(None, SimpleNamespace(), time_limit_s=30.)
    assert seen == [600., 30.]


def test_decode_only_skips_the_finisher(monkeypatch):
    import stowage.planner as sp

    calls = []
    monkeypatch.setattr(sp, "finalize", lambda *a, **k: calls.append(1))

    class Result:
        def model_copy(self, update):
            return update
    with run_c1.ballast_v4_finalize(True, None, True):
        assert sp.finalize(None, Result(), time_limit_s=9.)["status"] == "error"
    assert calls == []


def test_store_candidates_is_off_by_default_and_stores_every_sample(tmp_path, monkeypatch):
    import llm.stowlm.backtrack as bt

    plain = bt.generate_backtracking
    with run_c1.store_candidates(None):
        assert bt.generate_backtracking is plain
    plan = SimpleNamespace(model_dump=lambda mode: {"id": "p"})
    decoder = SimpleNamespace(inner=SimpleNamespace(engine=SimpleNamespace(instance=SimpleNamespace(id="m"), to_plan=lambda: plan)))
    outcomes = iter(("complete", "dead_end"))
    monkeypatch.setattr(bt, "generate_backtracking", lambda *a, **k: (SimpleNamespace(status=next(outcomes)), {"backtracks": 2}))
    with run_c1.store_candidates(str(tmp_path)):
        bt.generate_backtracking(None, [], decoder, temperature=0., seed=0)
        bt.generate_backtracking(None, [], decoder, temperature=.7, seed=1)
    first, second = (json.loads((tmp_path / "m" / f"sample-0{i}.json").read_text()) for i in range(2))
    assert first["plan"] == {"id": "p"} and second["plan"] is None and second["status"] == "dead_end"
