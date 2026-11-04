"""Schedule unchanged independent-holdout replay prefixes ahead of the queue."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

RUN = Path(__file__).resolve().parent
OUT = RUN/"replay-prefetch"
LOGS = RUN/"replay-prefetch-logs"
LEVELS = ("light", "medium", "full")
THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")


def audit_module():
    spec = importlib.util.spec_from_file_location("prefix_audit", RUN/"d49_holdout_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_publish(audit, level, pins, *, frozen_path=None, expected_count=48):
    import numpy as np
    for pin in pins:
        assert audit.pin(audit.ROOT/pin["path"]) == pin
    source_path = RUN/"ordinary/feeder_1700"/f"holdout-{level}.json"
    source = json.loads(source_path.read_text())
    owner = audit.driver()
    _, _, _, before = audit.prepare("feeder_1700", level, source, owner)
    raw = json.loads((RUN/"ordinary/feeder_1700/raw.json").read_text())
    assert raw["physical_inputs"] == before
    source_prefix = frozen_path or OUT/"replay/feeder_1700"/f"{level}.json"
    prefix = json.loads(source_prefix.read_text())
    binding = dict(source=audit.pin(source_path), physical_inputs=before,
        states_sha256=audit.digest(source["list_row_samples"]))
    assert prefix["input_binding"] == binding
    assert prefix["physical_inputs_end"] == before
    assert prefix["ship"] == "feeder_1700" and prefix["level"] == level
    assert prefix["complete"] is False and prefix["expected_samples"] == 64
    assert len(prefix["states"]) == expected_count
    for index, replay in enumerate(prefix["states"]):
        saved = source["list_row_samples"][index]
        assert replay["index"] == index and replay["sample_sha256"] == audit.digest(saved)
        for name in ("trim_m", "list_rad", "draught_m", "sf_n", "bm_nm"):
            observed = np.asarray(replay["exact"][name])
            expected = np.asarray(saved["exact_"+name])
            assert np.allclose(observed, expected, rtol=1e-9, atol=1e-6)
            assert replay["recorded_replay_max_absolute_differences"][name] == float(np.max(np.abs(observed-expected)))
        assert np.isclose(replay["exact"]["displacement_kg"], saved["mass_kg"], rtol=1e-12, atol=1e-5)
    for pin in pins:
        assert audit.pin(audit.ROOT/pin["path"]) == pin
    destination = RUN/"holdout-audit/replay/feeder_1700"/f"{level}.json"
    canonical_pid = RUN/"verification-logs"/f"ordinary-replay-feeder_1700-{level}.pid"
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = dict(validated=True, source=audit.pin(source_prefix), source_pins=pins,
        destination=str(destination), samples=expected_count)
    if destination.exists() or canonical_pid.exists():
        result.update(published=False, reason="canonical replay or PID already exists")
    else:
        try:
            os.link(source_prefix, destination)
            result.update(published=True, reason="atomic exclusive hardlink")
        except FileExistsError:
            result.update(published=False, reason="canonical replay appeared; exclusive publication skipped")
    return result


def worker(level):
    audit = audit_module()
    pins = [audit.pin(RUN/"d49_holdout_audit.py"), audit.pin(RUN.parent/"driver.py"),
            audit.pin(RUN/"ordinary/feeder_1700"/f"holdout-{level}.json")]
    start = time.monotonic()
    command = ["/usr/bin/nice", "-n", "10", sys.executable,
        str(RUN/"d49_holdout_audit.py"), "--phase", "replay", "--ship", "feeder_1700",
        "--level", level, "--limit", "48", "--input-root", str(RUN/"ordinary"),
        "--output", str(OUT)]
    env = os.environ.copy()
    env.update({name:"1" for name in THREADS})
    record = dict(command=command, wrapper_pid=os.getpid(), source_pins=pins)
    path = LOGS/f"{level}.execution.json"
    with (LOGS/f"{level}.log").open("w") as stream:
        proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
        record["child_pid"] = proc.pid
        path.write_text(json.dumps(record,indent=2)+"\n")
        status = proc.wait()
    record.update(exit_code=status,numerical_seconds=time.monotonic()-start,
        peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    path.write_text(json.dumps(record,indent=2)+"\n")
    if status:
        raise RuntimeError(f"replay prefix failed for {level}")
    record["publication"] = validate_publish(audit,level,pins)
    record["total_seconds"] = time.monotonic()-start
    path.write_text(json.dumps(record,indent=2)+"\n")


def run(level):
    env = os.environ.copy()
    env.update({name:"1" for name in THREADS})
    with (LOGS/f"{level}-wrapper.log").open("w") as stream:
        proc = subprocess.Popen([sys.executable,str(Path(__file__)),"worker",level],
            stdout=stream,stderr=subprocess.STDOUT,env=env)
        (LOGS/f"{level}.wrapper.pid").write_text(str(proc.pid)+"\n")
        status = proc.wait()
    if status:
        raise RuntimeError(f"replay prefix wrapper failed for {level}")


if __name__ == "__main__":
    LOGS.mkdir(exist_ok=True)
    if len(sys.argv)>1 and sys.argv[1]=="worker":
        worker(sys.argv[2])
    else:
        start=time.monotonic()
        (LOGS/"controller.pid").write_text(str(os.getpid())+"\n")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(run,level) for level in LEVELS]):
                future.result()
        (LOGS/"campaign.execution.json").write_text(json.dumps(dict(
            passed=True,workers=3,seconds=time.monotonic()-start),indent=2)+"\n")
