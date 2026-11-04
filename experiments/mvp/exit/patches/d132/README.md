D132 audit dependency epoch — staged design, NOT ACTIVATED

This patch does not delay the existing host resume. Nothing under live `src/`
or `tests/` was changed. Apply only at an authorized fresh-process boundary.

API: `snapshot()` fingerprints outcome dependencies; `process_snapshot()` retains
one immutable process-start capture; `assert_current(expected)` rejects drift;
`epoch_id(expected)` namespaces audit caches. Mixed runner/runtime/driver modules
use the AST closure of evaluation entry points and their referenced globals.
Training, re-verification, capacity and queue functions stay outside this audit
fingerprint. D129 full-source receipts and child admission remain unchanged.

Coverage includes decode/masks/layers, checker/physics/finisher, catalogue TOML,
fleet/port settings and declared checksum-pinned hull data, tokenizer/config,
package versions/build RECORD hashes and numerical/thread environment settings.
Adapter/base weights retain their existing separate planner content admission.
Cache identities additionally bind effective decode and finisher/check budgets.

The unified diff updates runner audit/baseline caches, audit-runtime entry/exit,
final-recipe identity, selection's optional audit-manifest argument, the owned
checker protocol and progress reporting. Parent/worker epoch equality is checked
before verification, followed by current-disk checks before and after.

D134 closes the innermost checker ACK gap. A plain JSON envelope carries the
expected epoch and unique request nonce before unpickling any Instance/MovePlan.
The child captures its loaded-start manifest before importing model/physics,
then returns loaded-start/end manifests and actual consumed source/resource
hashes. The parent requires exact manifest/nonce equality before accepting any
completed report, including physical failures used as preference evidence.

The single-threaded checker child temporarily validates SourceFileLoader's actual
source bytes and compiles them directly, bypassing stale pyc files. It buffers
validated primary file reads, preventing a change-and-revert from producing an
accepted hybrid project-source/resource result. Its mismatch exception derives
from BaseException. Missing/wrong ACK, drift and expiry remain unavailable,
never PASS or a negative preference label. Normal checker exceptions retain
`checker_exception`; expiry retains `watchdog_expired`, with ACK failure logged
separately. Non-audit calls keep `expected_outcome_epoch=None` and the existing
D124/D129 behavior. Full training receipt epochs remain unchanged.

AST module-initialization coverage includes class bodies, decorators, effectful
function defaults/annotations and assignment initializers, not only statements
such as `if` or `for`. Plain unused training function body changes stay outside.

APPROXIMATION: whole stowage and instruction/Spark package inventories are
conservative. The latter avoids ignoring optional imports from instruction
package initialization; no Spark call or spend is performed by this policy.
Future symbol-level narrowing could avoid invalidating audits for unrelated
instruction/Spark edits.

Remaining activation requirements:

- Run the full staged-overlay integration suite and a fresh audit smoke. The
  live tree still has its old all-source audit guard, so directly probing this
  new policy against unpatched runner code can correctly refuse its old
  `llm.exit.code_epoch` import. This package is a reviewed design patch, not a
  production-ready activation claim.
- The saved-plan physics ACK probe below passed. A host model/audit smoke is
  still required before activation; the ordinary transport fixture alone is
  not evidence about physics or model decoding.
- Import/read guards cover project modules loaded by SourceFileLoader and
  primary inputs consumed through Python open/Path readers. Installed native
  libraries and OS/native direct reads remain under build/source-freeze policy;
  this is not a sandbox against hostile process modification. Keep source frozen.
- Wheel RECORD hashes identify installed builds, not arbitrary tampering with
  installed package files. External model weight integrity uses the existing
  planner checks rather than repeatedly hashing huge shards here.
- Existing baseline bindings need an explicit reviewed migration or fresh audit;
  this patch does not silently reinterpret legacy all-source cache identities.

Validation: `UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1 uv run pytest experiments/mvp/exit/patches/d132/tests -q
-o 'addopts=--import-mode=importlib'` — 25 passed in 0.16 s.
`git apply --check experiments/mvp/exit/patches/d132/audit-outcome-epoch.diff` passed.
An isolated temporary copy with the four patched modules and new policy captured
305 real dependency pins in 2.095 s, CPU only. This tests fingerprint startup;
it is not a model decode or actual physics audit.

`UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1 uv run python
experiments/mvp/exit/patches/d132/verify_overlay_ack.py` runs the real owned-child
ACK protocol in an isolated code/config copy. The first successful probe bound
305 pins, imported 19 project modules, verified 302 resource reads and returned
matching loaded-start/end ACKs in 3.972 s (6.118 s total); MLX was not imported.
No live source, live tests or original evaluation artifacts are edited.

D134 saved-plan physics validation:

`UV_CACHE_DIR=$PWD/.uv-cache OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
VECLIB_MAXIMUM_THREADS=1 uv run python
experiments/mvp/exit/patches/d132/verify_overlay_ack.py --record
experiments/mvp/exit/deployment-d131/gen-000/reverification-d131/maps/0008/case-0000/winner.record.json
--output experiments/mvp/exit/patches/d132/physics-ack-probe.json`

Result: full checker PASS and exact input binding, weighted moves 8.0, nonce and
loaded-start/end manifest ACKs matched all 305 pins. The guarded child imported
85 project modules and verified 302 resource reads. Checker 25.889 s; complete
protocol 26.415 s; total isolated probe 33.825 s. Child peak RSS 606,355,456 bytes;
MLX was not imported and no GPU work ran. Primary source/config files and private
APFS copy-on-write cache clones were used; original artifacts were unchanged.
Estimate: 1 core, at most 4 GB and 90 s; actual: 1 core, 0.61 GB and 33.83 s.

Patch SHA-256:
`f5ed072b6b723a6c24543f2ee6130339d82e81af1c6f5c2efcfb124630c64147`.
