"""Offline, single-core judge rehearsal of an already copied candidate tree.

Run with --tree COPY --cache LOCAL_UV_CACHE --archive LOCAL_KCS_ZIP --out NEW_DIR.
The output directory must be new: prior evidence is never overwritten. Source
files in COPY are not repaired; only the fresh environment and data setup write
there. Numerical probes use ordinary synthetic unit fixtures, never test v2.1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


GUARD = r'''
import ipaddress, json, os, socket
from pathlib import Path
def allowed(host):
    if isinstance(host, bytes): host=host.decode('ascii','replace')
    if host in (None,'','localhost'): return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False
def deny(host):
    with Path(os.environ['REHEARSAL_NETWORK_LOG']).open('a') as f:
        f.write(json.dumps({'pid':os.getpid(),'blocked_host':str(host)})+'\n')
    raise RuntimeError('External network disabled by judge rehearsal')
for name in ('getaddrinfo','gethostbyname','gethostbyname_ex'):
    original=getattr(socket,name)
    def wrapped(host,*a,_original=original,**kw):
        if not allowed(host): deny(host)
        return _original(host,*a,**kw)
    setattr(socket,name,wrapped)
for name in ('connect','connect_ex'):
    original=getattr(socket.socket,name)
    def wrapped(self,address,*a,_original=original,**kw):
        if isinstance(address,tuple) and not allowed(address[0]): deny(address[0])
        return _original(self,address,*a,**kw)
    setattr(socket.socket,name,wrapped)
'''

WORKER = r'''
import io, json, os, pathlib, runpy, sys
root=pathlib.Path.cwd().resolve()
# A worker outside the copy needs the same cwd search entry as ``python -m``.
sys.path.insert(0,str(root))
out=pathlib.Path(os.environ['REHEARSAL_OUT'])
pathlib.Path(os.environ['REHEARSAL_OWNED_PID']).write_text(json.dumps({'pid':os.getpid()}))
def save(name,obj): (out/(name+'.json')).write_text(json.dumps(obj,indent=2,default=str)+'\n')
def origins():
    result={}
    for name,module in list(sys.modules.items()):
        if name.split('.')[0] in ('stowage','llm','app','eval') and getattr(module,'__file__',None):
            p=pathlib.Path(module.__file__).resolve()
            assert p.is_relative_to(root),(name,str(p))
            result[name]=str(p)
    save(os.environ['REHEARSAL_LABEL']+'-origins',result)
def fixture():
    from stowage.io import load_instance
    return load_instance('data/fixtures/empty_ship.json')
def main():
    mode=sys.argv[1]
    if mode=='origins':
        import stowage,llm,app
        from stowage.params import repo_root
        assert repo_root().resolve()==root
    elif mode=='data-setup':
        from stowage.data_setup import main
        return main(['--archive',sys.argv[2],'--root',str(root)])
    elif mode=='tests':
        import pytest
        return pytest.main(sys.argv[2:])
    elif mode in ('app-help','eval-help'):
        sys.argv=['app' if mode=='app-help' else 'eval.harness','--help']
        runpy.run_module(sys.argv[0],run_name='__main__')
    elif mode=='checker':
        from stowage.model import PlannerResult
        from stowage.checker.full import check,report_matches_inputs
        instance=fixture()
        result=PlannerResult.model_validate(json.loads(pathlib.Path('app/tests/fixtures/model_run/instances/fixture_empty_ship/result.json').read_text())['result'])
        report=check(instance,result.plan)
        binding=report_matches_inputs(report,instance,result.plan)
        assert binding
        save('checker',{'passed':report.passed,'binding':binding,'classification':'diagnostic synthetic fixture, not an approved demo plan','report':report.model_dump(mode='json')})
    elif mode=='securing':
        from stowage.actions import get_action_space
        from stowage.feasibility import FeasibilityConfig,make_feasibility_engine
        from llm.securing import SecuringEngine
        instance=fixture().model_copy(deep=True)
        instance.vessel.hull.hull_offsets_ref='params/fleet/feeder_500.toml#hull_form'
        # Exercise real pinned fleet asset loading without cold-building every
        # fleet tank. This hybrid unit fixture is not a real fleet plan.
        inner=make_feasibility_engine(instance,get_action_space('boxslot'),config=FeasibilityConfig(extra={'global_screen':0}))
        secured=SecuringEngine(inner)
        result={'constructed':True,'hull_reference':'params/fleet/feeder_500.toml#hull_form','bounds_available':secured.screen.bounds_available,'unavailable_reason':secured.screen.unavailable_reason,'metrics':secured.securing_metrics(),'classification':'synthetic unit fixture with real checksum-pinned fleet hull reference; no full fleet vessel or approved plan claim'}
        save('securing',result)
        assert result['bounds_available'],result
    elif mode=='app-http':
        from http.client import HTTPResponse
        from app.backend import build_replay,make_demo_plan
        from app.flow import DemoService
        from app.server import make_handler
        from app.instruct import InstructService
        instance=fixture(); plan=make_demo_plan(instance)
        payload=build_replay(instance,plan)
        service=DemoService(instance,plan,initial_replay=payload,discover=False)
        handler=make_handler(payload,service,test_authority='localhost',stage_options={'live_physics':False,'live_baseline':False})
        handler.instruct_live=False
        class MemorySocket:
            def __init__(self,request=b''): self.incoming=io.BytesIO(request);self.outgoing=bytearray()
            def makefile(self,mode,*args): return self.incoming
            def sendall(self,data): self.outgoing.extend(data)
        paths=['/api/health','/api/demo','/api/stage','/api/stage/explain','/api/stage/explain?live=1','/api/instruct/examples','/','/stage','/stage-director.css','/stage-shots.js','/stage-director.js','/stage-bays.js','/stage.js','/stage-instruct.css','/stage-instruct.js']
        responses=[]
        for path in paths:
            transport=MemorySocket(f'GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n'.encode())
            handler(transport,('127.0.0.1',12345),object())
            response=HTTPResponse(MemorySocket(bytes(transport.outgoing)));response.begin();raw=response.read()
            item={'path':path,'status':response.status,'bytes':len(raw),'content_type':response.headers.get('content-type')}
            (out/('http-'+path.replace('/','_').replace('?','_').replace('=','_')+'.body')).write_bytes(raw)
            if path=='/api/demo' and response.status==200:
                data=json.loads(raw);item.update(checker_passed=data['checker']['passed'],shippable=data['metadata']['shippable'],validation_status=data['metadata']['validation_status'],explanation_source=data.get('explanation',{}).get('source'))
                assert not item['checker_passed'] and not item['shippable']
                assert item['explanation_source']=='template'
            if path.startswith('/api/stage/explain') and response.status==200:
                data=json.loads(raw);item.update(source=data.get('source'),live_used=data.get('live_used'))
                assert item['source']=='template' and item['live_used'] is False
            responses.append(item)
        instructions=InstructService(None,live=False)
        refusal=instructions.parse('Judge rehearsal unrecorded instruction. Do not call Spark.')
        assert refusal['cost_usd']=='0' and refusal['refusal']['code']=='no_recorded_response'
        save('app-http',{'classification':'production HTTP handler with in-memory socket; no bound TCP/browser/visual claim','responses':responses,'keyless_instruction_refusal':refusal,'live_physics':False,'live_baseline':False,'discovery':False})
        assert all(item['status']==200 for item in responses),responses
    else: raise ValueError(mode)
    return 0
try:
    raise SystemExit(main())
finally:
    origins()
'''

TESTS = [
    "tests/model/test_io.py", "tests/model/test_instance.py",
    "tests/model/test_geometry.py", "tests/model/test_fixtures.py",
    "tests/actions/test_occupancy.py", "tests/rules/test_imdg_class_table.py",
    "tests/vessel/test_tanks_geometry.py", "tests/vessel/test_hull_offsets.py",
    "tests/llm/test_mask_port_cache.py", "app/tests/test_identity.py",
    "tests/data_setup", "tests/llm/maskspeed/test_securing_fail_closed.py",
    "app/tests/test_server.py",
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tree", "cache", "archive", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reuse-env", action="store_true", help="Harness recovery only: retain a previously installed scratch environment")
    parser.add_argument("--steps", nargs="+", choices=("sync", "origins", "data-setup", "fast-tests", "securing", "app-help", "eval-help", "checker", "app-http"), help="Optional explicitly partial rehearsal; default runs every step")
    args = parser.parse_args(argv)
    tree, cache, archive, out = [getattr(args, n).resolve() for n in ("tree", "cache", "archive", "out")]
    if not tree.is_dir() or not cache.is_dir() or not archive.is_file():
        parser.error("tree/cache/archive must already exist")
    if (tree / ".venv").exists() and not args.reuse_env:
        parser.error("fresh-copy rehearsal requires no existing .venv")
    out.mkdir(parents=True, exist_ok=False)
    runtime = tree.parent / ("rehearsal-runtime-" + out.name)
    runtime.mkdir(exist_ok=False)
    guard = runtime / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD)
    worker = runtime / "worker.py"
    worker.write_text(WORKER)
    shutil.copyfile(worker, out / "worker.py")
    shutil.copyfile(guard / "sitecustomize.py", out / "sitecustomize.py")
    shutil.copyfile(Path(__file__).resolve(), out / "rehearse.py")
    copied_archive = runtime / "kcs_offsets.ZIP"
    shutil.copyfile(archive, copied_archive)
    env = os.environ.copy()
    for key in tuple(env):
        if key in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONHOME", "SPARK_CREDIT_READY", "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"} or any(part in key.upper() for part in ("API_KEY", "ACCESS_TOKEN", "SECRET")):
            env.pop(key, None)
    env.update(PYTHONPATH=str(guard), UV_CACHE_DIR=str(cache), UV_OFFLINE="1",
        UV_PYTHON_DOWNLOADS="never", UV_CONCURRENT_DOWNLOADS="1", UV_CONCURRENT_INSTALLS="1",
        UV_CONCURRENT_BUILDS="1", UV_LINK_MODE="copy", PYTHONDONTWRITEBYTECODE="1",
        HF_HOME=str(runtime / "empty-hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1", NUMEXPR_NUM_THREADS="1", RAYON_NUM_THREADS="1",
        TOKENIZERS_PARALLELISM="false", REHEARSAL_OUT=str(out),
        REHEARSAL_NETWORK_LOG=str(out / "network-blocks.jsonl"),
        STOWAGE_VESSEL_CACHE_DIR=str(runtime / "vessel-cache"))
    state = {"tree": str(tree), "cache": str(cache), "runtime": str(runtime),
        "archive_sha256": hashlib.sha256(copied_archive.read_bytes()).hexdigest(),
        "estimate": {"cores": 1, "ram_gb": 2, "gpu": False, "wall_s": 180},
        "jobs": [], "completed": False, "test_v21_inputs_used": False,
        "model_weights_or_tokenizer_tested": False, "approved_demo_bundle_tested": False}
    state["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    state["reused_environment"] = args.reuse_env
    selected = set(args.steps or ("sync", "origins", "data-setup", "fast-tests", "securing", "app-help", "eval-help", "checker", "app-http"))
    state["selected_steps"] = sorted(selected)
    state["partial_rehearsal"] = bool(args.steps)

    def save():
        (out / "summary.json").write_text(json.dumps(state, indent=2) + "\n")

    def run(label, command):
        row = {"label": label, "command": command, "cwd": str(tree), "reaped": False}
        state["jobs"].append(row)
        child_record = out / (label + ".owned.json")
        child_env = dict(env, REHEARSAL_LABEL=label, REHEARSAL_OWNED_PID=str(child_record))
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        def limits():
            resource.setrlimit(resource.RLIMIT_CPU, (120, 125))
        with (out / (label + ".stdout")).open("wb") as stdout, (out / (label + ".stderr")).open("wb") as stderr:
            process = subprocess.Popen(command, cwd=tree, env=child_env, stdout=stdout, stderr=stderr, preexec_fn=limits)
            row.update(pid=process.pid, owned_pids=[process.pid])
            save()
            timed_out = False
            try:
                code = process.wait(timeout=180)
            except subprocess.TimeoutExpired:
                timed_out = True
                if child_record.exists():
                    owned = json.loads(child_record.read_text())["pid"]
                    row["owned_pids"].append(owned)
                    save()
                    try:
                        os.kill(owned, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                process.terminate()
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait(timeout=5)
        if child_record.exists():
            row["owned_pids"] = sorted(set(row["owned_pids"] + [json.loads(child_record.read_text())["pid"]]))
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        rss = after.ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        row.update(exit_code=code, reaped=True, timed_out=timed_out,
            wall_s=time.monotonic() - started,
            cpu_s=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
            waited_children_peak_rss_bytes_cumulative=rss)
        save()
        print(f"{label}: exit={code}, {row['wall_s']:.3f}s wall", flush=True)
        return code

    save()
    if "sync" in selected and run("sync", ["uv", "sync", "--offline"]) != 0:
        return 1
    common = ["uv", "run", "--offline", "--no-sync", "python", str(worker)]
    if "origins" in selected:
        run("origins", common + ["origins"])
    setup_code = run("data-setup", common + ["data-setup", str(copied_archive)]) if "data-setup" in selected else 0
    if setup_code == 0:
        if "fast-tests" in selected:
            run("fast-tests", common + ["tests", *TESTS, "-q", "-m", "not slow and not gpu and not net",
                "-p", "no:cacheprovider", "--basetemp", str(runtime / "pytest"), "--junitxml", str(out / "tests.xml")])
        if "securing" in selected:
            run("securing", common + ["securing"])
    for label in ("app-help", "eval-help", "checker", "app-http"):
        if label in selected:
            run(label, common + [label])
    junit = out / "tests.xml"
    if junit.exists():
        suites = list(ET.parse(junit).getroot().iter("testsuite"))
        state["tests"] = {key: sum(int(suite.attrib.get(key, 0)) for suite in suites)
                          for key in ("tests", "errors", "failures", "skipped")}
    network = out / "network-blocks.jsonl"
    state["network_blocked_attempt_count"] = len(network.read_text().splitlines()) if network.exists() else 0
    state["completed"] = True
    state["operational_steps_passed"] = all(row["exit_code"] == 0 for row in state["jobs"])
    save()
    return 0 if state["operational_steps_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
