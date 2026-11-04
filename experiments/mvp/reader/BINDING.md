# Frontier 0.9 under a sentence — D199 route 3

Completed 20 preregistered constrained runs on validation maps only, five per constraint type. **16/20 freshly audited passes; 16/20 real binding replans.** This cohort was deliberately selected by inspecting stored baseline violations and necessary capacity screens. It is a binding demonstration, not a blind success-rate estimate.

Muse Spark parsed all 20/20 literal sealed sentences exactly in their actual ship contexts before planning began. **Spend: $0.02685175 / $1**, 20 calls, all reconciled to the authoritative shared ledger and its managed `docs/compute.md` row. No training paraphrases, reader tuning, real company data, `src/` edits, git commands, or repair were used.

The baseline is the stored best passing Frontier 0.9 plan, freshly checked twice here; no new unconstrained planner runs were spent. That comparator already includes a port-blocked start; the paired rows show observed costs with the hard instruction added. Each case makes at most one constrained portfolio call. Search uses the baseline’s stored search allocation, a 120-second constructor cap, the same V4 finisher with 300 seconds per candidate, and the full checker. There are two map workers, one native CP/finisher worker each; load threshold 128 (≥36). Per-case claims prevent automatic retries of failed or interrupted planner attempts.

The reader driver binds hybrid’s existing `plan_and_check` function code to private dependency globals; it imports and reuses Frontier’s constructors, master, ALNS, sequencing, finisher, and checker. Every admitted start and search candidate is checked against the instruction. Constructor masks and the copied master static table intersect the original legal domains; every finished result is re-audited. A separate fresh process then repeats the full checker, physical move replay, completion and instruction-filtered mask replay.

**Shared-source change:** another executive updated hybrid’s provenance telemetry during this run. The observed boundary is old telemetry through `max_tiers-01`, new telemetry from `max_tiers-02`. Independent read-only review found the constructor/master/start/search/ranking/finishing policy unchanged for our empty-extra-start calls. New score-witness bookkeeping and final contribution replay preserve the existing constrained score/gates but can change wall-clock-bounded iteration counts. This is not a single byte-frozen implementation cohort. The new contribution dependency was not in the original preregistration; old/new hashes, the review, and observed source snapshots are archived in `runs/binding/upstream-change-review.json` and `runs/binding/source-observed/`. No parser, compiler, constrained portfolio, or Frontier solver changes were made in response to outcomes; binding claims rest on the fresh audits.

**Cohort-screen limitation:** the original global capacity screen missed per-box DG exclusions. Post-run geometry diagnostics prove `dg_keep_away-02`, `-03`, and `-05` leave a required 40-foot class-8 box with zero allowed physical paired footprints. These are infeasible requests on those cargo slices, not evidence of planner quality, and cannot count as binding successes. `runs/binding/cohort-diagnostics.json` records every examined footprint and blocker. The future selector now checks each actual box and refuses overwriting the frozen cohort. The original 20 cases were neither replaced nor rerun; four regression tests cover this correction.

The parser interface is `parse(sentence, context) -> (validated_answer, evidence)`. The public entry point is `instruction_planner.plan_sentence(instance, sentence, parser=...)`. Muse Spark supplies that answer today; a Glimmer reader can replace it without changing the compiler or planner. The compiler sees only the parsed structure, never an expected label. Refusals do not execute.

OnePortPerHatch derives a strict cover→POD mapping from the instance using physical-cover selectors on Frontier’s unchanged static master. Every footprint cell, hold and deck, obeys the mapping; retained cargo fixes its covers. A static packing witness is not a checker certificate. A mapping mismatch alone does not make a baseline violate the sentence: binding is judged by actual mixed discharge ports on a physical cover. Each case uses one instance-derived allocation; no mapping sweep or global-optimality claim is made. Any observed cost increase is not a proven minimum penalty of the sentence.

A **binding** result requires a freshly passing baseline that intrinsically violates the sentence, a changed container-move sequence, and a newly passing constrained plan with an independent successful audit (D166/D170). Ballast metadata changes alone do not count. Failures and unchanged/nonbinding cases remain below.

| Case / validation map | Sentence | Spark constraint | Before moves | After moves | Pass | Fresh audit | Binding | Wall s |
|---|---|---|---:|---:|---|---|---|---:|
| close_bay-01 / `random.tiny.20000032.0.8` | Bay 01 is closed to all loading until further notice. | `{"type":"close_bay","bay":1}` | 8.0 | 8.0 | yes | PASS | yes | 393.9 |
| close_bay-02 / `random.tiny.20000044.0.5` | Bay 01 is closed to all loading until further notice. | `{"type":"close_bay","bay":1}` | 8.0 | 8.0 | yes | PASS | yes | 397.1 |
| close_bay-03 / `sim.tiny.20000017` | Bay 01 is closed to all loading until further notice. | `{"type":"close_bay","bay":1}` | 8.0 | 8.0 | yes | PASS | yes | 452.3 |
| close_bay-04 / `random.tiny.20000038.0.2` | Bay 01 is closed to all loading until further notice. | `{"type":"close_bay","bay":1}` | 7.0 | 7.0 | yes | PASS | yes | 452.5 |
| close_bay-05 / `random.tiny.20000008.0.5` | Bay 01 is closed to all loading until further notice. | `{"type":"close_bay","bay":1}` | 6.0 | 6.0 | yes | PASS | yes | 295.5 |
| max_tiers-01 / `random.tiny.20000032.0.8` | Zero tiers in the hold of bay 3 — nothing below the hatch there. | `{"type":"max_tiers","bay":3,"zone":"hold","max_tiers":0}` | 8.0 | 8.0 | yes | PASS | yes | 411.9 |
| max_tiers-02 / `random.tiny.20000044.0.5` | Zero tiers in the hold of bay 3 — nothing below the hatch there. | `{"type":"max_tiers","bay":3,"zone":"hold","max_tiers":0}` | 8.0 | 8.0 | yes | PASS | yes | 439.3 |
| max_tiers-03 / `sim.tiny.20000017` | Zero tiers in the hold of bay 3 — nothing below the hatch there. | `{"type":"max_tiers","bay":3,"zone":"hold","max_tiers":0}` | 8.0 | 8.0 | yes | PASS | yes | 440.9 |
| max_tiers-04 / `random.tiny.20000038.0.2` | Zero tiers in the hold of bay 3 — nothing below the hatch there. | `{"type":"max_tiers","bay":3,"zone":"hold","max_tiers":0}` | 7.0 | 7.0 | yes | PASS | yes | 481.7 |
| max_tiers-05 / `random.tiny.20000008.0.5` | No containers on deck in bay 2. | `{"type":"max_tiers","bay":2,"zone":"deck","max_tiers":0}` | 6.0 | 8.0 | yes | PASS | yes | 394.2 |
| dg_keep_away-01 / `sim.tiny.20000005` | Class 3 — sorry, class 8 — one bay from bay 1 at minimum. | `{"type":"dg_keep_away","imdg_class":"8","min_bays":1,"away_from":{"kind":"bay","bay":1}}` | 4.0 | 4.0 | yes | PASS | yes | 512.7 |
| dg_keep_away-02 / `random.small.40100048.0.2` | Class 3 — sorry, class 8 — one bay from bay 1 at minimum. | `{"type":"dg_keep_away","imdg_class":"8","min_bays":1,"away_from":{"kind":"bay","bay":1}}` | 7.0 | — | no | FAIL | no | 346.7 |
| dg_keep_away-03 / `sim.small.40100022` | Class 3 — sorry, class 8 — one bay from bay 1 at minimum. | `{"type":"dg_keep_away","imdg_class":"8","min_bays":1,"away_from":{"kind":"bay","bay":1}}` | 4.0 | — | no | FAIL | no | 341.7 |
| dg_keep_away-04 / `random.small.40100003.0.2` | Class 3 — sorry, class 8 — one bay from bay 1 at minimum. | `{"type":"dg_keep_away","imdg_class":"8","min_bays":1,"away_from":{"kind":"bay","bay":1}}` | 12.0 | 12.0 | yes | PASS | yes | 467.4 |
| dg_keep_away-05 / `sim.small.40100041` | Class 3 — sorry, class 8 — one bay from bay 1 at minimum. | `{"type":"dg_keep_away","imdg_class":"8","min_bays":1,"away_from":{"kind":"bay","bay":1}}` | 8.0 | — | no | FAIL | no | 354.5 |
| one_port_per_hatch-01 / `sim.small.40100046` | One port per hatch. | `{"type":"one_port_per_hatch"}` | 9.0 | 10.0 | yes | PASS | yes | 423.9 |
| one_port_per_hatch-02 / `sim.small.40100030` | One port per hatch. | `{"type":"one_port_per_hatch"}` | 4.0 | 4.0 | yes | PASS | yes | 497.3 |
| one_port_per_hatch-03 / `random.small.40100044.0.8` | One port per hatch. | `{"type":"one_port_per_hatch"}` | 18.0 | 18.0 | yes | PASS | yes | 334.7 |
| one_port_per_hatch-04 / `random.small.40100040.0.5` | One port per hatch. | `{"type":"one_port_per_hatch"}` | 15.0 | — | no | FAIL | no | 108.4 |
| one_port_per_hatch-05 / `sim.small.40100002` | One port per hatch. | `{"type":"one_port_per_hatch"}` | 10.0 | 9.0 | yes | PASS | yes | 411.5 |

Wall time includes parsing, software allocation, planning and the fresh audit; runs share a busy machine, so these are observations rather than matched speed comparisons.

## OnePortPerHatch move effect

4/5 hatch cases passed the independent audit; 4/5 meet the binding criterion.

- `sim.small.40100046`: 9 → 10 weighted moves (+1); 10 final container placements changed; later restows 0, later hatch lifts 4.
- `sim.small.40100030`: 4 → 4 weighted moves (+0); 11 final container placements changed; later restows 0, later hatch lifts 2.
- `random.small.40100044.0.8`: 18 → 18 weighted moves (+0); 12 final container placements changed; later restows 0, later hatch lifts 4.
- `random.small.40100040.0.5`: no passing constrained plan; No constrained plan passed the driver.
- `sim.small.40100002`: 10 → 9 weighted moves (-1); 13 final container placements changed; later restows 0, later hatch lifts 4.

On these 4 mutually passing cases, total moves are 41 → 41 (+0.00%; means 10.25 → 10.25). The strict port rule reaches the checked frontier plan through a sentence Spark read. This establishes the language-to-constraint contribution on passing instructed plans; it does not attribute software’s allocation or search skill to the LLM, or establish a population saving.

The failed hatch case `one_port_per_hatch-04` obtained an OPTIMAL static allocation, but both constructors reached dead ends and the deck-4 master hit its time limit after cuts; lower tier attempts were infeasible. No admissible start emerged. This is a portfolio failure under the one chosen allocation, not a proof that the sentence is globally infeasible. No alternative allocation or extra replan was tried.

## Case audit details

- **close_bay-01**: baseline violations 1; changed final placements 1; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **close_bay-02**: baseline violations 1; changed final placements 1; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **close_bay-03**: baseline violations 1; changed final placements 1; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **close_bay-04**: baseline violations 1; changed final placements 1; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **close_bay-05**: baseline violations 2; changed final placements 2; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **max_tiers-01**: baseline violations 2; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **max_tiers-02**: baseline violations 2; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **max_tiers-03**: baseline violations 2; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **max_tiers-04**: baseline violations 2; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **max_tiers-05**: baseline violations 1; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **dg_keep_away-01**: baseline violations 1; changed final placements 3; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **dg_keep_away-02**: baseline violations 1; changed final placements —; mask not run, complete not run, checker not run, all loaded not run. Failure: No constrained plan passed the driver
- **dg_keep_away-03**: baseline violations 1; changed final placements —; mask not run, complete not run, checker not run, all loaded not run. Failure: No constrained plan passed the driver
- **dg_keep_away-04**: baseline violations 1; changed final placements 6; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **dg_keep_away-05**: baseline violations 1; changed final placements —; mask not run, complete not run, checker not run, all loaded not run. Failure: No constrained plan passed the driver
- **one_port_per_hatch-01**: baseline violations 2; changed final placements 10; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **one_port_per_hatch-02**: baseline violations 2; changed final placements 11; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **one_port_per_hatch-03**: baseline violations 1; changed final placements 12; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.
- **one_port_per_hatch-04**: baseline violations 1; changed final placements —; mask not run, complete not run, checker not run, all loaded not run. Failure: No constrained plan passed the driver
- **one_port_per_hatch-05**: baseline violations 1; changed final placements 13; mask True, complete True, checker True, all loaded True. No instruction/allocation violations.

Original execution commands, from repository root (do not repeat the 20-case campaign). The source guard intentionally refuses a new run after upstream source drift; the final command regenerates this report entirely offline from completed artifacts:

```sh
TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.run_binding prepare
TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 STOWAGE_IN_PORT_WORKERS=1 UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.run_binding run --load-limit 128
UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -m experiments.mvp.reader.binding_report
```

Evidence: `binding-cases.json`, `runs/binding/preregister.json`, per-case parser/plan/audit JSON, `runs/binding/spend.json`, `runs/binding/verification.json`, `binding-tests.log`, `binding-pipeline-tests.log`, `binding-interface-tests.log`, `binding-cohort-tests.log`, `binding-final-tests.log`, and `binding-test-collection.log` (59 tests pass in the final complete reader suite, including concurrent GPU-harness unit tests). Literal sealed sentences were used for this instructed planning demonstration only; no reader training or hyperparameter/prompt selection used them.
