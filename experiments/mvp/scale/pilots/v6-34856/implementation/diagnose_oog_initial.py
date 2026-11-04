"""One bounded TRAIN initial-mask/local-rule diagnostic, never a certificate."""
from __future__ import annotations
import fcntl
import os
from pathlib import Path
import threading
import time

import scale_solver as s


def main():
    pid = os.getpid()
    folder = s.HERE / "pilots" / f"oog-initial-{pid}"
    control = s.HERE / "control-v4.json"
    started = time.monotonic()
    result = {"pid": pid, "certificate": False, "primary_outcome": False,
              "initial_mask": {}, "placement_checks": []}

    def inventory(active):
        with (s.HERE / "oog-diagnostic-control.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            data = s.read(control)
            pids = set(data.get("owned_diagnostic_pids", []))
            pids.add(pid) if active else pids.discard(pid)
            data["owned_diagnostic_pids"] = sorted(pids)
            s.write(control, data)

    def save():
        result["elapsed_s"] = time.monotonic() - started
        s.write(folder / "result.json", result)

    def hard_timeout():
        result["status"] = "watchdog_timeout"
        save()
        inventory(False)
        os._exit(124)

    s.write(folder / "owner.json", {"pid": pid, "started_at": s.now(), "workers": 1,
                                    "watchdog_seconds": 90, "native_threads": 1})
    inventory(True)
    watchdog = threading.Timer(90, hard_timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        s.load_base(s.HERE / "runtime/base")
        from stowage.actions import get_action_space
        from stowage.checker.rules import placement_violations
        from stowage.conventions import parse_slot
        from stowage.feasibility import FeasibilityConfig
        from stowage.io import load_instance
        from stowage.rules.catalogue import rule_freeze_v1
        from stowage.solver.twostep import _canonical_instance
        from stowage.solver.twostep_adapter import make_planning_engine
        import numpy as np

        relative = "experiments/mvp/strategy/fillsweep/maps/train/medium/feeder_800/f60-high/seed-192200100.json"
        path = s.train_path(relative)
        result.update(input=relative, input_sha256=s.sha(path), source_sha256=s.sha(Path(__file__)),
                      runtime=s.read(s.HERE / "runtime/base/COMPLETE.json"), stage="setup")
        save()
        original = load_instance(path)
        with rule_freeze_v1(enabled=False):
            canonical = _canonical_instance(original)
            boxes = {box.id: box for box in canonical.containers}
            ids = ("QZXU0000307", "QZXU0001258")
            engine = make_planning_engine(canonical, get_action_space("boxslot"),
                                         config=FeasibilityConfig(extra={"global_screen": 0}))
            t = engine.T
            result.update(stage="initial_legal_pairs", setup_s=time.monotonic()-started)
            save()
            before = time.monotonic()
            bis, sis = engine.legal_pairs()
            result["initial_legal_pairs_time_s"] = time.monotonic() - before
            for cid in ids:
                bi = t.box_index[cid]
                allowed = sis[bis == bi]
                result["initial_mask"][cid] = {
                    "box": boxes[cid].model_dump(mode="json"),
                    "static_count": int(t.static[bi].sum()), "native_legal_count": len(allowed),
                    "native_legal_slots_sample": [t.slot_ids[int(si)] for si in allowed[:12]]}
            result["stage"] = "public_local_rules"
            save()
            for cid in ids:
                if time.monotonic()-started >= 75:
                    result["status"] = "soft_deadline"
                    break
                bi = t.box_index[cid]
                selected = []
                for deck in (False, True):
                    for ordinal in (0, 1):
                        candidates = [int(si) for si in np.flatnonzero(t.static[bi])
                                      if bool(t.slot_deck[si]) == deck and int(max(t.slot_k[si])) == ordinal]
                        if not candidates:
                            continue
                        candidates.sort(key=lambda si: t.slot_ids[si])
                        near = min(candidates, key=lambda si: (abs(parse_slot(t.slot_ids[si]).bay-18),
                                                               parse_slot(t.slot_ids[si]).row))
                        selected.extend(dict.fromkeys([candidates[0], near]))
                for si in selected[:8]:
                    if time.monotonic()-started >= 75:
                        result["status"] = "soft_deadline"
                        break
                    before = time.monotonic()
                    violations = placement_violations(canonical, canonical.onboard, boxes[cid],
                                                      t.slot_ids[si], call_index=canonical.call_index)
                    result["placement_checks"].append({"container_id": cid, "slot": t.slot_ids[si],
                        "on_deck": bool(t.slot_deck[si]), "ordinal": int(max(t.slot_k[si])),
                        "native_initial_legal": bool(np.any(sis[bis == bi] == si)),
                        "time_s": time.monotonic()-before,
                        "violations": [v.model_dump(mode="json") for v in violations]})
                    save()
            result.update(status=result.get("status", "complete"), stage="complete")
            save()
        print({"pid": pid, "folder": str(folder), "status": result["status"],
               "initial_counts": {cid:row["native_legal_count"] for cid,row in result["initial_mask"].items()},
               "checks": len(result["placement_checks"])}, flush=True)
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
        save()
        raise
    finally:
        watchdog.cancel()
        inventory(False)
        s.write(folder / "completion.json", {"pid":pid,"finished_at":s.now(),"status":result.get("status"),
                                              "elapsed_s":time.monotonic()-started})


if __name__ == "__main__":
    main()
