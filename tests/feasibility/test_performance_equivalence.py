"""C2-on masks must equal the corrected exhaustive pre-performance path.

The three saved, source-pinned current-hull instances are the performance audit's
instances; seeded walks exercise distinct immutable prefixes without regeneration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import random

import numpy as np
import pytest

from stowage.io import load_instance

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "experiments/feasibility/performance-followup"
spec = importlib.util.spec_from_file_location("feasibility_performance_audit", AUDIT / "measure.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("rung", ["tiny", "small", "medium"])
@pytest.mark.parametrize("seed", [1704, 9049])
def test_c2_allowed_action_set_matches_exhaustive_seeded_sweep(rung, seed):
    instance = load_instance(AUDIT.parent / "fix-t4-measure" / f"{rung}-current.json")
    before = audit.make_engine(instance, True, "before")
    after = audit.make_engine(instance, True, "after")
    stale = "unavailable: production linear policy epoch is stale; requalify"
    if (before._global is None and after._global is None
            and before.diagnostics["global_screen"] == after.diagnostics["global_screen"] == stale):
        pytest.skip("D173 E: both C2 engines lack qualification for the production linear-policy epoch; "
                    "requalify before comparing masks. See experiments/mvp/postfreeze/FAILURES-D171.md (E).")
    assert before._global is not None and after._global is not None, (
        before.diagnostics["global_screen"], after.diagnostics["global_screen"]
    )
    def baseline_with_d55():
        # D55 intentionally narrows the old mask. Apply the shared new local cap to the preserved
        # exhaustive baseline so this regression continues to isolate the C2 performance change.
        flat = before.legal_index()
        T, st = before.T, before._cur
        boxes, slots = flat // len(T.slot_ids), flat % len(T.slot_ids)
        stacks = T.slot_stacks[slots]
        js = np.maximum(stacks, 0)
        cap = after.T.extra["stack_visibility_top"]
        room = np.min(np.where(stacks >= 0, cap[js] - T.stack_base_z[js] - st.sh[js], np.inf), axis=1)
        return flat[T.height[boxes] <= room + 1e-9]

    rng = random.Random(seed)
    for _ in range(4):
        expected = baseline_with_d55()
        np.testing.assert_array_equal(after.legal_index(), expected)
        # Include stage-B actions and exact ordering in the comparison.
        old = tuple(before.decode_index(int(f)) for f in expected) + before.legal_discharges()
        assert tuple(after.legal_actions()) == old
        if not old:
            break
        action = rng.choice(old)
        before.push(action)
        after.push(action)
        # Copies and undo must preserve the prefix caches and mask semantics.
        np.testing.assert_array_equal(after.copy().legal_index(), baseline_with_d55())
    if after.depth:
        after.pop()
        before.pop()
        np.testing.assert_array_equal(after.legal_index(), baseline_with_d55())
