"""Only cheap synthetic builders; subprocess target for the singleflight guard."""
from __future__ import annotations

import argparse
import fcntl
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(os.environ["D132_RUNTIME_ROOT"]).resolve()
HERE = Path(__file__).resolve().parent


def append(path, value):
    with path.open("a") as stream:
        stream.write(json.dumps(value) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--die-with-lock", action="store_true")
    parser.add_argument("--acquire-after-death", action="store_true")
    args = parser.parse_args()
    directory = args.directory
    if args.die_with_lock or args.acquire_after_death:
        from stowage.cache_singleflight import cache_singleflight
        with cache_singleflight(directory / "dead-owner.json", enabled=True) as acquired:
            assert acquired
            if args.die_with_lock:
                (directory / "owner-acquired").write_text(str(os.getpid()))
                os._exit(7)  # Intentional abrupt exit, no signal or foreign PID.
            print(json.dumps({"acquired_after_abrupt_owner_exit": True, "pid": os.getpid()}), flush=True)
        return
    # A second OS process exercises locking, while this shared lane serializes
    # imports and all cache/JSON work. Release it only during blocking waits.
    original_flock = fcntl.flock
    lane_descriptor = os.open(directory / "one-cpu-lane.lock", os.O_CREAT | os.O_RDWR, 0o600)

    def acquire_cpu():
        original_flock(lane_descriptor, fcntl.LOCK_EX)

    def release_cpu():
        original_flock(lane_descriptor, fcntl.LOCK_UN)

    acquire_cpu()
    source = ROOT / "src/stowage/vessel/fleet_cache.py"
    name = "stowage.vessel.synthetic_singleflight_process"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    module.__file__ = str(ROOT / "src/stowage/vessel/fleet_cache.py")
    exec(compile(source.read_text(), str(source), "exec"), module.__dict__)
    module._cache_key = lambda *args: "synthetic-content-key"
    module._cache_dir = lambda: directory / "cache"
    def observed_flock(descriptor, operation):
        if operation == fcntl.LOCK_EX:
            append(directory / "lock-attempts.jsonl", {"pid": os.getpid()})
            release_cpu()
            try:
                return original_flock(descriptor, operation)
            finally:
                acquire_cpu()
        return original_flock(descriptor, operation)

    fcntl.flock = observed_flock  # Test observation only, never a production hook.
    class SyntheticPayload:
        def __init__(self, value):
            self.value = value

        def model_dump_json(self):
            return json.dumps(self.value, sort_keys=True, separators=(",", ":"))

        @classmethod
        def model_validate_json(cls, payload):
            value = json.loads(payload)
            if value != {"synthetic": True, "values": [1, 2, 3]}:
                raise ValueError("Invalid synthetic guard payload")
            return cls(value)

    # Real Vessel JSON/nested-mutation equality is covered by the unit guards;
    # process coordination uses this tiny deterministic stand-in, no builder.
    module.Vessel = SyntheticPayload
    vessel = SyntheticPayload({"synthetic": True, "values": [1, 2, 3]})

    def build():
        append(directory / "builds.jsonl", {"pid": os.getpid()})
        deadline = time.monotonic() + 20
        release_cpu()
        try:
            while not (directory / "release-builders").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("guard controller did not release synthetic builder")
                time.sleep(0.01)
        finally:
            acquire_cpu()
        return vessel

    result = module.cached_vessel("synthetic", slice_name="full", with_hull=False,
                                 params_bytes=b"", build=build)
    print(json.dumps({"pid": os.getpid(), "json_sha256": sha256(result.model_dump_json().encode()).hexdigest(),
                      "expected_json_sha256": sha256(vessel.model_dump_json().encode()).hexdigest()}), flush=True)
    os.close(lane_descriptor)


if __name__ == "__main__":
    main()
