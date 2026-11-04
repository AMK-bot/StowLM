"""Two builders named build.py (experiments/mvp/ui/replay and experiments/mvp/ui/static_play) in one pytest session.

Each is loaded by path under its own module name; a plain `import build` let whichever came first shadow the other.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_replay_tests_and_the_static_play_tests_pass_in_one_session():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([".", "src", "experiments/mvp/frontier", "experiments/mvp/frontier/lab"])}
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "app/tests/test_v6_third_pass.py",
                          "experiments/mvp/ui/static_play"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    assert "has no attribute" not in run.stdout, run.stdout[-3000:]
    assert run.returncode == 0, run.stdout[-3000:]
