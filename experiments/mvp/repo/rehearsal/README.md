# Regenerated-copy rehearsal receipts

`final-20261030/` is the final receipt. All nine operational steps passed on
`/private/tmp/stowlm-judge-99-3-final-20261030/proposed`, without source repairs.
`verification.json` independently checks the complete step set, zero exit codes,
reaping, 175 tests (174 passed, one expected optional derived-offset skip), all
15 HTTP responses, real missing/tampered KCS rejection, and restored member hash.

The command was:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 RAYON_NUM_THREADS=1 \
uv run --offline --no-sync python experiments/mvp/repo/rehearse.py \
  --tree /private/tmp/stowlm-judge-99-3-final-20261030/proposed \
  --cache /private/tmp/stowlm-judge-99-3-xzmzsouq/uv-cache-full \
  --archive data/raw/kcs/kcs_offsets.ZIP \
  --out experiments/mvp/repo/rehearsal/final-20261030
```

For the freeze, use a newly copied candidate tree and a new output directory.
The tool rejects the source repository and trees containing `.git`. These
main-only safety checks were added after the measured final launch; three
negative checks passed in `preflight-check.json`. Its worker snapshot and
launch-time harness SHA are preserved. The final tool also requires every
selected step to complete before reporting operational success.

Measured final job totals: **93.78 s wall, 47.71 s CPU**, one worker/native thread,
no GPU. Estimate: 180 s, one core, 2 GiB. Cumulative waited-child maximum RSS was
192,724,992 bytes (183.80 MiB), not a sampled simultaneous process-tree peak.
No blocked external-network attempts were recorded; uv was explicitly offline.

The checker invocation passed and its report binds to the fixture inputs; the
unchanged checker correctly **rejects** the diagnostic synthetic fixture.
The HTTP payload also reports NOT VALIDATED and not shippable. This is not an
approved showcase or a browser/visual pass. Explanations use templates without
API keys; the unrecorded instruction is refused offline at zero reported cost.

The securing probe uses the ordinary synthetic fixture with the real
`params/fleet/feeder_500.toml#hull_form` reference. Actual checksum-pinned asset
validation and construction succeed. The fixture lacks `max_gm_lashing_m`, so
its bounds are unavailable and `securing_active` is false. The result explicitly
does not claim an active fleet screen or full-fleet feasibility. Removing and
tampering with the generated scratch KCS asset each raises
`SecuringConfigurationError`, including after normal construction, then the
original verified bytes are restored.

Earlier receipts are retained separately:

* `regenerated-20261030/`: original trial. Installation, data setup, tests, eval
  help and checker ran successfully. Three probes failed because an external
  harness worker lacked the working-directory import entry. The final tool
  inserts that entry explicitly, matching `python -m` behavior. No repository
  source repair was needed.
* That original trial also cold-built the complete feeder_500 vessel before
  securing construction. It exceeded the **180 s** wall guard (96.06 CPU s),
  before producing a securing result. Only its recorded owned PIDs were stopped;
  the uv child was waited/reaped. This remains an open cold-build performance
  diagnostic and was not repeated.
* `regenerated-20261030-recovery/`: explicitly partial recovery of affected
  probes. App help, origins and all HTTP requests passed. A stronger-than-needed
  assertion that the synthetic fixture had active securing bounds failed; the
  final receipt reports those bounds honestly instead.

The curated checked demo bundle and its full-fleet active-screen replay remain
pending the freeze and the 9.2/10.2 artifact copy. No model weights/tokenizer or
test-v2.1 instance was loaded by this rehearsal.
