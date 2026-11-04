"""Observe one real medium draw's C1 failures without changing any screen result."""
from collections import Counter
import json
import os
from pathlib import Path
import time

import numpy as np

from stowage.actions import get_action_space
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.feasibility.capacity import NestedHall
from stowage.generate import random_yard
from stowage.checker.rules_imdg import imdg_placement_violations

HERE = Path(__file__).resolve().parent


def diagnose(instance):
    engine = make_feasibility_engine(instance, get_action_space("boxslot"),
        config=FeasibilityConfig(lookahead=0, extra={"global_screen": 0}))
    st, T, flow = engine._cur, engine.T, engine._flow
    completion = engine.completion_check()
    summary = engine._capacity_state(st)
    usable, caps = summary["usable"], summary["caps"]
    rem = np.flatnonzero(st.rem)
    by_id = {box.id: box for box in instance.containers}
    def box_record(i):
        b = by_id[T.box_ids[int(i)]]
        return {"id": b.id, "length_ft": b.length_ft, "mass_kg": b.gross_mass_kg,
                "reefer": b.reefer, "special_stowage": b.special_stowage,
                "dg": b.dg.model_dump(mode="json") if b.dg else None,
                "static_allowed_slots": int(T.static[i].sum()),
                "compatible_cells": int(flow.rows[flow.box_class[i]].sum())}
    restricted = []
    PS, flags = engine._class_tables()
    J = np.arange(len(T.stack_ids))
    for c in np.unique(flow.box_class[rem]).tolist():
        if not flags[c]:
            continue
        members = rem[flow.box_class[rem] == c]
        cnt = PS[c, J, np.minimum(st.fill+usable, PS.shape[2]-1)]-PS[c, J, np.minimum(st.fill, PS.shape[2]-1)]
        failures = []
        for units in (1, 2):
            selected = members[T.units[members] == units]
            if not len(selected):
                continue
            if units == 1:
                counts = np.where(st.nlong > 0, 0, cnt)
                lim = caps
            else:
                P = engine._pairs
                counts = engine._pair_cnt(st, usable, cnt)
                lim = np.minimum(caps[P[:, 0]], caps[P[:, 1]])
            hall = NestedHall(T.mass[selected], np.ones(len(selected), dtype=np.int64), lim, counts)
            if not hall.ok:
                failures.append({"units": units, "count": len(selected), "usable_compatible_positions": int(counts.sum()),
                    "min_surplus": hall.min_surplus,
                    "deficits": [{"mass_level_kg": float(m), "demand": int(d), "available": int(a)}
                                 for m, d, a in zip(hall.levels, hall.dem, hall.cells) if a < d]})
        if failures:
            restricted.append({"class": int(c), "boxes": [box_record(i) for i in members], "failures": failures})
    F = engine._dg_F(st)
    dg = []
    for cls, allowed in F.items():
        members = rem[(T.dg_class[rem] == cls) & T.is_dg[rem]]
        if int(allowed.sum()) >= len(members):
            continue
        representative = by_id[T.box_ids[int(members[0])]]
        matching = np.flatnonzero(T.slot_n == T.units[members[0]])
        rule_counts, messages = Counter(), {}
        for s in matching:
            for v in imdg_placement_violations(instance, instance.onboard, representative,
                                              T.slot_ids[int(s)], call_index=instance.call_index):
                rule_counts[v.rule_id] += 1
                messages.setdefault(v.rule_id, set()).add(v.message)
        dg.append({"class": int(cls), "demand": len(members), "allowed_slots": int(allowed.sum()),
                   "boxes": [box_record(i) for i in members], "imdg_reasons": dict(rule_counts),
                   "imdg_messages": {rule: sorted(msg)[:4] for rule, msg in messages.items()}})
    return {"completion": completion, "box_count": len(rem), "cell_count": len(T.cell_ids),
            "slot_count": len(T.slot_ids), "DG_aboard_count": len(st.dg_aboard),
            "c1g_deficits": dg, "c1h_deficits": restricted,
            "no_static_slot_boxes": [box_record(i) for i in rem if not T.static[i].any()]}


def main():
    start = time.perf_counter()
    real = random_yard.completion_failures
    captured = []
    def observed(instance):
        answer = real(instance)
        if not captured and len(instance.load_list) > 1:
            report = diagnose(instance)
            assert tuple(sorted(k for k, v in report["completion"].items() if k.startswith("c1") and not v)) == answer
            instance_path = HERE / "c1-random500-seed0.instance.json"
            instance_path.write_text(instance.model_dump_json())
            captured.append(report)
        return answer
    random_yard.completion_failures = observed
    audit = random_yard.RandomGenerationReport()
    error = None
    try:
        random_yard.generate_random_instance("medium", seed=0, vessel_name="feeder_500", max_attempts=1, report=audit)
    except ValueError as exc:
        error = str(exc)
    result = {"pid": os.getpid(), "wall_s": time.perf_counter()-start, "error": error,
              "diagnosis": captured[0] if captured else None}
    (HERE / "c1-random500-seed0.json").write_text(json.dumps(result, indent=2, default=lambda x: int(x))+"\n")
    print(json.dumps(result, indent=2, default=lambda x: int(x)))


if __name__ == "__main__":
    main()
