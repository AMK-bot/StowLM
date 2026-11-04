# Fix-wave production smoke — generator 3.0.0

Four bounded specifications use feeder_500, Laem Chabang-like port, seed 1,
noise 0.2, default D36 quantity and an explicitly non-DG service. Every production
screen stays active, including actual hull construction. Accepted inputs are
saved and loaded again with equality checked. They are planning inputs, with
no solver witness or full-checker feasibility certificate.

| Rung | Targets requested | Result | Attempts | Time, s |
|---|---:|---|---:|---:|
| tiny | 4 | accepted; JSON round trip verified | 3 | 3.160 |
| small | 34 | accepted; JSON round trip verified | 1 | 1.536 |
| medium | 121 | rejected; background allocation cannot balance | 3 | 9.246 |
| full | 262 | accepted; JSON round trip verified | 1 | 1.370 |

The manifest pins generator 3.0.0, cargo 3.1.0, simulator 1.1.0 and vessel
condition `c3-fixed-background-6`, plus SHA-256 for every Python source under
`src/stowage` and TOML parameter file before and after generation. No source
changed during this run (`source_drift=[]`). There are three instance files;
medium has an explicit rejection audit and no instance file. Each accepted
instance's digest is recorded in its audit and the manifest.

Medium rejected all three candidates under `expert.physical.slot_occupancy`
while reconditioning fixed background; it never reached simulation or completion
checks. This reproduces the lower-tier medium allocation blocker with the new
D36 region-sized booking. **Successful medium real-hull serialization remains
deferred**: the vessel owner must resolve its allocation/condition under the
D36 revisit process, then this same bounded integration recipe must accept
and serialize a medium input. No rejection is counted as successful round trip.

All three hulls include D40's vessel-provided signed trim, displacement and
bilge-keel fields; generator policy was not invented. At these pinned sources,
tiny/small fixed background cargo still lacks D42 per-box records; that chain
belongs to the vessel producer and must land before background securing and
visibility can be certified. This smoke establishes generator/serialization
integration only, not those checks.

Reproduce from the repository root into this dedicated small smoke directory
(the script writes at most four accepted instances, and preserves rejected
specifications too):

```sh
UV_CACHE_DIR=/private/tmp/stowage-review-uv OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 nice -n 10 uv run --no-sync python experiments/instances-sim/fix-wave/smoke.py
```

The historical generator 1.0.0 starter in the parent directory is unchanged and
cannot be resumed by the current generator. The larger current-version starter
and population screening audit are deferred to the leader's regeneration wave.
Four specifications do not estimate population acceptance rates.

Compute: the first production medium pilot took 63.227 s (53.690 s hull,
9.280 s screening), within the announced <90 s estimate. Based on that measured
pilot, the four-spec smoke reserved one CPU, <2 GB, no GPU and <2 minutes;
actual warm total was 15.323 s. Host admission showed load 10.30/8.39/7.82 and
95% memory free; fleet ledger had no reservations. `nice` was requested and
sandbox denied its priority change; the process still ran. No fleet ledger edits
were allowed by this task's fence.

The original geometry unit round-trip silently returning after three rejected
medium candidates was reproduced with an injected generator. That catch was
removed. Geometry round trips cover tiny/small/full; a separate deterministic
medium rejection test preserves fail-closed audit coverage. Empty-only accounting
fixtures now request an explicit diagnostic quantity, preserving D36's unchanged
production mass band. No hard check was weakened. The first legacy module run
was interrupted after 18 passes while an impossible default empty-only accounting
fixture retried; after isolating quantity, the complete module passed (54 cases).
