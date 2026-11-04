# 6.4 falsifiers on Modal (D26 amendment items 1-3; item 4, the mixed-outcome group rate, waits for SFT).
# Built from the production pieces in src/llm/rl/modal_grpo.py, so what runs here is what GRPO would run:
#   - rollout worker `_rollout_process` (vLLM 0.31, Model Runner V2, `StowMaskLP` = the real state-dependent
#     feasibility mask: one MaskedDecoder per sample, host Python, advanced from the fed token every step);
#   - weight sync `sync_policy_weights` (merge -> export LoRA-wrapped modules -> worker load_weights -> unmerge);
#   - trainer forward `make_masked_grpo_trainer(...)._get_per_token_logps_and_entropies` (masked log-probs over
#     the replayed `allowed_sets`) and loss `grpo_rollout_loss` (decision-token normalisation).
#
# WHAT IT MEASURES
#   F1 real-mask rollout throughput through vLLM on 8 tiny + 8 small instances (base weights):
#      tiny: 8 instances x G=8 in one batch (b64); small: two GRPO-step-shaped batches of 4 instances x G=8 (b32),
#      plus an unmasked control at b32 (forward-only tok/s at the same batch). Reports tok/s, the mask's host time
#      inside apply() and its share of wall time, statuses. Pass: a small step's rollout fits ~5 min.
#   F2 adapter-on HF vs vLLM masked log-prob agreement through the real sync, before and after one optimizer update
#      (nonzero toy adapter, `stowlm-adapters:/toy-adapter-peft`). Witness: vLLM BEFORE any sync (base weights) must
#      disagree with the adapter-on trainer, so a sync that silently did nothing fails. Pass: mean <= 0.01 nats,
#      max <= 1 nat, every position finite and compared, after sync 1 and after sync 2; the update must change the
#      trainer's log-probs.
#   F3 invocation proof: the override is wrapped with a counter (grad-enabled calls and positions); a witness checks
#      its outputs are the MASKED log-probs (> the full-vocab log-softmax at every decision position, == 0 at forced
#      positions); `grpo_rollout_loss`'s record must show it normalised over exactly the decision-token count.
#
# APPROXIMATIONS / LABELS
#   - Engine: C2 global screen off (`feasibility_extra={"global_screen": 0}`), the labelled 6.2-pilot setting while
#     the 3.7 linear policy is stale (the default engine dead-ends every state at depth 0 with
#     'Global stability model unavailable'). L1/L2 local checks and C1 stay on.
#   - Instances: experiments/base-mask/instances-v3-val (val split): used only for timing and agreement. The one
#     optimizer step trains a throwaway copy of the toy adapter that is never saved, so no val data reaches a model.
#   - Toy reward for the one update (+1 complete, 0 otherwise, group-normalised): the real checker reward isn't on
#     Modal yet (physics-cache Volume); its CPU cost is measured locally on the returned plans instead.
#
# SPEND (cumulative, review w2-rev-modal finding 1): before each remote call the local driver writes a reservation
# for the stage's whole remaining budget to runs/falsify-64-ledger.json and passes an ABSOLUTE deadline; the
# container watchdog exits at that wall-clock time, so a preempted and restarted incarnation can't extend it.
# Rates are list x 1.25 (metered was 1.09-1.20x the client estimate on 29 Oct). retries=0.
#
# RUN:  uv run --with modal modal run experiments/grpo-venue/falsify_64.py --cap 5
import json
import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1] if len(HERE.parents) > 1 else None  # None inside the container (/root/falsify_64.py)
LOCK = HERE / "modal_smoke.lock.txt"
MODEL_PATH = "/weights/Muse-Glimmer-30B@cec94fcf"
ADAPTER = "/adapters/toy-adapter-peft"
C2_OFF = {"global_screen": 0}
LEDGER = HERE / "runs" / "falsify-64-ledger.json"

BILLING_OVERHEAD = 1.25
H100_S, CORE_S, GIB_S = 0.001097, 0.0000131, 0.00000222
GPU_RATE = BILLING_OVERHEAD * (2 * H100_S + 8 * CORE_S + 128 * GIB_S)  # 2x H100, 8 cores, 128 GiB
CPU_RATE = BILLING_OVERHEAD * (4 * CORE_S + 16 * GIB_S)
STARTUP_S = 300  # billed boot/idle reserve held back from every stage's deadline
GPU_TIMEOUT_S = 1500

app = modal.App("stowlm-falsify-64")
weights_vol = modal.Volume.from_name("stowlm-glimmer-weights", create_if_missing=False)
adapter_vol = modal.Volume.from_name("stowlm-adapters", create_if_missing=False)
image = (modal.Image.debian_slim(python_version="3.12")
         .uv_pip_install(requirements=[str(LOCK)])
         .uv_pip_install("scipy==1.18.1", "ortools==9.15.6755", "numpy==2.3.5")
         .env({"VLLM_ENABLE_V1_MULTIPROCESSING": "0", "TOKENIZERS_PARALLELISM": "false",
               "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True", "PYTHONPATH": "/root/stowlm/src",
               "OPENBLAS_NUM_THREADS": "1"}))
if ROOT is not None:
    image = (image.add_local_dir(str(ROOT / "src"), "/root/stowlm/src", ignore=["**/__pycache__/**"])
             .add_local_dir(str(ROOT / "params"), "/root/stowlm/params"))


def _watchdog(deadline_epoch: float, name: str):
    """Exit at an absolute wall-clock deadline (survives restarts: a late incarnation exits at once)."""
    import threading

    def run():
        while True:
            if time.time() > deadline_epoch:
                print(f"[{name}] SPEND CAP: absolute deadline passed; exiting", flush=True)
                os._exit(42)
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()
    print(f"[{name}] watchdog: {deadline_epoch - time.time():.0f}s left", flush=True)


def _engine_and_codec(spec: dict):
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from stowage.model import Instance
    instance = Instance.model_validate(spec["instance"])
    tokenizer = load_tokenizer(MODEL_PATH)
    engine = make_feasibility_engine(instance, get_action_space(),
                                     config=FeasibilityConfig(extra=dict(spec["feasibility_extra"])))
    return instance, engine, ActionCodec(tokenizer, [c.id for c in instance.containers])


# ---------------------------------------------------------------------------------------------------------------
# CPU preflight: the image imports the project, builds every engine/decoder and encodes every prompt. No GPU.
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=image, cpu=4, memory=16 * 1024, timeout=600, retries=0, volumes={"/weights": weights_vol})
def preflight(deadline_epoch: float, instances: dict) -> dict:
    _watchdog(deadline_epoch, "preflight")
    import random
    import stowage.interfaces  # noqa: F401
    from llm.rl import modal_grpo as grpo
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from llm.stowlm.mask import random_masked_plan
    from stowage.model import Instance
    out, tokenizer = {"instances": {}}, load_tokenizer(MODEL_PATH)
    for name, inst in instances.items():
        spec = {"instance": inst, "eos_token_id": 0, "feasibility_extra": C2_OFF}
        instance, engine, codec = _engine_and_codec(spec)
        if engine.dead_end() is not None:
            raise RuntimeError(f"{name}: engine dead end at depth 0: {engine.dead_end()}")
        decoder = grpo.decoder_from_spec(spec)
        assert decoder.allowed_token_ids(), name
        t0 = time.perf_counter()
        dec = random_masked_plan(engine, codec, random.Random(0), max_tokens=400)
        n = len(dec.actions)
        prompt = encode_prompt(Instance.model_validate(inst), tokenizer)
        out["instances"][name] = {"prompt_len": len(prompt), "random_status": dec.status, "random_actions": n,
                                  "random_s": round(time.perf_counter() - t0, 3), "prompt_ids": list(prompt)}
    import numpy, scipy
    out["versions"] = {"numpy": numpy.__version__, "scipy": scipy.__version__}
    return out


# ---------------------------------------------------------------------------------------------------------------
# GPU run: GPU1 = production rollout worker (vLLM + real mask), GPU0 = HF bf16 + PEFT trainer.
# ---------------------------------------------------------------------------------------------------------------
@app.function(image=image, gpu="H100:2", cpu=8, memory=128 * 1024, timeout=GPU_TIMEOUT_S, retries=0,
              volumes={"/weights": weights_vol, "/adapters": adapter_vol})
def falsify(deadline_epoch: float, instances: dict, prompts: dict, lr: float = 1e-4, only_f2: bool = False) -> dict:
    _watchdog(deadline_epoch, "falsify")
    res = {"timeline": {}}
    try:
        return json.loads(json.dumps(_falsify(res, instances, prompts, lr, only_f2), default=str))
    except Exception as e:  # noqa: BLE001  (partial evidence beats a bare traceback)
        import traceback
        res.update(error=f"{type(e).__name__}: {e}"[:2000], traceback=traceback.format_exc()[-4000:])
        return json.loads(json.dumps(res, default=str))


def _falsify(res, instances, prompts, lr, only_f2=False):
    import multiprocessing
    import numpy as np
    import torch
    import stowage.interfaces  # noqa: F401
    from llm.rl import modal_grpo as grpo
    from llm.stowlm.mask import allowed_sets
    from transformers import MuseGlimmerForConditionalGeneration
    t_start = time.time()
    tl = res["timeline"]

    def mark(k):
        tl[k] = round(time.time() - t_start, 1)
        print(f"[falsify] {k} at {tl[k]}s", flush=True)

    # Worker first, so vLLM loads on GPU1 while the trainer loads on GPU0.
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=grpo._rollout_process, args=(child,))
    proc.start()
    child.close()
    try:
        torch.cuda.set_device(0)
        base = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL_PATH, dtype=torch.bfloat16,
                                                                   device_map={"": 0}, attn_implementation="sdpa")
        for name in ("vision_tower", "vision_adapter", "vision_projection"):
            if hasattr(base.model, name):
                getattr(base.model, name).to("cpu")
        torch.cuda.empty_cache()
        model = grpo.initialize_policy_adapter(base, ADAPTER)  # nonzero toy adapter, trainable
        model.train()
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
        mark("trainer_loaded")
        grpo._receive(parent, 900)
        mark("worker_ready")

        from transformers import AutoTokenizer
        eos = AutoTokenizer.from_pretrained(MODEL_PATH).eos_token_id
        res["eos_token_id"] = eos
        specs = {n: {"instance": inst, "eos_token_id": eos, "feasibility_extra": C2_OFF}
                 for n, inst in instances.items()}

        def rollout(names, group, max_tokens, tag, masked=True, fixed_tokens=None):
            batch = []
            for k, n in enumerate(names):
                for g in range(group):
                    spec = {**specs[n], "rid": f"{tag}-{n}-{g}"} if masked else None
                    batch.append({"prompt_ids": prompts[n], "spec": spec, "seed": 1000 * k + g,
                                  "max_tokens": fixed_tokens or max_tokens, "name": n})
            parent.send({"batch": batch})
            out = grpo._receive(parent, 900)
            out["names"] = [b["name"] for b in batch]
            n_tok = sum(map(len, out["tokens"]))
            summary = {"seqs": len(batch), "tokens": n_tok, "wall_s": round(out["wall_s"], 2),
                       "tok_s": round(n_tok / out["wall_s"], 1), "mask_apply_s": round(out["mask"]["apply_s"], 2),
                       "mask_host_share": round(out["mask"]["apply_s"] / out["wall_s"], 3),
                       "mask_calls": out["mask"]["apply_calls"], "mask_advances": out["mask"]["advances"],
                       "statuses": {s: out["status"].count(s) for s in set(map(str, out["status"]))},
                       "mean_len": round(n_tok / len(batch), 1), "max_len": max(map(len, out["tokens"]))}
            return out, summary

        tiny = sorted(n for n in instances if n.startswith("tiny"))
        small = sorted(n for n in instances if n.startswith("small"))
        # ---- F1: real-mask throughput, base weights (no sync yet) ----
        f1 = {}
        if only_f2:  # re-run of F2 only (review w2-rev-grpo): one base rollout per F2 prompt is the witness
            a_w, res["F2_witness_rollout"] = rollout(tiny[:4] + small[:4], 1, 1536, "f2w")
            mark("f2_witness_rollout")
        if not only_f2:
            a_tiny, f1["tiny_8x8_b64"] = rollout(tiny, 8, 512, "f1t")
            mark("f1_tiny")
            a_s1, f1["small_step1_4x8_b32"] = rollout(small[:4], 8, 1536, "f1s1")
            mark("f1_small1")
            a_s2, f1["small_step2_4x8_b32"] = rollout(small[4:], 8, 1536, "f1s2")
            mark("f1_small2")
            ctl_len = int(np.median([len(t) for t in a_s1["tokens"]])) or 256
            _, f1["small_unmasked_control_4x8_b32"] = rollout(small[:4], 8, 0, "f1c", masked=False, fixed_tokens=ctl_len)
            mark("f1_control")
            res["F1"] = f1
            res["F1_tokens"] = {"tiny": [a_tiny["names"], a_tiny["tokens"], a_tiny["status"]],
                                "small1": [a_s1["names"], a_s1["tokens"], a_s1["status"]],
                                "small2": [a_s2["names"], a_s2["tokens"], a_s2["status"]]}

        # ---- trainer-side masked log-probs through the production override, instrumented (F3) ----
        replay = {"sets": None}
        cls = grpo.make_masked_grpo_trainer(lambda ids, am, length: replay["sets"])
        calls = {"total": 0, "grad": 0, "positions": 0}
        original = cls._get_per_token_logps_and_entropies

        def counted(self, *a, **k):
            calls["total"] += 1
            calls["grad"] += int(torch.is_grad_enabled())
            out = original(self, *a, **k)
            calls["positions"] += int(out[0].numel())
            return out
        cls._get_per_token_logps_and_entropies = counted
        trainer = object.__new__(cls)
        trainer.temperature = grpo.ROLLOUT_TEMPERATURE
        engines = {n: _engine_and_codec(specs[n]) for n in instances}

        def policy_tokens(name, tokens):
            """Strip the transport EOS: replay until the decoder has no legal token (as score_rollout does)."""
            _, engine, codec = engines[name]
            from llm.stowlm.mask import MaskedDecoder
            dec, actual = MaskedDecoder(engine.copy(), codec), []
            for t in tokens:
                if not dec.allowed_token_ids():
                    break
                dec.advance(t)
                actual.append(t)
            return actual, dec.status

        def trainer_logps(name, actual, grad=False):
            _, engine, codec = engines[name]
            replay["sets"] = allowed_sets(engine, codec, actual)
            ids = torch.tensor([list(prompts[name]) + actual], device="cuda:0")
            with torch.set_grad_enabled(grad):
                lp, _, _ = trainer._get_per_token_logps_and_entropies(model, ids, torch.ones_like(ids), len(actual))
            return lp, replay["sets"], ids

        def agreement(out, label, adapter=True, floor=None):
            """The production agreement gate (grpo.agreement_failures: mean/p99/max, and within ``floor`` when given)
            on every policy position, plus the full error distribution; nothing is dropped (nonfinite positions fail).
            ``production_pass`` per rollout is the production per-rollout hard check (max <= gate max)."""
            import contextlib
            diffs, nonfinite, expected, rows, dec_d, forced_d, rel = [], 0, 0, [], [], [], []
            for name, tokens, sampler in zip(out["names"], out["tokens"], out["logps"]):
                actual, _ = policy_tokens(name, tokens)
                if not actual:
                    continue
                with (contextlib.nullcontext() if adapter else model.disable_adapter()):
                    lp, sets, _ = trainer_logps(name, actual)
                t = lp[0].float().cpu().numpy()
                s_ = np.asarray(sampler[:len(actual)], dtype=float)
                expected += len(actual)
                bad = ~(np.isfinite(t) & np.isfinite(s_))
                nonfinite += int(bad.sum())
                d = np.abs(t - s_)
                dec = np.array([len(x) > 1 for x in sets])
                diffs.append(d[~bad])
                dec_d.append(d[dec & ~bad])
                forced_d.append(d[~dec & ~bad])
                rel.append(np.stack([np.arange(len(d)) / max(len(d) - 1, 1), d], 1)[~bad])
                try:
                    grpo.assert_masked_agreement(t, s_)
                    prod = True
                except RuntimeError:
                    prod = False
                rows.append({"name": name, "len": len(actual), "decision": int(dec.sum()), "production_pass": prod,
                             "mean": float(d[~bad].mean()) if (~bad).any() else None,
                             "max": float(d[~bad].max()) if (~bad).any() else None,
                             "n_gt_0.1": int((d[~bad] > .1).sum()),
                             "argmax_pos": int(np.argmax(np.where(bad, -1, d)))})
            d = np.concatenate(diffs) if diffs else np.zeros(0)
            dd = np.concatenate(dec_d) if dec_d else np.zeros(0)
            fd = np.concatenate(forced_d) if forced_d else np.zeros(0)
            rp = np.concatenate(rel) if rel else np.zeros((0, 2))
            q = lambda x, v: float(np.quantile(x, v)) if x.size else None  # noqa: E731
            r = {"label": label, "adapter_on": adapter, "expected_positions": expected, "compared": int(d.size),
                 "nonfinite": nonfinite, "mean_abs_nats": float(d.mean()) if d.size else None,
                 "max_abs_nats": float(d.max()) if d.size else None,
                 "quantiles": {f"p{int(v * 1000) / 10:g}": q(d, v) for v in (.5, .9, .99, .999)},
                 "count_gt": {str(t_): int((d > t_).sum()) for t_ in (.01, .05, .1, .2, .5, 1.)},
                 "decision": {"n": int(dd.size), "mean": float(dd.mean()) if dd.size else None,
                              "max": float(dd.max()) if dd.size else None, "p99": q(dd, .99)},
                 "forced": {"n": int(fd.size), "max": float(fd.max()) if fd.size else None},
                 "by_relative_position": [{"bucket": f"{b / 4:.2f}-{(b + 1) / 4:.2f}",
                                           "max": float(rp[(rp[:, 0] >= b / 4) & (rp[:, 0] <= (b + 1) / 4), 1].max())
                                           if ((rp[:, 0] >= b / 4) & (rp[:, 0] <= (b + 1) / 4)).any() else None}
                                          for b in range(4)],
                 "gate": dict(grpo.AGREEMENT_GATE), "floor_slack": grpo.AGREEMENT_FLOOR_SLACK,
                 "rollouts_failing_production": sum(not x["production_pass"] for x in rows), "rollouts": rows}
            r["stats"] = ({"positions": int(d.size), "mean_abs_nats": float(d.mean()), "p99_abs_nats": q(d, .99),
                           "max_abs_nats": float(d.max())} if d.size else None)
            r["gate_failures"] = ((["no positions"] if not d.size else grpo.agreement_failures(r["stats"], floor))
                                  + (["nonfinite positions"] if nonfinite else [])
                                  + (["dropped positions"] if d.size != expected else []))
            r["pass"] = not r["gate_failures"]
            return r

        # ---- F2 witness: vLLM still has base weights, the trainer has the toy adapter on: must disagree ----
        if only_f2:
            witness_out = a_w
        else:
            witness_out = {k: [v[i] for i in range(0, 16, 4)] for k, v in a_tiny.items() if isinstance(v, list)}
        # bf16 floor first (same base rollouts, trainer adapter DISABLED: both sides base weights); the gate requires
        # it in the same run, and the synced comparisons below are judged against it.
        res["F2_base_floor"] = agreement(witness_out, "base floor (vLLM base vs trainer adapter-off)", adapter=False)
        floor_stats = res["F2_base_floor"]["stats"] if res["F2_base_floor"]["pass"] else None
        res["F2_witness_presync"] = agreement(witness_out, "pre-sync (vLLM base vs trainer adapter-on)", floor=floor_stats)
        # the frozen base must stay bit-identical across syncs (fix for the in-place bf16 merge, review w2-rev-grpo)
        lora_layers = [m for n_, m in model.named_modules() if hasattr(m, "base_layer") and hasattr(m, "lora_A")]
        base_probe = [m.base_layer.weight.detach().clone() for m in (lora_layers[0], lora_layers[-1])]
        mark("f2_witness")

        # ---- F2 sync 1 -> sample -> agree ----
        def timed_sync(label):
            t = time.perf_counter()
            grpo.sync_policy_weights(model, parent, time.monotonic() + 600, weight_file="/tmp/stowlm-sync.pt")
            res.setdefault("sync_s", {})[label] = round(time.perf_counter() - t, 1)
            mark(f"sync_{label}")
        timed_sync("1")
        f2_names = tiny[:4] + small[:4]
        out1, res["F2_rollout_after_sync1"] = rollout(f2_names, 2, 1536, "f2a")
        res["F2_after_sync1"] = agreement(out1, "after sync 1", floor=floor_stats)
        mark("f2_agree1")

        # ---- one GRPO update through the production loss (F3 instrumentation) ----
        optimizer = torch.optim.AdamW((p for p in model.parameters() if p.requires_grad), lr=lr)
        optimizer.zero_grad(set_to_none=True)
        record, witness, before = {}, {"checked": 0, "masked_gt_unmasked": 0, "forced_nonzero": 0}, {}
        n_decision = 0  # decision tokens of exactly the rollouts that enter the loss
        groups = {}
        for i, n in enumerate(out1["names"]):
            groups.setdefault(n, []).append(i)
        for n, idx in groups.items():
            rewards = [1.0 if policy_tokens(n, out1["tokens"][i])[1] == "complete" else 0.0 for i in idx]
            mu, sd = float(np.mean(rewards)), float(np.std(rewards))
            for i, r in zip(idx, rewards):
                actual, _ = policy_tokens(n, out1["tokens"][i])
                if not actual:
                    continue
                old, sets, ids = trainer_logps(n, actual)
                before[i] = old[0].float().cpu().numpy()
                decision = [len(s) > 1 for s in sets]
                if not any(decision):
                    continue
                n_decision += sum(decision)
                current, _, _ = trainer_logps(n, actual, grad=True)
                adv = (r - mu) / sd if sd > 0 else 1.0  # toy: a zero-variance group still gets a +1 push here
                loss = grpo.grpo_rollout_loss(current, old.detach(), adv, decision, group_size=len(idx), record=record)
                loss.backward()
                # mask witness on this rollout: masked log-prob vs full-vocab log-softmax of the same model
                if witness["checked"] < 4:
                    with torch.no_grad():
                        full = model(input_ids=ids, logits_to_keep=len(actual) + 1).logits[0, :-1].float()
                        full_lp = torch.log_softmax(full, -1).gather(1, torch.tensor(actual, device=full.device)[:, None])[:, 0]
                    m = current[0].detach().float()
                    dec_t = torch.tensor(decision, device=m.device)
                    witness["checked"] += 1
                    witness["masked_gt_unmasked"] += int((m[dec_t] > full_lp[dec_t]).all())
                    witness["forced_nonzero"] += int((m[~dec_t].abs() > 1e-6).sum())
                    witness.setdefault("mean_gap_nats", []).append(float((m[dec_t] - full_lp[dec_t]).mean()))
        params = [p for p in model.parameters() if p.requires_grad]
        grad_norm = float(torch.nn.utils.clip_grad_norm_(params, 1.0))
        finite = all(torch.isfinite(p.grad).all() for p in params if p.grad is not None)
        optimizer.step()
        mark("update")
        shift = []
        for i, old in before.items():
            n = out1["names"][i]
            actual, _ = policy_tokens(n, out1["tokens"][i])
            new, _, _ = trainer_logps(n, actual)
            shift.append(float(np.abs(new[0].float().cpu().numpy() - old).mean()))
        res["F3"] = {"override_calls": calls, "loss_record": record, "expected_decision_tokens": n_decision,
                     "normalised_over_decision_only": record.get("decision") == n_decision,
                     "mask_witness": witness, "grad_norm": grad_norm, "grad_finite": finite,
                     "update_logp_shift_mean_nats": float(np.mean(shift)) if shift else None, "lr": lr}
        res["F3"]["pass"] = bool(calls["grad"] >= record.get("calls", 0) > 0 and res["F3"]["normalised_over_decision_only"]
                                 and witness["checked"] and witness["masked_gt_unmasked"] == witness["checked"]
                                 and witness["forced_nonzero"] == 0 and finite and grad_norm > 0
                                 and res["F3"]["update_logp_shift_mean_nats"] and res["F3"]["update_logp_shift_mean_nats"] > 1e-4)

        # ---- F2 sync 2 (after the update) -> sample -> agree ----
        timed_sync("2")
        out2, res["F2_rollout_after_sync2"] = rollout(f2_names, 2, 1536, "f2b")
        res["F2_after_sync2"] = agreement(out2, "after sync 2 (post-update)", floor=floor_stats)
        mark("f2_agree2")
        res["base_bit_identical_after_syncs"] = all(
            torch.equal(m.base_layer.weight, b) for m, b in zip((lora_layers[0], lora_layers[-1]), base_probe))
        res["F2_pass"] = bool(floor_stats is not None and res["F2_after_sync1"]["pass"] and res["F2_after_sync2"]["pass"]
                              and not res["F2_witness_presync"]["pass"] and res["base_bit_identical_after_syncs"])
        res["peak_trainer_GB"] = round(torch.cuda.max_memory_allocated(0) / 1e9, 1)
        return res
    finally:
        if proc.is_alive():
            try:
                parent.send(None)
            except (BrokenPipeError, OSError):
                pass
            proc.join(timeout=10)
        if proc.is_alive():
            proc.terminate()  # only the child created above
            proc.join(timeout=5)


# ---------------------------------------------------------------------------------------------------------------
# Local driver: instances, cumulative ledger, absolute deadlines.
# ---------------------------------------------------------------------------------------------------------------
def _ledger(update: dict) -> dict:
    LEDGER.parent.mkdir(exist_ok=True)  # LEDGER may be re-pointed per job by main(--ledger)
    book = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"stages": []}
    book["stages"].append(update)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=2))
    tmp.replace(LEDGER)
    return book


@app.local_entrypoint()
def main(cap: float = 5.0, per_profile: int = 8, skip_gpu: bool = False, only_f2: bool = False,
         ledger: str = "", startup_s: int = STARTUP_S):
    global LEDGER, STARTUP_S
    if ledger:  # one ledger per job, so a job cap counts only that job's attempts
        LEDGER = HERE / "runs" / ledger
    STARTUP_S = startup_s
    base = ROOT / "experiments/base-mask/instances-v3-val"
    instances = {}
    for prof in ("tiny", "small"):  # simulated yard first; random yard fills (7 simulated small exist)
        files = [p for gen in ("simulated_yard", "random_yard")
                 for p in sorted((base / gen / prof).glob("seed-*.json")) if not p.name.endswith(".audit.json")]
        files = files[:per_profile]
        if len(files) < per_profile:
            raise SystemExit(f"only {len(files)} {prof} instances")
        for p in files:
            instances[f"{prof}-{p.stem}"] = json.loads(p.read_text())
    prior = 0.0  # every reservation counts in full until its completion entry replaces it with the charge
    for entry in (json.loads(LEDGER.read_text())["stages"] if LEDGER.exists() else []):
        if entry["status"] == "reserved":
            pending = entry["reserved_usd"]
            prior += pending
        else:
            prior += entry["charged_usd"] - pending
    results = {"cap_usd": cap, "prior_ledger_usd": round(prior, 3), "instances": sorted(instances),
               "rates_per_h": {"gpu": round(GPU_RATE * 3600, 2), "cpu": round(CPU_RATE * 3600, 2)}}

    def stage(name, fn, rate, typical_s, *args):
        nonlocal prior
        left = cap - prior
        if left < (STARTUP_S + typical_s) * rate:
            raise SystemExit(f"SPEND CAP: {name} needs ~${(STARTUP_S + typical_s) * rate:.2f}, ${left:.2f} left")
        deadline = time.time() + left / rate - STARTUP_S
        _ledger({"stage": name, "reserved_usd": round(left, 3), "deadline_epoch": deadline, "status": "reserved",
                 "at": time.time()})
        t = time.time()
        try:
            out, err = fn.remote(deadline, *args), None
        except Exception as e:  # noqa: BLE001
            out, err = None, f"{type(e).__name__}: {e}"[:1500]
        wall = time.time() - t
        charged = left if err else min(left, (wall + 60) * rate)  # errors keep the whole reservation
        prior += charged
        _ledger({"stage": name, "reserved_usd": round(left, 3), "charged_usd": round(charged, 3),
                 "wall_s": round(wall, 1), "status": "error" if err else "done", "at": time.time()})
        results.setdefault("stages", {})[name] = {"wall_s": round(wall, 1), "charged_usd": round(charged, 3),
                                                  "error": err}
        return out

    pre = stage("preflight", preflight, CPU_RATE, 120, instances)
    results["preflight"] = ({k: {kk: vv for kk, vv in v.items() if kk != "prompt_ids"}
                             for k, v in pre["instances"].items()} if pre else None)
    if pre:
        results["image_versions"] = pre["versions"]
    if pre and not skip_gpu:
        prompts = {k: v["prompt_ids"] for k, v in pre["instances"].items()}
        results["gpu"] = stage("falsify", falsify, GPU_RATE, 360 if only_f2 else 720, instances, prompts,
                               1e-4, only_f2)
    results["est_spent_usd"] = round(prior, 3)
    out = HERE / "runs" / f"falsify-64-{int(time.time())}.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    g = results.get("gpu") or {}
    print(json.dumps({k: g.get(k) for k in ("F1", "F2_pass", "F3", "sync_s", "timeline", "error")}, indent=1,
                     default=str)[:6000])
    print(f"wrote {out}; est spend ${prior:.2f}")
