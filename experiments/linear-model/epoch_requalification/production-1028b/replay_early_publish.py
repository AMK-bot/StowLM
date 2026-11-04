"""Freeze and publish a valid observed prefix before queued canonical replay."""
import importlib.util
import json
import os
from pathlib import Path
import time

RUN=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("prefetch_publish",RUN/"replay_prefetch.py")
prefetch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(prefetch)
audit=prefetch.audit_module()
started=time.monotonic()
pending=set(prefetch.LEVELS)
results=[]
while pending:
    for level in sorted(pending):
        destination=RUN/"holdout-audit/replay/feeder_1700"/f"{level}.json"
        marker=RUN/"verification-logs"/f"ordinary-replay-feeder_1700-{level}.pid"
        if destination.exists() or marker.exists():
            results.append(dict(level=level,published=False,reason="canonical already began"))
            pending.remove(level)
            continue
        source=RUN/"replay-prefetch/replay/feeder_1700"/f"{level}.json"
        if not source.exists():
            continue
        frozen=source.read_bytes()
        record=json.loads(frozen)
        count=len(record["states"])
        if count<16:
            continue
        snapshot=RUN/"replay-prefetch-logs"/f"{level}-publication-{count:02d}.json"
        with snapshot.open("xb") as stream:
            stream.write(frozen)
        execution=json.loads((RUN/"replay-prefetch-logs"/f"{level}.execution.json").read_text())
        result=prefetch.validate_publish(audit,level,execution["source_pins"],
            frozen_path=snapshot,expected_count=count)
        result.update(level=level,frozen_samples=count)
        results.append(result)
        pending.remove(level)
        (RUN/"replay-prefetch-logs/early-publication.json").write_text(json.dumps(dict(
            helper_pid=os.getpid(),seconds=time.monotonic()-started,results=results),indent=2)+"\n")
    if pending:
        time.sleep(1)
(RUN/"replay-prefetch-logs/early-publication.json").write_text(json.dumps(dict(
    helper_pid=os.getpid(),seconds=time.monotonic()-started,results=results),indent=2)+"\n")
print(json.dumps(results),flush=True)
