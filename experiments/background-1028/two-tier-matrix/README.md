# Two-tier baseline, before D29 utilisation tuning

This is an intermediate, verified source epoch, not the final profile choice.
Source manifest: `../sources/dcdb1ff8e04c80c811644b038b812c081b4a0b41ea2aa9a7f8c27b3ba6cfb53d.json`.
All 138 rows have identical start/end selected-source hashes and unique ship/rung/generator/seed keys.
This baseline manifest covers checker/ballast-solver/fleet/generator Python,
fleet parameters and versions, but omits other C1 dependencies and cargo/port/rule
parameter files. Concurrent changes there would not be detected. The final matrix
expands coverage to all `src/stowage` Python/JSON/TOML, all parameter JSON/TOML,
numerical runtime versions and relevant physics/BLAS environment variables.

The actual random-yard and simulated-yard generators ran one candidate per call
(`max_attempts=1`), with ordinary cargo rules and C1 enabled, C2 disabled, and
the production ballast-finisher/full-checker background gate enabled. Seeds were
0–9 for tiny/small and 0–2 for medium. No reduced cargo profile or physics bypass
was used. A separate D56 diagnostic evaluated each D57 securing arrangement at
the declared lashing design GM, retaining actual cargo, draught and equilibrium.

- All 120 tiny/small backgrounds passed every full-checker part at all three D57 states.
- Arrival B 2.3 ratio was at least 1.202 on those backgrounds, exceeding 1.05.
- All 138 arrangements passed the separate securing-at-design-GM diagnostic.
- Every tiny/small generator cell emitted at least one candidate; 50/120 passed all generator screens.
- Below-30% whole-pipeline cells were simulated/800/small (1/10), random/1700/small (2/10),
  and simulated/1700/small (2/10). Existing cargo/C1 screens, rather than the background gate, bound these yields.
- No medium candidate was emitted (0/3 in each cell). Four of six 800 medium backgrounds
  passed the background gate; the remaining 14 medium backgrounds failed the weather criterion
  and the ten-second repair search found no witness. A timeout is not an infeasibility proof.

`measurement-table.md` gives all 18 cells, min/median/max arrival B 2.3, GM/KG,
ballast, utilisation and generation seconds per accepted instance. `realism-summary.json`
and `realism-draws.jsonl` expose the continuous-equivalent background mass approximation,
including sub-2.2-t records; their TEU counts must not be described as physical empty ISO boxes.
The 1700's substantial TEU reduction motivated the subsequent lightweight-deck profile work.

Compute: two one-thread workers, six GB combined reservation, no GPU. Initial
90-minute conservative estimate was revised to 20 minutes after five draws per
worker; actual concurrent wall time was 586.59 seconds. Recorded generation time
summed over workers was 1112.57 seconds, plus 54.17 seconds for separate design-GM
diagnostics. Actual peak RSS was not measured. `matrix-compute.json` records UTC
starts/finishes, PIDs and per-worker elapsed times.
