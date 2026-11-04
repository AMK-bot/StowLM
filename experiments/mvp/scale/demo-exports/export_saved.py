"""Export the largest saved V6 TRAIN passes; freshly check imports serially.

No planning, repair, rule changes, plugin installation or verdict cache is used.
Only files beneath this script's directory are written.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
SCALE = HERE.parent
ROOT = SCALE.parents[2]
RUN = SCALE / "runs/v6"
BASE = SCALE / "runtime/base"
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "HIGHS_THREADS",
           "BLIS_NUM_THREADS", "STOWAGE_IN_PORT_WORKERS")


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def write(path, value):
    assert path.resolve().is_relative_to(HERE)
    tmp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    tmp.replace(path)


def guards():
    run = read(RUN / "run.json")
    declaration = read(RUN / "declaration.json")
    assert sha(RUN / "run.json") == declaration["run_sha256"]
    assert sha(BASE / "COMPLETE.json") == run["base_snapshot_sha256"]
    base = read(BASE / "COMPLETE.json")
    for name, expected in base["sources"].items():
        assert sha(BASE / name) == expected, name
        if name.startswith("src/"):
            assert sha(ROOT / name) == expected, name
    assert read(RUN / "completion.json")["status"] == "complete"
    return run, dict(runtime_files=len(base["sources"]),
                     workspace_src_files=sum(p.startswith("src/") for p in base["sources"]),
                     run_sha256=sha(RUN / "run.json"),
                     base_manifest_sha256=sha(BASE / "COMPLETE.json"))


def prepare():
    run, binding = guards()
    candidates = []
    for job in run["cohort"]["maps"]:
        folder = RUN / "maps" / job["map_id"]
        summary = read(folder / "summary.json")
        if not summary.get("passed"):
            continue
        instance = read(ROOT / job["input"])
        result = read(folder / "finished.json")
        report = read(folder / "report.json")
        assert job["split"] == "train" and "/train/" in job["input"]
        assert sha(ROOT / job["input"]) == job["input_sha256"]
        assert sha(folder / "finished.json") == summary["finished_sha256"]
        assert sha(folder / "report.json") == summary["report_sha256"]
        assert result["status"] == "ok" and not result["repaired"]
        assert report["passed"] is True and not report["violations"]
        assert report["count"]["loads"] == len(instance["load_list"]) == job["n_loads"]
        candidates.append(dict(map_id=job["map_id"], requested_load_boxes=len(instance["load_list"]),
                               checked_load_boxes=report["count"]["loads"], moves=summary["moves"], job=job))
    candidates.sort(key=lambda row: (-row["requested_load_boxes"], row["moves"], row["map_id"]))
    selected = []
    for rank, candidate in enumerate(candidates[:2], 1):
        folder = HERE / (f"{rank:02d}-" + candidate["map_id"])
        folder.mkdir(exist_ok=True)
        job = candidate["job"]
        source = RUN / "maps" / candidate["map_id"]
        instance = read(ROOT / job["input"])
        finished = read(source / "finished.json")
        result = dict(finished, schema_version=finished["plan"]["schema_version"])
        shutil.copyfile(ROOT / job["input"], folder / "instance.json")
        write(folder / "result.json", result)
        write(folder / "plan.json", finished["plan"])
        shutil.copyfile(source / "report.json", folder / "original-report.json")
        write(folder / "import.json", dict(instance=instance, input_sha256=job["input_sha256"],
                                           result=result, check_report=read(source / "report.json")))
        selected.append(dict(rank=rank, folder=folder.name, **candidate,
            source_files={"instance": {"path":job["input"], "sha256":sha(ROOT/job["input"])},
                          **{name:{"path":str((source/name).relative_to(ROOT)), "sha256":sha(source/name)}
                             for name in ("finished.json", "report.json", "summary.json")}},
            exported_files={name:sha(folder/name) for name in
                            ("instance.json", "result.json", "plan.json", "original-report.json", "import.json")}))
    manifest = dict(format="scale-demo-export-1", prepared_at=now(), source_binding=binding,
        ranking="Descending requested/load-list box count; ties ascending checked weighted nonproductive moves, then map_id",
        all_saved_passing_candidates=candidates, selected=selected,
        app_import_contract=dict(source_files={str(p.relative_to(ROOT)):sha(p) for p in
            (ROOT/"app/inputs.py", ROOT/"app/identity.py", ROOT/"app/__main__.py", ROOT/"app/README.md")},
            payload="{instance: complete original input, input_sha256: original file hash, result: versioned PlannerResult, check_report: certificate}",
            parser="app.inputs.load_inputs(plan_path=import.json, instance_path=instance.json)",
            no_corpus_resolution="Explicit original instance and embedded complete instance, same verified original-file checksum"),
        fresh_check_policy="Serial fresh processes; unchanged full.check_reference calls full.check with six optional numerical caches bypassed. Canonical projection/baseline and parsed-parameter caches remain as in the unchanged API. No stored verdict is accepted; no independent implementation claim.")
    write(HERE/"manifest.json", manifest)
    return manifest


def child(folder_name, recover=False):
    started=time.monotonic()
    folder=HERE/folder_name
    assert folder.parent == HERE and folder.is_dir()
    for key in THREADS:
        assert os.environ.get(key) == "1", key
    run,binding=guards()
    manifest=read(HERE/"manifest.json")
    selected=next(x for x in manifest["selected"] if x["folder"]==folder_name)
    for name, expected in selected["exported_files"].items():
        if recover and name=="import.json":
            continue  # Only its certificate envelope was already updated.
        assert sha(folder/name)==expected, name
    for item in selected["source_files"].values():
        assert sha(ROOT/item["path"])==item["sha256"], item["path"]
    for name,expected in manifest["app_import_contract"]["source_files"].items():
        assert sha(ROOT/name)==expected,name
    sys.path.insert(0,str(BASE/"src"))
    sys.path.insert(1,str(ROOT))
    from app.inputs import load_inputs
    import app.inputs as app_inputs
    from stowage.checker.full import check_reference, report_matches_inputs
    from stowage.rules.catalogue import rule_freeze_v1
    import stowage.checker.full as full
    assert Path(full.__file__).resolve().is_relative_to(BASE/"src")
    assert not any(name in sys.modules for name in ("oracle_v5", "finisher_v3", "carrier_v6"))
    loaded=load_inputs(instance_path=folder/"instance.json",plan_path=folder/"import.json")
    original=read(folder/"plan.json")
    imported=loaded.plan.model_dump(mode="json")
    # App retains complete result lineage. Any importer normalization must be
    # limited to provenance; no move, ballast, instruction or cargo change.
    assert {k:v for k,v in imported.items() if k!="provenance"} == {k:v for k,v in original.items() if k!="provenance"}
    before=time.monotonic()
    recovery=None
    if recover:
        from stowage.model import CheckReport
        owner=next(x for x in read(HERE/"local-ledger.json")["children"] if x["map_id"] in folder_name)
        assert owner["exit_code"] != 0 and owner.get("ended_at") and not owner.get("timeout")
        assert "AttributeError" in (folder/"fresh-check.log").read_text()
        assert "passed" in (folder/"fresh-check.log").read_text()
        stamp=(folder/"fresh-report.json").stat().st_mtime
        assert datetime.fromisoformat(owner["started_at"]).timestamp() <= stamp <= datetime.fromisoformat(owner["ended_at"]).timestamp()
        report=CheckReport.model_validate(read(folder/"fresh-report.json"))
        elapsed=None
        recovery=dict(reason="Fresh checker completed; export-only PartReport.passed metadata field was invalid. Recovered exact newly generated report without another checker call.",
                      original_owner=owner,loaded_script_sha256=sha(HERE/"export_saved.initial.py.txt"),
                      fresh_report_written_at=datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                      original_process_elapsed_s=(datetime.fromisoformat(owner["ended_at"])-datetime.fromisoformat(owner["started_at"])).total_seconds(),
                      exact_checker_elapsed_s="not retained before metadata formatting error")
    else:
        with rule_freeze_v1(enabled=False):
            report=check_reference(loaded.instance, loaded.plan)
        elapsed=time.monotonic()-before
    report_value=report.model_dump(mode="json")
    if not recover:
        write(folder/"fresh-report.json",report_value)
    assert report.passed is True and not report.violations
    assert report_matches_inputs(report, loaded.instance, loaded.plan)
    expected=read(folder/"original-report.json")
    assert report.count.weighted_nonproductive == expected["count"]["weighted_nonproductive"]
    assert report.count.loads == len(loaded.instance.load_list)
    payload=read(folder/"import.json");payload["check_report"]=report_value
    # A standalone --import-plan has no explicit-instance CLI parameter. The
    # complete embedded world and fresh canonical checker binding establish
    # content identity; retain original-byte checksums in manifest/result sidecars
    # instead of asking the app to rediscover a separate checksum witness.
    payload.pop("input_sha256",None)
    write(folder/"import.json",payload)
    # Exercise the actual import parser again against the new binding; no check.
    corpus_calls=[]
    discovery_codes={app_inputs._corpus_candidates.__code__,app_inputs.find_instance_path.__code__}
    def observe(frame,event,arg):
        if event=="call" and frame.f_code in discovery_codes:
            corpus_calls.append(frame.f_code.co_name)
    previous_profile=sys.getprofile()
    try:
        sys.setprofile(observe)
        again=load_inputs(plan_path=folder/"import.json")
    finally:
        sys.setprofile(previous_profile)
    assert not corpus_calls,corpus_calls
    assert again.plan == loaded.plan and again.instance == loaded.instance
    write(folder/"fresh-check.json",dict(status="passed",at=now(),pid=os.getpid(),
        checker_pid=recovery["original_owner"]["pid"] if recovery else os.getpid(),
        metadata_writer_pid=os.getpid(),
        source_binding=binding, checker_function="stowage.checker.full.check_reference",
        checker_source_sha256=sha(Path(full.__file__)),checker_seconds=elapsed,
        process_elapsed_s=time.monotonic()-started,native_threads={k:os.environ[k] for k in THREADS},
        metadata_recovery=recovery,
        no_repair=True,no_verdict_cache=True,optional_numerical_reuse=False,
        shared_api_caches="Canonical projection/baseline resolution and parsed-parameter LRU remain unchanged",
        original_plan_canonical_sha256=digest(original),imported_plan_canonical_sha256=digest(imported),
        app_provenance_normalization_only=original!=imported,full_checker_input_binding=True,
        standalone_app_parser_validated=True,corpus_discovery_calls=corpus_calls,
        fresh_report_sha256=sha(folder/"fresh-report.json"),original_report_sha256=sha(folder/"original-report.json"),
        export_sha256={p:sha(folder/p) for p in ("instance.json","result.json","plan.json","import.json")},
        parts={k:{"ran":v.ran,"error":v.error,"violations_count":len(v.violations)} for k,v in report.parts.items()},
        count=report.count.model_dump(mode="json"),violations_count=len(report.violations)))


def serial_checks(resume=False):
    manifest=read(HERE/"manifest.json")
    if resume:
        ledger=read(HERE/"local-ledger.json")
        assert ledger["active_child_pid"] is None
        ledger.setdefault("prior_launcher_pids",[]).append(ledger["launcher_pid"])
        ledger["launcher_pid"]=os.getpid()
    else:
        ledger=dict(launcher_pid=os.getpid(),started_at=now(),compute_worker_limit=2,
                    serial_checker_workers=1,native_threads=1,active_child_pid=None,children=[])
    write(HERE/"local-ledger.json",ledger)
    for entry in manifest["selected"]:
        folder=HERE/entry["folder"]
        if resume and (folder/"fresh-check.json").exists():
            entry["fresh_check"]=read(folder/"fresh-check.json")
            assert entry["fresh_check"]["status"]=="passed"
            entry["exported_files"]["import.json"]=sha(folder/"import.json")
            continue
        env=dict(os.environ,**{key:"1" for key in THREADS},PYTHONDONTWRITEBYTECODE="1",STOWLM_NO_CHECK_CACHE="1")
        with (folder/"fresh-check.log").open("w") as log:
            proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--child",entry["folder"]],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            record=dict(pid=proc.pid,map_id=entry["map_id"],started_at=now());ledger["children"].append(record);ledger["active_child_pid"]=proc.pid
            write(HERE/"local-ledger.json",ledger)
            try:record["exit_code"]=proc.wait(timeout=1200)
            except subprocess.TimeoutExpired:
                # Only the exact child PID created and recorded immediately above.
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                record.update(exit_code=proc.returncode,timeout=True)
            record["ended_at"]=now();ledger["active_child_pid"]=None;write(HERE/"local-ledger.json",ledger)
        entry["fresh_check"]=read(folder/"fresh-check.json") if(folder/"fresh-check.json").exists() else dict(status="failed",owner=record)
        entry["exported_files"]["import.json"]=sha(folder/"import.json")
        write(HERE/"manifest.json",manifest)
        print(json.dumps({"map_id":entry["map_id"],"fresh_check":entry["fresh_check"]}),flush=True)
        if record["exit_code"]!=0:break
    ledger["completed_at"]=now();write(HERE/"local-ledger.json",ledger)
    manifest["completed_at"]=now()
    manifest["status"]="passed" if all(x.get("fresh_check",{}).get("status")=="passed" for x in manifest["selected"]) else "failed"
    manifest["script_sha256"]=sha(Path(__file__))
    manifest["app_import_contract"].update(
        payload="{instance: complete original input, result: unchanged versioned PlannerResult, check_report: fresh canonically bound certificate}",
        parser="app.inputs.load_inputs(plan_path=import.json)",
        no_corpus_resolution="Complete embedded world plus fresh canonical checker content binding; actual standalone parser profiled with zero corpus-discovery calls. Original input-file SHA256 remains in manifest.")
    manifest["app_import_contract"].pop("final_payload",None)
    write(HERE/"manifest.json",manifest)
    if manifest["status"]!="passed":raise SystemExit(1)


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--prepare",action="store_true");parser.add_argument("--fresh-checks",action="store_true");parser.add_argument("--resume",action="store_true");parser.add_argument("--recover");parser.add_argument("--child")
    args=parser.parse_args()
    if args.child:
        try:child(args.child)
        except Exception:traceback.print_exc();raise
    elif args.recover:child(args.recover,recover=True)
    elif args.prepare:print(json.dumps(prepare(),indent=2))
    elif args.fresh_checks:serial_checks(resume=args.resume)
    else:parser.error("choose --prepare or --fresh-checks")
