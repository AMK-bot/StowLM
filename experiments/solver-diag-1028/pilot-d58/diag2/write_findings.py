"""Keep the early handoff concise while long-budget results arrive."""
import datetime
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    summary = json.loads((HERE / "long-summary.json").read_text())
    rows = [json.loads(line) for line in (HERE / "long-outcomes.jsonl").read_text().splitlines()]
    table = ["| case / ship | solver | status | actual solve s | complete / checker / ship / mask | first complete event s |",
             "|---|---|---|---:|---|---:|"]
    specs = {r["index"]: r for r in json.loads((HERE / "notes/selection.json").read_text())["selected"]}
    for record in rows:
        row = record["result"]
        event = [e["planner_elapsed_s"] for e in record["events"] if e["kind"] in ("complete_construction_returned", "cargo_only_prestow_returned")]
        timing = "—" if row.get("solve_s") is None else f"{row['solve_s']:.2f}"
        flags = [row.get("completion") == "complete", row.get("checker_passed"), row.get("shippable"), row.get("mask_replay_passed")]
        gate = " / ".join("yes" if v is True else "no" if v is False else "—" for v in flags) if record["done"] else "pending"
        first = "—" if not event else f"{min(event):.2f}"
        table.append(f"| {record['index']:03d} / {specs[record['index']]['spec']['vessel'].replace('feeder_', '')} | {record['solver']} | {record['state']} | {timing} | {gate} | {first} |")
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    aggregate = (HERE / "aggregate-table.md").read_text() if (HERE / "aggregate-table.md").exists() else ""
    text = f"""# First-incumbent diagnosis — updated {now}

**No first complete incumbent after four-times budgets.** The old searches repeatedly exhaust support/visibility-compatible room near completion. Two-step's first stage is DFS, not CP-SAT. Separately, two of the six selected inputs cannot pass the pinned checker's unconditional container-mass rating; the remaining four have no infeasibility proof.

## 1. Four-times budgets

Pinned original-pilot source; six unchanged failing small instances, two per ship. Cases 024/028 and032/036 balance random/simulated generators;044/045 are simulated because no random1700small instance was accepted. The subset was fixed before new outcomes, without selecting by how close a prefix came to completion.

Both planners receive1200s. Two-step retains its original half-budget pre-stow allocation (600s); a return there is recorded honestly. Completed trials: **{summary['completed']}/12**, complete cargo **{summary['complete_cargo']}**, all gates **{summary['full_chain_pass']}**. First-complete timestamps are observation-only hooks; full-checker timing is retained separately in first-events.jsonl. No complete result means no finite completion time was observed.

""" + (aggregate if aggregate else "\n".join(table)) + """

Per-case counts, timing and first-complete observations: [long-table.md](long-table.md). No finite completion time was observed when cargo construction did not finish.

The observed failure stage is first-candidate construction. Scalable's retained incomplete prefixes are checked and replayed, but cannot ship: the end marker is rejected while cargo is missing. Two-step returns no candidate. Ballast is not reached; this is not evidence that it missed a feasible ballast. Rule IDs and D57 states are retained in `runs/*/*/done.json` and [failure-detail.json](failure-detail.json). Partial-prefix lashing/loading failures do not establish a complete candidate's failure. Repair is disabled to measure first-candidate yield, as in the original raw pilot.

**Instance attribution added after the independent construct report:** case024 requires QZXU0000210 at30,930.279kg; case036 requires QZXU0000040 and QZXU0000415 at32,500kg each. D11 requires them aboard, while the pinned checker's unconditional hard securing mass guard permits at most30,500kg. Placement/ballast cannot alter this invariant. These two cases are checker-model instance contradictions, not physical impossibility claims. The other four remain search failures with unknown global feasibility. The constructor report's whole-file checker hash differs; [mass_contract.py](mass_contract.py) independently verifies the exact guard AST and input bytes in our own pin, with evidence in [mass-contract.json](mass-contract.json). No hard rule was relaxed.

## 2. Why two-step returns nothing

The old `_preplan` explicitly pushes public-engine states on a depth-first search stack. It tries a sequence, then backtracks after a dead end; it builds **no CP-SAT model**. The later terminal stage uses CP-SAT only after a complete cargo pre-stow exists. Sources: frozen `solver/twostep.py:_preplan/TwoStepPlanner.plan`; [Russell and Norvig, search chapter](https://aima.cs.berkeley.edu/), [OR-Tools solver/status documentation](https://developers.google.com/optimization/cp/cp_solver).

Case025 profile:34boxes,68cells,102slots,3468box-slot pairs. The necessary fractional C2 LP has683rows,68cargo-cell and18tank columns. A necessary LP permits fractional mass allocation; passing it does not assign whole containers or supply a loading sequence. It timed out after140.013s,20,265child pushes, maximum depth32/34. **90.1% of pushes occurred at depths30–32**;972depth32legal-set builds returned zero actions.

| Observed method | Calls | Inclusive time | Mean/call |
|---|---:|---:|---:|
| Full public legal-set build (`legal_index`) |5718|115.636s (82.6% of pre-stow)|20.22ms|
| Cached public membership (`is_legal`) |40530|0.235s|5.80µs|
| C2 LP, included within the legal-set work |5861|19.491s|3.33ms|
| Engine copy |20265|0.181s|8.93µs|

C2 was on, with zero indeterminate LP outcomes. These are nested monotonic timers; rows overlap. Instrumentation perturbs absolute throughput. Raw cProfile timing is internally inconsistent around native HiGHS and is **not used** for attribution. Detailed timers and source hashes: [profile/FINDINGS.md](profile/FINDINGS.md).

## 3. What blocks the retained scalable prefixes

| case | loaded | Missing cargo / actual obstruction | Engine first global dead end |
|---|---:|---|---:|
|025 /500|33/34|Last dry20ft:10open slots violate `expert.physical.no_20_on_40`; sole remaining slot exceeds D55roofcap by0.169483m.|35|
|033 /800|45/46|Last dry40ftHC:all7open long slots exceed D55roofcap; static placement checks otherwise clean.|54|
|045 /1700|52/58|All6missing dry40ft boxes have0immediate choices. Three individually checked against all8open long slots; D55rejects all, and even the shortest remaining box exceeds the cap. One slot also fails `expert.physical.stack_weight`.|53|

D55 is the necessary mask bound for `solas.v22.visibility`, not a newly emitted checker violation. Across145saved prefix states, every C1a–h/C2 completion check still passed. All saved actions replay legally. No earlier `engine.dead_end()` was flagged. In045 the watched boxes lose their immediate choices at depth47 while other cargo can still move; zero immediate choices alone is not global infeasibility. The original solver's aggregate counters do not recover a chronological notice delay. Details,42concrete probes and last-box IDs: [stalls/report.md](stalls/report.md).

## Action for w2-construct and other owners

Encode the shared **D55effective roof limits,20/40support compatibility, and remaining height/length scarcity** in the first assignment. Nominal stack height and coarse C1/C2 capacity alone leave these branches alive. Preserve the production public replay and full checker as the admission gates.

Generator/checker owners must also reconcile required booking gross mass with the synthetic securing rating. Cases024/036 cannot be rescued by solver or ballast improvements under this exact checker model. The later generator mass change does not retroactively change these pinned D58 input bytes.

Feasibility-owner proposal only: consistently include necessary D55effective height in C1capacity and incremental updates, plus remaining height/length-group bounds. Preserve optimism for future supports and test inclusion against exhaustive small completions. A clamp alone may remain too optimistic because the current relaxation uses the whole-instance minimum box height; its exact rejection effect is unverified. This is a stronger necessary-screen opportunity, not a demonstrated false acceptance.

No project `src/` edits. The source/parameter copy is byte-identical to pilot epoch8951f42c… (199files), recovered from an existing snapshot after live construct integration started. Every original qualification guard runs. A checked storage/identity adapter preserves absolute evidence identities after primitive/bank/helper byte equality checks; all actual measurements report C2on. Two pre-search admission failures are archived and excluded. No hard rule or tolerance was relaxed. Compute reservation4CPU/12GB/noGPU; long-run and probe timings are recorded separately. The baseline results describe the old implementation, not the new construct integration.
"""
    # Space prose while retaining technical IDs in linked raw records.
    replacements = {"and032": "and 032", ";044": "; 044", "1200s": "1,200s", "600s": "600s",
                    "34boxes": "34 boxes", "68cells": "68 cells", "102slots": "102 slots", "3468box": "3,468 box",
                    "683rows": "683 rows", "68cargo": "68 cargo", "18tank": "18 tank", "20,265child": "20,265 child",
                    "depth32": "depth 32", "972depth": "972 depth", "depths30": "depths 30", "145saved": "145 saved",
                    "42concrete": "42 concrete", "199files": "199 files", "reservation4CPU": "reservation 4 CPU",
                    "random1700small": "random feeder_1700 small", "Case025": "Case 025",
                    "C2on": "C2 on", "epoch8951": "epoch 8951", "C1capacity": "C1 capacity",
                    "D55effective": "D55 effective", "20/40support": "20/40 support",
                    "D55roofcap": "D55 roof cap", "by0.169483m": "by 0.169483m",
                    "dry20ft": "dry 20ft", "dry40ft": "dry 40ft", "10open": "10 open",
                    "all7open": "all 7 open", "All6missing": "All 6 missing", "have0immediate": "have 0 immediate",
                    "all8open": "all 8 open", "D55rejects": "D55 rejects", "In045": "In 045", "depth47": "depth 47"}
    replacements.update({"case024": "case 024", "case036": "case 036", "at30,930.279kg": "at 30,930.279kg",
                         "at32,500kg": "at 32,500kg", "most30,500kg": "most 30,500kg", "Cases024/036": "Cases 024/036"})
    for before, after in replacements.items():
        text = text.replace(before, after)
    if (HERE / "compute-summary.json").exists():
        compute = json.loads((HERE / "compute-summary.json").read_text())
        text += (f"\nFinal verification: `audit.py --final` checks all 12 outcomes, input/source identities, C2 admission, gates, prefix evidence and diagnostic script syntax. The 383 pinned evidence files were independently rehashed with no mismatches. Long dispatcher span {compute['long_dispatch_span_s']/60:.2f} minutes, summed worker wall {compute['long_worker_wall_sum_s']/3600:.3f} hours; largest recorded worker RSS {compute['largest_worker_peak_rss_gib']:.3f} GiB. At most four numerical jobs ran concurrently; aggregate RAM was not sampled. Estimate: four cores / 12 GB / about 63 minutes, no GPU. Actual profile attempts 177.436s plus stalls 62.513s; no cloud spend.\n")
    (HERE / "FINDINGS.md").write_text(text)
    print(json.dumps(dict(updated_utc=now, completed=summary["completed"])))


if __name__ == "__main__":
    main()
