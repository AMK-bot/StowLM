"""Launch nohup jobs in separate sessions, with persistent logs and PID files."""
import os
from pathlib import Path
import subprocess
import sys

OUT = Path(__file__).resolve().parent
env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
           VECLIB_MAXIMUM_THREADS="1", MKL_NUM_THREADS="1",
           PYTHONDONTWRITEBYTECODE="1", UV_NO_SYNC="1", UV_OFFLINE="1",
           UV_CACHE_DIR=str(OUT / "uv-cache"))
for ship in sys.argv[1:]:
    command = (["/bin/sh", str(OUT / "run_tests.sh")] if ship == "pytest" else
               ["uv", "run", "--offline", "--no-sync", "python",
                str(OUT / "run_ship.py"), ship])
    with (OUT / f"{ship}-detached.log").open("w") as log:
        process = subprocess.Popen(["nohup", *command], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   env=env, start_new_session=True, close_fds=True)
    (OUT / f"{ship}-detached.pid").write_text(str(process.pid)+"\n")
    print(ship, process.pid, flush=True)
