"""CPU-only smoke orchestration regressions: never import Modal or CUDA libraries."""
import ast
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/grpo-venue/modal_smoke.py"
LOCK = ROOT / "experiments/grpo-venue/modal_smoke.lock.txt"
ARCHIVED_Q2 = ROOT / "experiments/grpo-venue/runs/q2_payload-1793243882.json"
HELPERS = {"main", "q2_failure_reason", "q3_failure_reason", "q2_manifest", "q2_reuse_failure_reason", "_sha256",
           "_ledger_update", "allowed_at", "count_violations"}
CONSTANTS = {"MODEL_REV", "PINS", "MASK_CODE"}


def load_script(names, **ns):
    """Execute selected top-level functions/constants of the real script, stripping Modal decorators."""
    tree = ast.parse(SCRIPT.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            nodes.append(node)
        elif (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in CONSTANTS):
            nodes.append(node)
    ns = {**dict(json=json, os=os, Path=Path, SCRIPT_PATH=SCRIPT, LOCK=LOCK), **ns}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


def load_driver(tmp_path, q2, q3=None, check=None):
    calls = []

    def remote(name, result):
        def run(*args):
            calls.append(name)
            if check:
                check(name, args)
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)
        return SimpleNamespace(remote=run)

    ns = load_script(HELPERS, time=SimpleNamespace(time=lambda: 0.0), HERE=tmp_path,
                     CPU_RATE=0.001, GPU_RATE=0.001, TIMEOUT_S=1800, STARTUP_S=300, IDLE_S=60,
                     TYPICAL_S=dict(import_check=1, download=1, vllm=1, hf=1, trl=1),
                     LOCAL_ADAPTER=None, import_check=remote("Q1", {"ok": True}),
                     download_weights=remote("Q1b", {"ok": True}), vllm_stage=remote("Q2", q2),
                     hf_stage=remote("Q3", {} if q3 is None else q3), trl_stage=remote("Q4", {}))
    return ns["main"], calls


def valid_q2():
    spec = {"allowed": [1, 2], "after_sep": [1], "sep": 2}
    return {"per_request_applied_ok": True, "violations_masked_run": 0,
            "mixed_batch_violations_masked_rows": 0, "mixed_batch_violations_unmasked_rows": 1,
            "logprobs_missing": 0, "tokens_per_run": [2, 2],
            "_payload": {"prompt_ids": [9], "specs": [spec], "tokens": [[1, 2]],
                         "logprobs": [[-0.5, 0.0]]}}


def valid_q3(bwd_seq=12288):
    return {"logprob_check": {"positions": 2, "expected_positions": 2, "compared": 2, "nonfinite_vllm": 0,
                              "nonfinite_hf_fp32": 0, "nonfinite_hf_bf16": 0, "illegal_tokens_seen": 0,
                              "mean_abs_diff_fp32": 0.002, "max_abs_diff_fp32": 0.3, "frac_gt_1.0": 0.0},
            "backward": [{"seq": bwd_seq, "step": 0, "loss": 9.4, "grad_norm": 60.0},
                         {"seq": bwd_seq, "step": 1, "loss": 8.8, "grad_norm": 51.3}]}


def results(tmp_path):
    return json.loads((tmp_path / "modal_smoke_results.json").read_text())


@pytest.mark.parametrize("failure", ["exception", "mask", "payload", "missing_lp", "nan_lp", "illegal",
                                     "shape", "empty", "mixed_mask"])
def test_q2_failure_never_starts_later_gpu_stage(tmp_path, failure):
    q2 = valid_q2()
    if failure == "exception":
        q2 = RuntimeError("load failed")
    elif failure == "mask":
        q2["per_request_applied_ok"] = False
    elif failure == "payload":
        del q2["_payload"]
    elif failure == "missing_lp":
        q2["_payload"]["logprobs"][0][0] = None
    elif failure == "nan_lp":
        q2["_payload"]["logprobs"][0][0] = float("nan")
    elif failure == "illegal":
        q2["_payload"]["tokens"][0][0] = 99
    elif failure == "shape":
        q2["_payload"]["logprobs"] = []
    elif failure == "empty":
        q2["_payload"]["tokens"][0] = []
        q2["_payload"]["logprobs"][0] = []
    elif failure == "mixed_mask":
        q2["mixed_batch_violations_masked_rows"] = 1
    main, calls = load_driver(tmp_path, q2)
    main(cap=5, trl=True)
    assert calls == ["Q1", "Q1b", "Q2"]
    assert results(tmp_path)["verdict"].startswith("Q2 failed:")


def test_valid_q2_continues_but_does_not_claim_adapter_agreement(tmp_path):
    main, calls = load_driver(tmp_path, valid_q2())
    main(cap=5)
    assert calls == ["Q1", "Q1b", "Q2", "Q3"]
    assert results(tmp_path)["adapter_on_sampler_trainer_agreement_verified"] is False


def test_cpu_only_preserves_no_gpu_behavior(tmp_path):
    main, calls = load_driver(tmp_path, RuntimeError("must not call"))
    main(cap=5, only_cpu=True)
    assert calls == ["Q1", "Q1b"]


def test_cap_still_blocks_unaffordable_stage(tmp_path):
    main, calls = load_driver(tmp_path, valid_q2())
    with pytest.raises(SystemExit, match="SPEND CAP"):
        main(cap=0.0001)
    assert calls == []


# ---- cumulative spend cap: absolute deadline + durable reservation (w2-rev-modal, Wrong high) ----
def test_restarted_incarnation_inherits_absolute_deadline():
    """The review's counterexample: $10 left at $1/s, two incarnations of 6 s each. The old per-incarnation clock
    let both run ($12). With an absolute deadline the second incarnation exits once 10 s - reserve have passed."""
    import sys
    from unittest.mock import patch

    class StopProbe(Exception):
        pass
    clock, callbacks, exits = {"now": 0.0}, [], []

    def sleep(_):
        raise StopProbe()  # one poll per probe; the clock is advanced by hand

    class Thread:
        def __init__(self, target, **kw):
            callbacks.append(target)

        def start(self):
            pass

    def _exit(code):
        exits.append((clock["now"], code))
    ns = load_script({"_watchdog"}, time=SimpleNamespace(time=lambda: clock["now"], sleep=sleep),
                     os=SimpleNamespace(_exit=_exit))
    deadline = 0.0 + 10.0 / 1.0  # driver: t + (cap - spent - reserve) / rate, reserve 0 here
    with patch.dict(sys.modules, {"threading": SimpleNamespace(Thread=Thread)}):
        for start in (0.0, 6.0):  # incarnation 1 starts at 0 s, the preempted restart at 6 s
            clock["now"] = start
            n = len(callbacks)
            ns["_watchdog"](deadline, 1.0, "probe")
            for t in (start + 3.0, start + 6.0):
                clock["now"] = t
                if len(callbacks) > n:
                    try:
                        callbacks[-1]()
                    except StopProbe:
                        pass
    assert exits == [(12.0, 42)]  # incarnation 1 never exits; incarnation 2 exits past the 10 s deadline
    # A restart that boots after the deadline exits immediately, before any work.
    exits.clear()
    clock["now"] = 11.0
    with patch.dict(sys.modules, {"threading": SimpleNamespace(Thread=Thread)}):
        ns["_watchdog"](deadline, 1.0, "probe")
    assert exits == [(11.0, 42)]


def test_ledger_reservation_written_before_remote_call(tmp_path):
    ledger = tmp_path / "runs" / "spend_ledger.json"
    seen = []

    def check(name, args):
        entry = [e for e in json.loads(ledger.read_text())["entries"] if e["stage"] == {
            "Q1": "import_check", "Q1b": "download", "Q2": "vllm", "Q3": "hf"}[name]][-1]
        assert entry["status"] == "reserved"
        assert entry["deadline_epoch"] == args[0]  # the remote gets the absolute deadline, not a budget
        seen.append((name, entry["reserved_usd"]))
    main, calls = load_driver(tmp_path, valid_q2(), check=check)
    main(cap=5)
    assert [n for n, _ in seen] == calls == ["Q1", "Q1b", "Q2", "Q3"]
    entries = json.loads(ledger.read_text())["entries"]
    assert all(e["status"] == "done" for e in entries)
    assert seen[0][1] == 5  # first stage reserves everything left
    # deadline = 0 + (5 - 0.3 reserve) / 0.001
    assert entries[0]["deadline_epoch"] == pytest.approx(4700.0)


def test_errored_stage_keeps_full_reservation(tmp_path):
    main, calls = load_driver(tmp_path, RuntimeError("preempted twice"))
    main(cap=5, trl=True)
    res = results(tmp_path)
    assert res["stages"]["vllm"]["est_cost_usd"] == res["stages"]["vllm"]["reserved_usd"]
    assert res["est_spent_usd"] == pytest.approx(5.0)
    status = {e["stage"]: e["status"] for e in json.loads((tmp_path / "runs/spend_ledger.json").read_text())["entries"]}
    assert status["vllm"] == "error"


# ---- explicit Q3 gate ----
def test_valid_q3_starts_q4_and_records_pass(tmp_path):
    main, calls = load_driver(tmp_path, valid_q2(), valid_q3())
    main(cap=5, trl=True)
    assert calls == ["Q1", "Q1b", "Q2", "Q3", "Q4"]
    assert results(tmp_path)["Q3_verdict"] == "PASS"


def _q3_case(case):
    q3 = valid_q3()
    lc = q3["logprob_check"]
    if case == "empty_lc":
        q3["logprob_check"] = {}
    elif case == "big_diff":
        lc.update(mean_abs_diff_fp32=10.0, max_abs_diff_fp32=20.0)
    elif case == "max_over_1":
        lc["max_abs_diff_fp32"] = 1.5
    elif case == "frac_over_1":
        lc["frac_gt_1.0"] = 0.001
    elif case == "illegal":
        lc["illegal_tokens_seen"] = 1
    elif case in ("nonfinite_vllm", "nonfinite_hf_fp32", "nonfinite_hf_bf16"):
        lc[case] = 1
    elif case == "missing_counter":
        del lc["nonfinite_hf_bf16"]
    elif case == "dropped_positions":
        lc.update(positions=1, compared=1)
    elif case == "error":
        q3["error"] = "OutOfMemoryError: ..."
    elif case == "all_oom":
        q3["backward"] = [{"seq": s, "oom": "CUDA out of memory"} for s in (12288, 6144, 3072)]
    elif case == "shorter_seq":
        q3["backward"] = [{"seq": 12288, "oom": "CUDA out of memory"},
                          {"seq": 6144, "step": 0, "loss": 9.0, "grad_norm": 50.0},
                          {"seq": 6144, "step": 1, "loss": 8.0, "grad_norm": 40.0}]
    elif case == "warmup_only":
        q3["backward"] = q3["backward"][:1]
    elif case == "nan_grad":
        q3["backward"][1]["grad_norm"] = float("nan")
    elif case == "zero_grad":
        q3["backward"][1]["grad_norm"] = 0.0
    elif case == "nan_loss":
        q3["backward"][1]["loss"] = float("inf")
    return q3


@pytest.mark.parametrize("case", ["empty_lc", "big_diff", "max_over_1", "frac_over_1", "illegal", "nonfinite_vllm",
                                  "nonfinite_hf_fp32", "nonfinite_hf_bf16", "missing_counter", "dropped_positions",
                                  "error", "all_oom", "shorter_seq", "warmup_only", "nan_grad", "zero_grad",
                                  "nan_loss"])
def test_q3_failure_never_starts_q4(tmp_path, case):
    main, calls = load_driver(tmp_path, valid_q2(), _q3_case(case))
    main(cap=5, trl=True)
    assert calls == ["Q1", "Q1b", "Q2", "Q3"]
    res = results(tmp_path)
    assert res["Q3_verdict"].startswith("FAIL: ")
    assert res["Q4_trl_UNTESTED"].startswith("skipped: Q3 did not pass")


def test_skipped_q3_is_not_a_pass(tmp_path):
    main, calls = load_driver(tmp_path, valid_q2(), valid_q3())
    main(cap=5, trl=True, skip_hf=True)
    assert calls == ["Q1", "Q1b", "Q2"]
    assert results(tmp_path)["Q3_verdict"].startswith("FAIL")


# ---- Q2 payload reuse identity ----
def saved_q2(tmp_path, mutate=None, n_req=1, out_tokens=2):
    """Run the driver once so it saves a Q2 file with a manifest, then optionally tamper with it."""
    main, _ = load_driver(tmp_path, valid_q2())
    main(cap=5, n_req=n_req, out_tokens=out_tokens)
    path = Path(results(tmp_path)["q2_payload_saved"])
    saved = json.loads(path.read_text())
    assert saved["manifest"]["n_req"] == n_req and saved["manifest"]["out_tokens"] == out_tokens
    if mutate:
        mutate(saved)
        path.write_text(json.dumps(saved))
    return str(path)


def test_valid_manifest_reuse_skips_q2_and_runs_q3(tmp_path):
    path = saved_q2(tmp_path)
    main, calls = load_driver(tmp_path, RuntimeError("Q2 must not run"), valid_q3())
    main(cap=5, trl=True, q2_payload=path, n_req=1, out_tokens=2)
    assert calls == ["Q1", "Q1b", "Q3", "Q4"]
    assert results(tmp_path)["Q2_vllm"]["reused_from"] == path


def _refused(tmp_path, path, **kw):
    main, calls = load_driver(tmp_path, RuntimeError("Q2 must not run"), valid_q3())
    with pytest.raises(SystemExit, match="refused") as err:
        main(cap=5, trl=True, q2_payload=path, **{"n_req": 1, "out_tokens": 2, **kw})
    assert calls == []  # refused before any (CPU or GPU) stage is billed
    return str(err.value)


def test_tampered_logprobs_refused(tmp_path):
    def zero(saved):
        saved["payload"]["logprobs"] = [[0.0 for _ in row] for row in saved["payload"]["logprobs"]]
    assert "payload_sha256" in _refused(tmp_path, saved_q2(tmp_path, zero))


def test_tampered_q2_counters_refused(tmp_path):
    def fake(saved):
        saved["Q2_vllm"]["mixed_batch_violations_unmasked_rows"] = 999
    assert "q2_result_sha256" in _refused(tmp_path, saved_q2(tmp_path, fake))


def test_truncated_rows_refused_even_with_recomputed_digest(tmp_path):
    ns = load_script({"_sha256"})

    def truncate(saved):  # an attacker/bug that also recomputes the digest still fails the consistency checks
        pl = saved["payload"]
        pl["tokens"], pl["logprobs"] = [pl["tokens"][0][:1]], [pl["logprobs"][0][:1]]
        saved["manifest"]["payload_sha256"] = ns["_sha256"](json.dumps(pl, sort_keys=True))
    assert "out_tokens" in _refused(tmp_path, saved_q2(tmp_path, truncate))


def test_mismatched_request_shape_refused(tmp_path):
    assert "n_req/out_tokens" in _refused(tmp_path, saved_q2(tmp_path), n_req=16, out_tokens=2000)


def test_missing_manifest_refused(tmp_path):
    assert "no manifest" in _refused(tmp_path, saved_q2(tmp_path, lambda s: s.pop("manifest")))


def test_archived_payload_without_manifest_is_refused(tmp_path):
    # runs/q2_payload-1793243882.json (run 4) predates the manifest, so it can't be reused for a new Q3.
    ns = load_script({"q2_reuse_failure_reason", "q2_failure_reason", "q2_manifest", "_sha256", "allowed_at",
                      "count_violations"})
    saved = json.loads(ARCHIVED_Q2.read_text())
    assert ns["q2_reuse_failure_reason"](saved, 16, 2000).startswith("no manifest")
