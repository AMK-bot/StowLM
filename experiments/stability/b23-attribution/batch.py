"""Background-only B 2.3 over freshly generated tiny instances (3 ships x 2 generators).
Unballasted metrics + symmetric double-bottom sweep {0,1}^3 + a high-wing check. Saves instances.
Usage: batch.py SHARD NSHARDS. One thread."""
import json, sys, time
from pathlib import Path
from stowage.generate.instances import generate_simulated_instance
from stowage.generate.random_yard import generate_random_instance
from stowage.model import MovePlan
sys.path.insert(0, str(Path(__file__).parent))
from sweep import sweep, b23, evaluate, pairs

HERE = Path(__file__).parent
shard, nshards = int(sys.argv[1]), int(sys.argv[2])
jobs = [(g, v, s) for v in ("feeder_500", "feeder_800", "feeder_1700") for g in ("random", "simulated") for s in (2, 3, 4, 5)]
(HERE / "instances").mkdir(exist_ok=True)
for i, (g, v, s) in enumerate(jobs):
    if i % nshards != shard:
        continue
    t = time.time()
    try:
        fn = generate_random_instance if g == "random" else generate_simulated_instance
        inst = fn("tiny", seed=s, vessel_name=v)
        (HERE / "instances" / f"{g}-{v}-tiny-{s}.instance.json").write_text(inst.model_dump_json())
        plan = MovePlan(id="empty", instance_id=inst.id, call_index=inst.call_index, moves=[])
        base = b23(evaluate(inst, plan, {}))
        groups = [x for x in ("DB-A", "DB-M", "DB-F") if x in pairs(inst)]
        rows = sweep(inst, plan, groups, [0, 1])
        ok = [r for r in rows if not r.get("rules")]
        b23ok = [r for r in rows if r.get("worst_ratio", 0) >= 1]
        rec = dict(generator=g, vessel=v, seed=s, id=inst.id, lpp=inst.vessel.hull.lpp_m,
                   n_boxes=len(inst.containers), unballasted=base, groups=groups,
                   sweep_b23_pass=len(b23ok), sweep_stab_pass=[r["combo"] for r in ok],
                   sweep_best=max(rows, key=lambda r: r.get("worst_ratio", 0)), wall_s=time.time() - t)
    except Exception as e:
        rec = dict(generator=g, vessel=v, seed=s, error=f"{type(e).__name__}: {e}"[:500], wall_s=time.time() - t)
    with (HERE / f"batch-{shard}.jsonl").open("a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
    print(g, v, s, rec.get("error") or (round(rec["unballasted"]["worst_ratio"], 3), rec["unballasted"]["worst"],
          "sweep b23", rec["sweep_b23_pass"], "stab", len(rec["sweep_stab_pass"])), f"{rec['wall_s']:.0f}s", flush=True)
