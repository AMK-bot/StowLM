"""Guard, qualify, atomically promote, then verify the fresh production epoch."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

RUN = Path(__file__).resolve().parent
OWNER = RUN.parent
ROOT = RUN.parents[3]
ACCOUNT = OWNER.parent/"fix-t3-linear/d49_run.py"
LOGS = RUN/"finish-logs"
LOGS.mkdir(exist_ok=True)
SHIPS = ("feeder_500", "feeder_1000", "feeder_1700")


def run(name, args, *, always=False):
    prefix = LOGS/name
    execution = prefix.with_suffix(".execution.json")
    if execution.exists() and not always:
        old = json.loads(execution.read_text())
        if old.get("exit_code") == 0:
            print(json.dumps(dict(reused_command=name)), flush=True)
            return
        raise ValueError(f"preserve failed checkpoint for review before retry: {execution}")
    command = [sys.executable, str(ACCOUNT), str(prefix), sys.executable, *map(str, args)]
    process = subprocess.Popen(command)
    status = process.wait()
    print(json.dumps(dict(finished=name, exit_code=status)), flush=True)
    if status:
        raise RuntimeError(f"{name} failed; see {prefix}.log")


def ready():
    ordinary = RUN/"ordinary-logs/campaign.execution.json"
    if not ordinary.exists() or not json.loads(ordinary.read_text()).get("passed"):
        return False
    for ship in SHIPS:
        if not (RUN/"ordinary"/ship/"equivalence.json").exists():
            return False
        for version in (1, 2, 3):
            for phase in ("train", "holdout"):
                path = RUN/f"edges-v{version}"/ship/f"necessary-edges-{phase}.json"
                if not path.exists() or not json.loads(path.read_text()).get("complete"):
                    return False
    return (RUN/"edges-v3/feeder_500/necessary-review.json").exists()


def public_checks():
    run("cache-warm", ["-m", "stowage.cache_warm"])
    run("public-validate", [OWNER/"public_validate.py", "--root", RUN/"ordinary",
        "--output", RUN/"public-validation.json", "--latency-variants", "10"])
    run("aliases", [RUN/"alias_validate.py"])
    run("production-smoke", [RUN/"production_smoke.py"])


def replay_checks():
    run("holdout-replay", [RUN/"verification_campaign.py", "--phase", "ordinary", "--workers", "3"])
    run("input-manifest", [RUN/"verification_campaign.py", "--phase", "manifest"])


def main():
    started = time.monotonic()
    (RUN/"finish-controller.pid").write_text(str(os.getpid())+"\n")
    while not ready():
        if time.monotonic()-started > 2400:
            raise TimeoutError("fresh campaign evidence did not finish within wait budget")
        time.sleep(5)
    print(json.dumps(dict(evidence_ready_utc=datetime.now(timezone.utc).isoformat())), flush=True)
    run("guard-pre-candidate", [RUN/"closure/guard.py", "--check"], always=True)
    common = [OWNER/"promote.py", "--input", RUN/"ordinary", "--output", RUN/"promotion",
        "--include-necessary", "--necessary-edge-input", RUN/"edges-v3",
        "--necessary-calibration-enrichment", RUN/"edges-v1", RUN/"edges-v2"]
    run("candidate", common)
    run("candidate-gates", [RUN/"verification_campaign.py", "--phase", "candidate",
        "--bank", RUN/"promotion/candidate-bank.json"])
    run("margin-comparison", [RUN/"compare_reserves.py"])
    run("guard-pre-apply", [RUN/"closure/guard.py", "--check"], always=True)
    run("promotion", [*common, "--apply"])
    assert json.loads((RUN/"promotion/report.json").read_text())["applied"] is True
    # Six numerical CPUs at most: cache_warm's three ship workers and three
    # independent replay shards. Public tests start once their fresh evidence
    # manifest and all exact replay files have been published.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(public_checks), pool.submit(replay_checks)]
        for future in futures:
            future.result()
    run("linear-feasibility-tests", ["-m", "pytest", "tests/linear", "tests/feasibility", "-q",
        "-o", "junit_family=legacy", "--junitxml", RUN/"linear-feasibility-tests.xml"])
    run("results", [RUN/"summarize.py"])
    run("guard-final", [RUN/"closure/guard.py", "--check"], always=True)
    result = dict(passed=True, seconds=time.monotonic()-started,
        finished_utc=datetime.now(timezone.utc).isoformat())
    (RUN/"finish-execution.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
