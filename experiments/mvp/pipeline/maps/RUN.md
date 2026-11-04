# Envelope cohort procedure

All writes stay below this directory. The unmodified C6 corpus-builder and
src/params snapshot are used; no planner, score run or plan checker is invoked.
Generation can exceed twenty minutes because all 56 maps use independent
producer draws with unchanged physical acceptance gates; there is no search
budget or solver experiment. Two CPU workers maximum, numerical threads one.
The admission helper waits for fresh fleet capacity before launching each map.
The sandbox prohibits nice, ps and sysctl; none is used.

From repository root, use these environment variables for each command:

```sh
export UV_CACHE_DIR="$PWD/experiments/mvp/pipeline/maps/.uv-cache"
export PYTHONDONTWRITEBYTECODE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1
export MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab
uv run --offline --no-sync python experiments/mvp/pipeline/maps/prepare_runtime.py
uv run --offline --no-sync python experiments/mvp/pipeline/maps/probe_runtime.py
uv run --offline --no-sync python experiments/mvp/pipeline/maps/seed_audit_lexical_v1.py --output experiments/mvp/pipeline/maps/SEED-AUDIT-PRE-RAW.json
uv run --offline --no-sync python experiments/mvp/pipeline/maps/resolve_seed_audit.py
uv run --offline --no-sync python experiments/mvp/pipeline/maps/generate.py freeze
uv run --offline --no-sync python experiments/mvp/pipeline/maps/generate.py generate
uv run --offline --no-sync python experiments/mvp/pipeline/maps/finalize.py
```

The preserved full lexical audit and structural JSON candidate resolution are
explained in AMENDMENT-1.md; all scope/count/hash evidence is retained. `freeze`
verifies that evidence and binds the preregistration, generator and runtime
sources. `generate` never replaces an output. Completed sealed files
are resumed by hash only. Parent payloads and their producer audits are private
temporary generator inputs, removed before returning. Sealed status records
contain declared seed/cell identifiers, hashes and generator schema success,
without cargo counts or planning diagnostics.

The generator accepted all 56 maps; its final publication assertion stopped
because other sessions had begun using the open dev maps. POST-AUDIT-NOTE.md
documents the 54 exact development-lineage paths in the completed POST scan.
`finalize.py` retains that scan verbatim, recognizes only those narrow current
dev identities (never sealed seeds or metadata hits), and publishes from the
completed evidence. It hashes map bytes only; it does not rerun generation or
rescan the repository. Frozen generator, requests and preregistration stay intact.

`prepare_dev_set.py` made the 32 dev status records available earlier through
DEV-MANIFEST.json; the loader adapter now prefers the complete canonical manifest.
`prepare_linear_cache.py` prepared all four contexts' coefficients, but unchanged
production policy rejected qualification as stale. Its failure and 24 artifact
hashes remain in linear-cache-manifest.json and the canonical manifest; this
does not certify the optional legacy linear fallback as warm.

For verification, wait for capacity, then run the six tests with copied runtime
source first (avoids writes to shared numerical caches):

```sh
PYTHONPATH=experiments/mvp/pipeline/maps/runtime/src:experiments/mvp/frontier:experiments/mvp/frontier/lab:experiments/mvp/pipeline/maps \
uv run --offline --no-sync python -c 'from capacity import wait_for_slot; wait_for_slot(); import pytest; raise SystemExit(pytest.main(["experiments/mvp/pipeline/maps/test_maps.py", "-q", "-p", "no:cacheprovider", "--basetemp=experiments/mvp/pipeline/maps/tmp/pytest"]))'
```

The lab's unchanged positive provenance allowlist rejects every new map path.
Use `lab_set.approved_dev()` around its actual `catalog.load_set(dev.txt)`;
tests exercise that loader and verify sealed paths are rejected before reading
bytes. For future dev scoring, `lab_set.py score.py --set
experiments/mvp/pipeline/maps/dev.txt ...` invokes the lab runner with the same
bounded adapter; `score_ladder.py` is an equivalent alias for its runner with
dev-only provenance. This task does not execute either scorer. Future callers
must choose output paths/caches within their own authorized fence.

The manifest pins every map, generator and local helper, runtime source and
parameter file, cohort, set list, audit and dev report. `DEV-REPORT.md` and
`DEV-REPORT.json` expose only development statistics. The full seed audit
includes ignored/untracked paths and metadata across prior sets, explicitly
excluding forbidden raw trees and package caches. This limitation is recorded.

The sealed cohort remains unopened until task 3.1. Twenty-four paired tests
at 150 seconds each would take one hour; this is a budget projection, pending
task 1.12's frozen dev-only calibration, not a measured sealed runtime.
