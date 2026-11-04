# D132 heat effect

Nine selected pilot-2 VAL dead-end prefixes were replayed in baseline and
combined scratch runtimes. Three prefixes on one instance isolate the heat
inventory bug; two gain immediate engine escape, while the third remains dead.
These are prefix results, not checker-approved complete plans.

- [Results, limitations, exact commands and compute receipts](REPORT.md)
- `val-prefixes.json`: metadata policy proof and exact allowed VAL actions/hashes.
- `val-comparison.json`: actual legal-action additions and isolated heat probes.
- `train-escape-prefix.json`: exact 24-action TRAIN continuation handoff from the
  separate 214-trace guard, including input SHA and wrapper flags.
- `process-val-{baseline,candidate}/process.json`: owned/reaped execution receipts.

`prepare_val.py` excludes the historical 15-input base-mask TEST run after
reading only its aggregate config/manifest. It never opens TEST inputs or traces.
`replay_val.py` must run once per immutable scratch epoch under the shared masks
lane admission/ledger/owned wrapper. `compare_val.py` never promotes an immediate
escape into a full-plan cure. Backtracking blacklist state is not retained in
the old c/d committed traces; those comparisons are explicitly engine-prefix
replays. The parent owns completion, V4 ballast and full-checker/token gates.
