# D185 port-blocking baseline

Completed: 30 small train, 12 new medium train, and exactly one 55-map validation campaign. Read `FINAL_REPORT.md` and `FINAL_SUMMARY.json` for the frozen result; `verification.json` passes all 97 maps / 310 checker-report references, and all 27 tests pass. The proposer cohort was frozen at 21:37 EDT on 30 October.

The main six-CPU reservation is released. An owned idle renderer (PID 35014) still refreshes the live `BLOCKRULE.md`; its stop request was denied by the sandbox. Its separate zero-compute ledger note remains. The frozen final files are independent of this renderer.

All implementation and experiment artifacts live here. `rule.py` supplies a
headroom `BlockLayout`; `capacity_candidate.py` applies the frozen train-only
boundary-capacity correction on four medium masks. `study.py` reuses headroom's scoped placement hooks and
two-step solve wrapper, then applies the V4 finisher and unmodified full checker.
No `src/` change or model training is involved.

`cohort-small-train.json` is an outcome-blind sample of 30 train maps, excluding
the headroom and proposer lists. `cohort-medium.json` fixes the twelve new
current-generator train maps and is also the proposer medium cohort. The
historical merit review did not retain the identities of its fresh 30-map probe
or seven failed finisher calls; disjointness from those unidentified maps and
exact reproduction of those seven identities cannot be asserted.

Budgets are equal across default/rule and raw/compacted arms: small solves
120 s and V4 600 s; medium solves 300 s and V4 1,200 s. Native numerical threads,
CP workers and in-port acceptance workers are one. At most three map workers
plus their three owned V4 children are admitted across all campaigns. Initial
generation and diagnosis share that cap, leaving two main map workers.

Each map stores the rule, raw solve metrics/results, compaction result,
every finalizer return (including rejected plans), full reports and certificate
records. A plan passes only when finalize is `ok`, the full checker is `True`
and the independent layout audit is clean. Checked rule cost must be strictly
lower than checked default, unless default fails. Ties retain default. With
compaction enabled each arm first keeps the better passing raw/compacted plan.

Initial train launch commands (historical; the completed initial runs are archived
under `runs/initial-*-train`, and stable aliases point at the final epoch):

```sh
.venv/bin/python experiments/mvp/strategy/blockrule/study.py \
  --manifest experiments/mvp/strategy/blockrule/cohort-small-train.json \
  --out experiments/mvp/strategy/blockrule/runs/small-train --workers 2 --detach
.venv/bin/python experiments/mvp/strategy/blockrule/study.py \
  --manifest experiments/mvp/strategy/blockrule/cohort-medium.json \
  --out experiments/mvp/strategy/blockrule/runs/medium-train --workers 1 --detach
```

The admitted campaign may instead use `medium_campaign.py`, after generation
and diagnosis stop: it permits one active medium map alongside the two small
train workers, then expands medium to three only after the small controller's
completion file is written and its workers have drained. Idle pool processes
do no solver work. `admission.json` records the combined active-map/CPU cap;
the solver function, pinned source and budgets are identical.

`final_campaign.py` drained the initial studies, installed the frozen capacity
policy, and ran the four changed medium masks on train before validation.
Unchanged train artifacts retain explicit certificate inheritance and repeat
layout audits. It then froze the implementation and ran validation exactly once.
The one-time commands below have already completed; do not launch validation again. `campaign.py` requires all train maps to be
complete, rejects source drift, hashes the exact 55-map validation list, and
creates `validation-once.json` exclusively before launching validation:

```sh
.venv/bin/python experiments/mvp/strategy/blockrule/campaign.py freeze
.venv/bin/python experiments/mvp/strategy/blockrule/campaign.py validate --workers 3
```

Progress/report:

```sh
.venv/bin/python experiments/mvp/strategy/blockrule/report.py
.venv/bin/python -m unittest experiments.mvp.strategy.blockrule.test_rule \
  experiments.mvp.strategy.blockrule.test_capacity_candidate \
  experiments.mvp.strategy.blockrule.test_capacity_train -v
```

`BLOCKRULE.md` reports full-checked pairs with and without compaction and exact
two-sided sign tests. Its partial results remain labelled incomplete. A resumed
campaign reuses durable artifacts only if its source, input and budget record
matches. Do not remove the validation claim to run a different validation
recipe. Owned PIDs are in the explicit launch/worker files; no process pattern
termination is permitted.
