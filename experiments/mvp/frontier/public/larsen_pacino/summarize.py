"""Builds the tables in VALIDATION.md from evaluator output only (results/<tag>/instNN.json)."""
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import published as pub  # noqa: E402


def load(tag):
    out = {}
    for f in sorted(glob.glob(f"{HERE}/results/{tag}/inst*.json")):
        r = json.load(open(f))
        out[r["instance"]] = r
    return out


def geo(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def ratio_block(tag, key, label):
    res = load(tag)
    rs = []
    for k, r in res.items():
        p = pub.TABLE2[k - 1]
        ref = p[key]
        if key == "obj300" and k == 22:
            continue
        rs.append(r["obj"] / ref)
    n_feas = sum(r["feasible"] for r in res.values())
    return (f"| {label} | {len(res)} | {n_feas} | {sum(rs) / len(rs):.3f} | {geo(rs):.3f} | "
            f"{sorted(rs)[len(rs) // 2]:.3f} | {min(rs):.3f} | {max(rs):.3f} | {sum(x <= 1 for x in rs)} |")


def table(tag, key="obj"):
    res = load(tag)
    print("| # | Inst | Ours | Published | Ratio | Feasible | Iters | it/s (CPU) | Wall s | CPU s |")
    print("|---:|---|---:|---:|---:|---|---:|---:|---:|---:|")
    for k, r in res.items():
        p = pub.TABLE2[k - 1]
        s = r["search"]
        rate = s["iters"] / max(s["cpu"], 1e-9)
        print(f"| {k} | {r['name']} | {r['obj']:,.2f} | {p[key]:,.2f} | {r['obj'] / p[key]:.3f} | "
              f"{'yes' if r['feasible'] else 'NO'} | {s['iters']:,} | {rate:,.0f} | {s['wall']:.0f} | {s['cpu']:.0f} |")


def kpis(tag):
    res = load(tag)
    print("| # | UL | OV | HO | ES | MK | BP | NR | FB | VM | LCG in bounds | Abs TCG |")
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    tot = {}
    keys = ["UL", "OV", "HO", "ES", "MK", "BP", "NR", "FB"]
    for k, r in res.items():
        p = pub.TABLE2[k - 1]
        cells = [f"{r[x]:,} / {p[x]:,}" for x in keys]
        for x in keys:
            tot.setdefault(x, [0, 0])
            tot[x][0] += r[x]
            tot[x][1] += p[x]
        lo, hi = r["lm_bounds"]
        inb = lo <= r["lm"] <= hi
        print(f"| {k} | " + " | ".join(cells) + f" | {r['VM']:.3g} / {p['VM']:.3g} | {'yes' if inb else 'NO'} | {abs(r['tcg']):.3f} |")
    print("| Sum | " + " | ".join(f"{tot[x][0]:,} / {tot[x][1]:,}" for x in keys) + " | | | |")


def contrib(tag):
    """Objective share by KPI, summed over instances, ours against published."""
    res = load(tag)
    w = dict(UL=1000, HO=100, OV=100, MK=1, BP=20, ES=-10, NR=5, FB=-0.5, VM=0.0001)
    print("| KPI | Ours (sum of cost) | Published (sum of cost) | Difference |")
    print("|---|---:|---:|---:|")
    a_all = b_all = 0
    for x, c in w.items():
        a = sum(r[x] * c for r in res.values())
        b = sum(pub.TABLE2[k - 1][x] * c for k in res)
        a_all += a
        b_all += b
        print(f"| {x} | {a:,.0f} | {b:,.0f} | {a - b:+,.0f} |")
    print(f"| Total | {a_all:,.0f} | {b_all:,.0f} | {a_all - b_all:+,.0f} |")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "table":
        table(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "obj")
    elif cmd == "kpis":
        kpis(sys.argv[2])
    elif cmd == "contrib":
        contrib(sys.argv[2])
    elif cmd == "ratios":
        print("| Run | Inst | Feasible | Mean ratio | Geo mean | Median | Min | Max | Inst <= 1 |")
        print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for spec in sys.argv[2:]:
            tag, key, label = spec.split(":", 2)
            print(ratio_block(tag, key, label))
