# D137 revision 2 (D141 + D142): staged, NOT installed

Fixes rev-v4-j (`experiments/review/v4/rev-d137.md`) and logic-v4-exit (`experiments/review/v4/logic-d137.md`, D142) on top
of the original D137 patch, which stays as it was in `..`.

## Apply order (repo root, after 8.2 is done and the prefetcher is stopped)

```sh
P=experiments/mvp/exit/patches/d137
git apply $P/candidate-teacher-loop.diff $P/candidate-teacher-loop-tests.diff
git apply $P/revision2/01-loop-fixes-src.diff $P/revision2/02-loop-fixes-tests.diff
git apply $P/revision2/03-prefetch-outcome-id.diff
```

`final-recipe-single-finisher.diff` is already contained in the first two; don't apply it. `installed-manifest.json` here
holds the EXPECTED bytes (`production_installed: false`), and `build_revision2.py` proved that this order on a fresh copy
of the installed tree reproduces the tested tree (135 files).

Then, from a Claude/Metal session:

```sh
zsh experiments/mvp/exit/patches/d137/revision2/RESTART-HOST.sh --check-only
zsh experiments/mvp/exit/patches/d137/revision2/RESTART-HOST.sh
```

The second command runs in the foreground for the two audits (about 25 GPU-min), so start it detached. It audits 8.1,
then 8.2, writes `deployment-d137/parent-choice.json`, and queues the loop from that parent.

## What changed

| item | change | regression |
|---|---|---|
| P1 control epoch | Full dependency epoch captured before admission; bound into the identity, each receipt and `dataset-binding.json`; epoch, checker and dataset hashes re-asserted immediately before each control update | `test_d137_control.py` (3 new) |
| P2 teacher cache | `outcome_epoch` persisted and compared on create, reuse and read; disk re-asserted at write and read | `test_d141_teacher_epoch.py` |
| P2 replay cache | `exit_outcome_dependency_id` compared on reuse, stamped by all four writers, re-asserted at write. Records without it are rechecked | `test_replay.py` (3 new) |
| training receipts | `assert_current_training` also asserts the outcome dependencies | covered by the runner tests |
| P2 gold rounding | Coverage gate follows the gold maps actually selected (`gold_maps_selected`); unknown strata keep the strict rule | `test_d141_teacher_epoch.py` |
| F1 | Audit the fittest trained child. `audited_adapter`, `trained_child_audited` recorded. `persist_stop` refuses a no-gain stop with no audited child; a gate with no child is deferred, and a resume doesn't turn that into a stop | `test_d142.py` |
| F2 | `model_teacher_label`: greedy win, sampled win, `model_only_valid`, tie, `teacher_won`, split by novelty; `quotable_model_beat_teacher` counts only greedy wins on unseen maps | `test_d142.py` |
| F4 | `--first-gate-generation 3`; earlier audits write `gen-*/measurement-audit.json`, not `first-gate.json` (that file disarms the clock). Clock at start + 330 min | `test_d142.py`, restart tests |
| F5 | A trained child tying on passes with moves within +2 % becomes the parent | `test_d142.py` |
| F6 | `restart/parent_audit.py` audits both sequentially and writes the choice; the loop refuses to start without it; `--comparison-adapter` keeps the other in the final audit | restart tests, `test_d142.py` |
| F7 | A stop file breaks to the final population audit | `test_d142.py` |
| F8 | Child LR default 1.5e-5 | `test_d142.py` |
| F9 | Never-attempted maps first; every map records `gold_history` | `test_d137_map_mix.py` (2 new) |
| `--gens 6` | Default in the restart | restart tests |
| void rows | The restart scrubs an inherited `STOWAGE_BALLAST_V4`; `verify_candidate` refuses it, which voided the probe's rows | restart tests + smoke |

## Decisions in this revision (open for veto)

- **A child the elite held off is adopted only if it passes the audit.** Failing the audit leaves the parent as it was,
  and that isn't a rollback.
- **The control now compares against the audited child, at the first gate** (it was the parent at generation 1).
- **The parent rule's tie goes to 8.2.**
- **The parent audits use `rebaseline_on_host.py`'s 90 s finisher for both adapters.** The loop's own baseline uses 120 s.
  Equal for both, so the choice is fair, but those numbers aren't 9.1 numbers.
- **Old teacher and replay records are stale by design.** After install every replay row is rechecked once, so the
  prefetch shelf must be rebuilt with the patched script (03-prefetch-outcome-id.diff). `replay-prefetch-d132` can't be
  reused. The stamp includes the thread variables, so run prefetch and the loop with the same five set to 1 (both do); a
  mismatch only causes a recheck.

## Verified

- `tests/llm/exit` on the installed tree + original D137 + revision 2: **843 passed**, 0 failed, 342 s (807 passed before
  the changes). Log: `/private/tmp/d137r2-5.log`.
- `revision2/restart/tests`: 24 passed.
- Teacher smoke with the revised code, on real never-attempted small train maps: **`verified_teacher_maps` = 2 of 3**, two
  fully checked two-step passes (25 and 39 moves), one two-step failure, `STOWAGE_BALLAST_V4` unset before and after.

## Not verified

- No GPU or MLX run of the revised loop. The controller's fresh-code smoke at restart is the first.
- `parent_audit.py` end to end: only its choice rule and the receipt handling are tested; `rebaseline_on_host.py` itself
  is unchanged and ran earlier tonight.
- The patched prefetcher wasn't run (needs the patched `src`), and `d135/test_prefetch.py` wasn't rerun against it.
- F3 (attribution) is reporting only: the default write-up stays "no attribution claim".
