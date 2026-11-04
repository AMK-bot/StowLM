# Sandbox refresh for p22-demo

The hero is p110-lattice's 87-box feeder500. `fixtures/demo.json` points to
`fixtures/demo_source_hero.json`: the same hero loading destinations, crane order
and ballast, with one openly disclosed synthetic DG declaration. All vessel and
rule limits are unchanged. Historical 190-box sources and Spark recordings stay
historical; do not use their trim/stack challenges on stage.

The existing top-level `MoveVerdict`/`Explanation` shapes and per-case `fast`,
`fast_explanation`, `verdict`, `explanation`, `box_id`, `target_slot`, `confirmed`
fields remain. Case **values** change:

| Before | Current |
|---|---|
| `legal` | `legal` |
| `stability` | removed; the invented trim restriction is retired |
| `stack_weight` | `securing`, failing the existing limit with synthetic gear |
| `segregation` | `segregation`, sodium cyanide and hydrochloric acid |
| — | `crane_order`, the stow passes but the fixed order fails |

Update the hard-coded `CASE_NAMES` and film cues in `stage-v6breakit.js`; they
currently request `stack_weight`/`stability`. Do not silently alias a securing
failure to "stack weight" or a trim change to "stability".

Read the additive `verdict.gauges.outcome`, `deciding_cause`, `stow_assessment`
and `full_check_scope`. The three domain outcomes are:

1. **Passes as edited.**
2. **Stow is fine, but the existing crane order can't reach it. A replan might.**
3. **Stow breaks rule X.**

An unavailable calculation remains unavailable, never a made-up physical
failure. A failed crane order leads the card and sentence even when the
requested-stow snapshot also breaks a rule. Keep both findings, but label the
snapshot diagnostic. Change `app/v6_sandbox.py`'s `_rank`/`lead_rows` to honor
`deciding_cause` before cargo/physics sorting. Its current `view()` also needs to
retain the new gauge keys. `explanation.statements[0]` is the deterministic
deciding-cause status; Spark may not move it or omit evidence.

Show `gauges.scope_note` exactly for these loading edits:

> This edits one box and keeps the crane order and ballast. It doesn't replan.

Show both lift measures together using `gauges.lift_counts.display`, or its
`this_call_label` and `projected_later_label` fields. Example formatting:

> Crane lifts this call: +0. Projected later-port lifts: +24 (projection).

The +24 is a formatting example; show each verdict's actual engine-derived
delta. Failed replay has unavailable counts, not +0. On a pass also show:

> Legal isn't the same as good.

The current fully checked hero pass actually has **+0 this call, +28 projected
later-port lifts**. Preserve that measured +28 instead of copying the example's
+24.

Use the exact challenge contract label **Demo cargo and limits tightened for
this challenge**, immediately beside its `label_disclosure`: only the cargo
declaration changed, no limit was tightened. `modifications` specifies paint
UN1263-III → sodium cyanide UN1689-I for QZXU0000014. Acid QZXU0000349 UN1789-II
is unchanged; `limit_changes=[]`. Hero limits are not the old feeder800's
numbers. Securing uses the checker's approximation with declared synthetic gear.

For stored playback show **Recorded answer** and **Checked on** the recorded
date; this is not a recheck at launch. Keep **Fast screen, full check running**
for a genuinely running screen. Use "rechecked now" only after actually calling
the checker. Current fast frames come from the callback of the same request.
Do not quote the old 0.76-second v1 measurement; see README for the separately
measured 190-box low-load screen (3.224 seconds median, target unmet).
Current `demo.json` carries four fresh guarded Spark selections plus complete
offline templates. Their recording is `spark_demo_hero.json`; `spark_demo.json`
belongs to `history-v1/demo.json` and its archived renderer. Spark selected and
ordered checked statement IDs; label that job honestly. The 25 cache explanations
are recorded templates, not new Spark calls.

The additional cache is separate from the unchanged demo fixture shape:

```python
from experiments.mvp.pipeline.sandbox.demo_cache import lookup_cached_drag

recorded = lookup_cached_drag(instance, plan, box_id, target_slot)
if recorded is None:
    # Existing live worker path; never substitute a verdict for another input.
    ...
else:
    # Show the envelope's mode/label/checked_utc with its fast/full/explanation.
    ...
```

`fixtures/drag-cache.json` contains 25 requests fixed before the 30-drag audit;
the four showcase cases are separately recorded in `demo.json`. The cache checks
exact normalized instance/plan hashes, request, checker/parameter source bytes
and component versions. A hit does no new full check. Pin `drag-cache.sha256`
independently in release configuration and use `drag-cache.index.json` for the
exact prepared request list. The cache records diagnostic snapshots explicitly
and cannot turn one into a passing edited plan. Changed inputs or dependencies
miss the cache and return to the fresh checker path.
Use the exact instance and plan from `demo_source_hero.json`, including its
disclosed DG declaration and derived IDs. Passing the unmodified upstream hero
instead correctly misses this cache. Refresh p22's own loaded workers after
publishing the new fixture; this task does not stop another session's workers.
