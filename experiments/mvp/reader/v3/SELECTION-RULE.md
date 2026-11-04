# Reader v3 adapter selection rule

Fixed on 1 Nov 2026 about 21:30 EST, while training was still running at about iteration 3,300. No
candidate exists yet and none has been scored on these rows. The 80-row checks and val_loss up to 3,200
had been seen, but they played no part in this rule, which came as a written order.
`selection-freeze.json` hashes this file, the sample and the code.

## Why this exists

The 80-row checks bounce (75 to 88.75 %) at a constant learning rate. One check in 80 rows moves the score
by 1.25 points, so that sample cannot choose an adapter.

## Candidates

Built by `swa.py`, in fresh folders under `candidates/`, each with its hash and a `STOPPED.json`:

1. `final`: the adapter training stopped with. If training crashed, the last saved check, marked so.
2. `best-val-loss`: the single check in `checks-final/` with the lowest logged val_loss; ties go to the later.
3. `swa-last-3`: mean of the last 3 checks in `checks-final/`.
4. `swa-last-5`: mean of the last 5 checks in `checks-final/`.

The stopped run's `checks/` are not candidates. Each LoRA factor is averaged separately, so an average is
a point in parameter space, not the mean of the products (see `swa.py`).

## Selection sample

`selection-sample.jsonl`: 400 rows of `data-domain/valid.jsonl`, seed 199, disjoint from the 80-row sample.
50 rows from each of 8 strata: 4 rule types and 4 refusal codes, spread across families. Never sealed.
Equal strata differ from any real mix on purpose: each kind of answer gets the same say.

## Rule

Each candidate is scored by `evaluate.main` with the frozen decoder, the same schema mask, no repair.

**Highest exact-match count on the 400 rows wins. Ties go to the simplest, in this order: `final`, then
`best-val-loss`, then `swa-last-3`, then `swa-last-5`.** No other metric counts. All scores are recorded in
`results-final/selection.json`, including malformed and accepted-loosening counts. Identical weights share
one evaluation. A candidate whose evaluation fails has no score and is recorded so. There is no rerun, no
new candidate and no change of rule after any score.

The selected adapter gets the one sealed v3 score. Gates R1, R2, R3 and no-regression against v2 are reported
whatever they say. A candidate's selection score is never reported as its accuracy.

## Limits of this evidence

**Validation overstates sealed: v2 read 91.25 % on validation and 80.47 % sealed.** Validation shares the
generator and families the model trained around, and picking the best of four adds a small upward bias on
these rows. val_loss may have used some of these rows, which adds a little to that bias for `best-val-loss`.
Sealed has a different mix (48 of 128 loosening; 12.5 % here).
