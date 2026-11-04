# Public yard benchmarks — task 4.10

Measured 2026-10-27 on the local Apple M5 Max, one CPU core, no GPU or paid calls.
Bound validation completed on **12,408 published restricted-BRP optima**; all 12,500 local Zhu instances parsed and mapped. **Published move-sequence reproduction is now evidenced:** Bacci’s Figure 1 replays all nine published events and reproduces **3 relocations + 6 productive retrievals**, with only the three relocations entering the yard objective. All 12,500 local Zhu instances have been compared with the independently downloaded Bacci Group 6 source copy; ordered stack contents agree in every case.

The reusable loader and adapter live in `src/stowage/bench/`, so tasks 4.6/4.7 can use the same mapping. No move-engine, bound, model or frozen signature was changed.

| Instance set | Loaded | Proven targets matched | Engine yard bound ≤ optimum | Ordered LB1 ≤ optimum | Mean engine tightness | Mean ordered LB1 tightness | Time, s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Zhu H=3, restricted | 1,500 | 1,500 | 1,500/1,500 | 1,500/1,500 | 0.00% | 87.16% | 1.38 |
| Zhu H=4, restricted | 2,000 | 2,000 | 2,000/2,000 | 2,000/2,000 | 0.00% | 80.35% | 2.01 |
| Zhu H=5, restricted | 2,500 | 2,500 | 2,500/2,500 | 2,500/2,500 | 0.00% | 74.94% | 2.82 |
| Zhu H=6, restricted | 3,000 | 2,998 | 2,998/2,998 | 2,998/2,998 | 0.00% | 70.69% | 3.66 |
| Zhu H=7, restricted | 3,500 | 3,410 | 3,410/3,410 | 3,410/3,410 | 0.00% | 67.05% | 4.69 |
| **Total** | **12,500** | **12,408** | **12,408/12,408** | **12,408/12,408** | **0.00%** | **74.09%** | **16.60 wall** |

“Matched” in the table means an optimum target was joined to a verified identical instance. Published sequence matches are reported separately: **1 evaluated / 1 matched**, Bacci’s six-box Figure 1; this is not a sequence claim for every Zhu instance. Per-class results for all 125 classes are in [results.json](results.json). Group times measure mapping plus bound evaluation; total wall time additionally includes ZIP iteration, source identity (0.86 s) and published replay. The bound scan took **15.56 s**; peak process RSS was **80.3 MiB**. Timing varies with other work on this shared machine.

A lower bound counts work every valid solution must perform. Ordered **LB1** counts each box once when it sits above a box that must leave earlier; it is valid for restricted and unrestricted BRP. Tightness is the arithmetic mean of `bound / published optimum` over matched positive-optimum instances, not a ratio of totals. All 12,408 matched optima are positive; zero-optimum cases would be excluded from the mean and counted separately. Source: [Borjian et al., §2.2.1](https://arxiv.org/abs/1505.04229).

The actual frozen `stowage.bounds.lower_bound(instance).relocations` is also called on **every** mapped instance. It returns zero because the current Instance contract leaves the order of all required load boxes free. Fixed BRP retrieval priority is external to that contract. Its zero bound is safe but vacuous; the nonzero ordered component is reported separately and is not presented as the frozen engine's result. Do not sum the two overlapping bounds.

The targets come from [Bacci–Mattia–Ventura's restricted-BRP computational supplement](https://www.iasi.cnr.it/~tbacci/brp/instances/Computationals_BCRBRP.pdf). Only rows with `Opt == Lb` certify optima. Of 12,495 available extracted rows, 87 are unproven and excluded; five further local instances have no extracted published row. Those **92 unmatched cases receive mapping coverage only**, with no optimum or tightness claim. The means apply to the matched subset and are not extrapolated to unknown cases. See [evidence-notes.md](evidence-notes.md) and [evidence.json](evidence.json) for exact filenames, page references, dimensions, heights and hashes.

**Verified copy identity:** all **12,500/12,500** local instances match Bacci’s independent Group 6 archive in every bottom-to-top stack priority and header dimension. Byte equality is neither claimed nor expected: Bacci uses a three-integer `w h n` header and normalised whitespace; the Tanaka copy uses `w n`. [source-identity.json](source-identity.json) records both archive hashes, the canonical content hash and zero mismatches. The complete comparison took 0.79 s, one core, and runs again in every evidence workflow invocation.

**Protocol:** smaller priorities leave first; stacks run bottom to top; each target uses its published operational height `h` (3–7), with no extra tiers added. For unmatched mapping-only cases, `H+2` is an explicitly chosen scan height, not a published constraint. Changing this mapping-only height cannot affect these counting bounds. The replay helper checks restricted source-stack eligibility, uses `apply_move` for every relocation and retrieval, and increments the benchmark counts only after each successful event. The returned simulator trace deliberately remains illegal to operational `apply_plan` and `count_moves`: productive `gate_out` events cannot bypass the loading-plan objective. Its compressed input requires eager retrieval of accessible next-priority boxes. Retrievals use `gate_out` and are counted separately; BRP cost is `relocations`, not relocations plus the fixed N retrieval lifts.

**Variant:** these numeric checks validate against restricted optima only. An unrestricted preparation move may improve on a restricted solution; a restricted optimum is not a lower bound on the unrestricted problem. These results do not certify unrestricted planner optimality. BF's 721 cases are CPMP (sorting before retrieval), so they are excluded. No grouped-priority claim is made.

**Scope of the evidence:** this evaluates homogeneous abstract yard stacks, not vessel stability or a full checker pass. No physics checker was bypassed to claim a ship-safe plan. No downloaded program was executed or GPL solver code copied. The local MANIFEST leaves third-party dataset rights unresolved; the research archives are read in place and are not redistributed here.

Run from the repo root (the writable cache is needed in this sandbox):

```sh
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q
UV_CACHE_DIR=$PWD/.uv-cache uv run pytest -q -p no:cacheprovider tests/benchmarks --run-brp-benchmarks
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/benchmarks/run.py
```

The complete scan carries `@pytest.mark.slow`. The fixed project marker policy makes `--run-brp-benchmarks` include this benchmark slow test without enabling unrelated slow/GPU/network tests; an explicit `-m` expression still takes precedence. To select just the complete archive test, use `uv run pytest -q tests/benchmarks -m slow --run-brp-benchmarks`. The quick suite retains three published-target comparisons, a three-instance mapping sample, the published Figure 1 replay, strict parser tests and synthetic replay tests. Archive-dependent quick tests explicitly skip when public research copies are absent, so a green quick run alone does not establish public validation.

The evidence-producing `run.py` command **requires both public archives and the published illustration**, verifies their content identity, replays the published sequence and records each selected public check as `passed` in `results.json`; missing evidence exits with an error and writes no new results. `--archive` and `--source-archive` can point to downloaded research copies. `--limit N` is a pilot only, recorded as `scan_complete: false`; omit it for the full evidence run. No network access occurs during tests or the workflow.

Compute pilot: 100 mappings/bound evaluations took 0.183 s, peak 53.7 MiB. Linear extrapolation estimated about 23 s for 12,500 cases, one core, under 0.1 GiB, no GPU. The original scan took 10.74 s; the final evidence workflow with source verification and published replay took **16.60 s, 80.3 MiB**. Final benchmark tests: **42 passed** quick (1.11 s), **43 passed** with the complete archive opt-in (13.99 s), zero skipped in either run. JUnit results are in `fix-quick.xml` and `fix-opt-in.xml`. This benchmark job did not meet the heavy-job threshold; no fleet reservation or cloud spend was needed.

Proposed leader status: task 4.10 criteria met — public instance loading, published Figure 1 move-count reproduction and bound comparisons to certified public restricted optima are evidenced. Coverage remains 12,408 certified targets, with 92 mapping-only cases. No model approximation or frozen-interface change proposed.
