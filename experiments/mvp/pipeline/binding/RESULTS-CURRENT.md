# Engine 0.13.0 instruction binding: development measurements

CURRENT COMPATIBLE EVIDENCE ONLY: 0 rows; 30 historical/stale rows excluded from every denominator and required coverage. See `measurements/current-compatibility.json` for exact keys and reasons. Historical rows are immutable and remain evidence of their original implementation. Cached passes and failures require the same strict identity gate.

Measured development data only. `live` (shown as live-v1) and `live-v2` request 90 s extra work and 30 s finishing, with additive stage budgets recorded in each row. `live-v2` additionally sets `work_count_search=False,warm_polish=False` to remove the medium 900 s warm-polish floor; medium uses this profile. Tiny/small early live-v1 rows retain those medium-specific flags. These are not wall-clock deadlines: for at most 60 load boxes the engine preserves historical search and a 300 s finishing floor, potentially applied to multiple candidates; atomic checks can also overrun. `default` uses untouched engine Config and default budgets. All runs use seed 0, BLAS threads 1 and current fleet admission, with at most four outer jobs concurrently (numerical workers 1); verification ran while only three measurement workers were active.

| Rule type | Cell / budget | n | Passes | Screen refusals | Searched no-plan | Baseline violated | Binding passes | Median s | Max s | Median lifts / baseline |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | tiny / live-v1 | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| baseline | tiny / default | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| baseline | small / live-v1 | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| baseline | small / default | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| baseline | medium / live-v2 | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| baseline | medium / default | 0 | 0 | 0 | 0 | — | — | — | — | — / — |
| close_bay | tiny / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| close_bay | tiny / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| close_bay | small / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| close_bay | small / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| close_bay | medium / live-v2 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| close_bay | medium / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | tiny / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | tiny / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | small / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | small / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | medium / live-v2 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| max_tiers | medium / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | tiny / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | tiny / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | small / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | small / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | medium / live-v2 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| dg_keep_away | medium / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | tiny / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | tiny / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | small / live-v1 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | small / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | medium / live-v2 | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |
| one_port_per_hatch | medium / default | 0 | 0 | 0 | 0 | 0 | 0 | — | — | — / — (n=0) |

Each JSON row stores its exact rule, input path, admission reading, original engine attribution, checker evidence and plan when released. Rules are selected deterministically after the corresponding baseline: prefer a rule that baseline violates, with the fewest excluded placements and no simple capacity shortfall. This is a development selection procedure, not a held-out comparison.

Lift comparisons use only identical paired subsets where both the rule run and the matched same-profile baseline passed; paired n appears in the final column. Source snapshots and cache conditions are retained per row. New baseline runs use the adapter with an empty rule list to record checked incumbent events; earlier native-entry baseline rows remain unchanged. `first_checked_wall_s` and progress events describe the first legal publication, while table wall seconds describe the final returned plan after continuation and the mandatory release recheck.

Quick tiny contains only one natural DG-bearing map; its DG cell uses three distinct anchor/radius rules on that map, explicitly repeated. Other types use three tiny maps. Small uses three naturally DG-bearing maps. The frozen medium cohort contains four in-envelope medium12 feeder_500 maps plus two explicitly named C6 development maps: the fresh pipeline development cohort was absent when selection was frozen. No sealed paths are enumerated or opened.

The target is n=3 per rule/budget on tiny and small and n=6 on medium (96 constrained runs plus 24 shared baselines). `Binding passes` counts only passes whose corresponding no-rule plan violated that exact instruction. Baseline-obeyed instructions prove rechecking but do not demonstrate a binding replan. Rows still absent from the requested 24 rule/cell/budget groups are unmeasured, not successes. Full default measurements may be much slower than the live profile.

The fresh 0.6 development cohort was observed later at 2026-11-02T01:21:07Z (4 maps). Its paths, SHA-256 digests and timestamps are recorded in `measurements/late-dev-cohort.json`; existing paired comparisons retain the frozen cohort. Supplemental development runs wait until the required matrix completes.

## Cache conditions and provenance

The cache-seeding manifest records 2326 SHA-verified physical acceleration artifacts (1.704 GB), copied from the independent 0.6 runtime at Unix time 1793580185.124. No cargo maps or plans were copied. New rows explicitly record `physical_cache_seeded_at_start` and the seed manifest digest; source snapshots include runtime cache path controls. Early rows predate complete cache path isolation and may have accessed pre-existing shared native numerical caches; no shared files were cleaned up. Their wall times remain recorded as observed, with no claim that all early runs were uniformly cold or warm. Active pre-seed processes may encounter newly copied artifacts later in a run.

## Automatic attempt lifecycle (excluded from final denominators)

- `legacy-791ff4cf64b68079b96c5099d85e4a7f3e4ab509d28aad0d57c295a7adfc0557` / `cde19ebfb554b234355d522d`: unknown; recovered unfinished legacy evidence; liveness unverified. Evidence: `/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/pipeline/binding/measurements/recovered/1c5503dcb08240c89930341e8ace65d1`.
- `legacy-87eaf03adbffc8e01e0d00ae0cfbccf50a3c4fe98061a3314f752df5748e1621` / `a35bdd07495a7b4e26219e35`: unknown; recovered unfinished legacy evidence; liveness unverified. Evidence: `/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/pipeline/binding/measurements/recovered/a01ee37a1f4d4d6f95bcc24f34c39476`.
- `legacy-6d87b0d31ada6e1154d096f2d024804058cbc2339d8ea347c6b4987e43aa6cfe` / `abaaa6aab9ff0940f0640ce0`: unknown; recovered unfinished legacy evidence; liveness unverified. Evidence: `/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/pipeline/binding/measurements/recovered/b20ab9631b044f798b8fd84f900f2d2c`.
- `legacy-9034eb2d0d2ddfb2d56cc96c18bda771dae727c51a1cfab7d88201d231db6d24` / `78c1578ba2dc4593af9d3625`: unknown; recovered unfinished legacy evidence; liveness unverified. Evidence: `/Users/anda/Projects/meta-hackathon-stowage/experiments/mvp/pipeline/binding/measurements/recovered/d8d909ad193d4670bda6947d9d1f602c`.

## Interrupted diagnostics (excluded from n and pass rates)

- `interrupted-cache-mtime-169eb1b09e0de0bb784e7421.json`: 779.6 s observed; verdict unknown. Physical cache copy had lost source mtimes. Native newest-grid selection could choose a different certified-grid identity and fall back to direct liquid-transfer evaluation. Stopped own session before correcting timestamps; verdict unknown.
- `interrupted-cache-mtime-69bf2dcb4257dbaeb1bb0e9d.json`: 780.1 s observed; verdict unknown. Physical cache copy had lost source mtimes. Native newest-grid selection could choose a different certified-grid identity and fall back to direct liquid-transfer evaluation. Stopped own session before correcting timestamps; verdict unknown.
- `interrupted-cache-mtime-cde19ebfb554b234355d522d.json`: 709.9 s observed; verdict unknown. Physical cache copy had lost source mtimes. Native newest-grid selection could choose a different certified-grid identity and fall back to direct liquid-transfer evaluation. Stopped own session before correcting timestamps; verdict unknown.
- `interrupted-medium-live-v1.json`: >=420 s observed; exact duration unknown; verdict unknown. The exposed default work_count_search and warm_polish flags can invoke max(900,extra_budget_s) medium polishing. A separate live-v2 profile disables these flags; untouched default remains separate.
- `interrupted-small-live-v1.json`: >=1500 s observed; exact duration unknown; verdict unknown. Prioritize a direct one_port_per_hatch check on the 88-box C6 development map. Small live/default matrix remains pending and resumable, not dropped.
- `interrupted-tiny43-cold.json`: 436.7 s observed; verdict unknown. Pre-seed worker still building canonical feeder800 hull after429seconds; restart uses exact seeded physical cache and scoped native cache paths.
