# Two-layer freeze (D275, D277)

Installing or testing this folder freezes nothing. Freeze the pipeline when 2.1
is final, then freeze the film when the demo is final.

## Scope and identity

`--scope pipeline` is the default. It writes `manifest-pipeline.json` and pins
engine identity, src, pipeline code, reader, adapters, prompts, resources,
stored runtime inputs, recordings and evidence. It excludes `app/` and
`experiments/mvp/ui/` from files, source hashes, links and evidence. Import
discovery still traverses app entry points to find their runtime dependencies.
Launch verification uses the frozen closure; app/UI edits do not change it.

`--scope film` writes `manifest-film.json`. It pins app/UI and film evidence:
figure citations, fill alternatives, result inputs, story and hero caches. It
records the pipeline manifest's path, SHA-256 and tree digest in `pipeline_layer`.
That dependency must already verify as a frozen pipeline manifest, even for a dry
film build. Changed pipeline manifest bytes or pipeline file drift block film
build and launch. Film may inherit weight hashes from that verified layer.

Product outputs cite the pipeline manifest instead of being pinned by it. The
pipeline exclusions include `package/fixtures/hero-run/`, its published
`package/hero-run-anchor.json`, `package/fixtures/history/`,
`package/fixtures/failed-hero-build-*/`, `package/fixtures/.hero-build-*/`,
`sealed/runs/`, `sealed/results/`, `sealed/measurements/`, and 3.2's prefetch
outputs: `explain/fixtures/hero/`, `explain/fixtures/hero-inputs.json`,
`explain/fixtures/readiness.json`, `explain/fixtures/spend-snapshot.json` and
`explain/budget/`. Prepared recording outputs are also excluded:
`reader_final/readings/{readings.jsonl,queue.json,offline-timing.json}`,
`radio/readings.json`, `xray/fixtures/real-trace.json` and the isolated
`lattice/hero-coverage/` campaign outputs (including its store and state), plus
the published `lattice/store/` catalogue and plans. Coverage publication appends
to that store after freeze. Other mutable lattice roots are `backup-campaign/`,
`screening/`, `standard-plan-pool/`, `film-observer/`, `audits/`, `releases/`,
`runs/` and `archive/producer-sources/`. Generated lattice reports are also
excluded from pipeline scope: `progress.json`, `RULES.md`, `README.md`,
`film-analysis.json`, `hero-instruction-candidates.json`, `LATEST_RELEASE.json`,
`hero-impossible.json` and `hero-facts-close-bay-9.json`.
These paths are relative
to `experiments/mvp/pipeline/`. The adopted reader's identity in
`reader_final/readings/timing.json` stays pinned; a post-freeze recording must
not rewrite it (owner p12-reader).
`freeze/suites/` is also generated after the pipeline freeze. Film pins the
completed outputs, overriding the usual runs/measurement/cache exclusions.
Other delivered product bundles need a recorded `pipeline_sha256` matching the
pipeline layer's manifest SHA-256; any missing or mismatched identity blocks
film readiness. One receipt can bind sibling payloads whose bytes film hashes.
Lattice entries use D270 Z4: film compares every recorded engine manifest and
binding source with the verified pipeline layer's identities. Both must match
to label an entry "computed on the frozen code"; otherwise it is listed as
"computed on pre-freeze tree X", including missing identities. These historical
labels do not block film readiness. Film pins the entries and their provenance.
Retained package history and failed/temporary builds are diagnostics: film pins
their bytes and retains their historical provenance without requiring them to
claim the current pipeline hash. The published hero and its anchor remain
subject to the current hash check; historical receipts are never rewritten.
Development fixtures, the fixed `lattice/hero.json` selection, copilot replays
and existing x-ray runtime inputs remain pinned; code and imported modules are
never treated as product output, except historical copied producer snapshots.
The selection/control inputs `lattice/hero.json`, `manifest.json`,
`campaign-control.json` and `hero-wordings.json` remain fixed at pipeline freeze;
their producers must not change them afterwards. Entire `repo/` and
`repo-health/` trees, including audit scan scripts, are reports excluded from
both layers. A film citation cannot silently bring those reports back into pins.

Both scopes record sorted per-file SHA-256 and a source-tree digest over sorted
`path + NUL + sha256 + newline` entries. `created_utc` is the generated timestamp.
Read-only Git metadata is provenance. Retain final manifest digests outside the
editable tree. The review-fixes Engine 0.13.0 manifest is authoritative. Candidate
diffs are reported, never applied; D269 installs none. Private keys are excluded.
Only the package currently publishes its public signing half.

Replay links record `{link_text, resolved_target, target_sha256}`. Their final
targets must be regular files inside the repo and independently pinned. Link text,
target and target bytes are checked. Outside/unpinned targets, directory aliases
and import-closure links are refused. Safe target-link chains are allowed.

Runs, measurements, caches, bytecode, temporary trees and job logs are normally
excluded. Explicit evidence overrides those exclusions within its scope. The
freeze tool's code, docs and fixtures are pinned. Sealed inputs are never opened;
public sealed-harness code is included. Old fixtures retain their measured code
identity; API names and reported model revisions are listed. Replays remain
labelled "Recorded answer". Unresolved film citations block film readiness with
owner p22-demo; absent optional fallback inputs are reported separately.

## Freeze-time commands

From the repository root, when each layer is ready:

```sh
bash experiments/mvp/pipeline/freeze/freeze.sh --scope pipeline
# p111 regenerates the hero package against manifest-pipeline.json here.
bash experiments/mvp/pipeline/freeze/freeze.sh --scope pipeline --after-hero
bash experiments/mvp/pipeline/freeze/freeze.sh --scope film --pipeline-manifest experiments/mvp/pipeline/freeze/manifest-pipeline.json
```

The pipeline wrapper builds and verifies its manifest, then hands off to p111.
After p111 rebuilds the hero package, `--after-hero` verifies the pipeline and
the hero's recorded pipeline hash before rerunning both suites, then verifies
the pipeline again. The package tests therefore see the rebuilt package. This
resume never rebuilds or replaces the pipeline manifest. The film wrapper runs
both suites, builds its manifest and invokes the launch guard. Both modes refuse
concurrent work on the same scope; a fresh build refuses replacement.
`--print-commands` previews the exact sequence without executing it. Pipeline is
the default scope for both the wrapper and builder.

Suite XML/counts are isolated under `suites/pipeline/` and `suites/film/`; neither
is pinned by the pipeline manifest. Before each suite, admission
waits for popcount(mask) >= 8, load1 <= 45 and reading age below 120 seconds.
Missing, invalid or busy readings retry. Both suites retain the repository marker
policy; the second runs after first-suite failure. Actual failures and exit codes
are retained. Xfails are separate from skips; generic JUnit cannot identify
non-strict xpasses. A written manifest does not mean the suites passed.

Legacy sealed 900-second and generic `runs/` restrictions were superseded by
p112's guarded pipeline-layer protocol and exact D265 profile checks; they do
not block this freeze or the supported sealed launcher.

## Launch verification

D284's third pipeline manifest pins `runtime-outputs.json` and its runtime
directory policy. The initial directory is
`experiments/mvp/frontier/engine/peel_ladder/assignment-ballast/`; development
run observations may add reviewed, narrow cache/scratch directories. The
builder excludes their contents in both scopes. Verification ignores only
unpinned, non-source files added below the manifest's allowed directories.
Previously pinned files remain checked for changes and removal, including
files inside a newly allowed directory. Added Python source is always drift,
including under nested cache directories. Imported source, unsafe paths and
symlink aliases cannot be allowed as runtime output. An older manifest never
inherits a later policy expansion. The leader builds the real v3 manifest;
editing or testing this tool does not replace it.

p112's D284 registry was reviewed and merged at 08:08 EST on 2 November 2026.
The four admitted directories are in `runtime-outputs.json`: assignment-ballast,
`pipeline/sealed/cache`, `pipeline/sealed/runtime-cache`, and the narrow
`pipeline/sealed/measurements/d284/discovery` subtree. Broad `pipeline/sealed/`
and the D284 parent were rejected because they contain source; staged imported
`.py.txt` snapshots remain outside the discovery exclusion. Private-key
directories retain their separate exclusions. `runtime-outputs-review.json`
records every decision and the discovery report's SHA-256; every observed
nonprivate written file is covered without admitting those source parents.

The public signature remains
`require_frozen(manifest_path, *, root=None, full_weights=False)`:

```python
from experiments.mvp.pipeline.freeze.verify import FreezeDrift, require_frozen

# Sealed, stored plans and showcases use the pipeline layer.
identity = require_frozen('experiments/mvp/pipeline/freeze/manifest-pipeline.json',
                          full_weights=True)
# Film mode uses the film layer and also verifies its pipeline dependency.
identity = require_frozen('experiments/mvp/pipeline/freeze/manifest-film.json')
```

The return is `{manifest_sha256, tree_digest}` for the selected manifest.
`FreezeDrift(RuntimeError).drifts` lists refusals. The guard rejects dry, unready,
unsupported or drifted manifests. Lower-level `verify(path)` returns drifts and
also supports dry inventories. Film verification checks its pipeline dependency's
exact manifest digest, tree digest and current files.

Default checks hash source/evidence bytes and stat weights for existence, size
and mtime_ns; differences fail. Full weight checking rehashes all weights and
propagates to a film layer's pipeline dependency. A pipeline build hashes every
weight fully. No verification loads models, searches, calls a network or runs a
suite. Write `manifest_sha256` into `pipeline_sha256` of every receipt. Regenerate
the hero package after the freeze. Manifest-file hashes and tree digests differ.

## Safe preparation

Run only this folder's fake-tree tests now:

```sh
UV_CACHE_DIR="$PWD/.uv-cache" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 uv run --offline --no-sync pytest experiments/mvp/pipeline/freeze -q -p no:cacheprovider --basetemp=experiments/mvp/pipeline/freeze/unit-tmp
```

For a nonfinal inventory, use `build.py --scope pipeline --no-weights --out` with a
fresh path inside freeze, then `verify.py` on that path. Dry artifacts cannot use
either final filename or serve as film dependencies. Outputs are contained inside
this folder. D270's unresolved intake gate must be explicitly adopted or cut.
