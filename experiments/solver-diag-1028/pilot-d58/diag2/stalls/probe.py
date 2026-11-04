"""Replay old scalable prefixes and explain bounded final candidate probes.

No search, finisher or full-physics checking executes. The production public
mask is authoritative. A second public engine with completion_screens=0 is an
explicit diagnostic only; its post-candidate completion_check exposes C1/C2.
All writes, including cache clones, stay below this script's directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
PILOT = HERE.parents[1]
ROOT = PILOT.parents[2]


class ProbeDeadline(BaseException):
    pass


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(pin):
    return {str(path.relative_to(pin)): sha(path) for base in ("src/stowage", "params")
            for path in sorted((pin / base).rglob("*")) if path.is_file() and "__pycache__" not in path.parts}


def save(path, value):
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")
    temp.replace(path)


def clone_cache(source, runtime):
    """APFS-clone read-only baseline storage, then redirect production defaults."""
    cache = runtime / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    marker = cache / "snapshot.json"
    if not marker.exists():
        for name in ("stability", "vessels", "linear"):
            if not (source / name).is_dir():
                raise ValueError(f"Missing pinned cache directory: {source / name}")
            target = cache / name
            if not target.exists():
                subprocess.run(["/bin/cp", "-cR", str(source / name), str(target)], check=True)
        save(marker, {"read_only_source": str(source), "storage_only": True})
    sys.path.insert(0, str(PILOT.parent))
    from cache_runtime import redirect_cache
    redirect_cache(runtime)


def install_write_fence():
    def allowed(path):
        if isinstance(path, int):
            return True
        try:
            return Path(os.fsdecode(path)).resolve().is_relative_to(HERE)
        except (TypeError, ValueError):
            return False
    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            write = (isinstance(mode, str) and any(char in mode for char in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if write and not allowed(path):
                raise PermissionError(f"stalls write outside scope: {path}")
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.truncate"):
            if not allowed(args[0]):
                raise PermissionError(f"stalls mutation outside scope: {args[0]}")
        elif event in ("os.rename", "os.link", "os.symlink"):
            if not all(allowed(path) for path in args[:2]):
                raise PermissionError(f"stalls mutation outside scope: {args[:2]}")
    sys.addaudithook(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, required=True)
    parser.add_argument("--case", type=int, choices=(25, 33, 45), required=True)
    parser.add_argument("--cache-source", type=Path, default=PILOT / "diag2/runtime/cache")
    parser.add_argument("--budget", type=float, default=150)
    parser.add_argument("--detailed-boxes", type=int, default=3)
    parser.add_argument("--slot-probes", type=int, default=24)
    args = parser.parse_args()
    args.pin = args.pin.resolve()
    if not (args.pin / "pyproject.toml").is_file():
        parser.error("pin needs pyproject.toml so parameter root cannot resolve to live source")
    output = HERE / f"{args.case:03d}"
    output.mkdir(exist_ok=True)
    runtime = HERE / "runtime"
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.pin / "src"))
    raw = PILOT / "runs/scalable" / f"{args.case:03d}"
    old = json.loads((raw / "result.json").read_text())
    expected = json.loads((raw / "source-end.json").read_text())
    baseline = sources(args.pin)
    differences = sorted(key for key in baseline if expected.get(key) != baseline[key])
    missing = sorted(key for key in expected if key.startswith(("src/stowage/", "params/")) and key not in baseline)
    if differences or missing:
        save(output / "pin-mismatch.json", dict(different=differences, missing=missing))
        raise ValueError("frozen source does not match this raw attempt")
    save(output / "source-start.json", baseline)
    start = time.monotonic()
    row = dict(case=args.case, input=old["input"], input_sha256=sha(Path(old["input"])),
               diagnostic=True, shipping_evidence=False, global_infeasibility_proved=False,
               budget_s=args.budget, source_pin=str(args.pin), source_matches_raw=True,
               prefixes=[], boxes=[], candidate_probes=[], stage="setup", worker_pid=os.getpid(),
               diagnostic_script_sha256=sha(Path(__file__)))
    save(output / "pid.json", dict(pid=os.getpid(), parent_pid=os.getppid(), owner="diag2/stalls/probe.py",
                                   source_pin=str(args.pin), case=args.case))
    def checkpoint(**updates):
        row.update(updates)
        row["elapsed_s"] = time.monotonic() - start
        save(output / "result.json", row)
    def deadline(signum, frame):
        raise ProbeDeadline("stall probe budget expired")
    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    signal.setitimer(signal.ITIMER_REAL, args.budget)
    try:
        clone_cache(args.cache_source.resolve(), runtime)
        sys.path.insert(0, str(PILOT / "diag2"))
        from frozen_paths import install
        adaptation = install(output)
        checkpoint(qualification_path_adapter=adaptation,
                   qualification_path_adapter_sha256=sha(PILOT / "diag2/frozen_paths.py"))
        install_write_fence()
        os.chdir(args.pin)
        from stowage.actions import get_action_space
        from stowage.actions.expander import Expander
        from stowage.actions.space import Place
        from stowage.checker.rules import placement_violations
        from stowage.conventions import cells_of_slot
        from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
        from stowage.feasibility.visibility_bounds import compute_visibility_caps
        from stowage.io import load_instance
        from stowage.model import MovePlan, Placement
        from stowage.params import repo_root
        from stowage.solver.securing_geometry import securing_geometry_violation, securing_state_violations
        assert repo_root() == args.pin
        for name in ("stowage.feasibility._engine", "stowage.solver.scalable", "stowage.checker.rules"):
            assert Path(importlib.import_module(name).__file__).resolve().is_relative_to(args.pin)
        instance = load_instance(Path(old["input"]))
        plan = MovePlan.model_validate_json((raw / "cargo.plan.json").read_text())
        space = get_action_space("boxslot")
        actions = [space.decode(action) for action in plan.actions]
        inventory = json.loads((HERE / "static-prefix-inventory.json").read_text())
        watch = [box["id"] for item in inventory if item["index"] == args.case for box in item["missing"]]
        engine = make_feasibility_engine(instance, space, config=FeasibilityConfig())
        if engine.diagnostics.get("global_screen") != "on":
            raise ValueError(f"Production C2 unavailable: {engine.diagnostics}")
        ex = Expander(instance)
        checkpoint(stage="production_replay", n_loads=len(instance.load_list), actions=len(actions))
        first_dead = None
        box_index = {cid: index for index, cid in enumerate(engine.index_boxes)}
        for depth in range(len(actions) + 1):
            legal = engine.legal_index()
            dead = engine.dead_end()
            loaded = sorted(cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] == "ship")
            next_action = actions[depth] if depth < len(actions) else None
            snap = dict(depth=depth, legal_places=len(legal), legal_discharges=len(engine.legal_discharges()),
                        loaded=len(loaded), completion=engine.completion_check(), complete=engine.is_complete(),
                        dead_end=None if dead is None else asdict(dead),
                        watched_boxes={cid: dict(where=ex.where.get(cid), legal_slots=int(
                            ((legal // engine.n_slots) == box_index[cid]).sum())) for cid in watch},
                        next_action=None if next_action is None else space.encode(next_action),
                        next_action_legal=None if next_action is None else engine.is_legal(next_action))
            row["prefixes"].append(snap)
            if dead is not None and first_dead is None:
                first_dead = depth
            checkpoint(first_engine_dead_end_depth=first_dead)
            if next_action is None:
                break
            if not snap["next_action_legal"]:
                raise ValueError(f"Recorded prefix action rejected at {depth}: {next_action}")
            engine.push(next_action)
            ex.step(next_action)
        missing_loads = sorted(cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != "ship")
        quay = sorted(cid for cid, location in ex.where.items() if location[0] == "quay")
        checkpoint(stage="diagnostic_replay", missing_loads=missing_loads, quay_pending=quay,
                   static_inventory_matches_actual_missing=sorted(watch) == missing_loads,
                   production_diagnostics=dict(engine.diagnostics), production_stats=asdict(engine.stats()))
        local_cfg = FeasibilityConfig(extra={"completion_screens": 0})
        local = make_feasibility_engine(instance, space, config=local_cfg)
        for action in actions:
            local.push(action)
        local_flat = set(map(int, local.legal_index()))
        production_flat = set(map(int, engine.legal_index()))
        box_ids, slot_ids, n_slots = local.index_boxes, local.index_slots, local.n_slots
        count = lambda cid, indices: sum(int(flat // n_slots) == box_ids.index(cid) for flat in indices)
        boxes = {box.id: box for box in instance.containers}
        visibility_caps = compute_visibility_caps(instance)
        open_cells = {next((cid for cid in stack.cell_ids if cid not in ex.occ), None)
                      for stack in ex.stacks.values()}
        for cid in missing_loads:
            box = boxes[cid]
            expected_cells = 1 if box.length_ft == 20 else 2
            geometric = [slot for slot in slot_ids if len(cells_of_slot(slot)) == expected_cells]
            opened = [slot for slot in geometric if set(cells_of_slot(slot)) <= open_cells]
            row["boxes"].append(dict(box=box.model_dump(mode="json"),
                where=ex.where.get(cid), geometric_slots=len(geometric), open_slots=opened,
                production_legal=count(cid, production_flat), local_d55_legal=count(cid, local_flat)))
        detailed = sorted(row["boxes"], key=lambda item: (item["local_d55_legal"], item["box"]["id"]))[:args.detailed_boxes]
        checkpoint(stage="candidate_probes", diagnostic_config=asdict(local_cfg),
                   diagnostic_qualification="L1/L2+D55 admission only; post-state C1/C2 for attribution; no shipping")
        for item in detailed:
            cid = item["box"]["id"]
            bi = box_ids.index(cid)
            local_slots = [slot_ids[flat % n_slots] for flat in sorted(local_flat) if flat // n_slots == bi]
            candidate_slots = list(dict.fromkeys(local_slots + item["open_slots"]))
            for slot in candidate_slots[:args.slot_probes]:
                action = Place(cid, slot)
                flat = local.index_of(action)
                probe = dict(box=cid, slot=slot, production_mask_legal=flat in production_flat,
                             local_d55_mask_legal=flat in local_flat,
                             securing_prefix_rejection=securing_geometry_violation(instance, ex, action))
                child_ex = ex.copy()
                try:
                    child_ex.step(action)
                    roofs = {}
                    for cell in cells_of_slot(slot):
                        stack = child_ex.stacks[child_ex.cells[cell].stack_id]
                        members = set(stack.cell_ids)
                        heights = [boxes[other].height_m + (boxes[other].oog.over_height_m if boxes[other].oog else 0.)
                                   for other, location in child_ex.where.items() if location[0] == "ship"
                                   and members.intersection(cells_of_slot(location[1]))]
                        roof = stack.base_z_m + sum(heights)
                        cap = visibility_caps.top_by_stack[stack.id]
                        roofs[stack.id] = dict(reconstructed_roof_m=roof, necessary_cap_m=cap,
                                              clearance_m=cap-roof)
                    probe["d55_reconstructed_clearances"] = roofs
                    ship = instance.onboard.model_copy(update={"placements": [
                        Placement(container_id=box, slot=location[1]) for box, location in child_ex.where.items()
                        if location[0] == "ship" and box != cid]})
                    violations = placement_violations(instance, ship, boxes[cid], slot)
                    probe["placement_violations"] = [v.model_dump(mode="json") for v in violations if v.severity == "hard"]
                except (ValueError, KeyError, TypeError) as error:
                    probe["move_or_placement_error"] = f"{type(error).__name__}: {error}"
                if probe["local_d55_mask_legal"]:
                    child = local.copy()
                    child.push(action)
                    completion = child.completion_check()
                    probe["post_candidate_completion"] = completion
                    probe["failed_completion_checks"] = [key for key, value in completion.items() if not value]
                    probe["diagnostic_complete"] = child.is_complete()
                    if child.is_complete():
                        probe["securing_final_rejections"] = list(securing_state_violations(instance, child_ex))
                row["candidate_probes"].append(probe)
                checkpoint(last_probed_box=cid, last_probed_slot=slot)
            item["detailed_candidates_total"] = len(candidate_slots)
            item["detailed_candidates_probed"] = min(len(candidate_slots), args.slot_probes)
            item["candidate_probe_exhaustive"] = len(candidate_slots) <= args.slot_probes
        checkpoint(stage="finished", diagnostic_stats=asdict(local.stats()))
    except (Exception, ProbeDeadline) as error:
        checkpoint(error=f"{type(error).__name__}: {error}", timed_out=isinstance(error, ProbeDeadline),
                   interrupted_stage=row.get("stage"), stage="stopped")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        ending = sources(args.pin)
        save(output / "source-end.json", ending)
        checkpoint(source_drift=sorted(key for key in set(baseline) | set(ending) if baseline.get(key) != ending.get(key)),
                   peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**30 if sys.platform == "darwin" else 2**20))
    print(json.dumps({key: row.get(key) for key in ("case", "stage", "elapsed_s", "missing_loads", "error", "source_drift", "peak_rss_gb")}))


if __name__ == "__main__":
    main()
