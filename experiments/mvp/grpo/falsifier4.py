# Falsifier 4 for 10.1 GRPO (D26 amendment item 4): does the SFT policy give GRPO a learning signal?
#
# GRPO learns only from groups whose rewards differ: each sample's advantage is (reward - group mean) / group std,
# so a group where every sample gets the same reward (all fail, or all pass with the same move count) contributes
# zero gradient (Shao et al. 2024, DeepSeekMath §4, https://arxiv.org/abs/2402.03300). The bar (D26): >= 30 % of
# groups MIXED, i.e. reward variance > 0.
#
# This script (Modal part): 1x H100. The production rollout worker (`llm.rl.modal_grpo._rollout_process`, vLLM 0.31
# Model Runner V2 + `StowMaskLP`, the real state-dependent mask) samples G=8 plans at T=1 for each of 8 tiny TRAIN
# instances (never test), with the MVP v4 mask (D79 C2 off + D95 securing layer, `modal_grpo.MVP_SPEC`). The policy is
# the tiny SFT PILOT adapter (experiments/mvp/sft/pilot-tiny/adapter, MLX QLoRA r8/scale 20, converted to PEFT with
# experiments/grpo-venue/convert_adapter.py, bit-identical round trip), merged into the bf16 base on CPU
# (`merged_weights_from_checkpoint`, fp32 merge rounded once) and loaded into vLLM through the production sync path
# (`apply_exported_weights`: load_weights + prefix-cache reset).
# Rewards are scored afterwards, off Modal, by `score_f4.py` with the full production reward (finisher V4 + full
# checker, `modal_grpo.mvp_reward_config()`), so the reward and its CPU time are those 10.1 would see.
#
# APPROXIMATION: the pilot adapter was trained against the MLX 4-bit base and is served on bf16 here (README §2:
# ~0.03-0.06 nats/token KL with a strong adapter). GRPO trains and samples in bf16, so this is the right setting for
# 10.1; it is not the MLX evaluation setting.
#
# SPEND: cumulative ledger runs/f4-ledger.json, reservation written before each remote call, absolute deadline passed
# into the container (watchdog exits there, also after a preemption restart), retries=0, rates list x 1.25.
# RUN: uv run --with modal modal run experiments/mvp/grpo/falsifier4.py --cap 4
import json
import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2] if len(HERE.parents) > 2 else None  # None inside the container
LOCK = (ROOT or Path("/")) / "experiments/grpo-venue/modal_smoke.lock.txt"  # read lazily at image build only
MODEL_PATH = "/weights/Muse-Glimmer-30B@cec94fcf"
ADAPTER = "/adapters/pilot-tiny-peft"
LEDGER = HERE / "runs" / "f4-ledger.json"
BILLING_OVERHEAD = 1.25
H100_S, CORE_S, GIB_S = 0.001097, 0.0000131, 0.00000222
GPU_RATE = BILLING_OVERHEAD * (H100_S + 8 * CORE_S + 96 * GIB_S)
CPU_RATE = BILLING_OVERHEAD * (4 * CORE_S + 16 * GIB_S)
STARTUP_S = 120

app = modal.App("stowlm-falsifier4")
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
    import threading

    def run():
        while True:
            if time.time() > deadline_epoch:
                print(f"[{name}] SPEND CAP: absolute deadline passed; exiting", flush=True)
                os._exit(42)
            time.sleep(2)
    threading.Thread(target=run, daemon=True).start()
    print(f"[{name}] watchdog: {deadline_epoch - time.time():.0f}s left", flush=True)


@app.function(image=image, cpu=4, memory=16 * 1024, timeout=600, retries=0, volumes={"/weights": weights_vol})
def preflight(deadline_epoch: float, instances: dict) -> dict:
    """CPU: the image imports the MVP mask (securing layer), builds every decoder, encodes every prompt."""
    _watchdog(deadline_epoch, "preflight")
    import stowage.interfaces  # noqa: F401
    from llm.rl import modal_grpo as grpo
    from llm.stowlm.format import load_tokenizer
    from llm.stowlm.format_prompt import encode_prompt
    from stowage.model import Instance
    tok, out = load_tokenizer(MODEL_PATH), {}
    for name, inst in instances.items():
        t = time.perf_counter()
        dec = grpo.decoder_from_spec({"instance": inst, "eos_token_id": 0, **grpo.MVP_SPEC})
        assert dec.allowed_token_ids(), name
        out[name] = {"prompt_ids": list(encode_prompt(Instance.model_validate(inst), tok)),
                     "decoder_s": round(time.perf_counter() - t, 3)}
    return {"instances": out, "versions": grpo.current_versions(), "scope": grpo.TRAINING_IMAGE_SCOPE}


@app.function(image=image, gpu="H100!", cpu=8, memory=96 * 1024, timeout=1200, retries=0,
              volumes={"/weights": weights_vol, "/adapters": adapter_vol})
def rollouts(deadline_epoch: float, instances: dict, prompts: dict, group: int = 8, max_tokens: int = 512) -> dict:
    _watchdog(deadline_epoch, "rollouts")
    res = {"timeline": {}}
    try:
        return json.loads(json.dumps(_rollouts(res, instances, prompts, group, max_tokens), default=str))
    except Exception as e:  # noqa: BLE001
        import traceback
        res.update(error=f"{type(e).__name__}: {e}"[:2000], traceback=traceback.format_exc()[-4000:])
        return json.loads(json.dumps(res, default=str))


def _rollouts(res, instances, prompts, group, max_tokens):
    import multiprocessing
    import torch
    from transformers import AutoTokenizer
    from llm.rl import modal_grpo as grpo
    t0 = time.time()

    def mark(k):
        res["timeline"][k] = round(time.time() - t0, 1)
        print(f"[f4] {k} at {res['timeline'][k]}s", flush=True)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    proc = ctx.Process(target=grpo._rollout_process, args=(child, "0"))  # rollout-only container: one GPU
    proc.start()
    child.close()
    try:
        t = time.perf_counter()  # CPU merge overlaps the worker's vLLM load
        merged = grpo.merged_weights_from_checkpoint(MODEL_PATH, ADAPTER)
        torch.save(merged, "/tmp/f4-sync.pt")
        res["merge"] = {"tensors": len(merged), "GB": round(sum(v.numel() * v.element_size() for v in merged.values())
                                                             / 1e9, 2), "s": round(time.perf_counter() - t, 1)}
        del merged
        mark("merged")
        grpo._receive(parent, 900)
        mark("worker_ready")
        parent.send({"weights": "/tmp/f4-sync.pt"})
        ack = grpo._receive(parent, 600)
        res["sync"] = ack
        mark("synced")
        eos = AutoTokenizer.from_pretrained(MODEL_PATH).eos_token_id
        names = sorted(instances)
        steps = [names[:4], names[4:]]  # two GRPO-step-shaped batches: 4 prompts x G
        res["steps"] = []
        for k, step_names in enumerate(steps):
            batch = [{"prompt_ids": prompts[n], "seed": 100 * k + 10 * i + g, "max_tokens": max_tokens,
                      "spec": {"instance": json.dumps(instances[n]), "eos_token_id": eos, "rid": f"s{k}-{n}-{g}",
                               **grpo.MVP_SPEC},
                      "name": n} for i, n in enumerate(step_names) for g in range(group)]
            parent.send({"batch": batch})
            out = grpo._receive(parent, 900)
            n_tok = sum(map(len, out["tokens"]))
            res["steps"].append({"names": [b["name"] for b in batch], "tokens": out["tokens"], "logps": out["logps"],
                                 "status": out["status"], "wall_s": round(out["wall_s"], 2), "seqs": len(batch),
                                 "tok": n_tok, "tok_s": round(n_tok / out["wall_s"], 1),
                                 "mask_apply_s": round(out["mask"]["apply_s"], 2),
                                 "mask_host_share": round(out["mask"]["apply_s"] / out["wall_s"], 3)})
            mark(f"step{k}")
        res["peak_GB"] = round(torch.cuda.max_memory_allocated() / 1e9, 1) if torch.cuda.is_available() else None
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


def pick_instances(per_cell: int = 1, extra_vessel: str = "feeder_800", total: int = 8) -> dict:
    """Train tiny inputs (never test), one per vessel x generator cell (most boxes first), topped up from
    ``extra_vessel``. Corpus inputs are the files the gold cases were generated from."""
    cells = {}
    for res in sorted((ROOT / "experiments/mvp/gold/train/tiny").glob("*/*/seed-*/result.json")):
        j = json.loads(res.read_text())
        _, _, vessel, gen, seed = j["case"].split("/")
        cells.setdefault((vessel, gen), []).append((-j.get("boxes", 0), j["case"]))
    chosen = [sorted(v)[i][1] for v in cells.values() for i in range(min(per_cell, len(v)))]
    extra = [c for k, v in sorted(cells.items()) if k[0] == extra_vessel for _, c in sorted(v)[per_cell:per_cell + 1]]
    chosen = (chosen + extra)[:total]
    out = {}
    for case in chosen:
        assert case.startswith("train/"), case
        out[case.replace("/", "-")] = json.loads((ROOT / f"experiments/mvp/corpus/{case}.json").read_text())
    return out


def _ledger(update: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    book = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"stages": []}
    book["stages"].append(update)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(book, indent=2))
    tmp.replace(LEDGER)


@app.local_entrypoint()
def main(cap: float = 4.0, group: int = 8):
    instances = pick_instances()
    prior, pending = 0.0, 0.0
    for e in (json.loads(LEDGER.read_text())["stages"] if LEDGER.exists() else []):
        if e["status"] == "reserved":
            pending = e["reserved_usd"]
            prior += pending
        else:
            prior += e["charged_usd"] - pending
    results = {"cap_usd": cap, "prior_ledger_usd": round(prior, 3), "instances": sorted(instances), "group": group}
    peft = ROOT / "experiments/mvp/grpo/pilot-tiny-peft"
    with adapter_vol.batch_upload(force=True) as b:
        b.put_directory(str(peft), "/pilot-tiny-peft")

    def stage(name, fn, rate, typical_s, *args):
        nonlocal prior
        left = cap - prior
        if left < (STARTUP_S + typical_s) * rate:
            raise SystemExit(f"SPEND CAP: {name} needs ~${(STARTUP_S + typical_s) * rate:.2f}, ${left:.2f} left")
        deadline = time.time() + left / rate - STARTUP_S
        _ledger({"stage": name, "reserved_usd": round(left, 3), "deadline_epoch": deadline, "status": "reserved"})
        t = time.time()
        try:
            out, err = fn.remote(deadline, *args), None
        except Exception as e:  # noqa: BLE001
            out, err = None, f"{type(e).__name__}: {e}"[:1500]
        wall = time.time() - t
        charged = left if err else min(left, (wall + 60) * rate)
        prior += charged
        _ledger({"stage": name, "reserved_usd": round(left, 3), "charged_usd": round(charged, 3),
                 "wall_s": round(wall, 1), "status": "error" if err else "done"})
        results.setdefault("stages", {})[name] = {"wall_s": round(wall, 1), "charged_usd": round(charged, 3),
                                                  "error": err}
        return out

    pre = stage("preflight", preflight, CPU_RATE, 120, instances)
    if pre:
        results["versions"], results["scope"] = pre["versions"], pre["scope"]
        prompts = {k: v["prompt_ids"] for k, v in pre["instances"].items()}
        results["prompt_lens"] = {k: len(v) for k, v in prompts.items()}
        results["gpu"] = stage("rollouts", rollouts, GPU_RATE, 420, instances, prompts, group)
    results["est_spent_usd"] = round(prior, 3)
    out = HERE / "runs" / f"f4-rollouts-{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, default=str))
    g = results.get("gpu") or {}
    print(json.dumps({k: g.get(k) for k in ("merge", "sync", "timeline", "error", "peak_GB")}, default=str))
    for s in g.get("steps", []):
        print({k: s[k] for k in ("seqs", "tok", "wall_s", "tok_s", "mask_host_share")},
              {x: s["status"].count(x) for x in set(map(str, s["status"]))})
    print(f"wrote {out}; est spend ${prior:.2f}")
