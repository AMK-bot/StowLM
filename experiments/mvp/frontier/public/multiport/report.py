"""Build the per-set tables of VALIDATION.md from run outputs. Every number here comes from a file.

Reported rehandles are the independent checker's, never the planner's. The published values are
typed in from Parreno-Torres et al. 2021 Tables 4 and 6, and Junqueira et al. 2020 Tables 7 and 8.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
RES = HERE / "results"

ORDER = [f"{i}{s}" for s in "AB" for i in range(1, 17)]
TABLE = {
    "1A": (1, 1), "2A": (4, 4), "3A": (14, 8), "4A": (20, 16), "5A": (0, 0), "6A": (5, 5),
    "7A": (66, 59), "8A": (63, 48), "9A": (0, 0), "10A": (12, 11), "11A": (33, 26), "12A": (82, 66),
    "1B": (0, 0), "2B": (0, 0), "3B": (7, 7), "4B": (8, 8), "5B": (0, 0), "6B": (0, 0),
    "7B": (44, 40), "8B": (32, 32), "9B": (0, 0), "10B": (0, 0), "11B": (0, 0), "12B": (34, 34),
    "13A": (164, 164), "14A": (872, 815), "15A": (2110, 2033), "16A": (4512, 4512),
    "13B": (45, 45), "14B": (234, 221), "15B": (749, 749), "16B": (1525, 1499),
}
BEST = {  # proven optima from the paper (gap 0): Tables 3, 4, 10, 11 as listed in academic.md §5
    "1A": 0, "2A": 1, "3A": 2, "5A": 0, "6A": 0, "9A": 0, "1B": 0, "2B": 0, "3B": 0, "4B": 0,
    "5B": 0, "6B": 0, "7B": 11, "9B": 0, "10B": 0,
}

TR1_PUB = {  # Table 4, Base: (optimal, feasible, mean rehandles over feasible, mean CPU s)
    ("Short", 6): (27, 27, 0.0, 3), ("Short", 8): (27, 27, 0.0, 20), ("Short", 10): (27, 27, 0.0, 107),
    ("Mixed", 6): (27, 27, 0.0, 3), ("Mixed", 8): (27, 27, 0.0, 18), ("Mixed", 10): (27, 27, 0.0, 123),
    ("Authentic", 6): (27, 27, 0.0, 6), ("Authentic", 8): (27, 27, 0.0, 53), ("Authentic", 10): (27, 27, 0.0, 337),
    ("Long", 6): (27, 27, 0.0, 5), ("Long", 8): (27, 27, 0.0, 43), ("Long", 10): (26, 27, 0.0, 362),
    ("Required", 6): (17, 27, 2.1, 1429), ("Required", 8): (3, 27, 4.4, 3273), ("Required", 10): (6, 27, 285.2, 2950),
}
TR4_PUB = {  # Table 6: Heur, IF 1 h, IF 2 h, IF 4 h
    ("Short", 6): (35.4, 0.4, 0.0, 0.4), ("Short", 8): (110.8, 19.1, 7.0, 5.3), ("Short", 10): (235.7, 87.9, 59.0, 23.7),
    ("Mixed", 6): (14.5, 0.0, 0.0, 0.0), ("Mixed", 8): (93.5, 11.9, 11.6, 10.9), ("Mixed", 10): (207.6, 40.7, 30.1, 26.8),
    ("Authentic", 6): (22.7, 2.4, 2.0, 1.8), ("Authentic", 8): (52.9, 19.2, 17.1, 12.3), ("Authentic", 10): (114.7, 55.8, 40.0, 25.1),
    ("Long", 6): (34.4, 4.9, 4.4, 4.4), ("Long", 8): (129.5, 38.0, 34.1, 34.7), ("Long", 10): (197.0, 110.6, 97.4, 88.3),
    ("Required", 6): (113.0, 54.9, 51.0, 49.8), ("Required", 8): (211.7, 135.0, 126.3, 122.0), ("Required", 10): (356.0, 256.9, 247.4, 222.0),
}
TR4_GROUP = {
    "Short": (127.3, 35.8, 22.0, 9.8), "Mixed": (106.2, 17.5, 13.9, 12.5), "Authentic": (63.4, 25.8, 19.7, 13.0),
    "Long": (120.3, 51.1, 45.3, 42.4), "Required": (226.9, 148.9, 141.6, 131.3),
}
TR4_ALL = (128.8, 55.8, 48.5, 41.8)
CLASSES = ["Short", "Mixed", "Authentic", "Long", "Required"]


def fmt_signed(v: int) -> str:
    return f"{v:+d}" if v else "0"


def junqueira() -> str:
    data = json.loads((RES / "junqueira_hr2_best.json").read_text())
    alt_path = RES / "junqueira_hr1_alt.json"
    alt = json.loads(alt_path.read_text()) if alt_path.exists() else {}
    lines = [
        "| Inst. | Boxes | Proven best | HR1 pub. | HR1 ours | diff | HR1 alt. reading | HR2 pub. | HR2 ours | diff | HR2 rounds | HR2 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    exact1 = exact2 = 0
    abs1 = abs2 = 0
    for n in ORDER:
        d = data[n]
        p1, p2 = TABLE[n]
        exact1 += d["hr1"] == p1
        exact2 += d["hr2"] == p2
        abs1 += abs(d["hr1"] - p1)
        abs2 += abs(d["hr2"] - p2)
        lines.append(
            f"| {n} | {d['boxes']:,} | {BEST.get(n, '–')} | {p1:,} | {d['hr1']:,} | {fmt_signed(d['hr1'] - p1)} | "
            f"{alt.get(n, '–')} | {p2:,} | {d['hr2']:,} | {fmt_signed(d['hr2'] - p2)} | {d['rounds']:,} | {d['hr2_s']} |"
        )
    extra = RES / "junqueira_16B_c266.json"
    lines.append("")
    if extra.exists():
        e = json.loads(extra.read_text())
        lines.append(f"16B re-run with the printed C = 266 (the file has C = {e['file_C']}): HR1 {e['hr1']:,} (published 1,525), HR2 {e['hr2']:,} (published 1,499), {e['rounds']} rounds, {e['hr2_s']} s.")
    small = [n for n in ORDER if int(n[:-1]) <= 12]
    e1s = sum(data[n]["hr1"] == TABLE[n][0] for n in small)
    e2s = sum(data[n]["hr2"] == TABLE[n][1] for n in small)
    lines.append("")
    lines.append(
        f"HR1 equals the published value on {e1s}/24 small and {exact1}/32 overall; total absolute difference {abs1:,}. "
        f"HR2 equals it on {e2s}/24 small and {exact2}/32 overall; total absolute difference {abs2:,}."
    )
    return "\n".join(lines)


def load_jsonl(prefix: str) -> dict:
    out = {}
    for path in sorted(RES.glob(f"{prefix}_*.jsonl")):
        for line in path.read_text().splitlines():
            rec = json.loads(line)
            out[rec["file"]] = rec
    return out


def tr1() -> str:
    recs = load_jsonl("tr1")
    chk = json.loads((RES / "tr1_check.json").read_text())
    rows = defaultdict(list)
    bad = 0
    for name, rec in recs.items():
        c = chk.get(name)
        if not rec.get("ok") or c is None or not c["ok"]:
            bad += 1
            continue
        rows[(rec["cls"], rec["n"])].append((c["rehandles"], rec))
    lines = [
        "| Class | N | Inst. | Pub. proven / feas. | Pub. mean | Ours mean | Gap | Ours at 0 | Ours proven | Ours max | Pub. CPU s | Ours mean s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    tot = defaultdict(int)
    for cls in CLASSES:
        for n in (6, 8, 10):
            items = rows[(cls, n)]
            vals = [v for v, _ in items]
            if not vals:
                continue
            o, f, r, cpu = TR1_PUB[(cls, n)]
            mean = sum(vals) / len(vals)
            zeros = sum(v == 0 for v in vals)
            proven = sum(v == 0 or (rec.get("status") == "optimal" and v == rec.get("rehandles_model")) for v, rec in items)
            wall = sum(rec["wall_s"] for _, rec in items) / len(items)
            tot[cls + "z"] += zeros
            tot[cls + "n"] += len(vals)
            tot[cls + "s"] += sum(vals)
            lines.append(
                f"| {cls} | {n} | {len(vals)} | {o} / {f} | {r:.1f} | {mean:.2f} | {mean - r:+.2f} | {zeros} | {proven} | {max(vals)} | {cpu:,} | {wall:.1f} |"
            )
    lines.append("")
    three = sum(tot[c + "z"] for c in ("Short", "Mixed", "Authentic"))
    nthree = sum(tot[c + "n"] for c in ("Short", "Mixed", "Authentic"))
    lines.append(f"Short + Mixed + Authentic at 0 rehandles: **{three} of {nthree}** (target 243). Checker failures or missing plans: {bad}.")
    disagree = [n for n, r in recs.items() if r.get("ok") and n in chk and chk[n].get("rehandles") != r.get("rehandles_model")]
    allv = [(v, r) for items in rows.values() for v, r in items]
    proven = sum(v == 0 or r.get("status") == "optimal" for v, r in allv)
    lines.append(f"Plans where the checker disagrees with the model's own count: {len(disagree)}. Proven optimal in all: {proven} of {len(allv)}.")
    unproven = [(n, chk[n]["rehandles"], r.get("bound")) for n, r in recs.items() if r.get("ok") and r.get("status") == "limit"]
    if unproven:
        lines.append("Open at the time limit (file, ours, HiGHS bound): " + "; ".join(f"{n[4:24]} {v} ≥ {b:.1f}" for n, v, b in sorted(unproven)[:40]))
    for c in ("Long", "Required"):
        if tot[c + "n"]:
            lines.append(f"{c}: {tot[c + 'z']} of {tot[c + 'n']} at 0, mean {tot[c + 's'] / tot[c + 'n']:.2f} (published Base over 81: {'0.0' if c == 'Long' else '97.2'}).")
    return "\n".join(lines)


def tr4() -> str:
    recs = {k: v for k, v in load_jsonl("tr4").items()}
    chk = json.loads((RES / "tr4_check.json").read_text())
    folds = {}
    for k in range(3):
        names = (RES / f"tr4_fold{k}.txt").read_text().split()
        folds[k] = (names, sum(n in recs for n in names))
    complete = [k for k, (names, cnt) in folds.items() if cnt == len(names)]
    status_line = "Folds complete: " + (", ".join(map(str, complete)) or "none") + ". " + "; ".join(f"fold {k}: {c}/{len(n)}" for k, (n, c) in folds.items()) + "."
    keep = {n for k in complete for n in folds[k][0]}
    recs = {n: r for n, r in recs.items() if n in keep}
    good, bad = [], []
    for name, rec in recs.items():
        c = chk.get(name)
        if rec.get("ok") and c and c["ok"] and c["lcg_ok"] and c["vcg_ok"]:
            good.append((c, rec))
        else:
            bad.append((name, c, rec))
    rows = defaultdict(list)
    for c, rec in good:
        rows[(rec["cls"], rec["n"])].append((c["rehandles"], rec))
    lines = [
        status_line,
        "",
        "| Class | N | Inst. | Ours mean | Heur | IF 1 h | IF 2 h | IF 4 h | Ours − IF 4 h | Ours mean s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cls in CLASSES:
        for n in (6, 8, 10):
            items = rows[(cls, n)]
            if not items:
                continue
            v = [x for x, _ in items]
            mean = sum(v) / len(v)
            h, a, b, d = TR4_PUB[(cls, n)]
            wall = sum(r["wall_s"] for _, r in items) / len(items)
            lines.append(f"| {cls} | {n} | {len(v)} | {mean:.1f} | {h} | {a} | {b} | {d} | {mean - d:+.1f} | {wall:.0f} |")
    lines.append("")
    lines.append(
        "| Class | Inst. | Ours at 0 | Ours mean | Ours max | Heur | IF 1 h | IF 2 h | IF 4 h |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    allv = []
    pub_mix = [0.0, 0.0, 0.0, 0.0]
    for cls in CLASSES:
        v = [x for n in (6, 8, 10) for x, _ in rows[(cls, n)]]
        allv += v
        if v:
            h, a, b, d = TR4_GROUP[cls]
            lines.append(f"| {cls} | {len(v)} | {sum(x == 0 for x in v)} | {sum(v) / len(v):.1f} | {max(v)} | {h} | {a} | {b} | {d} |")
    for (cls, n), items in rows.items():
        for k in range(4):
            pub_mix[k] += TR4_PUB[(cls, n)][k] * len(items)
    if allv:
        mean = sum(allv) / len(allv)
        lines.append(f"| **All** | **{len(allv)}** | **{sum(x == 0 for x in allv)}** | **{mean:.1f}** | **{max(allv)}** | {TR4_ALL[0]} | {TR4_ALL[1]} | {TR4_ALL[2]} | {TR4_ALL[3]} |")
        pm = [x / len(allv) for x in pub_mix]
        lines.append("")
        lines.append(
            f"Published means reweighted to the Class × N mix of these plans: Heur {pm[0]:.1f}, IF 1 h {pm[1]:.1f}, IF 2 h {pm[2]:.1f}, IF 4 h {pm[3]:.1f}."
        )
        lines.append(f"Zero-rehandle plans: {sum(v == 0 for v in allv)} of {len(allv)}. Max |LCG| over all plans {max(c['max_abs_lcg_ft'] for c, _ in good):.2f} ft (limit 20). VCG range {min(c['min_vcg_tiers'] for c, _ in good):.3f} to {max(c['max_vcg_tiers'] for c, _ in good):.3f} tiers from the centreline (limit: not above 0).")
        by = defaultdict(list)
        for c, rec in good:
            by[rec.get("status")].append(c["rehandles"])
        lines.append("By MIP outcome: " + "; ".join(f"{k}: {len(v)} plans, mean {sum(v) / len(v):.1f}" for k, v in sorted(by.items())) + ".")
        by_split = defaultdict(list)
        for c, rec in good:
            by_split[rec.get("t20")].append(c["rehandles"])
        lines.append("By 20' TEU share: " + "; ".join(f"{k} %: {len(v)} plans, mean {sum(v) / len(v):.1f}" for k, v in sorted(by_split.items())) + ".")
        lines.append(f"Total wall time {sum(r['wall_s'] for _, r in good) / 3600:.2f} h over {len(good)} plans; mean {sum(r['wall_s'] for _, r in good) / len(good):.0f} s, max {max(r['wall_s'] for _, r in good):.0f} s.")
    lines.append(f"Plans improved by the retry: {sum(1 for _, r in good if r.get('retry'))}. Plans failing a check or missing: {len(bad)}.")
    for name, c, rec in bad[:10]:
        lines.append(f"- {name.split('.txt')[0]}: {'no plan within the limit' if c is None else c}")
    return "\n".join(lines)


if __name__ == "__main__":
    which = sys.argv[1]
    print({"junqueira": junqueira, "tr1": tr1, "tr4": tr4}[which]())
