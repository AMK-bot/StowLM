"""Markdown tables from run_all.py JSONL output; optional before/after comparison.

    uv run python experiments/inport-order/summarize.py results/part1.jsonl
    uv run python experiments/inport-order/summarize.py results/part1.jsonl results/part1-reseq.jsonl
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(path):
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = ROOT / path
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _f(x, nd=4):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}g}" if abs(x) < 1e-3 or abs(x) >= 1e4 else f"{x:.{nd}f}".rstrip("0").rstrip(".")
    return str(x)


def cells(row):
    """Per-plan summary cells (dict) or None-ish markers if not measured."""
    m = row.get("measure")
    planner = row.get("planner") or {}
    if row.get("generation_status") != "ok":
        return dict(status=f"no instance ({row.get('generation_status')})")
    if not m:
        return dict(status=f"no measure (planner {planner.get('status')}, has_plan={planner.get('has_plan')})",
                    planner=planner.get("status"))
    if m.get("error"):
        return dict(status="measure error: " + m["error"][:80], planner=planner.get("status"))
    ff = m.get("first_failure") or {}
    eff = (m.get("profile") or {}).get("effective", {})
    arr = (m.get("profile") or {}).get("arrival", {})
    n_states = m.get("n_states")
    k = m.get("k")
    verdict = {True: "PASS", False: "FAIL", None: "n/a (pass-1 only)"}[m.get("inport_pass")]
    if m.get("inport_pass") is None and m.get("pass1_all_ok") is not None:
        verdict = "pass-1 ok" if m["pass1_all_ok"] else "pass-1 fails"
    first = "—"
    crit = None
    if ff:
        crit = ff.get("criterion")
        if str(ff.get("rule", "")).startswith("checker."):
            crit = f"{crit} [{ff['rule']}: numerical/input error, not physics]"
        first = f"s{ff.get('state')} ({ff.get('phase')}) {crit} m={_f(ff.get('margin'))}"
    return dict(status="ok", planner=planner.get("status"), n=m.get("n_moves"),
                ballast=(m.get("ballast") or {}).get("mode"), k=k, n_states=n_states,
                k_str=("n+1" if k is not None and n_states is not None and k >= n_states else _f(k)),
                verdict=verdict, first=first, is_list=ff.get("is_list") if ff else None,
                list_heel=ff.get("list_with_heel_exhausted") if ff else None,
                min_list=eff.get("min_list_margin_rad"), min_list_state=eff.get("min_list_state"),
                neg_list=eff.get("negative_list_states"),
                arr_neg_list=arr.get("negative_list_states"), arr_min_list=arr.get("min_list_margin_rad"),
                heel_first=arr.get("first_heel_exhausted_state"),
                wall=m.get("wall_s"), moves_unchanged=m.get("moves_unchanged"), passed=m.get("inport_pass"),
                criterion=crit, complete=m.get("complete"))


def key(row):
    return (row["case_id"], row["method"])


def table(rows):
    out = ["| case | ship | rung | method | planner status | n moves | complete? | ballast | k / states | in-port | "
           "first failure (state, phase, criterion, margin) | list+HEEL exhausted? | min list margin rad (state) | "
           "# neg list states (eff / arrival-only) | first HEEL-exhausted arrival state | wall s |",
           "|" + "---|" * 16]
    for r in rows:
        c = cells(r)
        if c["status"] != "ok":
            out.append(f"| {r['case_id']} | {r['ship']} | {r['rung']} | {r['method']} | {c.get('planner', '—')} | "
                       f"— | — | — | — | {c['status']} | — | — | — | — | — | — |")
            continue
        out.append(f"| {r['case_id']} | {r['ship']} | {r['rung']} | {r['method']} | {c['planner']} | {c['n']} | "
                   f"{_f(c['complete'])} | {c['ballast']} | {c['k_str']} / {c['n_states']} | {c['verdict']} | {c['first']} | "
                   f"{_f(c['list_heel'])} | {_f(c['min_list'])} (s{_f(c['min_list_state'])}) | "
                   f"{_f(c['neg_list'])} / {_f(c['arr_neg_list'])} | {_f(c['heel_first'])} | {_f(c['wall'], 3)} |")
    return "\n".join(out)


def rates(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["ship"], r["rung"], r["method"])].append(cells(r))
    out = ["| ship | rung | method | plans attempted | measured (complete) | in-port PASS | FAIL | pass-1 only | "
           "first failure = list (P3) | list with HEEL exhausted | other first-failure criteria |",
           "|" + "---|" * 11]
    for (ship, rung, method), cs in sorted(groups.items()):
        meas = [c for c in cs if c["status"] == "ok"]
        p = sum(1 for c in meas if c["passed"] is True)
        f = sum(1 for c in meas if c["passed"] is False)
        n1 = sum(1 for c in meas if c["passed"] is None)
        lst = sum(1 for c in meas if c.get("is_list"))
        lh = sum(1 for c in meas if c.get("list_heel"))
        other = Counter(c["criterion"] for c in meas if c.get("criterion") and not c.get("is_list"))
        out.append(f"| {ship} | {rung} | {method} | {len(cs)} | {len(meas)} ({sum(1 for c in meas if c.get('complete'))}) | {p} | {f} | {n1} | {lst} | {lh} | "
                   f"{', '.join(f'{k}×{v}' for k, v in other.most_common()) or '—'} |")
    return "\n".join(out)


def compare(before, after):
    amap = {key(r): r for r in after}
    out = ["| case | method | n moves | moves unchanged? | in-port before → after | k before → after | "
           "first failure before → after | # neg list states before → after | min list margin before → after |",
           "|" + "---|" * 9]
    for r in before:
        a = amap.get(key(r))
        cb = cells(r)
        ca = cells(a) if a else {"status": "missing"}
        if cb["status"] != "ok" and ca["status"] != "ok":
            continue
        g = lambda c, k: _f(c.get(k)) if c.get("status") == "ok" else c.get("status", "—")[:30]
        out.append(f"| {r['case_id']} | {r['method']} | {g(cb, 'n')} | {_f(ca.get('moves_unchanged'))} | "
                   f"{g(cb, 'verdict')} → {g(ca, 'verdict')} | {g(cb, 'k_str')} → {g(ca, 'k_str')} | "
                   f"{g(cb, 'first')} → {g(ca, 'first')} | {g(cb, 'neg_list')} → {g(ca, 'neg_list')} | "
                   f"{g(cb, 'min_list')} → {g(ca, 'min_list')} |")
    return "\n".join(out)


def main(argv):
    if not argv or len(argv) > 2:
        raise SystemExit(__doc__)
    before = load(argv[0])
    print("## Per plan\n")
    print(table(before))
    print("\n## Rates per ship × rung × method\n")
    print(rates(before))
    if len(argv) == 2:
        after = load(argv[1])
        print("\n## After (second file) per ship × rung × method\n")
        print(rates(after))
        print("\n## Before → after\n")
        print(compare(before, after))


if __name__ == "__main__":
    main(sys.argv[1:])
