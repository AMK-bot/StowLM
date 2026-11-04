"""Generate current, fully gated D58 backgrounds with resumable evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
spec = importlib.util.spec_from_file_location("fresh_bench_helpers", HERE.parent / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=24)
    args = ap.parse_args()
    from stowage.generate.instances_v3 import CAMPAIGN_VERSION, generate, physics_metadata
    from stowage.io import save_instance
    from stowage.feasibility import make_feasibility_engine
    from stowage.actions import get_action_space

    requests_path = HERE / "fresh-planned-requests.json"
    if not requests_path.exists():
        original = json.loads((HERE.parent / "promote/fresh-planned-requests.json").read_text())["requests"]
        requests = []
        for request in original:
            request = {**request, "c2_screened": True, "campaign_version": CAMPAIGN_VERSION,
                       "n_boxes": 4 if request["size"] == "tiny" else 8}
            requests.append(request)
        write(requests_path, dict(requests=requests, created=now(),
            scenario_assumption="Explicit n_boxes 4 tiny / 8 small; normal ship region, full background physics and C2 remain enabled."))
    requests = json.loads(requests_path.read_text())["requests"]
    manifest_path = HERE / "fresh-manifest.json"
    physics = physics_metadata(c2_screened=True)
    sources = bench.pins()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert manifest["physics"] == physics, "Physics changed since fresh corpus admission"
        assert manifest["sources_before"] == sources, "Sources changed since fresh corpus admission"
        for row in manifest["instances"]:
            assert bench.sha(ROOT / row["path"]) == row["sha256"]
            assert bench.sha(ROOT / row["audit_path"]) == row["audit_sha256"]
    else:
        manifest = dict(status="running", requested=24, generated=0, accepted_background_instances=0,
            complete=False, created=now(), instances=[], rejected=[], physics=physics,
            sources_before=sources, environment=bench.runtime(),
            seeds=[1704, 1705, 1706, 1707, 1708],
            planned_requests=dict(path=str(requests_path.relative_to(ROOT)), sha256=bench.sha(requests_path),
                matrix="3 ships x 2 rungs x 2 generators x 2 seeds", c2_screened=True,
                generation_api="stowage.generate.instances_v3.generate(spec)"))
    done = {row["id"] for row in manifest["instances"]} | {row["id"] for row in manifest["rejected"]}
    started = time.perf_counter()
    try:
        for request in requests[:args.limit]:
            if request["id"] in done:
                continue
            manifest.update(status="running", current_request=request, current_started=now())
            write(manifest_path, manifest)
            t0 = time.perf_counter()
            try:
                instance, audit = generate(request)
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                error = dict(id=request["id"], request=request, error=repr(exc), elapsed_s=elapsed,
                             audit=getattr(exc, "audit", None), recorded=now())
                write(HERE / (request["id"] + ".rejected.json"), error)
                manifest["rejected"].append(error)
                write(manifest_path, manifest)
                print("REJECTED", request["id"], round(elapsed, 3), repr(exc), flush=True)
                continue
            assert audit["accepted"] is True and audit["c2_screened"] is True
            assert audit["background_checks"] > 0 and audit["background_last"]["accepted"] is True
            engine = make_feasibility_engine(instance, get_action_space())
            assert engine.diagnostics["global_screen"] == "on", engine.diagnostics
            assert engine.completion_check()["c2"] is True
            audit["fresh_c2_verification"] = dict(global_screen=engine.diagnostics["global_screen"],
                                                  completion=engine.completion_check())
            audit["fresh_sources_after"] = bench.pins()
            assert audit["fresh_sources_after"] == sources, "Sources changed during accepted generation"
            path = HERE / (request["id"] + ".json")
            audit_path = path.with_suffix(".audit.json")
            save_instance(instance, path)
            write(audit_path, audit)
            row = dict(id=request["id"], path=str(path.relative_to(ROOT)), sha256=bench.sha(path),
                uncompressed_sha256=bench.sha(path), generator=request["generator"], rung=request["size"],
                vessel=request["vessel"], load_boxes=len(instance.load_list), measurement=False,
                audit_path=str(audit_path.relative_to(ROOT)), audit_sha256=bench.sha(audit_path),
                c2_screened=True, background_accepted=True, generation_s=time.perf_counter() - t0,
                provenance=instance.provenance.model_dump(mode="json"))
            manifest["instances"].append(row)
            manifest.update(generated=len(manifest["instances"]),
                accepted_background_instances=len(manifest["instances"]), updated=now(),
                maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            write(manifest_path, manifest)
            print("ACCEPTED", request["id"], round(row["generation_s"], 3), "n_boxes", row["load_boxes"], flush=True)
    finally:
        manifest.update(last_process_wall_s=time.perf_counter() - started,
                        sources_after=bench.pins(), updated=now(),
                        maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        manifest["complete"] = len(manifest["instances"]) == 24
        manifest["status"] = "complete" if manifest["complete"] else "partial"
        write(manifest_path, manifest)


if __name__ == "__main__":
    main()
