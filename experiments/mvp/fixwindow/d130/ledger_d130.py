"""Reserve/release only this executive's heavy-job row under the fleet lock."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OWNER = "mvp-construct/D130-showcase-references-lane1"
LEDGER = ROOT / "experiments/fleet/ledger.md"
OUT = ROOT / "experiments/mvp/fixwindow/d130"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("queue", "reserve", "finish"))
    parser.add_argument("--job", required=True)
    parser.add_argument("--estimate-s", type=float, default=900)
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()
    receipt = OUT / f"ledger-{args.job}.json"
    with LEDGER.open("r+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        lines = stream.readlines()
        own = [line for line in lines if line.startswith(f"| {OWNER} |")]
        if args.action == "queue":
            if own:
                raise RuntimeError("owned row already exists")
            record = {"job": args.job, "status": "queued", "cores": 0,
                      "observed_utc": datetime.now(timezone.utc).isoformat()}
            lines.append(f"\n| {OWNER} | D130 {args.job} | 0 | 0 | no | QUEUED: needs1core/4GB; pilot est{args.estimate_s:g}s; no worker running | {record['observed_utc']} |\n")
        elif args.action == "reserve":
            if (own and any(float(line.split('|')[3].strip()) != 0 for line in own)) or receipt.exists():
                raise RuntimeError("owned row/job already exists")
            lines = [line for line in lines if not line.startswith(f"| {OWNER} |")]
            cores = ram = 0.
            for line in lines:
                fields = [field.strip() for field in line.split("|")]
                if not line.startswith("|") or len(fields) < 9:
                    continue
                try:
                    cores += float(fields[3]); ram += float(fields[4])
                except ValueError:
                    pass
            admission = json.loads((OUT / "admission.json").read_text())
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(admission["observed_utc"])).total_seconds()
            if not admission["fits"] or age > 90 or cores + 1 > 16 or ram + 4 > 112:
                raise RuntimeError("fresh admission/reservations do not fit; queue")
            record = {"job": args.job, "estimate": {"cores": 1, "ram_gb": 4, "gpu": False, "wall_s": args.estimate_s},
                      "started_utc": datetime.now(timezone.utc).isoformat(), "admission": admission, "status": "reserved"}
            lines.append(f"\n| {OWNER} | D130 {args.job} | 1 | 4 | no | {args.estimate_s:g}s; native1; owned checkpointed worker | {record['started_utc']} |\n")
        else:
            if len(own) != 1 or args.record is None:
                raise RuntimeError("one owned row and completed process receipt required")
            record = json.loads(receipt.read_text())
            actual = json.loads(args.record.read_text())
            if not actual.get("reaped"):
                raise RuntimeError("worker has not been reaped")
            lines = [line for line in lines if not line.startswith(f"| {OWNER} |")]
            record.update(status="finished", finished_utc=datetime.now(timezone.utc).isoformat(),
                          process_receipt=str(args.record), actual={key: actual.get(key) for key in
                          ("owned_pid", "wall_s", "cpu_s", "peak_waited_child_rss_bytes", "exit_code", "source_drift")})
        stream.seek(0); stream.write("".join(lines)); stream.truncate(); stream.flush()
        (OUT / ("ledger-queued.json" if args.action == "queue" else receipt.name)).write_text(json.dumps(record, indent=2) + "\n")
    if args.action == "finish":
        with (ROOT / "experiments/fleet/history.md").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.write(f"\n- {record['finished_utc']} {OWNER}/{args.job}: estimate 1core/4GB/noGPU/{record['estimate']['wall_s']:g}s; actual {json.dumps(record['actual'], sort_keys=True)}; receipt {receipt.relative_to(ROOT)}.\n")
    print(json.dumps({key: value for key, value in record.items() if key != "admission"}))


if __name__ == "__main__":
    main()
