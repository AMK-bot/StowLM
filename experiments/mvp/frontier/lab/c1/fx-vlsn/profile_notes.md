# Exact scoring accelerator and differential evidence

`FastScorer(problem).score(actions)` returns an exact float in a guarded domain,
or `None` to request normal replay/rejection. The caller must retain its normal
feasibility mask and full finished-plan checker. This accelerator is not a
certificate of departure stowage legality. A faster score alone does not establish
fewer lifts or better passes.

The supported domain is one complete, unique `Place` per load-list box, initially
in the yard, no initial onboard boxes, no initial quay buffer or open covers, no
discharges, no active frozen stubs, and rule-freeze-v1 disabled. The activation
switch is checked again on every call, so activating crossing/port-refusal rules
cannot silently reuse legacy costs. Unsupported cases return `None`. Root's
search kernel falls back to `Problem.score` and independently replays every
archived candidate; the full shared scorecard remains the pass authority.

The model has two coupled graphs. Physical ship columns and hatch dependencies
form a directed graph from lower boxes to upper boxes and hold accesses to hatch
deck cargo; later-port lifts are upward closures of each port's discharge set.
The yard is a changing stack retrieval system, where removing a buried target
forces default-rule relocations. Yard relocations cannot be priced as a static
inversion count: a relocated load-list blocker can block another retrieval and
move repeatedly.

The implementation independently re-expresses the existing owner logic in
`actions/expander.py` (`yard_target` preference and lazy covers),
`moves/_physical.py` (bottom-up support, length fit and reachability), and
`moves/_projection.py` (D15 upward dependency closure after each port's boxes
are removed). It does not import downloaded algorithm code or change owners.
No new paper is needed for this engineering component; research citations for
the neighbourhood and recombination belong in the main NOTES.md.

Static slot geometry and candidate yard locations are preindexed. Yard cost is
cached by the exact box-loading order (8192 orders), since under the guarded
legacy policy destination slots do not affect yard relocations. Ship support,
hatch accesses and future closures are recomputed cheaply for each candidate.
This is an exact specialized score with memoization, not an incremental delta
update. Yard candidates are sorted by distance; once a legal clean destination
is found, only equal-distance ties can beat it, preserving the owner's existing-
stack-before-fresh-ground preference.

## Correctness

On official `quick_small`, nine of ten maps have stored passing starts. With
seed 612 and 180 attempted randomly selected baseline neighbourhood operations
per map, plus deliberate illegal mutations, **1333 non-None action sequences**
were compared against `Problem.score`: **zero score/legality mismatches** in the
supported complete-load domain. There were 857 exact numeric fast scores; 866
sequences were replay-legal because replay also accepts the deliberately omitted
last load (one per map), whereas the complete-load fast scorer declines it.
The remaining candidates exercise unsupported/infeasible cases including gaps,
slot collisions, wrong length, reversed orders, repeated actions and unknown
slots. Tests also explicitly activate rule-freeze-v1 and verify fallback.

Before official-set selection was available, a separate 408-candidate run on the
first four stored-pass allowed validation maps also produced zero mismatches.
This was used for profiling, not outcome tuning.

A separate check compares root's `CachedSequencer.order` with original
`alns.Sequencer.order` on 50 randomly generated physically valid complete
assignments per stored-pass quick_small map (450 total). The differential check
is also included in `test_fast_score.py` for every exact-fast-scored candidate.

## Interleaved unprofiled timings

These are mean milliseconds per attempted non-None sequence, including rejected
candidates, on a busy shared machine. Each candidate's fast call was immediately
followed by its original replay call; neither was profiled in this table.

| Map | Candidates | Exact fast scores | Replay ms | Fast ms |
| --- | ---: | ---: | ---: | ---: |
| random.small.40100011.0.8 | 137 | 83 | 25.642 | 0.407 |
| random.small.40100023.0.8 | 143 | 90 | 45.459 | 0.615 |
| random.small.40100036.0.2 | 140 | 87 | 42.176 | 0.724 |
| random.small.41100002.0.2 | 129 | 75 | 26.720 | 2.925 |
| random.small.41100018.0.2 | 144 | 90 | 39.494 | 0.774 |
| sim.small.40100010 | 141 | 89 | 23.655 | 0.605 |
| sim.small.40100025 | 169 | 117 | 39.039 | 0.800 |
| sim.small.40100046 | 162 | 112 | 36.853 | 0.416 |
| sim.small.41100007 | 168 | 114 | 36.360 | 0.815 |
| Candidate-weighted mean | 1333 | 857 | **35.307** | **0.869** |

The mixed-candidate score path was **40.6x faster** here. This is not a 40.6x
end-to-end search claim: sequencing, mask rejection and the finisher remain, and
unhandled cases still replay. Most maps were over 1000 evaluations/second for
this score path, but the slowest map was about 342/second. Caches were cold at
map creation and warmed naturally through the mutation sequence.

## Original score profile

The initial four-map differential run profiled only the original `Problem.score`:
408 calls, 13.010 seconds cumulative (cProfile changes absolute timing).

| Component | Cumulative seconds | Share of score time |
| --- | ---: | ---: |
| `ActionSpace.expand` | 10.052 | 77.3% |
| `apply_plan` | 2.912 | 22.4% |
| `parse_moves` canonicalization inside expand | 3.291 | 25.3% |
| Both `Expander` constructions inside expand | 3.145 | 24.2% |
| `yard_target` | 2.346 | 18.0% |

Rows overlap: the last three are inside expansion. Expansion canonicalizes
moves into actions, which repeats substantial expansion work; real objective
replay then validates and counts again. Catalogue context construction and
lookups are also visible. This supports specialized scoring as a useful part,
without claiming that the original small move neighbourhood is sufficient.

Reproduce from the repository root (one CPU-busy process):

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=src:experiments/mvp/frontier:experiments/mvp/frontier/lab \
nice -n 5 .venv/bin/python \
  experiments/mvp/frontier/lab/c1/fx-vlsn/test_fast_score.py \
  --set quick_small --maps 10 --candidates 180
```

Add `--profile` for the profile, or `--out evidence.json` for optional JSON output
inside this experiment folder. macOS sandbox priority adjustment was refused
(`nice: setpriority: Operation not permitted`); the command nevertheless ran.
Numerical thread limits were set and only one busy child process was run at once.
