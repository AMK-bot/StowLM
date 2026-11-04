"""Runs the search on benchmark instances and scores every plan with the independent evaluator.

Resumable: an instance whose result file exists is skipped. All runs use `nice -n 10`.
Example: uv run --no-project --python 3.12 python run_benchmark.py --tag s60 --time 60 --threads 2 --parallel 1
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import evaluator as ev  # noqa: E402
import published as pub  # noqa: E402

ROOT = "/Users/anda/Projects/meta-hackathon-stowage/data/raw/larsen-pacino"


def start(k, a):
    plan = f"{HERE}/plans/{a.tag}/inst{k:02d}.plan"
    nice = [] if os.nice(0) >= 10 else ["nice", "-n", "10"]   # never stack niceness past 10
    cmd = nice + [f"{HERE}/bin/{a.bin}", "--vessel", pub.path_vessel(ROOT, k),
           "--instance", pub.path_instance(ROOT, k), "--plan", plan, "--time", str(a.time),
           "--threads", str(a.threads), "--seed", str(a.seed), "--clock", a.clock] + a.extra.split()
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True), plan


def finish(k, proc, plan, a):
    out, _ = proc.communicate()
    search = json.loads(out)
    v = ev.read_vessel(pub.path_vessel(ROOT, k))
    inst = ev.read_instance(pub.path_instance(ROOT, k))
    r = ev.evaluate(v, inst, ev.read_plan(plan), lcg_shift=a.lcg_shift)
    r.pop("profile")
    r["search"] = search
    r["instance"] = k
    r["name"] = pub.name(k)
    with open(f"{HERE}/results/{a.tag}/inst{k:02d}.json", "w") as fh:
        json.dump(r, fh, indent=1, default=str)
    p = pub.TABLE2[k - 1]
    print(f"inst {k:2d} {pub.name(k):8s} obj {r['obj']:11.2f} pub60 {p['obj']:11.2f} ratio {r['obj'] / p['obj']:.3f} "
          f"feas {r['feasible']} {r['violations']} UL {r['UL']} HO {r['HO']} OV {r['OV']} its {search['iters']} "
          f"wall {search['wall']:.0f} cpu {search['cpu']:.0f}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--time", type=float, default=60)
    p.add_argument("--threads", type=int, default=2)
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--clock", default="cpu")
    p.add_argument("--extra", default="")
    p.add_argument("--instances", default="1-27")
    p.add_argument("--bin", default="alns")
    p.add_argument("--lcg-shift", type=float, default=0.0, help="diagnostic; also pass it in --extra")
    a = p.parse_args()
    if a.parallel * a.threads > 2:
        sys.exit("at most 2 cores here")
    lo = []
    for part in a.instances.split(","):
        x = part.split("-")
        lo += list(range(int(x[0]), int(x[-1]) + 1))
    os.makedirs(f"{HERE}/plans/{a.tag}", exist_ok=True)
    os.makedirs(f"{HERE}/results/{a.tag}", exist_ok=True)
    todo = [k for k in lo if not os.path.exists(f"{HERE}/results/{a.tag}/inst{k:02d}.json")]
    running = []
    while todo or running:
        while todo and len(running) < a.parallel:
            k = todo.pop(0)
            proc, plan = start(k, a)
            running.append((k, proc, plan))
        time.sleep(0.5)
        for item in list(running):
            if item[1].poll() is not None or True:
                # stdout is small, so poll is safe; communicate collects it
                if item[1].poll() is not None:
                    running.remove(item)
                    finish(item[0], item[1], item[2], a)


if __name__ == "__main__":
    main()
