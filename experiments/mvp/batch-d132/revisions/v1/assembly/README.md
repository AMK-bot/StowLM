# D132 scratch assembly and scheduled transaction

No production installation has been performed. `INSTALL.sh` is an operator entry
point for the leader's generation boundary; it does not wait for or infer that
boundary. The delivery package currently fails closed until the final combined
qualification is sealed. The application lazy-input hook is omitted because its
only target is `app/inputs.py`, owned by mvp-ui.

Frozen runtimes:

```
/private/tmp/stowlm-d132-assembly-20261030/baseline
/private/tmp/stowlm-d132-assembly-20261030/candidate
```

`baseline-manifest.json` pins 229 original `src/stowage` files. The combined
candidate adds one module and changes nine source paths in total. Four TOML-owner
test paths also differ. `combined-manifest.json` pins every before/after source
file and exact patch bytes. No TEST input body, model weight, GPU or network is
used. Public KCS bytes/provenance were copied into both scratch roots.

Patch order is heat links, TOML production/test patches, singleflight. TOML and
singleflight overlap only in `vessel/fleet_cache.py`'s source fingerprint list.
The rebased singleflight patch keeps both `toml_cache.py` and
`cache_singleflight.py`, preserves TOML's imports/selected-value calls, and inserts
the reviewed hook unchanged. Singleflight remains default OFF; enabling it uses
`STOWAGE_VESSEL_SINGLEFLIGHT=1`. Source fingerprint changes create new cache keys:
there is no key renaming, numerical-cache bypass or acceptance change.

The package archives sequential patches and exact before/after payloads. Runtime
installation uses those already scratch-applied bytes rather than resolving
patches on production. Before any source write it verifies package/tool checksums,
the entire live `src/stowage` inventory including additions and modes, all 13
target preconditions (including absence of the new module), and qualified
parameters/configuration/raw asset/fast-test sources. Atomic file replacement and
an owned backup/journal permit recovery. Rollback verifies backups and rejects
unknown target edits and unrelated source additions/removals/edits; it removes
only the batch's new file and restores original bytes and modes. Whole-source
pre/post equality is checked. Root, state, lock, journal and backup paths reject
symlinks through any ancestor, including the supplied root itself. Use physical
root paths (for example `/private/tmp/...` rather than a symlink alias).
Atomicity is per file, not an instantaneous whole-tree swap. All code-reading
workers and source-editing owners must be quiescent before install or rollback;
no worker or source edit may start until it finishes. The tool lock serializes
installer invocations only.
`--check-only` makes no source or metadata writes and is not a lock reservation;
the real invocation repeats the preconditions.

## Qualification and release

`package_batch.py` without arguments prepares an **unqualified** delivery. To seal,
the parent supplies a JSON document containing:

```json
{
  "status": "PASS",
  "combined_manifest_sha256": "7db1cd9a526cf7b4824d6157fad7aa3ba7c300133fd4749924b6336f03c3f470",
  "toml_owner_final": true,
  "guards": [
    {"name": "checker", "path": "experiments/mvp/batch-d132/...", "sha256": "...", "passed": true},
    {"name": "mask", "path": "experiments/mvp/batch-d132/...", "sha256": "...", "passed": true},
    {"name": "manual-gm", "path": "experiments/mvp/batch-d132/...", "sha256": "...", "passed": true}
  ]
}
```

The parent must assess those evidence contents: this packaging tool checks their
hash binding/status assertion, rather than replacing the checker/mask evaluators.
It additionally rejects live source drift and changed TOML patch bytes. Run:

```sh
UV_CACHE_DIR=/private/tmp/stowlm-judge-99-3-xzmzsouq/uv-cache-full \
uv run --offline --no-sync python \
  experiments/mvp/batch-d132/assembly/package_batch.py \
  --qualification-manifest experiments/mvp/batch-d132/QUALIFIED.json
```

After the leader explicitly opens the generation-boundary window:

```sh
experiments/mvp/batch-d132/INSTALL.sh --check-only
experiments/mvp/batch-d132/INSTALL.sh
```

Both shell entry points support `--root /absolute/scratch/root` for rehearsal.
`INSTALL.sh` rejects `--skip-fast-tests` and its accepted long-option
abbreviations before invoking Python. The hidden direct Python CLI flag remains
available for synthetic transaction fault tests only. Installed-state checks are
idempotent and repeat the fast tests. For an owned rollback:

```sh
experiments/mvp/batch-d132/ROLLBACK.sh --check-only
experiments/mvp/batch-d132/ROLLBACK.sh
```

Fast suites execute in one native thread without GPU/network, using the target
root's source first on `PYTHONPATH`. The heat override environment is removed.
They include real installed-hook guards, not an old review candidate module.
Logs and exact source backup/journal are below the target root's
`experiments/mvp/batch-d132/assembly/install-state/`.

## Executed mechanical guards

- Synthetic transaction faults: **18/18 PASS**. Corrupt payload/backup/tooling, missing
  qualification, symlink, source addition/foreign edit, incomplete transaction,
  idempotent restore, absence restoration and mode restoration are exercised.
- Combined cheap synthetic regressions: **40/40 PASS** (16 TOML, four public lock,
  12 heat/scalar parity, eight actual combined-hook guards).
- Actual combined two-process synthetic guard: baseline **two** builds, flag OFF
  **two**, flag ON **one**; all six JSON hashes match. Both enabled lock attempts
  precede first publication; abrupt owned-process exit releases the lock. Eight
  owned children were reaped, no foreign process signalled. One active CPU lane,
  estimated 20 seconds/600 MB; measured **1.155 s wall**, **1.232 CPU s** summed
  children, **42.88 MiB** maximum waited-child RSS (not combined-process peak).
- Private-copy shell rehearsal: check/install/idempotent install/check rollback/
  rollback/idempotent rollback all PASS; exact whole-source restoration. The two
  install invocations each passed **337 regression + 407 checker tests**, taking
  **16.197 s** and **15.090 s** in the final tooling-checksum version
  (`install-rehearsal-toolpins.json`). This private package explicitly overrode the
  qualification flag only to test mechanics; the delivery remains unqualified.
  This is not a substitute for the 100-plan, 214-trace or manual/GM numerical gates.

The later shell skip-flag rejection changes the INSTALL tooling checksum.
Its focused tests execute only a copied shell entry point with a fake `uv`;
regenerate the package/tool pins and rerun a fresh private mechanical rehearsal
before sealing the delivery for installation.
The final focused release-path run passed **55 tests in 1.197 s**: 20 existing
transaction guards, 17 new root/state/backup path and metadata-free preflight
guards, and 18 copied-shell skip-option guards. These do not replace the fresh
private mechanical rehearsal required after the tooling checksum changes.

No numerical approximation is introduced. The process rendezvous uses a small
synthetic payload only to test locking; real complete-Vessel/report equivalence
belongs to the combined checker guard.

## Final release tooling rehearsal (02:42 EDT)

After the independent release review added shell skip-flag rejection, state-path
symlink rejection, metadata-free check-only operation, and actual mask/tokenizer
input pins, the package was regenerated **unqualified**. A new private-root
rehearsal passed all six shell steps and restored the exact original source tree.
Both install invocations again passed **337 regression + 407 checker tests**, in
**13.005 s** and **12.787 s**. Total owned rehearsal worker: **27.080 s wall /
26.374 CPU s / 370.64 MiB** peak waited-child RSS; estimate 45 s, one core, no GPU,
2 GB execution estimate with a conservative 4 GB ledger reservation. The worker
was reaped and the reservation released.

Final receipts are `install-rehearsal-final-release.json` and
`FINAL-RELEASE-AUDIT.json`; logs are retained in `rehearsal-fast-final-release/`.
That audit confirms immutable baseline/candidate source and unchanged production.
The delivery's qualification flag remains false pending the parent's numerical
100-plan/mask/manual/GM gate and final seal. No production script was invoked.
