"""Independent scalar oracle for aligned long-box capacity after a placement."""
from types import SimpleNamespace

import numpy as np

from stowage.feasibility._engine import IncrementalEngine


def test_pair_capacity_matches_scalar_oracle_with_shared_pairs():
    rng = np.random.default_rng(8044)
    pairs = np.array([(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (1, 5)], dtype=np.int64)
    member = np.full((6, 3), -1, dtype=np.int64)
    for j in range(6):
        ids = np.flatnonzero((pairs == j).any(axis=1))
        member[j, :len(ids)] = ids
    e = object.__new__(IncrementalEngine)
    e._pairs, e._member = pairs, member
    e.T = SimpleNamespace(units=np.array([1, 2, 2, 1, 2, 1, 2, 2]), stack_ids=tuple(range(6)))
    rem = np.arange(8)
    for _ in range(100):
        fill, usable = rng.integers(0, 4, size=(2, 6))
        st = SimpleNamespace(fill=fill)
        if int(e._pair_cnt(st, usable).sum()) < int((e.T.units[rem] == 2).sum()):
            continue  # no candidate can rescue a prefix that already violates this necessary bound
        boxes = rng.integers(0, 8, size=40)
        stacks = rng.integers(0, 6, size=(40, 2))
        stacks[:, 1] = np.where(rng.random(40) < .5, -1, stacks[:, 1])
        same = stacks[:, 0] == stacks[:, 1]
        stacks[same, 1] = -1
        new = [np.minimum(rng.integers(0, 4, size=40), np.maximum(usable[np.maximum(stacks[:, n], 0)] - 1, 0))
               for n in range(2)]
        expected = []
        for k, b in enumerate(boxes):
            f, u = fill.copy(), usable.copy()
            for n in range(2):
                j = stacks[k, n]
                if j >= 0:
                    f[j] += 1
                    u[j] = new[n][k]
            capacity = sum(max(min(f[a] + u[a], f[c] + u[c]) - max(f[a], f[c]), 0)
                           for a, c in pairs)
            expected.append(capacity >= int((e.T.units[rem] == 2).sum()) - int(e.T.units[b] == 2))
        actual = e._c1c(st, rem, usable, boxes, stacks, [], new)
        assert np.array_equal(actual, expected)
