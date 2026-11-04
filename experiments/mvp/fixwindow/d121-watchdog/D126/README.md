# D126 gold watchdog variant — unapplied

This proposal supersedes **only the gold-driver** portion of the earlier D121
watchdog proposal. Its private expiry is `_WatchdogExpired(BaseException)` as
required by D126. All D121 candidate files, patch pins and receipts remain
historical and unchanged. Do not apply both gold variants.

Future target: `experiments/mvp/gold_tools/driver.py`. No live gold driver,
checker, smoke/lookahead/loop module or frozen runtime was modified. The parent
must first observe `FROZEN-D126`, confirm the producer is stopped, complete the
window, and explicitly release the gold-driver application step. This proposal
does not establish those gates or authorize an early application.

## Semantics

* The alarm raises a private `BaseException`, so ordinary `Exception`/`OSError`
  catches in checker/cache paths cannot consume it.
* The expiry latch and post-return monotonic check remain authoritative even
  if an unusual inner `BaseException` catch consumes the interruption or signal
  delivery is delayed. No wall cutoff is restored inside physical criteria.
* Nested scopes retain the earliest active limit, propagate an outer expiry
  and restore its remaining timer/handler without renewing the allowance.
  A legacy outer handler raising an ordinary exception is converted to this
  boundary's private expiry. Existing configuration budgets stay unchanged.
* The gold stage explicitly catches `(_WatchdogExpired, TimeoutError)`, clears
  any early `passed` flag, and records `unavailable`, `watchdog_timeout` and
  `operational_status='unavailable'`. Only then does it convert private expiry
  to operational `TimeoutError` for the existing outer worker logger. It never
  changes a returned physical report, margin, rule or tolerance.
* Ordinary unexpired errors and operator `KeyboardInterrupt`/other cancellation
  retain their meaning. The helper remains a main-thread, process-alarm API.
  A one-shot signal cannot guarantee prompt interruption of opaque native code;
  late return is nevertheless unavailable. Hard preemption remains an owned
  subprocess policy, not a new threshold invented here.

AST regression harnesses must compile the new `_WatchdogExpired` class before
`deadline()` and the nested stage. Besides the original `contextmanager`,
`signal` and `time` globals, `_WatchdogExpired` is the only new runtime global.
There is no shared global deadline stack. `PROPOSAL.json` pins the source,
candidate, patch and preserved historical D121 candidate hashes.

The direct diagnostic helper `gold_tools/probe_ballast_240.py` also calls
`driver.deadline()` outside the gold stage. After future application it must
explicitly handle the private `BaseException` if it needs to record a timeout;
its existing broad `except Exception` is insufficient. It is not patched here:
the newly extended fence permits the driver only. The normal gold publication
path is protected by the explicit stage conversion described above.

## Verification

`test_gold_watchdog.py`: **17 passed, no skips/errors/failures, 0.275 seconds**;
receipt `gold-helper-tests.xml`. It compiles candidate ASTs with fake clocks and
signals, without importing live project providers. Coverage includes both catch
hierarchies, swallowed/delayed expiry, expired failure classification, nested
limits/restoration, legacy outer handlers, unchanged ordinary cache fallback
metrics, stage conversion, cancellation, invalid limits and historical pins.

The separately coordinated cold_analysis D126 receipt is now **8/8 passed,
0.590 seconds**, no skips/errors/failures, at
`experiments/mvp/fixwindow/d126/watchdog-tests.xml`. Its four actual candidate
class/deadline/stage integrations pin the same candidate hash and cover normal
completion, direct alarm propagation through real checker cache/part paths,
an inner broad-BaseException catch, and delayed signal delivery. In the latter
two modes the synthetic physical part completes, but the outer stage still
records unavailable and rejects admission. The normal mode preserves acceptance;
ordinary damaged-cache fallback is separately retained. Evidence JSON is
`fixwindow/d126/watchdog-{normal,alarm,caught_base,delayed_delivery}.json`.

Those tests still use fake clocks/signals and a synthetic isolated part, not an
entire gold worker or numerical ship build. Do not substitute the older D121
integration, which used an Exception-derived alarm. No complete ship plan or
model inference is certified by these tests.

Exact executed helper command:

```sh
UV_CACHE_DIR=$PWD/.uv-cache UV_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
VECLIB_MAXIMUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
uv run --offline --no-sync pytest \
  experiments/mvp/fixwindow/d121-watchdog/D126/test_gold_watchdog.py \
  -q -p no:cacheprovider \
  --junitxml=experiments/mvp/fixwindow/d121-watchdog/D126/gold-helper-tests.xml
```

Read-only applicability check passed, exit 0:

```sh
git apply --check experiments/mvp/fixwindow/d121-watchdog/D126/gold-driver-d126.patch
```

Patch scope is one file, 67 insertions and 6 deletions: one class, one existing
watchdog helper, and its existing stage exception boundary. Recheck source pins
and applicability after the owner/window gate; no git state changed here.

Compute: one core, native threads one, no GPU/network/signals/corpus input or
numerical builder. Estimate below 1 GB and 60 s; actual pytest tool wall was
0.599 s, JUnit 0.275 s. Peak RSS was not sampled. All commands completed and no
owned background process remains.
