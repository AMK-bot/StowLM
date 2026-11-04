"""Prepare only the runbook's private cache mirror, never build a vessel/table."""
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    run = Path(sys.argv[1]).resolve()
    started = time.perf_counter()
    source = Path("experiments/mvp/showcase_tools/run_hero.py")
    spec = importlib.util.spec_from_file_location("d126_cache_setup", source)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.isolate_caches(run)
    record = {"wall_s": time.perf_counter() - started, "vessels_built": 0,
              "tables_built": 0, "source_changes": False, "cache_root": str(run / "cache"),
              "mirror_marker": (run / "cache/.mirror-complete").read_text(),
              "members": {name: sum(path.is_file() for path in (run / "cache" / name).rglob("*"))
                          for name in ("linear", "stability")},
              "reuse": "Read-only links by the existing runbook convention; misses replace privately; native qualification remains authoritative"}
    (run / "CACHE-PREPARED.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
