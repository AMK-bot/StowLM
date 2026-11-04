"""Read-only diagnostic: inspect the first geometry fixture C1 candidate."""
from collections import Counter
import json
from unittest.mock import patch

import numpy as np
from stowage.generate import instances
from stowage.vessel.fleet_slices import load_fleet_preset
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.actions import get_action_space
from stowage.feasibility.tables import class_params

class Captured(Exception):
    pass

geometry = load_fleet_preset("feeder_500", "full", with_hull=False)
original = instances.screen_load_list

def screen(vessel, boxes, **kwargs):
    return [reason for reason in original(vessel, boxes, **kwargs)
            if reason != "checker.missing_vessel_data"]

def completion(instance):
    engine = make_feasibility_engine(instance, get_action_space("boxslot"),
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
    checks = engine.completion_check()
    T, flow = engine.T, engine._flow
    summary = engine._capacity_state(engine._cur)
    rows = []
    ids = set(instance.load_list)
    by_id = {box.id: box for box in instance.containers}
    for group in sorted(set(flow.box_class.tolist())):
        indices = np.flatnonzero(flow.box_class == group)
        rows.append(dict(group=int(group), boxes=int(len(indices)),
            compatible_cells=int(flow.rows[group].sum()),
            requested_units=int(T.units[indices].sum()),
            equipment=dict(Counter(by_id[T.box_ids[k]].iso_type for k in indices)),
            special=dict(Counter(tag for k in indices for tag in by_id[T.box_ids[k]].special_stowage)),
            lengths=dict(Counter(int(T.length[k]) for k in indices)),
            reefers=int(sum(by_id[T.box_ids[k]].reefer for k in indices))))
    out = dict(checks=checks, targets=len(ids), cells=len(instance.vessel.cells),
        deck_cells=int(sum(T.stack_deck[j] * len(T.stack_cells[j]) for j in range(len(T.stack_ids)))),
        usable=int(summary["usable"].sum()),
        class_params=class_params(instance), e1_threshold=T.e1_threshold,
        e6_threshold=T.e6_threshold, e6_fallback=T.e6_fallback,
        static_zero_ids=[T.box_ids[k] for k in range(len(T.box_ids)) if not T.static[k].any()],
        groups=rows,
        stacks=[dict(id=T.stack_ids[j], usable=int(summary["usable"][j]),
            max_height=float(T.stack_hmax[j]), on_deck=bool(T.stack_deck[j]))
            for j in range(len(T.stack_ids))])
    counterfactuals = {}
    for label, overrides in {
        "E2_gross_mode_with_old_feasibility": {"expert.e2.weight_order": {"order_mode": "gross_mass_kg"}},
        "missing_legacy_E6_fallback": {"expert.e6.heavy_under_deck": {"allow_on_deck_if_no_compatible_hold_cell": None}},
    }.items():
        modified = instance.model_copy(update={"rule_overrides": overrides})
        altered = make_feasibility_engine(modified, get_action_space("boxslot"),
            config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
        counterfactuals[label] = altered.completion_check()
    out["counterfactuals"] = counterfactuals
    print(json.dumps(out, sort_keys=True, default=str))
    raise Captured

with patch.object(instances, "_full_vessel", lambda name: geometry), \
     patch.object(instances, "screen_load_list", screen), \
     patch.object(instances, "completion_failures", completion):
    try:
        instances.generate_simulated_instance("small", seed=1, without_dg=True, max_attempts=5)
    except Captured:
        pass
