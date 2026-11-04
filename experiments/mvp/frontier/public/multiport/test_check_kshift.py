"""Hand-made cases for the replay checker. Each plan's right answer was worked out on paper.

Run: .venv/bin/python -I test_check_kshift.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kshift import Bad, check_tr1, check_tr4  # noqa: E402


def expect_bad(fn, inst, plan, text):
    try:
        fn(inst, plan)
    except Bad as exc:
        assert text in str(exc), exc
        return
    raise AssertionError(f"accepted a plan with: {text}")


# Three ports, one stack, two tiers. Boxes: 0: 0->2, 1: 0->1, 2: 1->2.
F = [[0, 1, 1], [0, 0, 1], [0, 0, 0]]
inst = {"N": 3, "B": 1, "C": 1, "R": 2, "m": {"F": F}}
boxes = [[0, 2], [0, 1], [1, 2]]

res = check_tr1(inst, {"boxes": boxes, "grids": [[[0, 1]], [[0, 2]]]})
assert res["rehandles"] == 0 and res["full_every_leg"]

# The 0->1 box under the 0->2 box: the upper box must come off at port 1, even if it goes back.
res = check_tr1(inst, {"boxes": boxes, "grids": [[[1, 0]], [[0, 2]]]})
assert res["rehandles"] == 1 and res["per_port"] == [0, 1]

# Same box lifted although the grid shows it unmoved: the box below it left, so it counts.
res = check_tr1(inst, {"boxes": [[0, 1], [0, 2], [1, 2]], "grids": [[[0, 1]], [[2, 1]]]})
assert res["rehandles"] == 1

expect_bad(check_tr1, inst, {"boxes": boxes, "grids": [[[0, 1]]]}, "one grid per")
expect_bad(check_tr1, inst, {"boxes": boxes[:2], "grids": [[[0, 1]], [[0, None]]]}, "transport matrix")
expect_bad(check_tr1, inst, {"boxes": boxes, "grids": [[[0, 1]], [[0, 1]]]}, "wrong boxes")
expect_bad(check_tr1, inst, {"boxes": boxes, "grids": [[[None, 0]], [[0, 2]]]}, "hanging")

# TR4: three ports, two bays of one stack, two tiers. Weights picked so the moments are easy.
zero = [[0] * 3 for _ in range(3)]
m = {f"{p}[{w}]": [row[:] for row in zero] for p in "TF" for w in ("Light", "Medium", "Heavy")}
m["T[Heavy]"][0][2] = 2  # two 21 t 20' boxes, 0->2
m["F[Light]"][0][1] = 1  # one 10 t 40', 0->1
m["F[Light]"][1][2] = 1  # one 10 t 40', 1->2
inst4 = {"N": 3, "B": 2, "C": 1, "R": 2, "m": m}
boxes4 = [[0, 2, 20, 21], [0, 2, 20, 21], [0, 1, 40, 10], [1, 2, 40, 10]]
e = [None, None]
good = {
    "boxes": boxes4,
    "grids": [
        [[[0, 1], [2, 2]], [e, e]],  # pair of 20' low, 40' on top, in bay 0
        [[[0, 1], [3, 3]], [e, e]],
    ],
}
res = check_tr4(inst4, good)
assert res["rehandles"] == 0
# Bay 0 is 20 ft aft of midships, so a ship loaded only there has LCG -20 ft: exactly at the limit.
assert abs(res["max_abs_lcg_ft"] - 20.0) < 1e-9 and res["lcg_ok"]
# Moment about the centreline of two tiers: (42 * -0.5 + 10 * 0.5) / 52 < 0.
assert res["vcg_ok"] and abs(res["max_vcg_tiers"] - (-16.0 / 52.0)) < 1e-9

expect_bad(check_tr4, inst4, {"boxes": boxes4, "grids": [[[[2, 2], [0, 1]], [e, e]], [[[0, 1], [3, 3]], [e, e]]]}, "20' on 40'")
expect_bad(check_tr4, inst4, {"boxes": boxes4, "grids": [[[[0, None], [2, 2]], [[1, None], e]], [[[0, 1], [3, 3]], [e, e]]]}, "half-full")
expect_bad(check_tr4, inst4, {"boxes": boxes4, "grids": [[[e, [2, 2]], [[0, 1], e]], [[[0, 1], [3, 3]], [e, e]]]}, "hanging")

# A 40' over a pair where only one 20' leaves: the 40' is lifted through one column, and the box
# in the other column stays. Here 40' 0->2 sits on 20's 0->1 and 0->2.
m2 = {k: [row[:] for row in zero] for k in m}
m2["T[Light]"][0][1] = 1
m2["T[Light]"][0][2] = 1
m2["T[Light]"][1][2] = 1
m2["F[Light]"][0][2] = 1
inst5 = {"N": 3, "B": 1, "C": 1, "R": 2, "m": m2}
boxes5 = [[0, 1, 20, 7], [0, 2, 20, 7], [1, 2, 20, 7], [0, 2, 40, 10]]
plan5 = {"boxes": boxes5, "grids": [[[[0, 1], [3, 3]]], [[[2, 1], [3, 3]]]]}
res = check_tr4(inst5, plan5)
assert res["rehandles"] == 1 and res["per_port"] == [0, 1]

print("ok")
