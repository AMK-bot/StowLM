"""Recheck only the prepared public/synthetic fix-window evidence references."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "experiments/mvp/fixwindow/d126-20261030"


def main():
    pins = json.loads((ROOT / "experiments/mvp/fixwindow/manual/source-pins.json").read_text())
    rows = []
    for relative, expected in pins["input_files"].items():
        actual = sha256((ROOT / relative).read_bytes()).hexdigest()
        rows.append(dict(path=relative, expected_sha256=expected, actual_sha256=actual,
                         matches=actual == expected))
    record = dict(observed_utc=datetime.now(timezone.utc).isoformat(), references=rows,
                  passed=all(row["matches"] for row in rows),
                  qualification="Source/evidence integrity only; full manual checker tests qualify physics.")
    (OUT / "references.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(dict(passed=record["passed"], reference_count=len(rows))))
    assert record["passed"], record


if __name__ == "__main__":
    main()
