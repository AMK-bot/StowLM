"""Overlap an unchanged 256-request prefix; exclusively publish if unused."""
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
OWNER = RUN.parent
OUT = RUN / "lattice-prefetch"
LOGS = RUN / "lattice-prefetch-logs"
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")
THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
           "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS")


def load_sampler():
    spec = importlib.util.spec_from_file_location("prefetch_v3", OWNER/"edge_grid_v3.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_publish(ship):
    sampler = load_sampler()
    ctx = sampler.context(ship, RUN/"ordinary")
    source = sampler.shard_path(OUT, ship, 0, 1)
    record = json.loads(source.read_text())
    requests = [sampler.materialize(ctx, request)
                for request in sampler.specifications("train", ctx.base)]
    config = record["sampler_config"]
    assert len(requests) == 800
    assert record["complete"] is False and record["pilot"] is False
    assert record["attempts"] == 256 and len(record["states"]) == 256
    assert record["sampler_source"] == ctx.source
    assert record["sampler_base_source"] == ctx.base_source
    assert record["sampler_source_sha256"] == ctx.source["sha256"]
    assert record["sampler_builder_version"] == sampler.VERSION
    assert record["physical_inputs_start"] == ctx.before == record["physical_inputs_end"]
    assert record["physical_inputs_unchanged"] is True
    assert config["requests_sha256"] == ctx.base.digest(requests)
    assert config["attempts"] == config["new_lattice_attempts"] == 800
    assert config["shard_count"] == 1 and config["shard_index"] == 0
    assert config["training_seeds"] == [54935]
    assert config["sampler_stage"] == "lattice-shard"
    assert config["affine_feasibility_filter"] is False
    assert [state["request"] for state in record["states"]] == requests[:256]
    assert record["samples"] == sum(state["admitted"] for state in record["states"])
    assert record["list_row_samples"] == [state for state in record["states"] if state["admitted"]]
    for name in ("family_calibration_cache_key", "response_calibration_identity",
                 "reference_identity", "family_domain"):
        assert record["calibration"].get(name) == ctx.diagnostics.get(name)
    inputs = dict(physical_inputs=ctx.before, config=config,
        builder_version=sampler.VERSION, source_sha256=ctx.source["sha256"])
    assert record["sampler_inputs_hash"] == ctx.base.digest(inputs)
    sampler.checked_inputs(ctx)
    pin = ctx.base.pin(source)
    destination = sampler.shard_path(RUN/"edges-v3", ship, 0, 1)
    canonical_execution = RUN/"edge-logs"/f"{ship}-v3-lattice.execution.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = dict(validated=True, source=pin, destination=str(destination))
    if destination.exists() or canonical_execution.exists():
        result.update(published=False, reason="canonical shard or execution already exists")
    else:
        try:
            os.link(source, destination)
            result.update(published=True, reason="atomic exclusive hardlink")
        except FileExistsError:
            result.update(published=False, reason="canonical shard appeared; exclusive publication skipped")
    return result


def worker(ship):
    start = time.monotonic()
    command = ["/usr/bin/nice", "-n", "10", sys.executable,
        str(OWNER/"edge_grid_v3.py"), "--ship", ship, "--phase", "lattice",
        "--shard-count", "1", "--shard-index", "0", "--limit", "256",
        "--prepared", str(RUN/"ordinary"), "--output", str(OUT)]
    env = os.environ.copy()
    env.update({name: "1" for name in THREADS})
    record = dict(command=command, wrapper_pid=os.getpid())
    path = LOGS/f"{ship}.execution.json"
    with (LOGS/f"{ship}.log").open("w") as stream:
        proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=env)
        record["child_pid"] = proc.pid
        path.write_text(json.dumps(record, indent=2)+"\n")
        status = proc.wait()
    record.update(exit_code=status, numerical_seconds=time.monotonic()-start,
        peak_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    path.write_text(json.dumps(record, indent=2)+"\n")
    if status:
        raise RuntimeError(f"prefetch numerical command failed for {ship}")
    record["publication"] = validate_publish(ship)
    record["total_seconds"] = time.monotonic()-start
    path.write_text(json.dumps(record, indent=2)+"\n")
    print(json.dumps(record), flush=True)


def run(ship):
    env = os.environ.copy()
    env.update({name: "1" for name in THREADS})
    with (LOGS/f"{ship}-wrapper.log").open("w") as stream:
        proc = subprocess.Popen([sys.executable, str(Path(__file__)), "worker", ship],
            stdout=stream, stderr=subprocess.STDOUT, env=env)
        (LOGS/f"{ship}.wrapper.pid").write_text(str(proc.pid)+"\n")
        status = proc.wait()
    if status:
        raise RuntimeError(f"prefetch worker failed for {ship}")


if __name__ == "__main__":
    LOGS.mkdir(exist_ok=True)
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker(sys.argv[2])
    else:
        start = time.monotonic()
        (LOGS/"controller.pid").write_text(str(os.getpid())+"\n")
        with ThreadPoolExecutor(max_workers=3) as pool:
            for future in as_completed([pool.submit(run, ship) for ship in SHIPS]):
                future.result()
        (LOGS/"campaign.execution.json").write_text(json.dumps(dict(
            passed=True,workers=3,seconds=time.monotonic()-start),indent=2)+"\n")
