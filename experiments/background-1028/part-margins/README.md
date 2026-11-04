# Supplementary strength and visibility margins

Six accepted random-yard instances were regenerated with `max_attempts=1`, then their background-only empty plans were checked independently using the returned finisher ballast witness. All full-checker parts passed in all 18 D57 states. The final 138-draw matrix was not changed.

| Ship | Rung | Seed | Max SF utilization | Max BM utilization | Min visibility distance clearance, m |
|---|---|---:|---:|---:|---:|
| feeder_500 | tiny | 1 | 0.153808 | 0.138257 | 184.539 |
| feeder_500 | small | 1 | 0.189209 | 0.184354 | 207.214 |
| feeder_800 | tiny | 1 | 0.210857 | 0.218473 | 156.633 |
| feeder_800 | small | 1 | 0.298570 | 0.217974 | 186.432 |
| feeder_1700 | tiny | 1 | 0.277872 | 0.292093 | 43.863 |
| feeder_1700 | small | 7 | 0.349457 | 0.344852 | 88.819 |

SF is longitudinal shear force; BM is the bending moment in the hull girder. A utilization of 1 reaches the supplied seagoing strength limit. Worst utilization was 0.349457 for SF and 0.344852 for BM, leaving at least 65.05% and 65.51% of those limits unused. Minimum forward visibility distance clearance was 43.863 m. Horizontal blind-sector and clear-sector margins were at least 20 degrees and 175 degrees.

Every saved physical margin is positive. The 42 zero margins are boolean weather-table applicability flags (a true value of 1 compared with a requirement of 1), not physical clearances. There are no negative margins. All original metrics, margins, parts and violations are saved in the six `.report.json` files.

The supplementary source epoch `92548e09ce2a40440cddc81bad49ca001fc247f69c6c3175533ba89fa7765656` stayed unchanged throughout the run. It differs from matrix epoch `61bd0391cbcb9822b27db085fca906f4940c1aa5e5dedcaba20d39205ba8b0ee` only in:

- `src/stowage/checker/stability_liquid_tables.py`
- `src/stowage/checker/stability_reuse.py`
- `src/stowage/checker/stability_tables.py`
- `src/stowage/checker/strength_assessment.py`
- `src/stowage/checker/visibility_range.py`
- `src/stowage/vessel/hydrostatics.py`

No generator or builder file changed. The source/runtime manifests are in the parent `sources/` folder. The bounded paired comparison in `paired-stability.json` found all 1,134 common numeric stability metrics bitwise identical across these six backgrounds and their three states (990 physical/context values and 144 execution diagnostics). This does not establish global equivalence of the checker epochs.

Commands:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/part-margins/collect.py > experiments/background-1028/part-margins/run.log 2>&1
UV_CACHE_DIR=$PWD/.uv-cache uv run python experiments/background-1028/part-margins/compare.py > experiments/background-1028/part-margins/paired-stability.log
```

Compute: one CPU core, 3 GB reserved, no GPU; 180 seconds estimated versus 144.67 seconds actual. Peak main-process RSS was 1689.9 MiB; reusable finisher children were excluded. The worker exited.
