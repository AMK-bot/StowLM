"""Fresh D58 pilot: canonical production specs, rejection retry, C2 required.

Every requested seed is retained, including rejected/error shards. There is no
replacement selection after observing a rejection. Production metadata guards
remain active, so moving source is reported rather than silently accepted.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
             "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[name] = "1"
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE.parent))
from cache_runtime import redirect_cache, enforce_scope


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def selections():
    from stowage.generate.instances_v3 import scenario_specs
    # A fresh seed offset preserves the producer's canonical distributions.
    specs = scenario_specs(seed=10_280_000, profiles=["tiny", "small", "medium"],
                           c2_screened=True)
    selected = []
    for rung in ("tiny", "small", "medium"):
        for ship in ("feeder_500", "feeder_800", "feeder_1700"):
            for generator in ("random_yard", "simulated_yard"):
                rows = [row for row in specs if row["size"] == rung
                        and row["vessel"] == ship and row["generator"] == generator]
                selected.extend(rows[:1 if rung == "medium" else 4])
    assert len(selected) == 54
    return selected


def snapshot():
    from stowage.generate.instances_v3 import physics_metadata
    files = [ROOT / "src/stowage/generate/instances_v3.py",
             *sorted((ROOT / "src/stowage/solver").rglob("*.py"))]
    return {"physics": physics_metadata(c2_screened=True),
            "additional_sources": {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest()
                                   for p in files}}


class GenerationDeadline(BaseException): pass

def worker(index, runtime):
    def expired(signum, frame):raise GenerationDeadline("generation outer cap expired")
    signal.signal(signal.SIGTERM,expired);signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,295)
    redirect_cache(runtime)
    enforce_scope()
    from stowage.generate.instances_v3 import shard
    started = time.perf_counter()
    spec = selections()[index]
    before = snapshot()
    row = dict(index=index, spec=spec, source_start=before,
               started_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    try:
        row.update(shard((str(HERE / "generation" / "instances"),
                          {**spec, "physics_sha256": before["physics"]["sha256"]})))
    except (Exception,GenerationDeadline) as exc:
        row.update(status="error", error_type=type(exc).__name__, error=str(exc),
                   traceback=traceback.format_exc())
    signal.setitimer(signal.ITIMER_REAL,0)
    try:
        from stowage.solver.ballast_deadline import shutdown_worker
        shutdown_worker()
    except Exception:pass
    row.update(source_end=snapshot(), elapsed_s=time.perf_counter()-started,
               peak_rss_gb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss /
                   (1024**3 if sys.platform == "darwin" else 1024**2))
    row["source_changed"] = row["source_start"] != row["source_end"]
    atomic(HERE / "generation" / "outcomes" / f"{index:03d}.json", row)
    print(json.dumps({k: row.get(k) for k in ("index", "status", "path", "elapsed_s",
                                             "peak_rss_gb", "error", "source_changed")}), flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int)
    parser.add_argument("--runtime", type=Path, default=HERE / "runtime")
    parser.add_argument("--manifest", action="store_true")
    args = parser.parse_args()
    if args.manifest:
        # The selected canonical requests are fixed before sampling.
        atomic(HERE / "generation" / "selection.json", dict(
            planned=54, specs=selections(), source=snapshot(),
            assumptions=["4 tiny/small canonical prefix specs per ship and generator; all four ports.",
                         "2 medium per ship, one canonical prefix spec per generator.",
                         "Production max_attempts=50; C2 required; no n_boxes override."]))
    if args.index is not None:
        if not 0 <= args.index < 54:
            parser.error("index must be 0..53")
        worker(args.index, args.runtime)


if __name__ == "__main__":
    main()
