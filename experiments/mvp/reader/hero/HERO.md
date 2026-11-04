# v5 3.2 prep: hero sentence and map

Status: **complete**. 5/5 fixed shortlisted cases have new fresh-process results; 5 pass every binding/visibility gate at no more than two extra weighted nonproductive lifts. Spark spend: **$0.01449300**, restart-safe ceiling $1. This is a deliberately selected demonstration, not an unselected success-rate or language-accuracy benchmark. Spark is the reader here; final Glimmer-reader rehearsal remains task 3.2.

Criteria were fixed in `CRITERIA.json` / `criteria-freeze.json` before inventory, shortlist, calls or checks. The shortlist was then frozen with independent intended constraints in `manifest.json` before Spark read it. At most five map/sentence pairs; failures stay reported, with no replacement or repair. Only validation/train inputs are allowed; this inventory found its archived candidates in named validation runs, not held-out test or test-v3 data.

## Recommended hero

**“Keep the corrosives one bay clear of bay one.”**

Map: `random.small.40100003.0.2` — `experiments/mvp/corpus/val/small/feeder_800/random_yard/seed-040100003.json` (SHA256 `0ef1a72eaad061cf96cef568bcfbecd575e4e74dd1e44c3b59b574247bd8c981`).

**12 → 12 weighted nonproductive lifts (+0)**. The freshly checked stored before plan breaks the independently fixed instruction. The after plan passes the full checker, parsed-rule and intended-rule audits, move replay and complete instruction-aware mask replay. It changes **6 loaded-box final placements**; **1 matching DG box changes physical bay**. The reader's structured output exactly matches the frozen intended rule; the full checker alone cannot detect a language misreading.

- Highlight `QZXU0000122` (20 ft, IMDG 8): `0010282` → `0030482`, physical bays [1] → [3]; 1 twenty-foot bay pitch.

Show the sentence, then the exact structured reading, then the changed box and the prohibited bay zone. Bay 01 → 03 is **one** twenty-foot bay, not two. Label the plan recorded and the ship/port synthetic. The current reader label is Muse Spark; a final on-device Glimmer label requires rereading this same sentence with that adapter and checking its exact intended interpretation.

Before/after assets: `experiments/mvp/reader/hero/runs/plans/hero-01.json`; new audit: `hero/runs/audits/hero-01.json`. The after plan was reused from the same-constraint recorded replan, not newly searched for this wording. The new sentence was read by Spark and its meaning and saved plans were rechecked in a new process.

## Fixed shortlist: all outcomes

| Case / map | Sentence | New Spark exact read | New full checks before / after | Binding + visible | Before → after lifts | Δ | Source / failure |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| hero-01 / random.small.40100003.0.2 | Keep the corrosives one bay clear of bay one. | True | True / True | True | 12 → 12 | 0.0 | reused_recorded_constrained_plan |
| hero-02 / sim.tiny.20000005 | Keep the corrosives one bay clear of bay one. | True | True / True | True | 4 → 4 | 0.0 | reused_recorded_constrained_plan |
| hero-03 / random.tiny.20000008.0.5 | Do not load any containers in bay 1. | True | True / True | True | 6 → 6 | 0.0 | reused_recorded_constrained_plan |
| hero-04 / random.tiny.20000032.0.8 | Do not load any containers in bay 1. | True | True / True | True | 8 → 8 | 0.0 | reused_recorded_constrained_plan |
| hero-05 / random.tiny.20000038.0.2 | Do not load any containers in bay 1. | True | True / True | True | 7 → 7 | 0.0 | reused_recorded_constrained_plan |

## Wall time: do not merge these clocks

The historical planner component is the archived instruction-aware Frontier/finisher/checker time. Historical worker wall includes loading, before/after checks and orchestration. Neither is a fresh timing of this new wording. New audit wall is the new process's work including checks; new execution sum is new parse plus new audit. Reused candidates have **no new replanning run**. A final live replan needs its own quiet-machine end-to-end timing; do not promise that an archived component sum is live latency.

| Case | Historical planner component (s) | Historical worker wall (s) | New Spark brief read (s) | New replan | New fresh audit (s) | New execution sum (s) |
| --- | ---: | ---: | ---: | --- | ---: | ---: |
| hero-01 | 313.580 | 368.960 | 3.308 | not run (reuse) | 108.558 | 111.866 |
| hero-02 | 319.176 | 400.871 | 3.250 | not run (reuse) | 129.220 | 132.471 |
| hero-03 | 181.203 | 264.390 | 3.558 | not run (reuse) | 115.875 | 119.433 |
| hero-04 | 274.556 | 319.917 | 3.012 | not run (reuse) | 115.964 | 118.976 |
| hero-05 | 297.360 | 378.457 | 2.827 | not run (reuse) | 115.008 | 117.836 |

## Evidence and execution limits

The intended rule is fixed independently before reading and never put in Spark's prompt. A refusal, malformed answer or wrong structured meaning fails the hero gate; it is not corrected or replaced. The saved before plan must actually violate the intended instruction. The saved after plan must freshly pass both the physical full checker and the intended/parsed semantics; container movement signatures and loaded placements must change. Ballast-only or order-only changes cannot qualify. Every candidate's signed lift delta stays visible, including expensive or failed candidates.

Selection openly uses historical success, cost and movement outcomes to find a clear demo. Candidates with an existing same-constraint checked after plan are preferred to avoid unnecessary searches. This does not establish a general reader benefit, a new planner saving, or a new end-to-end replan under the new wording. Static screens are necessary only. The full checker checks the plan against physical rules and the rule as read; exact comparison to the separately fixed intended constraint is what detects a wrong parse here.

One native-one hero worker runs at a time, in addition to the untouched two-worker cohort controller (the worker-scope assumption was declared in the criteria). Each new hero audit/solve obeys D206: leader core-state mask bit count ≥8, load1 ≤45, age <120 seconds. Requests, output, parser/model identity and adapter hash (null for Spark) are stored in `runs/parser/`; admitted readings, fresh PIDs and checks are in `runs/audits/`. No repair, no retry of an unreturned attempt, no unconstrained fallback. The two cohorts remain running; their files and controller are not edited.

Reproduce from the repository root:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.hero.run prepare
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python -m experiments.mvp.reader.hero.run run
.venv/bin/python -m experiments.mvp.reader.hero.report
```

Credentials use the existing external `.env` loader into process environment only. No key is passed as an argument or placed in hero artifacts. All authored files for this task are under `experiments/mvp/reader/hero/`.

## Search coverage and ready-to-load shot assets

The inventory examined 112 validation maps and 498 baseline-violating, static-screen-admitted candidates. 13 had reusable same-rule recorded after plans; 12 had linked historical fresh audits. The five fixed top-ranked pairs were read and checked without replacement. No train map was needed to fill this archive-led shortlist; no test or test-v3 input was opened.

Ready-to-load exports are `demo/before-plan.json`, `demo/after-plan.json`, `demo/reading.json` and `demo/shot.json`. The shot file carries the map hash, model/recorded labels, highlighted DG box, before/after counters and distinct historical/new timing fields. These are the same freshly audited plans, not a new search. `runs/verification.json` records terminal source/budget/admission/binding checks. Run `.venv/bin/python -m experiments.mvp.reader.hero.finalize` after regenerating the report to rebuild these exports and this final section.
