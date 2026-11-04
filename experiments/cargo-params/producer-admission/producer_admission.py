"""Producer admission audit with per-attempt drop attribution (w2-cargo-c, plan 2.5).

Runs the unmodified public random_yard producer (rule freeze on, C2 off) and records,
for every candidate attempt, which DG boxes it carried and which gate rejected it.
Observation only: screen_load_list is wrapped by a pass-through recorder that returns
the original result unchanged, and the report's rejection dicts log their writes.
No gate is disabled, no demand relabelled. Seed 710 must reproduce the v1 load list.

Usage (repo root): uv run python experiments/cargo-params/producer-admission/producer_admission.py \
    --profiles dg_stress feeder_export --seeds 710 711 712 --output <json>
"""
import argparse
from collections import Counter
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import signal
import time

from stowage.generate import random_yard
from stowage.rules.catalogue import rule_freeze_v1

BUCKETS = {"completion.c1": "c1_screen", "background.acceptance": "background_gate",
           "background.construction": "background_gate", "expert.physical.slot_occupancy": "slot_admission",
           "expert.physical.size_fit": "slot_admission", "imdg.stowage.3_2": "dg_adapter_screen"}


class LogDict(dict):
    """dict that records (attempt, key) for every write; values behave unchanged."""

    def __init__(self, report, log, name):
        super().__init__()
        self._report, self._log, self._name = report, log, name

    def __setitem__(self, key, value):
        self._log.append((self._report.attempts, self._name, key))
        super().__setitem__(key, value)


def classify(box):
    return None if box.dg is None else {"class": box.dg.imdg_class, "un": f"{int(box.dg.un_number):04d}"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", nargs="+", default=["dg_stress", "feeder_export"])
    parser.add_argument("--seeds", nargs="+", type=int, default=[710])
    parser.add_argument("--max-attempts", type=int, default=20)
    parser.add_argument("--case-timeout", type=int, default=420)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.with_suffix(".pid").write_text(f"{os.getpid()}\n")
    paths = sorted([*Path("src/stowage").rglob("*.py"), *Path("params").rglob("*.toml")])

    def pins():
        return {str(path): sha256(path.read_bytes()).hexdigest() for path in paths}

    initial = pins()
    started = time.perf_counter()
    result = {"definition": "admitted = DG boxes in the returned Instance.load_list; drop reason = gate that "
              "rejected the candidate attempt carrying the box", "size": "tiny", "vessel": "feeder_500",
              "port": "laem_chabang_like", "blocking_ratio": 0.3, "c2_screen": False, "activation": True,
              "max_attempts": args.max_attempts, "seeds": args.seeds, "runs": [],
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest(), "pid": os.getpid()}

    def save():
        result["wall_s"] = time.perf_counter() - started
        result["max_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        tmp = output.with_suffix(".partial")
        tmp.write_text(json.dumps(result, sort_keys=True, indent=2, default=str) + "\n")
        tmp.replace(output)

    def timeout(signum, frame):
        raise TimeoutError(f"case exceeded {args.case_timeout}s budget")

    signal.signal(signal.SIGALRM, timeout)
    original_screen = random_yard.screen_load_list
    with rule_freeze_v1(enabled=True):
        for profile in args.profiles:
            for seed in args.seeds:
                report = random_yard.RandomGenerationReport()
                log, candidates = [], {}
                for name in ("rejections", "completion_rejections", "background_rejections"):
                    setattr(report, name, LogDict(report, log, name))

                def recording_screen(vessel, boxes, _report=report, _candidates=candidates):
                    # First call in an attempt sees the full candidate; later calls are single-box probes.
                    _candidates.setdefault(_report.attempts, [classify(box) for box in boxes])
                    return original_screen(vessel, boxes)

                random_yard.screen_load_list = recording_screen
                entry = {"profile": profile, "seed": seed, "status": "running"}
                result["runs"].append(entry)
                save()
                print("START", profile, seed, flush=True)
                case_started = time.perf_counter()
                signal.alarm(args.case_timeout)
                instance = None
                try:
                    instance = random_yard.generate_random_instance("tiny", seed=seed, blocking_ratio=.3,
                        vessel_name="feeder_500", port_name="laem_chabang_like", report=report,
                        max_attempts=args.max_attempts, c2_screen=False, cargo_profile=profile,
                        d62_crossing_share=0.)
                    assert report.accepted is True
                    entry["status"] = "emitted"
                except Exception as error:
                    entry.update(status="not_emitted", error_type=type(error).__name__, error=str(error))
                finally:
                    signal.alarm(0)
                    random_yard.screen_load_list = original_screen
                attempts = []
                for attempt in range(1, report.attempts + 1):
                    keys = sorted({key for a, name, key in log if a == attempt and name == "rejections"})
                    detail = sorted({f"{name}:{key}" for a, name, key in log
                                     if a == attempt and name != "rejections"})
                    boxes = candidates.get(attempt)
                    last = attempt == report.attempts
                    if keys:
                        outcome = "rejected"
                    elif last and entry["status"] == "emitted":
                        outcome = "emitted"
                    elif boxes is None:
                        outcome, keys = "rejected", ["draw.unsupported_cargo"]
                    else:
                        outcome = "unresolved"  # e.g. timeout mid-attempt
                    buckets = sorted({BUCKETS.get(key, f"other:{key}") for key in keys})
                    attempts.append({"attempt": attempt, "outcome": outcome, "rejections": keys, "buckets": buckets,
                                     "detail": detail, "n_boxes": None if boxes is None else len(boxes),
                                     "dg": [row for row in (boxes or []) if row]})
                if instance is not None:
                    by_id = {box.id: box for box in instance.containers}
                    emitted = [classify(by_id[cid]) for cid in instance.load_list]
                    entry["load_list"] = list(instance.load_list)
                    entry["admitted_dg"] = [row for row in emitted if row]
                    # Cross-check the producer's own admitted audit and our last-attempt record.
                    observed = Counter(row["class"] for row in entry["admitted_dg"])
                    entry["admitted_matches_report"] = dict(observed) == report.cargo_draws["admitted"]["dg_by_class"]
                    entry["admitted_matches_last_attempt"] = (sorted(map(str, attempts[-1]["dg"]))
                                                              == sorted(map(str, entry["admitted_dg"])))
                entry["attempts"] = attempts
                entry["cargo_draws"] = {key: report.cargo_draws.get(key) for key in
                                        ("initial_requested", "admitted", "cargo_call_counts")}
                entry["stages"] = {stage: {k: v.get(k) for k in ("n_boxes", "dg_by_class", "dg_by_un")}
                                   for stage, v in report.cargo_draws.get("stages", {}).items()}
                entry["rejections"] = dict(report.rejections)
                entry["completion_rejections"] = dict(report.completion_rejections)
                entry["background_rejections"] = dict(report.background_rejections)
                entry["wall_s"] = time.perf_counter() - case_started
                entry["source_changes"] = [p for p, d in pins().items() if initial.get(p) != d]
                save()
                print(json.dumps({k: entry.get(k) for k in ("profile", "seed", "status", "wall_s", "error",
                      "admitted_dg", "admitted_matches_report", "admitted_matches_last_attempt")}, default=str),
                      flush=True)
                for row in attempts:
                    print("  ", json.dumps(row), flush=True)
    save()
    print("TOTAL", result["wall_s"], "max_rss_bytes", result["max_rss_bytes"], flush=True)


if __name__ == "__main__":
    main()
