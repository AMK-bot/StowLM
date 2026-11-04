"""The process-pool mask backend is a pure relocation: same allowed sets, statuses and plans as the in-thread
decoder for the same token stream (C2 off stub, so it runs while the linear policy epoch is stale). CPU only.

    uv run pytest -q -p no:cacheprovider experiments/eval-c1/tests/test_pool.py -o addopts=""
"""

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
TINY = HERE.parents[1] / "experiments/solver-diag-1028/pilot-d58/generation/instances/random_yard/tiny/seed-110280000.json"


@pytest.mark.skipif(not TINY.exists(), reason="pilot-d58 tiny input missing")
@pytest.mark.parametrize("workers", [1, 3])
def test_pool_proxies_match_in_thread_decoder(workers):
    import stowage.interfaces  # noqa: F401
    from stowage.frozen import stub_override
    from stowage.io import load_instance

    from host_bench import rollout
    from pool import STUB_REASON, c2_off_engine

    instance = load_instance(TINY)
    with stub_override("feasibility.make_engine", c2_off_engine, reason=STUB_REASON):
        ref, trace = rollout(instance, batch=5, workers=0, max_steps=10_000, seed=3, c2=False)
        got, _ = rollout(instance, batch=5, workers=workers, max_steps=10_000, seed=3, c2=False,
                         check_against=trace)  # raises on the first differing allowed set
    assert got["statuses"] == ref["statuses"] and got["actions_per_row"] == ref["actions_per_row"]
    assert got["lock_steps"] == ref["lock_steps"]
