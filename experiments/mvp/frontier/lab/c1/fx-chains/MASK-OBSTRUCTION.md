# Legacy OOG mask obstruction: parameter resolution mismatch

The current native mask unconditionally rejects ordinary placement of 11 required width/length OOG boxes across **8 of the 12 D185 maps**, and `QZXU0000009` on quick-small `sim.small.40100034`. This is an implementation-level search obstruction, not evidence that those maps are physically or checker infeasible. It invalidates an interpretation of these particular failures as merely bounded chain-packing dead ends.

## Independent read-only audit

Read `results/oog-prefilter-census.json`, the 13 corresponding inputs, the frozen `strategy/blockrule/cohort-medium.json`, and the native source. Checked all input hashes; every D185 input matches its frozen manifest hash. All 12 medium arrivals have zero movable onboard placements. Every listed affected box is required by `load_list`, has nonzero lateral or longitudinal OOG, and does **not** request `away_from_heat`. There is no instance or port override for either raw OOG-clearance alias. The catalogue's default activation is `rule_freeze_v1 = false`.

The parent generated the census using the native parameter readers: every row records raw clearance `null`, resolved catalogue clearance `0.0`. The independent input/source audit confirms why those readers differ; it did not rerun a numerical solver, mask rollout or checker.

| D185 seed | Affected required boxes | Input SHA256 |
|---|---|---|
| 185100000 | QZXU0000539 | e5c94abe50f8bcf87f221d56c092e28c6b00e093a31de318db5765092015be8b |
| 185100011 | QZXU0000375, QZXU0000499 | 5ede34ef191cf722ab7592eb1cdaafb90187c92edbf7164dc4c0b66a85ad0e1c |
| 185100101 | QZXU0001026 | 0da11f945d9e7e86359028726dd44fdd9bb45987b4f9ac2578842e01b87fa2c1 |
| 185100110 | QZXU0000415 | 3d5b7e00def750fb05e557e50cc13b67f5665656e70dcfe44657757e4d8bde7e |
| 185100200 | QZXU0000863, QZXU0001330 | fae70002e353976435e767114cef18fe0b46cf7557adf3249db061f9e6fb3277 |
| 185100201 | QZXU0003380 | f7f6952044846880a0e3b7c0e796fe649af1fd05417ae8c6177b1b319774a457 |
| 185100210 | QZXU0001520, QZXU0001622 | e382873834897a84f656cdfe2e51857d25a9bf30a485bfbe6de7bb352d4fbee8 |
| 185100211 | QZXU0004236 | bcb4cdf52840486ab000efb7d3600ca09094acdc0052c4100418bfdc331a2573 |

The other four D185 maps (001, 010, 100, 111) have no box satisfying this particular obstruction predicate. This does not establish their feasibility. The additional small input hash is `0bafad1b85e3c191d8ae08e218f30434df667805c34867406f92635a6d87b88a`.

## Source proof for ordinary placement

In `src/stowage/feasibility/_engine.py:_evaluate`, under the legacy branch:

1. A target with `box.oog is not None` has `T.fallback = True`.
2. `_oog_heat` is true only when the same OOG box requests `away_from_heat`. All listed targets have `_oog_heat = False`.
3. The non-forced candidate therefore goes to `_e9_batch(..., all_boxes=True)`.
4. `_e9_batch` calls `rules_special._parameter(..., 'oog_clearance', 'minimum_clearance_m')`. That helper reads only port/instance values; it does not resolve catalogue defaults. Here it returns `None`, and `_nonnegative(None)` is `None`.
5. `cand_wl` is true for every listed target. The exact executable rejection is:

```python
if all_boxes:
    if clr is None:
        ok &= ~cand_wl
```

Thus the target's result is false regardless of slot, occupied neighbors, support floor, OOG roof, ballast, D55, weight order or completion-screen setting. It can reject before any genuinely binding geometry test. Enlarging a chain model, finding a better assignment, or reserving a wider OOG lane cannot make this branch admit the box.

By contrast, the legacy exact checker in `checker/rules_special.py:special_violations` resolves the same parameter through `_clearance_parameter`. With these inputs it receives the catalogue's `0.0` default and evaluates actual envelope intersections. Zero margin does **not** waive intersecting-envelope rules; it merely is valid data rather than missing data. The fast-path comment claiming that this raw missing value necessarily produces an exact-checker missing-data violation is not true when the catalogue supplies the default.

## Forced hold access and scope

The outer `_evaluate` removes forced hatch-restow pairs before its ordinary E9 block. It would therefore be incorrect to infer a universal action prohibition from the outer block alone.

An additional source trace shows that the current `_restow_places` does not finally accept those pairs without further mask evaluation: it clears the hatch, builds `cleared`, calls `_evaluate(cleared, allowed_slots=..., allowed_boxes=...)`, and returns only original pairs present in `cleared.mask`. The covers are now open, so that recursive evaluation is ordinary placement and reaches the same legacy OOG prefilter. Hatch clearing changes cargo occupancy, not the raw parameter value or target's OOG/heat attributes. This source trace closes the apparent bypass in the current implementation; it was not independently exercised in this read-only audit.

There is also a narrower proof needing no assumption about the recursive path. The shared score interface accepts only one `Place` per load-list box, and every scored candidate must place the complete load list exactly once (`lab/score.py:_actions`). All these arrivals are empty. A forced hold-restow evicts at least one deck box, necessarily a previously placed required load. Reloading it requires another `Place` for that box, which the interface excludes. A load-once complete constructor therefore cannot use such an eviction as a workaround, even if a different engine revision admitted the OOG through that path. `engine.is_complete()` additionally requires the quay buffer to be empty.

Accordingly the durable conclusion is **an obstruction to complete native-mask construction under the pinned legacy runtime and the lab's load-once interface**. It is not a checker infeasibility certificate, Hall certificate, physical impossibility result, or statement about arbitrary full move plans, other rule activations, explicit parameter overrides, future source revisions, or remanifested cargo. No checker, mask, catalogue or input was changed to bypass it.

## Fresh diagnostic lineage and remaining evidence limits

`results/special-hand-diagnostic.json` records the manually informed small seed reaching 11/12, with only `QZXU0000009` at `0020682` ready. A further reproducible fresh parent test, `verify_mask_obstruction.py`, passed and writes `results/mask-obstruction-probe.json`: the slot is open, exact `placement_violations` on the engine's own ship state returns `[]`, `_e9_batch` is false, and native `is_legal` is false. Raw clearance is `None` while resolved clearance is `0.0`. The source proof above does not depend on this numerical comparison.

`SMALL_FAILURE.md` correctly identifies genuine heat-floor and OOG-neighbor constraints omitted by the initial CP, but those geometric repairs cannot cure this separate mask defect. A separate explicitly manual diagnostic then expands the complete seed, runs unchanged V4 finishing successfully, and obtains a fresh full-checker pass at **14 lifts with zero violations** (`results/small-checker-diagnostic/`). The input hash is unchanged. This establishes checker feasibility of that small input, while it remains native-mask rejected. It is excluded from mask-legal quick passes and is not a new constructor or D185 success.

The appropriate next step is an owner-reviewed native-mask parameter-resolution fix with a regression demonstrating equality between the legacy fast path and exact special checker on catalogue-default inputs, followed by fresh quick/medium measurements. That work is outside this experiment's permitted source scope. Adding an explicit zero override or activating another rule regime only inside this experiment would change the pinned rules/input contract and was not done.

## Pinned bytes

| Source/evidence | SHA256 |
|---|---|
| `src/stowage/feasibility/_engine.py` | 2d505524484c1576a6b61ce98a39af84a11f0387bee65bb34ad282aec516e81e |
| `src/stowage/checker/rules_special.py` | bad52b0135ed37682e5b8c33d68dd7e9ff0266814e5a39a8f84e2882c307cef3 |
| `src/stowage/rules/catalogue.toml` | a34a24ed0a1a8ca1a4797153d6c6452fe56fc436d33f0bc427fbd6b87eb51cae |
| `experiments/mvp/frontier/lab/score.py` | 9b21155a90e3c0cd8d1e9c4c9565501f5f8f08f29458dadb2d1c653e34b8061e |
| `results/oog-prefilter-census.json` | 8d30a80aec69d000258ad4e3310b12eb31c2429c6f1fd2f4c71efbb8cae2b9dd |
| `results/special-hand-diagnostic.json` | 10991d79a6fa92848a9cc2871f92ae98a4b0ff060dffe57f30e0b196a3d599b7 |
