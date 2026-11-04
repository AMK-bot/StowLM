"""Reassign only released retry slots; reserve one slot for continuations."""
import datetime
import json
import time
from dispatch import HERE, save

jobs = json.loads((HERE / "harness-retry-selection.json").read_text())
deadline = datetime.datetime.fromisoformat(json.loads((HERE / "dispatch-admission.json").read_text())["deadline_utc"]).timestamp()
history = []
previous = None
while time.time() < deadline and not (HERE / "dispatch-summary.json").exists():
    remaining = sum(not (HERE / "runs" / solver / f"{index:03d}" / "done.json").exists()
                    for _, index, solver, _ in jobs)
    # A retry pool has at most3 numeric jobs. Its unfinished count only falls.
    # Main + retry + the1 continuation slot therefore never exceeds6.
    limit = 5 - min(3, remaining)
    if limit != previous:
        save(HERE / "control.json", {"workers": limit})
        history.append(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            main_limit=limit, retry_unfinished=remaining,
                            reserved_continuation=1, maximum_numeric_workers=limit + min(3, remaining) + 1))
        save(HERE / "slot-admission-history.json", history)
        previous = limit
    time.sleep(2)
