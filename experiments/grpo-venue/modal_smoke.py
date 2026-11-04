# GRPO-venue smoke test on Modal for StowLM (Muse Glimmer 30B). Plan task 0.8 / 6.4; research in cuda-stack.md.
# Q1/Q1b passed on Modal CPU on 2026-10-27; Q2/Q3 GPU stages remain unrun.
#
# ---------------------------------------------------------------------------------------------------------------
# WHAT IT MEASURES (each stage answers one venue question; it stops early if a cheaper stage fails)
#   Q1  [CPU] Does the pinned stack (torch 2.13.0 / transformers 5.17.0 / vllm 0.31.0 / trl 1.16.0 / peft 0.21.2)
#       resolve and import on a Modal image? The image is built from modal_smoke.lock.txt (209 packages). That lock
#       was produced locally with `uv pip compile --python-platform x86_64-manylinux_2_28`, metadata only, so the
#       *resolve* half is already answered (yes). The *import* half is answered here: versions, Glimmer present in
#       the transformers and vLLM registries, the LogitsProcessor ABC and TRL method signatures. No GPU starts if this fails.
#   Q1b [CPU] Download the pinned snapshot (cec94fc..., ~59.6 GB) into a Modal Volume. This runs on CPU so no GPU
#       time is billed while downloading. It is idempotent (a marker file skips it on re-runs).
#   Q2  [1x H100 80GB] vLLM 0.31.0 loads Glimmer text-only (language_model_only=True, plus an optional
#       hf_overrides architectures=["MuseGlimmerForCausalLM"]). A custom V1 LogitsProcessor class (toy
#       "feasibility mask": per-request allowed set from SamplingParams.extra_args, plus a state rule that depends on
#       the previous token) is checked to be applied *per request* (masked rows: 0 violations; unmasked rows in the
#       same batch: >0). Throughput: 16 parallel requests x 2,000 output tokens, without vs with the processor.
#       Logprobs are returned in logprobs_mode="processed_logprobs" (after the mask).
#   Q3  [1x H100 80GB, separate function: vLLM bf16 ~56 GB + HF bf16 ~60 GB can't share 80 GB]
#       HF MuseGlimmerForConditionalGeneration bf16 + PEFT LoRA (the toy adapter from convert_adapter.py if it was
#       uploaded, otherwise a fresh LoRA with the anchored regex). For the vLLM-sampled tokens it computes
#       masked log-probs logp(a) = z_a - logsumexp_{v in allowed} z_v, with z = 20*tanh(mult*h.W^T/20) on the gathered
#       allowed rows of lm_head (no full-vocab logits). With the HF adapter disabled it reports
#       |HF masked - vLLM processed| (a base-only check, D9) and the unmasked full-vocab gap, i.e. the bias you'd
#       get if training ignored the mask. Adapter-on agreement through the actual 6.4 sync path is pending. Then one
#       LoRA forward+backward+AdamW step at seq 12,288 with gradient checkpointing: step time and peak memory.
#   Q4  [optional, --trl, OFF by default, UNTESTED] 2-step TRL GRPOTrainer with a subclass overriding
#       _get_per_token_logps_and_entropies for a mask (HF-generate path, bad_words_ids as the mask).
#
# HOW TO RUN (only after the user approves the spend, and log it in docs/compute.md afterwards)
#   modal run experiments/grpo-venue/modal_smoke.py                  # Q1 -> Q1b -> Q2 -> Q3, cap $15
#   modal run experiments/grpo-venue/modal_smoke.py --cap 6          # tighter cap
#   modal run experiments/grpo-venue/modal_smoke.py --only-cpu       # Q1 + Q1b only (~$0.05)
#   modal run experiments/grpo-venue/modal_smoke.py --trl            # also Q4 (UNTESTED sketch)
#   Other flags: --n-req 16 --out-tokens 2000 --bwd-seq 12288 --no-arch-override --async-sched --eager --skip-hf
#   Optional before the run: convert an MLX adapter to models/toy-adapter-peft with convert_adapter.py mlx2peft;
#   the local entrypoint uploads it to the "stowlm-adapters" Volume. Afterwards free the storage:
#   `modal volume delete stowlm-glimmer-weights` (60 GiB x $0.09/GiB/month ~ $5.40/month, prorated).
#   Results: printed JSON blob + experiments/grpo-venue/modal_smoke_results.json.
#
# ESTIMATED COST (rates from docs/compute.md, verified 2026-10-27; per second; durations are MY ESTIMATES, unmeasured)
#   GPU stage rate = H100 $0.001097 + 8 cores x $0.0000131 + 64 GiB x $0.00000222 = $0.0013439/s ($4.84/h)
#   CPU stage rate = 4 cores x $0.0000131 + 16 GiB x $0.00000222 = $0.0000879/s ($0.32/h)
#   | stage                         | est. duration                                   | est. cost |
#   | image build (heavy, ~10 GB)   | ~8-12 min builder time (UNVERIFIED if billed)   | <= $0.06  |
#   | Q1 import check (CPU)         | ~2 min                                          |   $0.01   |
#   | Q1b download 59.6 GB (CPU)    | 2-5 min at 200-500 MB/s, +1 min commit; 20 min at 50 MB/s | $0.02-0.11 |
#   | Q2 vLLM (H100)                | ~12 min: start 1 + load 2-3 + compile/graphs 2-4 + runs 3 + slack | $0.97 |
#   | Q3 HF + LoRA step (H100)      | ~15 min: load 3-4 + 16 scoring passes 2 + 2 bwd steps 2-4 + slack | $1.21 |
#   | Q4 TRL (opt, H100)            | ~20 min                                         |  ($1.61)  |
#   | Volume storage                | 60 GiB, $5.40/month while it exists             | ~$0.18/day |
#   | TOTAL default                 | ~30 min of GPU                                  | ~$2.3 (+storage) |
#   | worst case (every GPU stage hits timeout 1800 s + 300 s startup) | 2 x 2,100 s x $0.0013439 + CPU | ~$5.8 ($8.6 with --trl) |
#   The table uses list prices. Admission and accounting use rate x BILLING_OVERHEAD (1.25): the retained billing
#   export metered ~1.09-1.20x the client estimate (a4: $0.694 metered vs $0.581 estimated, +19.5%), so the client
#   clock does NOT over-estimate; 1.25 covers that with a little slack (fix 2026-10-28, review w2-rev-modal).
#   Spend cap (cumulative across preemptions): before each stage the local entrypoint refuses the stage unless
#   typical + STARTUP_S x rate fits in (cap - spent). It then writes a durable reservation to runs/spend_ledger.json
#   (stage, reserved_usd = everything left, absolute deadline_epoch, status) BEFORE the remote call, and passes the
#   absolute deadline_epoch = now + (cap - spent - STARTUP_S x rate) / rate to the function. The in-container watchdog
#   exits once time.time() > deadline_epoch. GPU functions are preemptible and a preempted input restarts, but the
#   restarted incarnation inherits the same absolute deadline, so all incarnations together run inside one window;
#   the STARTUP_S reserve covers boot, the 5 s poll and scale-down idle. After return the stage is charged
#   (wall + IDLE_S) x rate; if it errored or was interrupted the full reservation is kept (no refunds inferred).
#   APPROXIMATIONS: the deadline compares the container clock to the Mac clock (NTP skew, seconds); every
#   incarnation started after the deadline still bills its boot until the watchdog's first check; the number of
#   Modal preemption retries is not bounded here. The hard Modal timeout per incarnation stays as a backstop.
#
# WHAT EACH RESULT WOULD CHANGE IN THE VENUE DECISION
#   Q1 fails (import / version mismatch, e.g. a CUDA 13 driver problem; the lock pulls nvidia-cuda-runtime 13.0.96)
#      -> the CUDA path costs debugging days. Try the cu129 torch index or vLLM 0.30, else GRPO locally in MLX.
#   Q2 vLLM can't load Glimmer, or the custom LP isn't applied per request / the state rule is violated
#      -> no fast masked rollouts on CUDA. Fall back to rollout_func + HF generate(logits_processor=...) (slow) or
#      MLX rollouts. LP overhead > ~30% -> vectorise the mask (bitmask tensors) before GRPO. tok/s sets the GRPO
#      step-time estimate: 64 rollouts x ~10k tokens / tok/s.
#   Q3 |HF masked - vLLM processed| mean <~ 0.02 nats with no positions > 1 nat -> base-only agreement evidence.
#      Adapter-on agreement through the actual 6.4 synchronization path is still required. Large -> precision/softcap mismatch, so fix it
#      before training. The unmasked gap shows how biased an unmasked trainer would be (D9). The 12k backward peak
#      memory decides 1x H100 server-mode trainer (fits) vs H200/B200 or a shorter max completion (doesn't fit).
#   Q4 (if run) -> whether TRL's GRPOTrainer can be used as-is with a subclass or we write our own GRPO loop.
#
# VERIFIED vs UNVERIFIED (vLLM sources read at tag v0.31.0 on GitHub, 2026-10-27)
#   VERIFIED by reading source:
#     vllm/v1/sample/logits_processor/interface.py: LogitsProcessor ABC L60-109 (validate_params classmethod L61-69,
#       __init__(vllm_config, device, is_pin_memory) L71-75, apply(logits)->Tensor L77-84, is_argmax_invariant L86-94,
#       update_state(batch_update|None) L96-109); BatchUpdate L36-57 (process removed, then added, then moved);
#       AddedRequest = (index, params, prompt_tok_ids, output_tok_ids) L29, where output_tok_ids is a live reference.
#     vllm/v1/sample/logits_processor/builtin.py: process_dict_updates L374-411 (the helper used below).
#     vllm/v1/sample/logits_processor/__init__.py: LOGITSPROCS_GROUP L48, AdapterLogitsProcessor L246.
#     vllm/v1/sample/sampler.py: non-argmax-invariant LPs run in apply_logits_processors L400-401, after
#       allowed_token_ids/bad_words and before penalties/temperature; processed_logprobs come from the top-k/top-p
#       sampler after temperature (L286-302), so at temperature 1, top_p 1 they equal log_softmax(masked logits).
#     vllm/v1/sample/ops/topk_topp_sampler.py L216-225: processed_* modes force the native (non-FlashInfer) sampler.
#     vllm/config/model.py L100-107: LogprobsMode values. vllm/sampling_params.py L349: extra_args (L683-701 only
#       checks int range). vllm/entrypoints/llm.py L221: LLM(logits_processors=[str|type]).
#     vllm/engine/arg_utils.py L624: language_model_only; L601 logprobs_mode; L811 async_scheduling.
#     vllm/config/vllm.py L1576-1635: async scheduling is ON by default even with custom LPs.
#     vllm/model_executor/models/muse_glimmer.py (v0.31.0): has_vision from config (L121-125); vision built inside
#       _mark_tower_model, which is skipped when all mm limits are 0 (interfaces.py L337-347); compute_logits applies
#       multiplier then softcap (L1646-1654). Both registry archs map to the same class (registry L182/L537), so the
#       arch override alone does NOT drop the vision tower. language_model_only does.
#     HF: modeling_muse_glimmer.py (5.21 in .venv) forward L1161-1182: logits = 20*tanh(lm_head(h)*mult/20).
#   UNVERIFIED (this run is what verifies them):
#     - CUDA driver support (CPU imports passed; no GPU has been tested);
#     - that a LogitsProcessor class defined inside a function works with VLLM_ENABLE_V1_MULTIPROCESSING=0 (set
#       below so the class needn't be importable by FQCN in an engine subprocess);
#     - that with async scheduling ON, output_tok_ids already holds the previous token when apply() runs.
#       Default here is async OFF; --async-sched turns it on, and the violation counter then measures it;
#     - that logits row i == persistent batch index i (true without speculative decoding, which we don't enable);
#     - the TRL 1.16 sketch (Q4) as a whole; the method signature is from the wheel, grpo_trainer.py L1419-1438.
# ---------------------------------------------------------------------------------------------------------------
import json
import os
import random
import time
from pathlib import Path

import modal

APP_NAME = "stowlm-grpo-venue-smoke"
MODEL_ID = "meta-models/Muse-Glimmer-30B"  # public, apache-2.0, not gated (HF API, 2026-10-27)
MODEL_REV = "cec94fcfd6512f2cfe708138c32533b346ea4f69"
PINS = ["torch==2.13.0", "transformers==5.17.0", "vllm==0.31.0", "trl==1.16.0", "peft==0.21.2"]
HERE = Path(__file__).resolve().parent
LOCK = HERE / "modal_smoke.lock.txt"
# Local-only path. Inside a container this file is /root/modal_smoke.py, so parents[1] doesn't exist (fix 2026-10-27).
LOCAL_ADAPTER = HERE.parents[1] / "models" / "toy-adapter-peft" if len(HERE.parents) > 1 else None
# Anchored full-match regex (cuda-stack.md, PEFT traps): text tower only, never the vision tower's q/k/v_proj.
LORA_REGEX = (r"model\.language_model\.layers\.\d+\.(self_attn\.(q_proj|k_proj|v_proj|o_proj|gate_proj)"
              r"|mlp\.(gate_proj|up_proj|down_proj))")

# ---- rates (docs/compute.md, Modal list prices verified 2026-10-27, per second) ----
H100_S = 0.001097
CPU_CORE_S = 0.0000131
MEM_GIB_S = 0.00000222
GPU_CPU, GPU_MEM_GIB = 8, 64
CPU_CPU, CPU_MEM_GIB = 4, 16
GPU_RATE = H100_S + GPU_CPU * CPU_CORE_S + GPU_MEM_GIB * MEM_GIB_S
CPU_RATE = CPU_CPU * CPU_CORE_S + CPU_MEM_GIB * MEM_GIB_S
BILLING_OVERHEAD = 1.25  # metered/estimated was 1.09-1.20 on 2026-10-29 (runs/gpu-smoke-2026-10-29.billing.json)
GPU_RATE *= BILLING_OVERHEAD
CPU_RATE *= BILLING_OVERHEAD
TIMEOUT_S = 1800
STARTUP_S = 300  # also the per-stage reserve for billed boot / poll slack / scale-down idle (held back from the deadline)
IDLE_S = 60  # billed scale-down idle after a call returns (ESTIMATE, Modal's default window; not measured)
SCRIPT_PATH = Path(__file__).resolve()
# Functions whose source defines the mask semantics; their sha256 binds a saved Q2 payload to this code.
MASK_CODE = ("allowed_at", "count_violations", "build_specs", "_make_mask_lp_cls")
TYPICAL_S = {"import_check": 120, "download": 360, "vllm": 720, "hf": 900, "trl": 1200}

WEIGHTS_DIR = "/weights"
ADAPTER_DIR = "/adapters"
MODEL_PATH = f"{WEIGHTS_DIR}/Muse-Glimmer-30B@{MODEL_REV[:8]}"
MASK_KEY = "stow_toy_mask"

app = modal.App(APP_NAME)
weights_vol = modal.Volume.from_name("stowlm-glimmer-weights", create_if_missing=True)
adapter_vol = modal.Volume.from_name("stowlm-adapters", create_if_missing=True)

light_image = (modal.Image.debian_slim(python_version="3.12")
               .uv_pip_install("huggingface_hub==1.33.0")
               .env({"HF_XET_HIGH_PERFORMANCE": "1"}))
heavy_image = (modal.Image.debian_slim(python_version="3.12")
               # The lock is read lazily at build time (Modal build_dockerfile), so this line is safe inside containers.
               .uv_pip_install(requirements=[str(LOCK)])
               .env({"VLLM_ENABLE_V1_MULTIPROCESSING": "0", "TOKENIZERS_PARALLELISM": "false",
                     "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}))


# ---------------------------------------------------------------------------------------------------------------
# Shared pure-python mask rule (used by the vLLM processor AND the HF replay, so both see identical semantics)
# ---------------------------------------------------------------------------------------------------------------
def allowed_at(spec: dict, prev: int | None) -> list[int]:
    """Toy feasibility rule. After the separator token only `after_sep` is legal, otherwise `allowed`.
    The previous token may not repeat, unless it is the only legal token (keeps the set non-empty)."""
    s = spec["after_sep"] if prev == spec["sep"] else spec["allowed"]
    if prev is not None and prev in s and len(s) > 1:
        s = [t for t in s if t != prev]
    return s


def count_violations(spec: dict, toks: list[int]) -> int:
    bad, prev = 0, None
    for t in toks:
        bad += t not in allowed_at(spec, prev)
        prev = t
    return bad


def q2_failure_reason(result: dict | None, payload: dict | None) -> str | None:
    """Validate Q2 evidence before admitting another paid GPU stage (no ML imports)."""
    import math

    if not isinstance(result, dict) or result.get("per_request_applied_ok") is not True:
        return "vLLM stage failed or per-request mask check failed"
    for key in ("violations_masked_run", "mixed_batch_violations_masked_rows", "logprobs_missing"):
        if result.get(key) != 0:
            return f"invalid Q2 mask/logprob result: {key}"
    if result.get("mixed_batch_violations_unmasked_rows", 0) <= 0:
        return "mixed-batch negative control did not exercise the mask"
    if not isinstance(payload, dict):
        return "missing comparison payload"
    try:
        prompt, specs, tokens, logprobs = (payload[k] for k in ("prompt_ids", "specs", "tokens", "logprobs"))
        if not prompt or not specs or not (len(specs) == len(tokens) == len(logprobs)):
            return "empty or inconsistent comparison payload"
        for spec, row, lp_row in zip(specs, tokens, logprobs):
            if not row or len(row) != len(lp_row):
                return "empty or inconsistent token/logprob rows"
            if count_violations(spec, row):
                return "comparison payload contains illegal tokens"
            if any(not isinstance(lp, (int, float)) or not math.isfinite(lp) for lp in lp_row):
                return "comparison payload contains missing or nonfinite logprobs"
    except (KeyError, TypeError, ValueError):
        return "malformed comparison payload"
    return None


def q3_failure_reason(result: dict | None, payload: dict | None, bwd_seq: int) -> str | None:
    """Explicit Q3 pass gate (no ML imports). Q3 passes only on the complete, finite comparison population within the
    documented limits (mean <= 0.02 nats, max <= 1 nat, none above 1 nat), zero illegal tokens, and a measured
    (step 1) finite-gradient LoRA step at exactly the requested bwd_seq. Shorter OOM fallbacks are reported in
    `backward` but fail the gate (fix 2026-10-28: the old gate was "no error and logprob_check is a dict")."""
    import math

    def finite(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

    if not isinstance(result, dict):
        return "Q3 stage failed or did not run"
    if result.get("error"):
        return f"Q3 stage error: {str(result['error'])[:200]}"
    lc = result.get("logprob_check")
    if not isinstance(lc, dict) or not lc:
        return "no log-prob comparison"
    try:
        expected = sum(len(row) for row in payload["tokens"])
    except (KeyError, TypeError):
        return "missing comparison payload"
    for key in ("expected_positions", "positions", "compared"):
        if lc.get(key) != expected:
            return f"log-prob population {key}={lc.get(key)} != {expected} payload tokens"
    for key in ("nonfinite_vllm", "nonfinite_hf_fp32", "nonfinite_hf_bf16", "illegal_tokens_seen"):
        if lc.get(key) != 0:
            return f"{key}={lc.get(key)} (must be 0)"
    mean, mx, frac = lc.get("mean_abs_diff_fp32"), lc.get("max_abs_diff_fp32"), lc.get("frac_gt_1.0")
    if not (finite(mean) and mean <= 0.02):
        return f"mean |HF - vLLM| {mean} nats > 0.02"
    if not (finite(mx) and mx <= 1.0):
        return f"max |HF - vLLM| {mx} nats > 1.0"
    if frac != 0:
        return f"frac_gt_1.0={frac} (must be 0)"
    steps = result.get("backward")
    if not isinstance(steps, list):
        return "no backward steps"
    measured = [st for st in steps if isinstance(st, dict) and st.get("seq") == bwd_seq and st.get("step") == 1
                and "oom" not in st]
    if not measured:
        got = sorted({st.get("seq") for st in steps if isinstance(st, dict) and "oom" not in st} - {None})
        return f"no measured non-OOM backward step at seq {bwd_seq} (non-OOM seqs: {got})"
    st = measured[-1]
    if not finite(st.get("loss")):
        return f"nonfinite backward loss {st.get('loss')}"
    if not (finite(st.get("grad_norm")) and st["grad_norm"] > 0):
        return f"backward grad_norm {st.get('grad_norm')} is not finite and positive"
    return None


def _sha256(data: bytes | str) -> str:
    import hashlib
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def q2_manifest(payload: dict, q2: dict, n_req: int, out_tokens: int) -> dict:
    """Identity of a saved Q2 result: model revision, mask code, lock, pins and content digests (local only)."""
    import ast
    src = SCRIPT_PATH.read_text()
    segs = {n.name: ast.get_source_segment(src, n) for n in ast.parse(src).body
            if isinstance(n, ast.FunctionDef) and n.name in MASK_CODE}
    return {"model_rev": MODEL_REV, "script_sha256": _sha256("\n".join(segs[k] for k in MASK_CODE)),
            "lock_sha256": _sha256(LOCK.read_bytes()), "pins": list(PINS),
            "payload_sha256": _sha256(json.dumps(payload, sort_keys=True)),
            "q2_result_sha256": _sha256(json.dumps(q2, sort_keys=True)), "n_req": n_req, "out_tokens": out_tokens}


def q2_reuse_failure_reason(saved: dict, n_req: int, out_tokens: int) -> str | None:
    """Refuse a reused Q2 file unless its manifest matches the current model/code/lock/pins and its content digests,
    it still passes q2_failure_reason, and its rows agree with the stored request/token counts."""
    if not isinstance(saved, dict) or not isinstance(saved.get("manifest"), dict):
        return "no manifest (files saved before 2026-10-28 have none and can't be bound to this code)"
    v, payload, man = saved.get("Q2_vllm"), saved.get("payload"), saved["manifest"]
    if not isinstance(v, dict) or not isinstance(payload, dict):
        return "missing Q2 result or payload"
    try:
        now = q2_manifest(payload, v, man.get("n_req"), man.get("out_tokens"))
    except (TypeError, ValueError) as e:
        return f"cannot recompute manifest: {e}"
    bad = sorted(k for k in now if man.get(k) != now[k])
    if bad:
        return f"manifest mismatch: {', '.join(bad)}"
    if (man["n_req"], man["out_tokens"]) != (n_req, out_tokens):
        return f"saved n_req/out_tokens {man['n_req']}/{man['out_tokens']} != requested {n_req}/{out_tokens}"
    failure = q2_failure_reason(v, payload)
    if failure:
        return f"saved Q2 no longer validates: {failure}"
    rows = (payload["specs"], payload["tokens"], payload["logprobs"])
    if any(len(r) != man["n_req"] for r in rows):
        return f"payload has {[len(r) for r in rows]} rows, manifest n_req {man['n_req']}"
    if any(len(r) != man["out_tokens"] for r in payload["tokens"]):
        return f"a token row length differs from out_tokens {man['out_tokens']}"
    tpr = v.get("tokens_per_run")
    if tpr is not None and (not isinstance(tpr, list) or len(tpr) < 2
                            or tpr[1] != sum(map(len, payload["tokens"]))):
        return f"tokens_per_run {tpr} disagrees with the payload's {sum(map(len, payload['tokens']))} tokens"
    return None


def build_specs(tok, n_req: int) -> tuple[list[dict], dict]:
    def single(s):
        ids = tok.encode(s, add_special_tokens=False)
        return ids[0] if len(ids) == 1 else None
    letters = [t for t in (single(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ") if t is not None]
    digits = [t for t in (single(c) for c in "0123456789") if t is not None]
    sep = single("\n")
    assert sep is not None and len(letters) >= 20 and len(digits) >= 8, (sep, len(letters), len(digits))
    specs = []
    for i in range(n_req):  # every request gets a different set, so per-request application is testable
        let = [t for j, t in enumerate(letters) if j % 4 != i % 4]
        dig = digits[i % len(digits):] or digits
        after = let[:1] if i % 4 == 0 else let[: 4 + i % 5]  # i%4==0: forced token after "\n" -> logprob must be 0
        specs.append({"allowed": sorted(set(let + dig + [sep])), "after_sep": sorted(set(after)), "sep": sep})
    return specs, {"letters": len(letters), "digits": len(digits), "sep": sep}


def _watchdog(deadline_epoch: float, rate: float, name: str):
    """Kill this container once the wall clock passes the stage's ABSOLUTE deadline (set by the driver before the
    call). A preempted and restarted incarnation gets the same deadline, so it can't restart the budget clock (fix for
    w2-rev-modal: the old per-incarnation `elapsed x rate > budget` let two incarnations spend $12 of a $10 budget).
    A restart that boots after the deadline exits at once. The Modal timeout is the per-incarnation backstop."""
    import threading

    def expired():
        if time.time() > deadline_epoch:
            print(f"[{name}] SPEND CAP: past deadline {deadline_epoch:.0f} (now {time.time():.0f}); exiting", flush=True)
            os._exit(42)
            return True
        return False

    if expired():
        return

    def run():
        while not expired():
            time.sleep(5)
    threading.Thread(target=run, daemon=True).start()
    left = deadline_epoch - time.time()
    print(f"[{name}] watchdog: {left:.0f}s left to deadline (~${left * rate:.2f} at ${rate * 3600:.2f}/h)", flush=True)


def _ledger_update(path: Path, run_id: str, name: str, **fields):
    """Durable spend ledger (JSON, replaced atomically): one entry per (run, stage), updated in place."""
    data = json.loads(path.read_text()) if path.exists() else {"entries": []}
    entry = next((e for e in data["entries"] if e["run"] == run_id and e["stage"] == name), None)
    if entry is None:
        entry = {"run": run_id, "stage": name}
        data["entries"].append(entry)
    entry.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(data, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _plain(x):
    """JSON round trip so a stage result never needs torch/numpy to deserialize on the Mac."""
    return json.loads(json.dumps(x, default=str))


def _gpu_probe() -> dict:
    import subprocess
    import torch
    # str(): torch.__version__ is a TorchVersion, which can't be unpickled on the Mac (no torch) -> retry 1 lost Q2
    out = {"torch": str(torch.__version__), "cuda_build": str(torch.version.cuda),
           "cuda_available": bool(torch.cuda.is_available())}
    try:
        out["nvidia_smi"] = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                                            "--format=csv,noheader"], capture_output=True, text=True,
                                           timeout=30).stdout.strip()
    except Exception as e:  # noqa: BLE001
        out["nvidia_smi"] = repr(e)
    if not out["cuda_available"]:
        raise RuntimeError(f"CUDA not usable (driver vs CUDA {torch.version.cuda} runtime?): {out}")
    return out


# ---------------------------------------------------------------------------------------------------------------
# Q1: import check (CPU, heavy image). Fail fast before any GPU.
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=heavy_image, cpu=CPU_CPU, memory=CPU_MEM_GIB * 1024, timeout=900)
def import_check(deadline_epoch: float) -> dict:
    _watchdog(deadline_epoch, CPU_RATE, "import_check")
    import importlib
    import importlib.metadata as md
    import inspect
    res, ok = {"versions": {}, "checks": {}}, True
    for p in ["torch", "transformers", "vllm", "trl", "peft", "accelerate", "flashinfer-python", "triton",
              "nvidia-cuda-runtime", "huggingface-hub", "xgrammar"]:
        try:
            res["versions"][p] = md.version(p)
        except md.PackageNotFoundError:
            res["versions"][p] = None
    for spec in PINS:
        n, v = spec.split("==")
        good = res["versions"].get(n) == v
        res["checks"][f"pin {spec}"] = good
        ok &= good

    def check(name, fn, critical=True):
        nonlocal ok
        try:
            res["checks"][name] = fn() or True
        except Exception as e:  # noqa: BLE001
            res["checks"][name] = f"FAIL {type(e).__name__}: {e}"[:400]
            ok &= not critical

    check("import torch/transformers/peft/trl", lambda: [importlib.import_module(m) for m in
                                                         ["torch", "transformers", "peft", "trl"]] and None)
    check("transformers MuseGlimmerForConditionalGeneration",
          lambda: importlib.import_module("transformers").MuseGlimmerForConditionalGeneration and None)
    check("import vllm", lambda: importlib.import_module("vllm") and None)

    def vllm_registry():
        from vllm.model_executor.models.registry import ModelRegistry
        archs = set(ModelRegistry.get_supported_archs())
        need = {"MuseGlimmerForCausalLM", "MuseGlimmerForConditionalGeneration"}
        assert need <= archs, need - archs
    check("vllm registry has Glimmer", vllm_registry)

    def lp_iface():
        from vllm.v1.sample.logits_processor import LogitsProcessor
        from vllm.v1.sample.logits_processor.builtin import process_dict_updates  # noqa: F401
        return {m: str(inspect.signature(getattr(LogitsProcessor, m))) for m in
                ["__init__", "apply", "update_state", "is_argmax_invariant", "validate_params"]}
    check("vllm LogitsProcessor interface", lp_iface)

    def trl_sig():
        from trl import GRPOTrainer
        return str(inspect.signature(GRPOTrainer._get_per_token_logps_and_entropies))[:400]
    check("trl GRPOTrainer._get_per_token_logps_and_entropies", trl_sig, critical=False)
    res["ok"] = bool(ok)
    return res


# ---------------------------------------------------------------------------------------------------------------
# Q1b: weights -> Volume (CPU only, so the download is not billed at GPU rates)
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=light_image, cpu=CPU_CPU, memory=CPU_MEM_GIB * 1024, timeout=TIMEOUT_S,
              volumes={WEIGHTS_DIR: weights_vol})
def download_weights(deadline_epoch: float) -> dict:
    _watchdog(deadline_epoch, CPU_RATE, "download")
    from huggingface_hub import snapshot_download
    marker = Path(MODEL_PATH) / ".complete"
    if marker.exists():
        return {"cached": True, **json.loads(marker.read_text())}
    t0 = time.time()
    snapshot_download(MODEL_ID, revision=MODEL_REV, local_dir=MODEL_PATH,
                      allow_patterns=["*.json", "*.safetensors", "*.jinja", "tokenizer*"])
    dt = time.time() - t0
    size = sum(f.stat().st_size for f in Path(MODEL_PATH).rglob("*") if f.is_file())
    info = {"bytes": size, "seconds": round(dt, 1), "MB_per_s": round(size / dt / 1e6, 1)}
    marker.write_text(json.dumps(info))
    weights_vol.commit()
    return {"cached": False, **info}


# ---------------------------------------------------------------------------------------------------------------
# Q2: vLLM load + custom V1 LogitsProcessor + throughput (1x H100)
# ---------------------------------------------------------------------------------------------------------------
def _make_mask_lp_cls():
    """Build the toy feasibility-mask LogitsProcessor for vLLM 0.31.0's default Model Runner V2
    (vllm/v1/worker/gpu/sample/logits_processor/interface.py). Attempt 1 (2026-10-29) used the older V1 ABC
    (vllm.v1.sample.logits_processor) and the V2 loader rejected it (loader.py L86-91: "not a subclass of
    vllm.v1.worker.gpu.sample.logits_processor.LogitsProcessor").
    V2 contract (read from the 0.31.0 wheel): __init__(vllm_config, req_states); add_request(slot, params) -> bool
    (False = never touch this request); apply_staged_writes(); apply(logits[rows, V] fp32, ctx). apply() runs after
    bad-words/grammar and before temperature/min_p/top-k/top-p (sampler.py L231-270), once for the whole batch, so rows
    are filtered by ctx.expanded_idx_mapping (row -> request slot).
    The mask is fully on device (no host sync per step): per-slot boolean rows for `allowed` and `after_sep`, the
    previous token is ctx.input_ids (the token fed at this row), and the first output position (pos == prompt_len-1,
    where the fed token is the last prompt token) counts as prev=None, mirroring allowed_at().
    Defined inside a function because vllm isn't installed on the machine that runs `modal run`."""
    import torch
    from vllm.v1.worker.gpu.sample.logits_processor import LogitsProcessor

    class StowToyMaskLP(LogitsProcessor):
        stats = {"apply_calls": 0, "rows_masked": 0, "apply_s": 0.0, "neg_prev": 0}

        @classmethod
        def validate_params(cls, sampling_params):
            spec = (sampling_params.extra_args or {}).get(MASK_KEY)
            if spec is not None and not (spec.get("allowed") and spec.get("after_sep")):
                raise ValueError(f"{MASK_KEY} needs non-empty 'allowed' and 'after_sep'")

        def __init__(self, vllm_config, req_states):
            self.rs = req_states  # read-only view of the runner's per-slot buffers
            n, V, dev = req_states.max_num_reqs, req_states.vocab_size, req_states.device
            self.V = V
            self.active = torch.zeros(n, dtype=torch.bool, device=dev)
            self.allow = torch.zeros(n, V, dtype=torch.bool, device=dev)
            self.after = torch.zeros(n, V, dtype=torch.bool, device=dev)
            self.sep = torch.full((n,), -1, dtype=torch.long, device=dev)
            self.pending: dict[int, dict | None] = {}  # slot -> spec (None = deactivate), flushed before the forward

        def add_request(self, req_idx, sampling_params):
            spec = (sampling_params.extra_args or {}).get(MASK_KEY)
            self.pending[req_idx] = spec  # slots are recycled: always (re)initialise, including to "off"
            return spec is not None

        def apply_staged_writes(self):
            for slot, spec in self.pending.items():
                if spec is None:
                    self.active[slot] = False
                    continue
                self.allow[slot].zero_()
                self.allow[slot, torch.tensor(spec["allowed"], device=self.allow.device)] = True
                self.after[slot].zero_()
                self.after[slot, torch.tensor(spec["after_sep"], device=self.after.device)] = True
                self.sep[slot] = spec["sep"]
                self.active[slot] = True
            self.pending.clear()

        def apply(self, logits, ctx):
            t0 = time.perf_counter()  # host launch time only (no sync); the tok/s comparison is the real overhead
            slot = ctx.expanded_idx_mapping.long()
            on = self.active[slot]
            prev = ctx.input_ids.long()
            first = ctx.pos.long() == (self.rs.prompt_len.gpu[slot].long() - 1)
            is_sep = (prev == self.sep[slot]) & ~first
            allow = torch.where(is_sep[:, None], self.after[slot], self.allow[slot])  # (rows, V) bool
            # state rule: no immediate repeat of prev, unless it is the only legal token (mirrors allowed_at)
            prev_c = prev.clamp(0, self.V - 1)[:, None]
            at_prev = allow.gather(1, prev_c)[:, 0]
            drop = ~first & at_prev & (allow.sum(1) > 1)
            allow.scatter_(1, prev_c, (at_prev & ~drop)[:, None])
            V = min(logits.shape[1], self.V)
            logits[:, :V].masked_fill_(on[:, None] & ~allow[:, :V], float("-inf"))
            if logits.shape[1] > V:  # padded vocab columns are never legal for masked rows
                logits[:, V:].masked_fill_(on[:, None], float("-inf"))
            StowToyMaskLP.stats["apply_calls"] += 1
            StowToyMaskLP.stats["rows_masked"] += int(ctx.idx_mapping_np.size)  # rows seen (masked or not)
            StowToyMaskLP.stats["apply_s"] += time.perf_counter() - t0
            return logits

    return StowToyMaskLP


@app.function(image=heavy_image, gpu="H100!", cpu=GPU_CPU, memory=GPU_MEM_GIB * 1024, timeout=TIMEOUT_S,
              startup_timeout=STARTUP_S, volumes={WEIGHTS_DIR: weights_vol})
def vllm_stage(deadline_epoch: float, n_req: int, out_tokens: int, arch_override: bool, async_sched: bool,
               eager: bool, max_model_len: int = 16384, gpu_mem_util: float = 0.90) -> dict:
    _watchdog(deadline_epoch, GPU_RATE, "vllm")
    res = {"probe": _gpu_probe()}
    try:
        return _plain(_vllm_stage_body(res, n_req, out_tokens, arch_override, async_sched, eager, max_model_len,
                                       gpu_mem_util))
    except Exception as e:  # noqa: BLE001  (partial evidence beats a bare traceback; the driver still fails Q2)
        import traceback
        res.update(error=f"{type(e).__name__}: {e}"[:1500], traceback=traceback.format_exc()[-3000:],
                   per_request_applied_ok=False)
        return _plain(res)


def _vllm_stage_body(res, n_req, out_tokens, arch_override, async_sched, eager, max_model_len, gpu_mem_util):
    t_start = time.time()
    os.environ["VLLM_ENABLE_V1_MULTIPROCESSING"] = "0"
    import torch
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams

    weights_vol.reload()
    assert (Path(MODEL_PATH) / ".complete").exists(), "weights missing: run download_weights first"
    tok = AutoTokenizer.from_pretrained(MODEL_PATH)
    specs, vocab_info = build_specs(tok, n_req)
    prompt_ids = tok.encode("Stowage plan, bay 01, Laem Chabang -> Singapore:\n")  # includes BOS if template adds it
    res.update({"vocab_groups": vocab_info, "prompt_len": len(prompt_ids)})
    LP = _make_mask_lp_cls()

    def make_llm(override: bool):
        kw = dict(model=MODEL_PATH, tokenizer=MODEL_PATH, dtype="bfloat16", max_model_len=max_model_len,
                  gpu_memory_utilization=gpu_mem_util, language_model_only=True, logits_processors=[LP],
                  logprobs_mode="processed_logprobs", async_scheduling=bool(async_sched), enforce_eager=eager,
                  seed=0, max_num_seqs=max(n_req, 16))
        if override:
            kw["hf_overrides"] = {"architectures": ["MuseGlimmerForCausalLM"]}
        return LLM(**kw)

    t0 = time.time()
    try:
        llm = make_llm(arch_override)
        res["arch_override_used"] = arch_override
    except Exception as e:  # noqa: BLE001
        if not arch_override:
            raise
        res["arch_override_error"] = f"{type(e).__name__}: {e}"[:600]
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        llm = make_llm(False)
        res["arch_override_used"] = False
    res["engine_init_s"] = round(time.time() - t0, 1)  # load + compile + CUDA graphs + KV alloc
    free, total = torch.cuda.mem_get_info()
    res["gpu_mem_used_after_init_GB"] = round((total - free) / 1e9, 1)

    prompts = [{"prompt_token_ids": prompt_ids} for _ in range(n_req)]

    def sp(i, n_tok, masked):
        return SamplingParams(temperature=1.0, top_p=1.0, max_tokens=n_tok, min_tokens=n_tok, ignore_eos=True,
                              seed=1000 + i, logprobs=0, extra_args={MASK_KEY: specs[i]} if masked else None)

    def run(n_tok, masked_flags):
        t = time.time()
        outs = llm.generate(prompts, [sp(i, n_tok, m) for i, m in enumerate(masked_flags)], use_tqdm=False)
        dt = time.time() - t
        toks = [list(o.outputs[0].token_ids) for o in outs]
        lps = [[(lp[t].logprob if lp is not None and t in lp else None) for t, lp in zip(tk, o.outputs[0].logprobs)]
               for tk, o in zip(toks, outs)]
        return dt, toks, lps

    run(32, [True] * n_req)  # warm-up (also exercises the processor once)
    dt_a, toks_a, _ = run(out_tokens, [False] * n_req)
    LP.stats.update(apply_calls=0, rows_masked=0, apply_s=0.0, neg_prev=0)
    dt_b, toks_b, lps_b = run(out_tokens, [True] * n_req)
    stats_b = dict(LP.stats)
    mixed = [i % 2 == 0 for i in range(n_req)]
    _, toks_c, _ = run(256, mixed)

    n_a, n_b = sum(map(len, toks_a)), sum(map(len, toks_b))
    flat_lp = [x for row in lps_b for x in row if x is not None]
    forced = [(i, j) for i in range(n_req) for j in range(1, len(toks_b[i]))
              if len(allowed_at(specs[i], toks_b[i][j - 1])) == 1]
    res.update({
        "tok_s_no_mask": round(n_a / dt_a, 1), "tok_s_mask": round(n_b / dt_b, 1),
        "mask_overhead_pct": round(100 * (dt_b / dt_a - 1), 1), "gen_s_no_mask": round(dt_a, 1),
        "gen_s_mask": round(dt_b, 1), "tokens_per_run": [n_a, n_b],
        "lp_apply_s_total": round(stats_b["apply_s"], 2), "lp_apply_calls": stats_b["apply_calls"],
        "lp_rows_masked": stats_b["rows_masked"], "lp_negative_prev_seen": stats_b["neg_prev"],
        "violations_unmasked_run": sum(count_violations(specs[i], toks_a[i]) for i in range(n_req)),
        "violations_masked_run": sum(count_violations(specs[i], toks_b[i]) for i in range(n_req)),
        "mixed_batch_violations_masked_rows": sum(count_violations(specs[i], toks_c[i]) for i in range(n_req) if mixed[i]),
        "mixed_batch_violations_unmasked_rows": sum(count_violations(specs[i], toks_c[i]) for i in range(n_req) if not mixed[i]),
        "logprobs_missing": sum(x is None for row in lps_b for x in row),
        "logprob_max": max(flat_lp) if flat_lp else None,
        "forced_positions": len(forced),
        "forced_logprob_max_abs": max((abs(lps_b[i][j]) for i, j in forced if lps_b[i][j] is not None), default=None),
        "stage_s": round(time.time() - t_start, 1),
    })
    res["per_request_applied_ok"] = (res["violations_masked_run"] == 0 and res["mixed_batch_violations_masked_rows"] == 0
                                     and res["mixed_batch_violations_unmasked_rows"] > 0)
    # payload for Q3 (stripped from the printed blob)
    res["_payload"] = {"prompt_ids": prompt_ids, "specs": specs, "tokens": toks_b, "logprobs": lps_b}
    return res


# ---------------------------------------------------------------------------------------------------------------
# Q3: HF bf16 + PEFT masked log-probs vs vLLM, and one LoRA step at ~12k (1x H100)
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=heavy_image, gpu="H100!", cpu=GPU_CPU, memory=GPU_MEM_GIB * 1024, timeout=TIMEOUT_S,
              startup_timeout=STARTUP_S, volumes={WEIGHTS_DIR: weights_vol, ADAPTER_DIR: adapter_vol})
def hf_stage(deadline_epoch: float, payload: dict | None, bwd_seq: int, n_req: int) -> dict:
    _watchdog(deadline_epoch, GPU_RATE, "hf")
    res = {"probe": _gpu_probe(), "comparison_scope": "base-only (adapter disabled)",
           "adapter_on_sampler_trainer_agreement_verified": False,
           "adapter_sync_check": "pending: nonzero adapter through actual task 6.4 synchronization path"}
    try:
        return _plain(_hf_stage_body(res, payload, bwd_seq, n_req))
    except Exception as e:  # noqa: BLE001  (keep partial evidence, e.g. the log-prob check before a backward OOM)
        import traceback
        res.update(error=f"{type(e).__name__}: {e}"[:1500], traceback=traceback.format_exc()[-3000:])
        return _plain(res)


def _hf_stage_body(res, payload, bwd_seq, n_req):
    t_start = time.time()
    import numpy as np
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoTokenizer, MuseGlimmerForConditionalGeneration

    weights_vol.reload()
    adapter_vol.reload()
    dev = torch.device("cuda:0")
    t0 = time.time()
    # Retry 2 (2026-10-29) loaded with a per-module device_map dict naming only the weight-bearing prefixes and then
    # failed with "Tensor on device meta" in the first elementwise op: probably a weightless (buffer-only) module
    # outside those prefixes stayed on meta, and the matmuls before it silently propagated meta tensors. So: load
    # everything on the GPU (~63.5 GB fits in 80), THEN move the vision parts (~2B params) to CPU to free ~4 GB for the
    # 12k backward, and refuse to continue if any parameter or buffer is still on meta.
    model = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL_PATH, dtype=torch.bfloat16, device_map={"": 0},
                                                                attn_implementation="sdpa")
    for name in ("vision_tower", "vision_adapter", "vision_projection"):
        if hasattr(model.model, name):
            getattr(model.model, name).to("cpu")
    torch.cuda.empty_cache()
    meta = [n for n, t in list(model.named_parameters()) + list(model.named_buffers()) if t.is_meta]
    assert not meta, f"meta tensors after load: {meta[:10]}"
    res["device_map"] = "loaded all on GPU, vision moved to CPU; no meta tensors"
    res["hf_load_s"] = round(time.time() - t0, 1)
    tc = model.config.text_config
    mult, cap = float(tc.output_multiplier), float(tc.final_logit_softcapping)
    res.update({"output_multiplier": mult, "softcap": cap})

    ad_path = Path(ADAPTER_DIR) / "toy-adapter-peft"
    if (ad_path / "adapter_config.json").exists():
        model = PeftModel.from_pretrained(model, str(ad_path), is_trainable=True)
        res["adapter"] = "toy-adapter-peft from Volume (convert_adapter.py)"
    else:
        model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, bias="none",
                                                 target_modules=LORA_REGEX, task_type="CAUSAL_LM"))
        res["adapter"] = "fresh LoRA r16 (B=0, so adapter-on == base)"
    leaked = [n for n, m in model.named_modules() if hasattr(m, "lora_A") and "vision" in n]
    assert not leaked, f"LoRA leaked into vision tower: {leaked[:3]}"
    res["lora_modules"] = sum(1 for _, m in model.named_modules() if hasattr(m, "lora_A") and len(m.lora_A))
    base = model.get_base_model()
    lm, W = base.model.language_model, base.lm_head.weight  # lm returns post-norm last_hidden_state
    torch.cuda.reset_peak_memory_stats()
    res["gpu_mem_after_load_GB"] = round(torch.cuda.memory_allocated() / 1e9, 1)

    def masked_logps(ids_list, n_prompt, spec, full=False, grad=False):
        """z = cap*tanh(mult*(h.W_U^T)/cap) on the union U of legal tokens; logp(a) = z_a - logsumexp_{S_t} z."""
        comp = ids_list[n_prompt:]
        U = sorted(set(spec["allowed"]) | set(spec["after_sep"]))
        col = {t: j for j, t in enumerate(U)}
        M = np.zeros((len(comp), len(U)), dtype=bool)
        a_col = np.zeros(len(comp), dtype=np.int64)
        illegal, prev = 0, None
        for i, a in enumerate(comp):
            S = allowed_at(spec, prev)
            M[i, [col[t] for t in S]] = True
            if a in S:
                a_col[i] = col[a]
            else:
                illegal += 1
            prev = a
        ids = torch.tensor([ids_list], device=dev)
        with torch.set_grad_enabled(grad):
            h = lm(input_ids=ids).last_hidden_state[0, n_prompt - 1:-1]  # position t-1 predicts token t
            Ut = torch.tensor(U, device=dev)
            raw = h @ W.index_select(0, Ut).T  # bf16 (L, |U|)
            z32 = cap * torch.tanh(raw.float() * mult / cap)  # fp32 transform
            zbf = (cap * torch.tanh(raw * mult / cap)).float()  # bf16 transform (what vLLM / HF forward do)
            Mt = torch.from_numpy(M).to(dev)
            ac = torch.from_numpy(a_col).to(dev)[:, None]
            out = {}
            for k, z in (("fp32", z32), ("bf16", zbf)):
                out[k] = z.gather(1, ac)[:, 0] - torch.logsumexp(z.masked_fill(~Mt, float("-inf")), dim=-1)
            out["forced"] = torch.from_numpy(M.sum(1) == 1).to(dev)
            if full:
                lse, za = [], []
                tok_t = torch.tensor(comp, device=dev)
                for s in range(0, h.shape[0], 256):  # chunked full-vocab normaliser (no B x L x V tensor)
                    zf = cap * torch.tanh((h[s:s + 256] @ W.T).float() * mult / cap)
                    lse.append(torch.logsumexp(zf, dim=-1))
                    za.append(zf.gather(1, tok_t[s:s + 256, None])[:, 0])  # same matmul path as the normaliser
                out["unmasked"] = torch.cat(za) - torch.cat(lse)
        return out, illegal

    # --- 3a. HF masked vs vLLM processed log-probs (base model: adapter disabled, matches vLLM's weights) ---
    if payload:
        P, specs = payload["prompt_ids"], payload["specs"]
        diffs32, diffsbf, gap_unmasked, forced_abs, logratio_seq = [], [], [], [], []
        adapter_shift, illegal_total, expected = [], 0, 0
        nonfinite = {"vllm": 0, "hf_fp32": 0, "hf_bf16": 0}
        t0 = time.time()
        for i in range(min(n_req, len(payload["tokens"]))):
            toks, v = payload["tokens"][i], payload["logprobs"][i]
            vt = torch.tensor([x if x is not None else float("nan") for x in v], device=dev)
            with model.disable_adapter():
                o, ill = masked_logps(P + toks, len(P), specs[i], full=True)
            illegal_total += ill
            # Count every position (fix 2026-10-28): nonfinite values on any side are counted, never silently
            # dropped; stats use the all-finite positions and q3_failure_reason fails if any nonfinite exist.
            expected += len(toks)
            fin = {"vllm": torch.isfinite(vt), "hf_fp32": torch.isfinite(o["fp32"]),
                   "hf_bf16": torch.isfinite(o["bf16"])}
            for k, m in fin.items():
                nonfinite[k] += int((~m).sum().item())
            ok = fin["vllm"] & fin["hf_fp32"] & fin["hf_bf16"]
            diffs32.append((o["fp32"] - vt)[ok].abs().cpu())
            diffsbf.append((o["bf16"] - vt)[ok].abs().cpu())
            gap_unmasked.append((o["unmasked"] - o["fp32"])[ok].abs().cpu())
            f = o["forced"] & ok
            if f.any():
                forced_abs.append(o["fp32"][f].abs().max().item())
            logratio_seq.append((o["fp32"] - vt)[ok].sum().item())  # log IS ratio over a whole rollout
            if i < 4 and "fresh" not in res["adapter"]:
                oa, _ = masked_logps(P + toks, len(P), specs[i])
                adapter_shift.append((oa["fp32"] - o["fp32"]).abs().mean().item())
        d32, dbf, gu = torch.cat(diffs32), torch.cat(diffsbf), torch.cat(gap_unmasked)
        res["logprob_check"] = {
            "positions": int(d32.numel()), "expected_positions": expected, "compared": int(d32.numel()),
            "nonfinite_vllm": nonfinite["vllm"], "nonfinite_hf_fp32": nonfinite["hf_fp32"],
            "nonfinite_hf_bf16": nonfinite["hf_bf16"], "illegal_tokens_seen": illegal_total,
            "mean_abs_diff_fp32": round(d32.mean().item(), 5), "mean_abs_diff_bf16": round(dbf.mean().item(), 5),
            "p99_abs_diff_fp32": round(d32.quantile(0.99).item(), 5), "max_abs_diff_fp32": round(d32.max().item(), 5),
            "frac_gt_0.1": round((d32 > 0.1).float().mean().item(), 5),
            "frac_gt_1.0": round((d32 > 1.0).float().mean().item(), 6),
            "per_token_IS_ratio_range": [round(float(np.exp(-d32.max().item())), 4), round(float(np.exp(d32.max().item())), 4)],
            "seq_log_IS_ratio_abs_max": round(max(abs(x) for x in logratio_seq), 4),
            "forced_hf_logprob_abs_max": max(forced_abs) if forced_abs else None,
            "unmasked_minus_masked_mean_abs": round(gu.mean().item(), 4),  # bias if the trainer ignored the mask
            "adapter_on_minus_off_mean_abs": [round(x, 4) for x in adapter_shift] or None,
            "scoring_s": round(time.time() - t0, 1),
        }
        # Cross-check that the gathered transform == the model's own forward logits (multiplier + softcap path)
        toks = payload["tokens"][0]
        ids = P + toks
        with torch.no_grad(), model.disable_adapter():
            lg = model(input_ids=torch.tensor([ids], device=dev), logits_to_keep=64).logits[0, :-1].float()
            tgt = torch.tensor(ids[-63:], device=dev)
            z_fwd = lg.gather(1, tgt[:, None])[:, 0]
            h = lm(input_ids=torch.tensor([ids], device=dev)).last_hidden_state[0, -64:-1]
            z_g = (cap * torch.tanh((h @ W.index_select(0, tgt).T).diagonal() * mult / cap)).float()
        res["forward_vs_gathered_logit_max_abs"] = round((z_fwd - z_g).abs().max().item(), 5)
    else:
        res["logprob_check"] = "skipped (no vLLM payload)"

    # --- 3b. one LoRA training step at ~bwd_seq tokens with gradient checkpointing ---
    torch.cuda.empty_cache()
    rng = random.Random(0)
    if payload:
        spec0, prompt = payload["specs"][0], payload["prompt_ids"]
    else:
        tok = AutoTokenizer.from_pretrained(MODEL_PATH)
        spec0, prompt = build_specs(tok, 1)[0][0], tok.encode("Stowage plan, bay 01:\n")
    model.train()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    base.config.use_cache = False
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=1e-5)
    res["trainable_params"] = sum(p.numel() for p in params)
    steps, seq = [], bwd_seq
    while seq >= 2048:
        try:
            for step in range(2):  # step 0 = warm-up, step 1 = measured
                comp, prev = [], None
                for _ in range(seq - len(prompt)):  # a rule-consistent random completion
                    prev = rng.choice(allowed_at(spec0, prev))
                    comp.append(prev)
                torch.cuda.synchronize()
                torch.cuda.reset_peak_memory_stats()
                t0 = time.time()
                o, _ = masked_logps(prompt + comp, len(prompt), spec0, grad=True)
                keep = ~o["forced"]  # forced positions carry no gradient (env_mask = 0, cuda-stack.md 2(b))
                loss = -(o["fp32"][keep]).mean()  # toy advantage = +1
                loss.backward()
                gn = torch.nn.utils.clip_grad_norm_(params, 1e9).item()
                opt.step()
                opt.zero_grad(set_to_none=True)
                torch.cuda.synchronize()
                steps.append({"seq": seq, "step": step, "s": round(time.time() - t0, 2),
                              "peak_GB": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                              "loss": round(loss.item(), 4), "grad_norm": round(gn, 4)})
                print("bwd", steps[-1], flush=True)
            break
        except torch.cuda.OutOfMemoryError as e:
            steps.append({"seq": seq, "oom": str(e)[:200]})
            opt.zero_grad(set_to_none=True)
            torch.cuda.empty_cache()
            seq //= 2
    res["backward"] = steps
    res["stage_s"] = round(time.time() - t_start, 1)

    # --- 3c. diagnose retry 2's failure: reload with the OLD per-module device_map dict and list what is on meta ---
    try:
        import gc
        del model, base, lm, W, opt, params
        gc.collect()
        torch.cuda.empty_cache()
        old = {"model.language_model": 0, "lm_head": 0, "model.vision_tower": "cpu",
               "model.vision_adapter": "cpu", "model.vision_projection": "cpu"}
        m2 = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL_PATH, dtype=torch.bfloat16, device_map=old,
                                                                 attn_implementation="sdpa")
        meta = [n for n, t in list(m2.named_parameters()) + list(m2.named_buffers()) if t.is_meta]
        dev = {}
        for n, t in list(m2.named_parameters()) + list(m2.named_buffers()):
            dev[str(t.device)] = dev.get(str(t.device), 0) + 1
        with torch.no_grad():
            h2 = m2.model.language_model(input_ids=torch.tensor([[1, 2, 3]], device="cuda:0")).last_hidden_state
        res["old_device_map_diagnosis"] = {
            "meta_tensors": meta[:20], "n_meta": len(meta), "tensor_count_by_device": dev,
            "lm_head_weight_device": str(m2.lm_head.weight.device), "hidden_device": str(h2.device),
            "hf_device_map_sample": dict(list(getattr(m2, "hf_device_map", {}).items())[:8])}
    except Exception as e:  # noqa: BLE001
        res["old_device_map_diagnosis"] = f"{type(e).__name__}: {e}"[:600]
    return res


# ---------------------------------------------------------------------------------------------------------------
# Q4 (optional, --trl): UNTESTED sketch. 2 GRPO steps with TRL, HF-generate path, mask = bad_words_ids.
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=heavy_image, gpu="H100!", cpu=GPU_CPU, memory=GPU_MEM_GIB * 1024, timeout=TIMEOUT_S,
              startup_timeout=STARTUP_S, volumes={WEIGHTS_DIR: weights_vol})
def trl_stage(deadline_epoch: float) -> dict:
    """UNTESTED. Shows where the masked log-prob override plugs into TRL 1.16. The mask here is a static banned set,
    so HF generate can enforce it via GenerationConfig.bad_words_ids (TRL forwards generation_kwargs into
    GenerationConfig, grpo_trainer.py L1155-1175). The real StowLM mask is state-dependent and needs rollout_func or vLLM."""
    _watchdog(deadline_epoch, GPU_RATE, "trl")
    res = {"probe": _gpu_probe(), "status": "UNTESTED sketch"}
    try:
        return _plain(_trl_stage_body(res))
    except Exception as e:  # noqa: BLE001
        import traceback
        res.update(error=f"{type(e).__name__}: {e}"[:1500], traceback=traceback.format_exc()[-3000:])
        return _plain(res)


def _trl_stage_body(res):
    t_start = time.time()
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer, MuseGlimmerForConditionalGeneration
    from trl import GRPOConfig, GRPOTrainer

    weights_vol.reload()
    tok = AutoTokenizer.from_pretrained(MODEL_PATH)
    digit_ids = [tok.encode(c, add_special_tokens=False) for c in "0123456789"]
    banned = sorted({ids[0] for ids in digit_ids if len(ids) == 1})

    class MaskedGRPOTrainer(GRPOTrainer):
        # Signature copied from trl 1.16.0 grpo_trainer.py L1419-1438 (wheel). We bypass fused_lm_head (L1497),
        # which has no mask argument, and normalise over V \ banned instead.
        def _get_per_token_logps_and_entropies(self, model, input_ids, attention_mask, logits_to_keep,
                                               batch_size=None, compute_entropy=False, compute_aux_loss=False,
                                               **mm_kwargs):
            batch_size = batch_size or input_ids.size(0)
            all_lp, all_ent = [], []
            for s in range(0, input_ids.size(0), batch_size):
                ids, am = input_ids[s:s + batch_size], attention_mask[s:s + batch_size]
                with self.accelerator.autocast():
                    logits = model(input_ids=ids, attention_mask=am, logits_to_keep=logits_to_keep + 1).logits
                logits = logits[:, :-1].float() / self.temperature  # HF logits already have multiplier + softcap
                logits[..., banned] = float("-inf")
                logp = torch.log_softmax(logits, dim=-1)
                all_lp.append(logp.gather(-1, ids[:, -logits_to_keep:, None])[..., 0])
                if compute_entropy:
                    p = logp.exp()
                    all_ent.append(-(p * logp.masked_fill(~torch.isfinite(logp), 0)).sum(-1))
            return torch.cat(all_lp), (torch.cat(all_ent) if compute_entropy else None), None

    model = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL_PATH, dtype=torch.bfloat16, device_map="cuda",
                                                                attn_implementation="sdpa")

    def reward(completions, **kw):  # toy: prefer short answers containing "A"
        return [float("A" in (c if isinstance(c, str) else str(c))) - 0.01 * len(str(c)) for c in completions]

    cfg = GRPOConfig(output_dir="/tmp/trl-smoke", max_steps=2, per_device_train_batch_size=2, num_generations=2,
                     max_completion_length=64, learning_rate=1e-5, bf16=True, gradient_checkpointing=True,
                     logging_steps=1, report_to=[], use_vllm=False, save_strategy="no",
                     generation_kwargs={"bad_words_ids": [[t] for t in banned]})
    ds = Dataset.from_list([{"prompt": "List three container IDs for bay 01:"}] * 8)
    trainer = MaskedGRPOTrainer(model=model, reward_funcs=reward, args=cfg, train_dataset=ds, processing_class=tok,
                                peft_config=LoraConfig(r=16, lora_alpha=32, target_modules=LORA_REGEX,
                                                       task_type="CAUSAL_LM"))
    t0 = time.time()
    out = trainer.train()
    res.update({"train_s": round(time.time() - t0, 1), "metrics": {k: v for k, v in out.metrics.items()},
                "log_history": trainer.state.log_history[-2:],
                "peak_GB": round(torch.cuda.max_memory_allocated() / 1e9, 1), "stage_s": round(time.time() - t_start, 1)})
    return res


# ---------------------------------------------------------------------------------------------------------------
# Local driver: spend cap, ordering, fail-fast, results blob
# ---------------------------------------------------------------------------------------------------------------
@app.local_entrypoint()
def main(cap: float = 15.0, trl: bool = False, only_cpu: bool = False, skip_hf: bool = False, n_req: int = 16,
         out_tokens: int = 2000, bwd_seq: int = 12288, arch_override: bool = True, async_sched: bool = False,
         eager: bool = False, q2_payload: str = ""):
    if q2_payload:  # validate a reused Q2 before ANY stage is billed
        saved = json.loads(Path(q2_payload).read_text())
        reason = q2_reuse_failure_reason(saved, n_req, out_tokens)
        if reason:
            raise SystemExit(f"--q2-payload {q2_payload} refused: {reason}")
    run_id, ledger = f"run-{int(time.time())}", HERE / "runs" / "spend_ledger.json"
    spent, results = 0.0, {"cap_usd": cap, "adapter_on_sampler_trainer_agreement_verified": False,
                         "adapter_sync_check": "pending: task 6.4 actual synchronization path",
                         "rates": {"gpu_stage_per_h": round(GPU_RATE * 3600, 3),
                                                     "cpu_stage_per_h": round(CPU_RATE * 3600, 3)}, "stages": {}}

    def stage(name, fn, rate, *args):
        nonlocal spent
        typical, reserve, left = TYPICAL_S[name] * rate, STARTUP_S * rate, cap - spent
        if typical + reserve > left:
            raise SystemExit(f"SPEND CAP: {name} needs ~${typical:.2f} + ${reserve:.2f} startup reserve, "
                             f"spent ~${spent:.2f} of ${cap:.2f}; abort")
        t = time.time()
        # Durable reservation BEFORE the call: every incarnation (preemption restarts included) must finish by this
        # absolute deadline, so this stage can't bill more than `left` in total; the reserve is held back for boot,
        # poll slack and scale-down idle. If the driver dies mid-call, the ledger still says "reserved".
        deadline = t + (left - reserve) / rate
        _ledger_update(ledger, run_id, name, reserved_usd=round(left, 4), deadline_epoch=deadline, rate=rate,
                       status="reserved", started_epoch=t)
        print(f"== {name}: typical ~${typical:.2f}, reserved ${left:.2f} (deadline in {deadline - t:.0f}s), "
              f"spent so far ~${spent:.2f}", flush=True)
        try:
            out, err = fn.remote(deadline, *args), None
        except BaseException as e:  # noqa: BLE001  (KeyboardInterrupt too: keep the reservation, then re-raise)
            out, err = None, f"{type(e).__name__}: {e}"[:1500]
            if not isinstance(e, Exception):
                spent += left
                _ledger_update(ledger, run_id, name, status="interrupted", charged_usd=round(left, 4))
                raise
        dt = time.time() - t
        # No refunds inferred: an errored call may have run to its deadline (or be retrying), so it keeps the full
        # reservation. A clean return is charged its client wall time (queueing included, so never below the
        # container's) plus the billed scale-down idle, even if that exceeds the reservation.
        charge = left if err else (dt + IDLE_S) * rate
        spent += charge
        _ledger_update(ledger, run_id, name, status="error" if err else "done", wall_s=round(dt, 1),
                       charged_usd=round(charge, 4))
        results["stages"][name] = {"wall_s": round(dt, 1), "est_cost_usd": round(charge, 3), "reserved_usd":
                                   round(left, 3), "deadline_epoch": round(deadline, 1), "error": err}
        results["est_spent_usd"] = round(spent, 3)
        return out

    def finish():
        blob = json.dumps(results, indent=2, default=str)
        print(blob)
        (HERE / "modal_smoke_results.json").write_text(blob)
        print(f"Estimated spend ~${spent:.2f}. Log it in docs/compute.md (ledger) and docs/evaluation.md.")

    r = stage("import_check", import_check, CPU_RATE)
    results["Q1_import"] = r
    if not r or not r.get("ok"):
        results["verdict"] = "Q1 failed: stack does not import on Modal; no GPU started"
        return finish()
    results["Q1b_download"] = stage("download", download_weights, CPU_RATE)
    if not results["Q1b_download"]:
        results["verdict"] = "weights download failed; no GPU started"
        return finish()
    if LOCAL_ADAPTER and LOCAL_ADAPTER.joinpath("adapter_config.json").exists():
        with adapter_vol.batch_upload(force=True) as b:
            b.put_directory(str(LOCAL_ADAPTER), "/toy-adapter-peft")
        results["adapter_uploaded"] = str(LOCAL_ADAPTER)
    if only_cpu:
        return finish()

    if q2_payload:  # reuse a passing Q2 (validated against its manifest at the top of main) instead of re-running vLLM
        v, payload = saved["Q2_vllm"], saved["payload"]
        results["Q2_vllm"] = {**v, "reused_from": q2_payload}
    else:
        v = stage("vllm", vllm_stage, GPU_RATE, n_req, out_tokens, arch_override, async_sched, eager)
        payload = v.pop("_payload", None) if v else None
        results["Q2_vllm"] = v
        if payload:
            out = HERE / "runs" / f"q2_payload-{int(time.time())}.json"  # unix time; reuse with --q2-payload
            out.parent.mkdir(exist_ok=True)
            # The manifest binds this evidence to the model, mask code, lock and content; reuse checks it.
            out.write_text(json.dumps({"Q2_vllm": v, "payload": payload,
                                       "manifest": q2_manifest(payload, v, n_req, out_tokens)}))
            results["q2_payload_saved"] = str(out)
    failure = q2_failure_reason(v, payload)
    if failure:
        results["verdict"] = f"Q2 failed: {failure}; no later GPU stage started"
        return finish()
    if not skip_hf:
        results["Q3_hf"] = stage("hf", hf_stage, GPU_RATE, payload, bwd_seq, n_req)
    q3_fail = q3_failure_reason(results.get("Q3_hf"), payload, bwd_seq)
    results["Q3_verdict"] = f"FAIL: {q3_fail}" if q3_fail else "PASS"
    if trl and q3_fail:
        results["Q4_trl_UNTESTED"] = f"skipped: Q3 did not pass ({q3_fail}); Q4 runs only after Q1-Q3 pass"
    elif trl:
        results["Q4_trl_UNTESTED"] = stage("trl", trl_stage, GPU_RATE)
    finish()
