"""Read-only producer/checker observation, written only to the fix-window fence."""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/mvp/fixwindow/d121-20261030"


def main():
    now = datetime.now(timezone.utc).isoformat()
    path = ROOT / "experiments/mvp/corpus/.runtime/small/campaign.json"
    raw = path.read_bytes()
    campaign = json.loads(raw)
    rows = campaign.get("outcomes", [])
    status = {"observed_utc": now, "producer": {
        "path": path.relative_to(ROOT).as_posix(), "sha256": sha256(raw).hexdigest(),
        "phase": campaign.get("phase"), "workers": campaign.get("workers"),
        "owner_pid": campaign.get("pid"), "phase_requested": campaign.get("phase_requested"),
        "requested": campaign.get("requested"), "outcomes": len(rows),
        "outcomes_by_split_status": dict(Counter(f"{row.get('split')}:{row.get('status')}" for row in rows)),
        "explicit_completion": {key: campaign.get(key) for key in ("complete", "completed", "ended_at_utc", "status")},
    }, "checker_files": {}, "tests": {}}
    for name in ("stability.py", "stability_budget.py"):
        status["checker_files"][name] = sha256((ROOT / "src/stowage/checker" / name).read_bytes()).hexdigest()
    for path in OUT.glob("*-tests.xml"):
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        status["tests"][path.name] = {key: sum(float(suite.attrib.get(key, 0)) for suite in suites)
            for key in ("tests", "failures", "errors", "skipped", "time")}
    (OUT / "STATUS.json").write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status))


if __name__ == "__main__":
    main()
