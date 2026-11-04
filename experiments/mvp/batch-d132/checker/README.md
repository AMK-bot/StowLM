# D132 full-checker guard (complete)

No production installation is performed by this lane. Sources are immutable assembly snapshots, imported with the snapshot `src` first and checked against their exact recorded file hashes. Each worker's current directory is the snapshot root. `assert_runtime_imports` rejects any live/installed Stowage module outside that snapshot.

Selection: `inventory.json` SHA256 `9c035de61e95ece71deae70458b7279e7dd3ddf1d9f1699261ded4f5a0701935`. These are the exact balanced100 saved D114 follow-up TRAIN/VAL input/plan pairs:36VAL tiny,24TRAIN tiny,40TRAIN small, all three ships. Every body was copied into this lane's content-addressed blobs after split/path/hash/ID exclusion checks. No TEST bodies were opened. Historical reports do not serve as baselines; both report sets are freshly computed from identical saved bytes.

Baseline assembly manifest SHA256 `1598d7cf10e1b5017696d6b5c2fb669ec534bb7f670dcee94df065b3faa95811`; checker runtime epoch `49c57e27487e6b7d3ec898b2aa055c47334bffbf14c87b6d85f1beb827ee5eff`. Combined candidate assembly manifest SHA256 `7db1cd9a526cf7b4824d6157fad7aa3ba7c300133fd4749924b6336f03c3f470`; runtime epoch `dd8b7e9554302a6974a30f013b9de9e04bbe50a68b5d020554ab194181e036f2`. Root authorized qualification of this immutable provisional candidate; production release remains conditional on final B owner pins. Any candidate byte changes require a new revision and affected guards, never mutation/reinterpretation of this run.

Full reports use canonical JSON (sorted keys, compact whitespace) and retain **every field and every number**, including full provenance. No tolerance comparator, masked float, deleted path or ignored provenance field is used. Heat-mask repair should not alter the full checker: expected100/100 byte-identical, with no permitted automatic exceptions. Differences are reported with exact JSON paths and values.

`PYTHONHASHSEED=0`, all native thread caps1, no GPU/network. Both numeric arms use `STOWAGE_VESSEL_SINGLEFLIGHT=1`; the baseline lacks the new hook and ignores that environment setting. Candidate qualification therefore exercises the active coordination hook. This serial guard is distinct from the installer's defaultOFF/ON concurrency unit tests. Initial baseline whole-vessel preparation preceded that instruction and used the environment default; it does not supply any checker verdict.

Qualified D126 stability-cache bytes are linked into each arm's own private stability directory. Full-vessel cache files are seeded under their exact existing source-qualified filenames. The unchanged production loader must accept each ordinary key; no key is renamed or forged. Candidate source changes can therefore cause real builder misses. Six full-vessel identities (three ships × hull/geometry) are measured separately, with preparation cost/cache hit state kept out of full-check timing. New cache writes remain private. Shared caches and immutable source/data are protected by the write audit hook; native qualification remains authoritative. The copied raw KCS asset is source-pinned even on cache hits.

Separate candidate checks: the current manual40 suite (`test_rules_heat_ids`, `test_fleet_manual_restore`, `test_fleet_lashing_design`, `test_fleet_d56`) and a real recomputation of all six loading conditions per ship. Required design-GM caps are exactly3.2838356051426465 /3.9584570964621637 /5.530108952747626m. Actual governing conditions are C3/C2/C3, all departure. This preserves the explicit erratum to the old D126 runbook's incorrect C3-every-ship assertion.

Owned process receipts contain exact argv/cwd/environment, PID, external timeout, wall/CPU/peak RSS, source hashes before/after and reaping. No alarm/profile callback is injected into numerical code. Each completed plan checkpoints its full report and source/input binding; a stopped/timed-out uncompleted plan never gets credited. `STOP` is observed between plans. `nice -n10` is attempted; this sandbox denies setpriority, and the warning is preserved. `ps` is likewise denied, without bypass.

Admission/reservation/launch are **separate dependent calls**, and the launcher independently requires the successful reservation receipt plus the matching live own1CPU row before spawning. The six-case pilot inadvertently launched after a refused reservation due to an earlier shell sequence; `ADMISSION-INCIDENT.json` and fleet history record the error, attempted recorded-PID termination denial, natural bounded completion/reaping, and the launcher fix. No denied signal was retried and no foreign process was targeted.

Entry points:

```sh
UV_CACHE_DIR=$PWD/.uv-cache UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync python experiments/mvp/batch-d132/checker/prepare.py inventory
UV_CACHE_DIR=$PWD/.uv-cache UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync python experiments/mvp/batch-d132/checker/prepare.py pin --runtime /private/tmp/stowlm-d132-assembly-20261030/baseline
UV_CACHE_DIR=$PWD/.uv-cache UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 uv run --offline --no-sync python experiments/mvp/batch-d132/checker/admission.py
```

After reviewing admission's `fits:true`, reserve a new named job with `ledger.py reserve`; only after that call succeeds launch `owned.py --reservation <successful ledger JSON> --runtime <snapshot> --out <new receipt dir> --limit <external seconds> --ram-gb4 -- <absolute worker.py> checks --runtime <snapshot> --arm baseline|candidate`. Candidate adds `--baseline <this lane/baseline>`. The actual commands use separate `--ram-gb 4` arguments. Use `setup`, `manual`, or `gm` worker modes for the separate gates. Do not start two workers from this lane; root owns aggregate D132 admission.

Final outcome and exact resource/incident disclosure: `REPORT.md`; sealed machine receipts: `summary.json`, `manual-gm.json`. All owned workers are reaped.
