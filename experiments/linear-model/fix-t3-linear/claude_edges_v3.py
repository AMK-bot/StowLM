"""Eight bounded lattice shards, then three explicit TRAIN merges/new holdouts."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SAMPLER = HERE.parent/"epoch_requalification/edge_grid_v3.py"
COUNTS = {"feeder_500": 3, "feeder_1000": 3, "feeder_1700": 2}


def run(ship, phase, index=0):
    prefix = HERE/f"claude-edge-v3-{ship}-{phase}-{index}"
    command = [sys.executable, str(HERE/"d49_run.py"), str(prefix), sys.executable,
        str(SAMPLER), "--ship", ship, "--phase", phase,
        "--prepared", str(HERE/"epoch-claude"), "--output", str(HERE/"epoch-claude-v3"),
        "--prior-training-root", str(HERE/"epoch-claude-v2"),
        "--shard-count", str(COUNTS[ship]), "--shard-index", str(index)]
    process = subprocess.Popen(command)
    if process.wait():
        raise RuntimeError(f"v3 sampler failed: {ship}/{phase}/{index}")


def main():
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(run, ship, "lattice", index)
            for ship, count in COUNTS.items() for index in range(count)]
        for future in as_completed(futures):
            future.result()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, ship, "finalize") for ship in COUNTS]
        for future in as_completed(futures):
            future.result()


if __name__ == "__main__":
    main()
