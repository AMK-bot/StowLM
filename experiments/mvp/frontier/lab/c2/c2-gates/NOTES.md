# Cycle 2 gates

This experiment changes neither feasibility rules nor the checker. The two
oracles narrow work before the normal full-check acceptance gate. No sealed
input was used. All code and evidence are confined to this folder. Python
bytecode was disabled and every numerical thread setting was one. Two CPU slots
were used for sustained measurements. There was one brief protocol deviation:
the generation-only inventory started while pytest was still finishing, alongside
the screen differential. Both jobs had completed before the requested inventory
interrupt arrived. No search or ladder timing overlapped that inventory, and
remaining launches were serialized against the two-slot limit.
`nice -n 5` was requested; the sandbox
reported that priority adjustment was not permitted and continued execution.

## Model and source

Cargo placements label a directed graph of slot supports and hold-to-hatch-deck
precedence. Lifts are physical yard retrieval relocations, hatch operations,
and projected later-port dependency closures. A swap generally changes labels
without changing that graph. Exact action prefixes also identify immutable
feasibility-engine states. See `MASK_NOTES.md` and `results/mask_graphs.json`
for a real small/medium graph census and the equivalence argument.

David J. Pearce and Paul H. J. Kelly, *A Dynamic Topological Sort Algorithm for
Directed Acyclic Graphs*, ACM JEA 11, 2006 (some bibliographies list 2007):
https://www.doc.ic.ac.uk/~phjk/Publications/DynamicTopoSortAlg-JEA-07.pdf .
Their method updates the affected order interval after graph-edge changes.
Same-slot swaps here leave support/hatch edges unchanged, and C1 already caches
that topology. We therefore retain the existing cached topological ordering
rather than implement an unnecessary dynamic update. A dirty-stack-only mask
would be unsound: completion capacity and dangerous-goods constraints can
couple untouched regions. The implemented singleton evaluator still executes
the owner's applicable global completion tests.

`search_kernel.py` copies C1 `fx-vlsn/search.py`, adding injected mask and optional
fixed-iteration stopping/cooling. It imports C1's unchanged `fast_score.py`.
`ladder_kernel.py` copies C1 `fx-scout/method.py`, renaming `propose` to `run`,
changing its sibling import path, and recording construction-gate counts.
`screen.py` copies the D57 sample context construction from checker/full.py and
calls public rule functions; it never supplies a passing shortcut. Both method
adapters call the mandated C1 `leader/e9fix.install()`.

## Measurements and gates

The mask differential compares every result to the real `_mask_ok`, including
rejections and explicit unsupported-domain fallback. PrefixMask assumes an
unchanged Problem/instance during its lifetime. It guards engine identity/root,
configuration, native engine type, global-screen state, rule-freeze state,
active frozen overrides, complete unique pure loads, and no discharges.

For search, `method.py:reference` and `method.py:propose` receive identical
ordered stored starts, seed 0, 200 iterations, and iteration-based cooling.
They both respect a generous 600-second wall cap; the recorded iteration count
must reach 200 for a fixed-work comparison. This measures the same search work
rather than equating a shorter time budget with identical search. Candidate
hashes and decisions are compared. The copied C1 inherited-ballast finishing
adapter avoids introducing a new ballast-search comparison. `lab/score.py`
still independently checks each accepted plan. `method.py:live` retains the
original wall-budget search behavior.

For the medium ladder, `medium_pair.txt` contains the first two official
`quick_medium` inputs, 88 and 162 boxes. Both arms run through `lab/score.py`.
`ladder.py` changes only its own copied kernel's diagnostic function and stores
each rung plan and diagnostic duration in `results/ladder-transcripts/`.
The partial diagnostic preserves every predicate used by the ladder's learning
function and its all-iscode test. If omitted diagnostics could affect those
predicates, or a plan might pass, it falls back to the unchanged full checker.
Raw partial rule lists can omit strength-only IDs; see `SCREEN_NOTES.md`.
It stops later voyage samples of a part only after that part's relevant positive
learning flag has become permanent; proving a flag absent still needs all samples.

Complete replay-legal stows and full-checker passes are reported separately.
Existing stored 0.9.0 has no measured row for the two medium maps, so their paired
comparison is against the fresh full-diagnostic scout reference, not an invented
0.9.0 failure. Timing profiles are diagnostic only; speed factors use unprofiled
paired runs. Results and verdict are recorded below after verification.

## Reproduction

Run from the repository root with `PYTHONDONTWRITEBYTECODE=1`,
`OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1`, and
`PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab`.

```
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c2/c2-gates/test_mask.py
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/c2/c2-gates/benchmark_screen.py --mode differential --count 200
nice -n 5 .venv/bin/python experiments/mvp/frontier/lab/score.py --set quick_small --method experiments/mvp/frontier/lab/c2/c2-gates/method.py:reference --finish experiments/mvp/frontier/lab/c2/c2-gates/method.py:finish --budget-s 600 --finalize-s 60 --workers 1 --out experiments/mvp/frontier/lab/c2/c2-gates/results/search-reference
```

Repeat the scorecard command with `:propose` and `results/search-fast`.
For the ladder use `--set experiments/mvp/frontier/lab/c2/c2-gates/medium_pair.txt`,
`ladder.py:reference`/`:propose`, `ladder.py:finish`, `--budget-s 300`,
`--finalize-s 60`, and `results/ladder-reference`/`results/ladder-fast`.
Run `compare_results.py` for the paired summary.

## Verified small search and safety results

`results/comparison.json` records the complete `quick_small` scorecard pair.
Both arms produced complete replay-legal candidates on 9/10 maps and full-checker
passes on 9/10. All nine candidate-bank hashes, all search decision counts, and
all final passes/lifts agree. Both fixed-work arms achieved 13.667 mean paired
lifts, against 0.9.0's 13.889 (two wins, seven ties, zero losses). This is a search
neighborhood effect shared by both arms, not a lift improvement from the mask.
Reference proposal time totals 279.132 s; accelerated proposal time totals
154.468 s: **1.807×**. Whole scorecard time is 397.040 versus 252.839 s:
**1.570×**. Thus the measured small search misses the 3× keep threshold.

The mask has zero disagreements across 2,220 attempted comparisons, covering
1,107 distinct map/action inputs; duplicates are disclosed in
`results/mask_inventory.json`. The additional fallback and freeze assertions
are counted separately. The screen has zero false `fail` results across 200
attempts (92 distinct inputs): 19 stored plans and 181 perturbations. All 30
full-checker passes in that corpus returned `unknown` from the screen. The
rejection-heavy corpus's gate-plus-full-for-unknown paired-call estimate is 2.87× faster,
not a medium ladder claim. Full profiles cover both 88- and 162-box plans.

All 36 tests passed: 25 shared scorecard tests plus 11 screen safety, fallback,
learning saturation and condition-prefix tests. The actual checker rejected
the intentionally illegal finished-plan integration case. No acceptance rule
or tolerance was altered. No source, shared helper, or sealed input was changed.

## Verified medium ladder

Both fresh arms ran the same copied scout ladder, seed 0 and nominal 300-second
proposal / 60-second finishing allowances on `medium_pair.txt`:

| Map | Complete replay-legal stow, reference / partial | Full-check passes, reference / partial | Lifts, reference / partial | Whole wall seconds, reference / partial |
| --- | --- | --- | --- | --- |
| 88 boxes | yes / yes | yes / yes | 121 / 121 | 342.998 / 335.399 |
| 162 boxes | yes / yes | no / yes | — / 184 | 347.905 / 353.430 |

Paired mean lifts are **121 versus 121** on the one mutual pass. Complete-stow
map counts are 2/2 in both arms; full-check pass counts are 1/2 and 2/2. The
162-box reference ends with no accepted candidates; the partial arm reaches a
further rung and independently passes at 184. This is one wall-budgeted paired
run, not evidence that the partial gate changed any acceptance predicate.
No 0.9.0 medium baseline is measured. Historical C1's 79-lift 88-box result was
not reproduced in this fresh pair; do not substitute that unpaired number.

Proposal totals are 666.895 versus 662.787 s (**1.006×**), and whole scorecard
totals are 690.903 versus 688.829 s (**1.003×**). The copied constructor uses
cooperative deadlines: proposal times overrun the nominal 300-second allowance
by 12–51 seconds here. Those overruns are included, never censored away. Both
arms preserve the original constructor's budget handling. Time-based work and
shared-machine timing can affect which rungs finish, so the extra pass needs
replication before attributing it to a stable algorithmic advantage.

`results/screen-ladder-paired.json` separately replays the seven reference rung
plans using full/partial/partial/full calls. All verdicts and every learning
predicate agree; zero mismatches. The aggregate diagnostic-call ratio is
**3.237×**, with public rule functions, native full fallback on possible passes,
and source-pinned omission rules. It is an AB/BA mixed-cache measurement, not
3.237× whole-ladder speed and not a replacement for the scorecard pair.

**Verdict:** keep the fail-only/exact-learning gate as a measured component and
the equivalence evidence; do not claim the requested 3× search or whole-ladder
speedup. Small search reaches 1.807× and whole medium ladder 1.003×. The next
target is the residual mask work on the slow 22-box map and constructor/tail
work on medium, rather than more topological-order caching. The mask wrapper
and live adapters remain experimental, with their explicit fallback domains.

## Residual mask profile

`profile_fast.py` profiles only the accelerated mask calls in the identical
200-iteration search on `random.small.41100002.0.2`. Its candidate-bank hash
matches the unprofiled arm. Of 50.656 profiled mask seconds, **48.730 s (96.2%)**
are final E6 checks, via 288 public `placement_violations` calls. Those repeated
calls spend 27.548 s in D42 background-capacity validation and 19.332 s in
background-evidence validation (nested costs overlap); canonical vessel loading
alone accounts for 17.635 s. C1 completion consumes only 1.210 s here.

This resolves the failed small-map speed hypothesis: after domain reduction,
the remaining wall is repeated validation of largely unchanged vessel/background
evidence inside final-placement E6 probes, rather than topological ordering or
the number of trial pairs. These checks also include candidate-dependent planned
stack masses, so caching their entire verdict by vessel identity would be
unsound. A next experiment should split immutable evidence parsing from those
changing mass checks in a guarded mask-only implementation, with another full
differential. This experiment does not monkeypatch those checker functions.
Artifacts: `results/mask_fast_profile.{json,txt,pstats}`.
