"""Never-promoted perf-1028 archive; opt in with --run-feasibility-archive.

Historical shared-source pins intentionally remain fail-closed. This experiment
is excluded before import from the default current-source qualification suite.
"""
import random
import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.feasibility_archive

_LOADER_PATH = Path(__file__).resolve().parents[2] / "experiments/feasibility/perf-1028/candidate.py"
_SPEC = importlib.util.spec_from_file_location("perf_1028_dg_candidate", _LOADER_PATH)
candidate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(candidate)


@pytest.mark.parametrize("name", ["rich_a", "rich_b", "tiny_a", "crowded_b"])
@pytest.mark.parametrize("seed", [1704, 9049])
def test_constant_dg_capacity_matches_enumerated_witnesses(h, request, name, seed):
    instance = request.getfixturevalue(name)
    old = candidate.make_engine(instance, {"global_screen": 0}, optimized=False)
    new = candidate.make_engine(instance, {"global_screen": 0}, optimized=True)
    assert type(old) is not type(new)
    rng = random.Random(seed)
    for _ in range(12):
        np.testing.assert_array_equal(new.legal_index(), old.legal_index())
        assert new.legal_discharges() == old.legal_discharges()
        actions = old.legal_actions()
        if not actions:
            break
        action = rng.choice(actions)
        old.push(action)
        new.push(action)
    if new.depth:
        old.pop()
        new.pop()
        np.testing.assert_array_equal(new.legal_index(), old.legal_index())
