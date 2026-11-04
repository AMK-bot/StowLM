"""Use the frozen DG-only isolated factory with the audited timing harness."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import resource
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
spec = importlib.util.spec_from_file_location("isolated_bench_helpers", HERE.parent / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)

import candidate

assert Path(candidate.__file__).resolve() == HERE / "candidate.py", "Wrong candidate factory"
original_pins = bench.pins


def pins():
    result = original_pins()
    for path in [HERE / "candidate.py", HERE / "candidate/manifest.json", Path(__file__),
                 *sorted((HERE / "candidate").glob("*.py"))]:
        result[str(path.relative_to(ROOT))] = bench.sha(path)
    return result


bench.pins = pins


def main():
    output = Path(sys.argv[sys.argv.index("--output") + 1])
    event_path = output.with_suffix(".event.json")
    started = time.perf_counter()
    before = pins()
    event = dict(status="running", started=datetime.now(timezone.utc).isoformat(),
                 factory=str(Path(candidate.__file__).relative_to(ROOT)), sources_before=before,
                 environment=bench.runtime(), nice_requested=10,
                 nice_note="Launcher attempts nice -n 10; inspect captured PTY output for sandbox refusal.")
    bench.write(event_path, event)
    try:
        bench.main()
        event["status"] = "complete"
    except BaseException as exc:
        event.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=repr(exc))
        raise
    finally:
        after = pins()
        event.update(process_elapsed_s=time.perf_counter() - started,
                     maxrss_bytes_macos=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     sources_after=after,
                     changed_sources={k: after.get(k) for k, v in before.items() if after.get(k) != v})
        bench.write(event_path, event)


if __name__ == "__main__":
    main()
