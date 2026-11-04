"""Cheap corpus progress; audit presence, not raw instance count, marks completion."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "experiments/mvp/corpus"
TOOLS = Path(__file__).resolve().parent


def main():
    counts = {}
    failures = []
    for split in ("test", "showcase", "train", "val"):
        rows = list((CORPUS / split).rglob("*.audit.json"))
        counts[split] = dict(Counter(p.relative_to(CORPUS / split).parts[0]
                                    if split != "showcase" else "all" for p in rows))
        failures.extend(str(p.relative_to(CORPUS)) for p in (CORPUS / split).rglob("*.failure.json"))
    cpu, ram = 0, 0
    for line in (ROOT / "experiments/fleet/ledger.md").read_text().splitlines():
        if not line.startswith("|"):
            continue
        columns = [c.strip() for c in line.split("|")]
        try:
            cpu_match = re.search(r"[0-9.]+", columns[3])
            ram_match = re.search(r"[0-9.]+", columns[4])
            if cpu_match and ram_match:
                cpu += float(cpu_match.group())
                ram += float(ram_match.group())
        except (ValueError, IndexError, AttributeError):
            pass
    workers = {}
    for i in range(8):
        path = TOOLS / f"trainval-{i}.log"
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        rows = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("status") in {"generated", "resumed", "failed"}:
                rows.append(row)
        workers[i] = dict(completed=len(rows), errors=sum(r.get("status") == "failed" for r in rows),
            ended=any('"elapsed_wall_s"' in line for line in lines),
            traceback=any("Traceback" in line for line in lines))
    print(json.dumps(dict(at=datetime.now().astimezone().isoformat(), counts=counts,
                         failures=failures[:5], ledger_cpu=cpu, ledger_ram_gb=ram,
                         workers=workers), sort_keys=True))


if __name__ == "__main__":
    main()
