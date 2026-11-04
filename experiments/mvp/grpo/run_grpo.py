# 10.1 GRPO launcher for the MVP (plan v4): our own loop on Modal, 2x H100, built only from production pieces in
# src/llm/rl/modal_grpo.py (D26 venue; falsifiers 1-3 passed 29 Oct, falsifier 4 measured by falsifier4.py).
#
# What one GRPO step does (Shao et al. 2024, DeepSeekMath §4, https://arxiv.org/abs/2402.03300):
#   1. sync: the trainer's LoRA is merged OUT OF PLACE (fp32, rounded once; the bf16 base is never touched) and
#      loaded into the vLLM sampler (`sync_policy_weights`; worker load_weights + prefix-cache reset);
#   2. rollouts: P prompts x G samples at T=1 through the real feasibility mask, MVP setting (`MVP_SPEC`: D79 C2 off
#      + D95 securing layer), one batched vLLM call (`_generate_batch`);
#   3. reward: every rollout scored in parallel ON THE MAC by the production reward (`score_rollout_job`: V4
#      finisher + full checker; pass = -weighted moves, any hard failure = -10,000; finisher/checker time-outs
#      dropped, never counted as passes). Modal CPUs run the finisher ~15x slower than the M5 (590 s vs 38 s for the
#      same plan, 29 Oct), so the GPU container is a warm Modal class (`GRPO.rollouts` / `GRPO.update`) and the local
#      entrypoint scores between the two calls;
#   4. advantages: per prompt group, (r - mean) / std (`normalize_group_rewards`); equal rewards -> zero gradient;
#   5. update: masked log-probs from the trainer (`make_masked_grpo_trainer` override, the D9 masked normaliser),
#      clipped surrogate normalised over decision tokens only (`grpo_rollout_loss`), beta = 0 (no KL term);
#      the agreement gate (`agreement_failures`: mean <= .01, p99 <= .1, max <= .5 nat, within 2x the base-only floor
#      measured in the same run) is checked BEFORE the optimizer step;
#   6. checkpoint: adapter + optimizer + metrics to the `stowlm-grpo-checkpoints` Volume (resumable).
#
# SPEND: a durable local ledger (runs/<run_id>-ledger.json) reserves the whole remaining cap BEFORE the remote call;
# the container hard-stops at that reservation's absolute deadline (`hard_stop`), including after a preemption
# restart, a resume, or long reward scoring. retries=0. Rates are list x 1.25.
#
# RUN (10.1, once 8.1's adapter is converted):
#   uv run python experiments/grpo-venue/convert_adapter.py mlx2peft <8.1 MLX adapter dir> experiments/mvp/grpo/sft-8.1-peft
#   OPENBLAS_NUM_THREADS=1 uv run --with modal modal run experiments/mvp/grpo/run_grpo.py --run-id grpo-101 \
#       --adapter experiments/mvp/grpo/sft-8.1-peft --steps 20 --prompts 4 --group 8 --cap 20 --score-workers 8
#   (add a line to experiments/fleet/ledger.md for the Mac scoring cores first)
# Resume after a stop: the same command (completed steps are read from the checkpoint Volume; the ledger keeps the
# spend already reserved, so --cap stays the run's total cap).
import json
import os
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2] if len(HERE.parents) > 2 else None
LOCK = (ROOT or Path("/")) / "experiments/grpo-venue/modal_smoke.lock.txt"  # read lazily at image build only
MODEL_PATH = "/weights/Muse-Glimmer-30B@cec94fcf"
BILLING_OVERHEAD = 1.25
H100_S, CORE_S, GIB_S = 0.001097, 0.0000131, 0.00000222
CPUS, MEM_GIB = 8, 160
GPU_RATE = BILLING_OVERHEAD * (2 * H100_S + CPUS * CORE_S + MEM_GIB * GIB_S)
STARTUP_S = 120
SCORE_PYTHON = "/opt/score/bin/python"
TIMEOUT_S = 6 * 3600  # outer backstop; the binding stop is the reservation's absolute deadline

def _needed_geometry(path) -> bool:
    """Vessel geometry caches of the tiny/small MVP feeders only (195 MB of the 2.1 GB vessels cache)."""
    name = Path(path).name
    return ".full.geometry." in name and name.split(".")[0] in ("feeder_500", "feeder_800", "feeder_1700")


app = modal.App("stowlm-grpo-mvp")
weights_vol = modal.Volume.from_name("stowlm-glimmer-weights", create_if_missing=False)
adapter_vol = modal.Volume.from_name("stowlm-adapters", create_if_missing=False)
ckpt_vol = modal.Volume.from_name("stowlm-grpo-checkpoints", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .uv_pip_install(requirements=[str(LOCK)])
         .uv_pip_install("scipy==1.18.1", "ortools==9.15.6755", "numpy==2.3.5")
         # The reward's own environment: the V4 finisher needs the project's numpy 2.5.4, which the vLLM stack
         # (numba) can't take, so rewards are scored by `llm.rl.score_batch` under this separate interpreter.
         .run_commands("python -m venv /opt/score",
                       "/opt/score/bin/pip install --no-cache-dir numpy==2.5.4 scipy==1.18.1 ortools==9.15.6755 "
                       "pydantic==2.14.1 pydantic-core==2.50.1 tokenizers==0.23.3")
         .env({"VLLM_ENABLE_V1_MULTIPROCESSING": "0", "TOKENIZERS_PARALLELISM": "false",
               "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True", "PYTHONPATH": "/root/stowlm/src",
               "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}))
if ROOT is not None:
    # Rewards are scored on the Mac, so the container needs only what the mask reads.
    image = (image.add_local_dir(str(ROOT / "src"), "/root/stowlm/src", ignore=["**/__pycache__/**"])
             .add_local_dir(str(ROOT / "params"), "/root/stowlm/params")
             # stowage.params.repo_root() finds the tree by pyproject.toml; without it vessel parameters can't be read
             # and the V4 finisher reports every plan as unknown:required_calculation (found 29 Oct).
             .add_local_file(str(ROOT / "pyproject.toml"), "/root/stowlm/pyproject.toml")
             # Public KCS hull data the hydrostatics/finisher read (data/raw/kcs, MANIFEST inside); without it the
             # V4 finisher fails every plan as unknown:required_calculation (found 29 Oct).
             .add_local_dir(str(ROOT / "data/raw/kcs"), "/root/stowlm/data/raw/kcs"))


@app.cls(image=image, gpu="H100:2", cpu=CPUS, memory=MEM_GIB * 1024, timeout=TIMEOUT_S, retries=0,
         scaledown_window=600, volumes={"/weights": weights_vol, "/adapters": adapter_vol, "/checkpoints": ckpt_vol})
class GRPO:
    run_id: str = modal.parameter()
    cfg_json: str = modal.parameter()
    deadline_epoch: int = modal.parameter()

    @modal.enter()
    def load(self):
        """Hard stop first, then the sampler (GPU1) and the trainer (GPU0); the base-only agreement floor is
        measured while the sampler still holds the base weights (D26 agreement gate)."""
        import multiprocessing
        import torch
        import stowage.interfaces  # noqa: F401
        from transformers import AutoTokenizer, MuseGlimmerForConditionalGeneration
        from llm.rl import modal_grpo as grpo
        from llm.stowlm.format import ActionCodec, load_tokenizer
        from llm.stowlm.format_prompt import encode_prompt
        from stowage.model import Instance
        grpo.hard_stop(float(self.deadline_epoch))
        t0 = time.time()
        self.timeline = {}
        self.cfg = cfg = json.loads(self.cfg_json)
        self.root = Path(f"/checkpoints/{self.run_id}")
        self.root.mkdir(parents=True, exist_ok=True)
        ckpt_vol.reload()
        adapter_vol.reload()
        instances = json.loads(Path(f"/adapters/{self.run_id}/instances.json").read_text())
        self.names = sorted(instances)
        self.texts = {n: json.dumps(instances[n]) for n in self.names}
        insts = {n: Instance.model_validate_json(self.texts[n]) for n in self.names}
        tok = load_tokenizer(MODEL_PATH)
        self.prompts = {n: list(encode_prompt(insts[n], tok)) for n in self.names}
        self.eos = AutoTokenizer.from_pretrained(MODEL_PATH).eos_token_id
        ctx = multiprocessing.get_context("spawn")
        self.parent, child = ctx.Pipe()
        self.proc = ctx.Process(target=grpo._rollout_process, args=(child, "1"))
        self.proc.start()
        child.close()
        torch.cuda.set_device(0)
        base = MuseGlimmerForConditionalGeneration.from_pretrained(MODEL_PATH, dtype=torch.bfloat16,
                                                                   device_map={"": 0}, attn_implementation="sdpa")
        for part in ("vision_tower", "vision_adapter", "vision_projection"):
            if hasattr(base.model, part):
                getattr(base.model, part).to("cpu")
        torch.cuda.empty_cache()
        self.model = model = grpo.initialize_policy_adapter(base, f"/adapters/{self.run_id}/adapter")
        model.train()
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
        self.params = [p for p in model.parameters() if p.requires_grad]
        self.optimizer = torch.optim.AdamW(self.params, lr=cfg["lr"])
        done = sorted(int(p.name[5:]) for p in self.root.glob("step-*") if (p / "complete.json").exists())
        self.begin = done[-1] if done else 0
        if self.begin:  # resume: adapter + optimizer from the last complete step
            from peft import set_peft_model_state_dict
            snap = torch.load(self.root / f"step-{self.begin:06d}" / "training.pt", map_location="cpu",
                              weights_only=False)
            set_peft_model_state_dict(model, snap["adapter"])
            self.optimizer.load_state_dict(snap["optimizer"])
        self.timeline["trainer_loaded"] = round(time.time() - t0, 1)
        self.state = {"sets": None}
        trainer_holder = {}

        def replay(ids, attention, length):
            t = trainer_holder["t"]
            override = getattr(t, "allowed_override", None)
            if override is not None:
                t.allowed_override = None
                return override
            return self.state["sets"]
        self.trainer = object.__new__(grpo.make_masked_grpo_trainer(replay))
        self.trainer.temperature = grpo.ROLLOUT_TEMPERATURE
        trainer_holder["t"] = self.trainer
        grpo._receive(self.parent, 1200)
        self.timeline["worker_ready"] = round(time.time() - t0, 1)
        n = self.names[0]
        self.floor = grpo.measure_agreement_floor(
            model, self.trainer, self.parent, prompt=self.prompts[n],
            spec={"instance": self.texts[n], "eos_token_id": self.eos, **grpo.MVP_SPEC}, engine=grpo.mvp_engine(insts[n]),
            codec=ActionCodec(tok, [c.id for c in insts[n].containers]), group_size=cfg["group"],
            max_tokens=cfg["max_tokens"], deadline=time.monotonic() + 900)
        self.timeline["floor"] = round(time.time() - t0, 1)
        import random
        order = [self.names[i % len(self.names)] for i in range(cfg["steps"] * cfg["prompts"])]
        random.Random(cfg["seed"]).shuffle(order)
        self.order = order

    @modal.method()
    def info(self) -> dict:
        return {"begin": self.begin, "timeline": self.timeline, "floor": self.floor}

    @modal.method()
    def rollouts(self, step: int) -> dict:
        from llm.rl import modal_grpo as grpo
        t = time.perf_counter()
        grpo.sync_policy_weights(self.model, self.parent, time.monotonic() + 600, weight_file="/tmp/grpo-sync.pt")
        sync_s = round(time.perf_counter() - t, 1)
        cfg = self.cfg
        chosen = self.order[step * cfg["prompts"]:(step + 1) * cfg["prompts"]]
        self.batch = [{"prompt_ids": self.prompts[n], "seed": 10_000 * step + 100 * i + g, "max_tokens": cfg["max_tokens"],
                       "spec": {"instance": self.texts[n], "eos_token_id": self.eos, "rid": f"{step}-{i}-{g}",
                                **grpo.MVP_SPEC}, "name": n, "group": i}
                      for i, n in enumerate(chosen) for g in range(cfg["group"])]
        self.parent.send({"batch": self.batch})
        self.out = grpo._receive(self.parent, 1200)
        return {"step": step, "names": [b["name"] for b in self.batch], "groups": [b["group"] for b in self.batch],
                "tokens": self.out["tokens"], "sync_s": sync_s, "rollout_s": round(self.out["wall_s"], 1),
                "mask_host_share": round(self.out["mask"]["apply_s"] / self.out["wall_s"], 3)}

    @modal.method()
    def update(self, step: int, scored: list) -> dict:
        import math
        import numpy as np
        import torch
        from llm.rl import modal_grpo as grpo
        from llm.rl.reward import normalize_group_rewards
        cfg, batch, out = self.cfg, self.batch, self.out
        if len(scored) != len(batch):
            raise ValueError("one score per rollout required")
        t = time.perf_counter()
        groups, adv = {}, [0.0] * len(batch)
        for k, b in enumerate(batch):
            groups.setdefault(b["group"], []).append(k)
        mixed = 0
        for idx in groups.values():
            a = normalize_group_rewards([scored[k]["value"] for k in idx], reasons=[scored[k]["reason"] for k in idx])
            mixed += any(x != 0 for x in a)
            for k, x in zip(idx, a):
                adv[k] = x
        self.optimizer.zero_grad(set_to_none=True)
        pairs, losses, used = [], [], 0
        for k, b in enumerate(batch):
            r = scored[k]
            decision = [len(s) > 1 for s in r["sets"]]
            if r["value"] is None or not r["actual"] or not any(decision):
                continue
            self.state["sets"] = [tuple(s) for s in r["sets"]]
            ids = torch.tensor([self.prompts[b["name"]] + r["actual"]], device="cuda:0")
            att = torch.ones_like(ids)
            with torch.no_grad():
                old, _, _ = self.trainer._get_per_token_logps_and_entropies(self.model, ids, att, len(r["actual"]))
            trainer_lp = old[0].float().cpu().numpy()
            sampler_lp = out["logps"][k][:len(r["actual"])]
            grpo.assert_masked_agreement(trainer_lp, sampler_lp)
            pairs.append((trainer_lp, sampler_lp))
            if adv[k] == 0:
                continue  # zero advantage: no gradient
            cur, _, _ = self.trainer._get_per_token_logps_and_entropies(self.model, ids, att, len(r["actual"]))
            loss = grpo.grpo_rollout_loss(cur, old, adv[k], decision, group_size=cfg["group"]) / len(groups)
            loss.backward()
            losses.append(float(loss.detach()))
            used += 1
        agreement = grpo.agreement_stats(pairs) if pairs else None
        if agreement is not None:
            fails = grpo.agreement_failures(agreement, self.floor)
            if fails:  # before the optimizer update
                raise RuntimeError(f"agreement gate failed at step {step}: {fails}")
        grad_norm = None
        if losses:
            grad_norm = float(torch.nn.utils.clip_grad_norm_(self.params, 1.0))
            if not math.isfinite(grad_norm):
                raise RuntimeError("nonfinite GRPO gradient")
            self.optimizer.step()
        update_s = round(time.perf_counter() - t, 1)
        passing = [r["moves"] for r in scored if r["passed"]]
        metrics = {"step": step + 1, "update_s": update_s, "rollouts": len(batch),
                   "passed": sum(r["passed"] for r in scored), "dropped": sum(r["value"] is None for r in scored),
                   "reasons": {x: sum(r["reason"] == x for r in scored) for x in sorted({r["reason"] for r in scored})},
                   "mean_moves_passing": float(np.mean(passing)) if passing else None, "mixed_groups": mixed,
                   "groups": len(groups), "rollouts_in_loss": used, "loss": sum(losses) if losses else None,
                   "grad_norm": grad_norm, "optimizer_updated": bool(losses), "agreement": agreement}
        target = self.root / f"step-{step + 1:06d}"
        target.mkdir(exist_ok=True)
        from peft import get_peft_model_state_dict
        self.model.save_pretrained(target / "adapter")
        torch.save({"adapter": get_peft_model_state_dict(self.model), "optimizer": self.optimizer.state_dict()},
                   target / "training.pt")
        (target / "complete.json").write_text(json.dumps({"metrics": metrics, "cfg": cfg}, default=str))
        ckpt_vol.commit()
        return metrics

    @modal.exit()
    def stop(self):
        proc = getattr(self, "proc", None)
        if proc is not None and proc.is_alive():
            try:
                self.parent.send(None)
            except (BrokenPipeError, OSError):
                pass
            proc.join(timeout=10)
            if proc.is_alive():
                proc.terminate()  # only the child created in load()
                proc.join(timeout=5)


def train_instances(profile: str, limit: int) -> dict:
    """TRAIN split inputs only (never test/val), stratified round-robin over vessel x generator."""
    cells = {}
    for res in sorted((ROOT / f"experiments/mvp/gold/train/{profile}").glob("*/*/seed-*/result.json")):
        case = json.loads(res.read_text())["case"]
        assert case.startswith("train/"), case
        cells.setdefault(tuple(case.split("/")[2:4]), []).append(case)
    out, i = {}, 0
    while len(out) < limit and any(i < len(v) for v in cells.values()):
        for v in cells.values():
            if i < len(v) and len(out) < limit:
                out[v[i].replace("/", "-")] = json.loads((ROOT / f"experiments/mvp/corpus/{v[i]}.json").read_text())
        i += 1
    return out


def _score_on_mac(names, tokens, step, instances, workers):
    """Production reward on the Mac CPU (``score_rollout_job``), in parallel; same MVP engine/reward config."""
    from concurrent.futures import ProcessPoolExecutor
    from llm.rl.modal_grpo import score_rollout_job
    from llm.stowlm.format import DEFAULT_TOKENIZER_PATH
    jobs = [(json.dumps(instances[n]), t, step, str(DEFAULT_TOKENIZER_PATH)) for n, t in zip(names, tokens)]
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(score_rollout_job, jobs))


@app.local_entrypoint()
def main(run_id: str, adapter: str, steps: int = 20, prompts: int = 4, group: int = 8, cap: float = 20.0,
         lr: float = 1e-5, max_tokens: int = 512, profile: str = "tiny", n_instances: int = 64, seed: int = 0,
         score_workers: int = 4, min_invocation_s: int = 600):
    if not run_id or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for c in run_id):
        raise SystemExit("run_id must be a lowercase slug")
    ledger = HERE / "runs" / f"{run_id}-ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    book = json.loads(ledger.read_text()) if ledger.exists() else {"stages": []}
    prior, pending = 0.0, 0.0
    for e in book["stages"]:
        if e["status"] == "reserved":
            pending = e["reserved_usd"]
            prior += pending
        else:
            prior += e["charged_usd"] - pending
    left = cap - prior
    if left < (STARTUP_S + min_invocation_s) * GPU_RATE:
        raise SystemExit(f"SPEND CAP: ${left:.2f} left of ${cap:.2f}; an invocation needs "
                         f"~${(STARTUP_S + min_invocation_s) * GPU_RATE:.2f}")
    instances = train_instances(profile, n_instances)
    cfg = {"steps": steps, "prompts": prompts, "group": group, "lr": lr, "max_tokens": max_tokens, "seed": seed,
           "profile": profile, "instances": sorted(instances), "mvp_spec": "D79 C2 off + D95 securing",
           "reward": "V4 finisher + full checker on the Mac (mvp_reward_config)", "beta": 0.0, "clip": 0.2}
    tmp = HERE / "runs" / f"{run_id}-instances.json"
    tmp.write_text(json.dumps(instances))
    with adapter_vol.batch_upload(force=True) as b:
        b.put_directory(str(Path(adapter).resolve()), f"/{run_id}/adapter")
        b.put_file(str(tmp), f"/{run_id}/instances.json")
    deadline = int(time.time() + left / GPU_RATE - STARTUP_S)

    def write(entry):
        book["stages"].append(entry)
        t_ = ledger.with_suffix(".tmp")
        t_.write_text(json.dumps(book, indent=2))
        t_.replace(ledger)
    write({"stage": "grpo", "reserved_usd": round(left, 3), "deadline_epoch": deadline, "status": "reserved",
           "at": time.time()})
    t0, history, err = time.time(), [], None
    try:
        g = GRPO(run_id=run_id, cfg_json=json.dumps(cfg), deadline_epoch=deadline)
        info = g.info.remote()
        print("[grpo] loaded", json.dumps(info, default=str), flush=True)
        for step in range(info["begin"], steps):
            r = g.rollouts.remote(step)
            t = time.perf_counter()
            scored = _score_on_mac(r["names"], r["tokens"], step, instances, score_workers)
            score_s = round(time.perf_counter() - t, 1)
            m = g.update.remote(step, scored)
            m.update(sync_s=r["sync_s"], rollout_s=r["rollout_s"], mask_host_share=r["mask_host_share"],
                     score_s=score_s, score_cpu_s=round(sum(x["score_s"] for x in scored), 1),
                     score_workers=score_workers, wall_s=round(time.time() - t0, 1))
            history.append(m)
            print("[grpo] step", json.dumps({k: v for k, v in m.items() if k != "agreement"}, default=str), flush=True)
            (HERE / "runs" / f"{run_id}-history.json").write_text(json.dumps({"info": info, "steps": history},
                                                                              indent=1, default=str))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:1500]
        print("[grpo] ERROR", err, flush=True)
    wall = time.time() - t0
    charged = left if err else min(left, (wall + 60) * GPU_RATE)
    write({"stage": "grpo", "reserved_usd": round(left, 3), "charged_usd": round(charged, 3), "wall_s": round(wall, 1),
           "status": "error" if err else "done", "at": time.time()})
    print(f"[grpo] done: {len(history)} steps, est ${prior + charged:.2f} of ${cap:.2f}; error={err}")


@app.local_entrypoint()
def score_check(rollouts: str, n: int = 2, workers: int = 2):
    """Local only (no remote call): the Mac scoring pool works under `modal run` and matches score_f4."""
    run = json.loads(Path(rollouts).read_text())
    step = run["gpu"]["steps"][0]
    names, tokens = step["names"][::8][:n], step["tokens"][::8][:n]
    insts = {nm: json.loads((ROOT / f"experiments/mvp/corpus/{nm.replace('-', '/', 4)}.json").read_text()) for nm in names}
    t = time.perf_counter()
    out = _score_on_mac(names, tokens, 0, insts, workers)
    print(round(time.perf_counter() - t, 1), [(r["reason"], r["value"], r["score_s"]) for r in out])
