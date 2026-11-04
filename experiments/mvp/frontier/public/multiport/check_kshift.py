"""Independent replay checker for k-shift plans (TR1 and TR4). Shares no code with the planner.

Reads the instance file itself, replays the plan port by port, and counts rehandles from positions
only. A reported number cannot come from the planner's arithmetic: the planner's own count is not
read here at all.

Rehandle rule, for one port p, comparing the grid leaving p-1 with the grid leaving p:
  1. A box is lifted if it is due at p, or if it is on board both times and its slot changed.
  2. Anything above a lifted box in the same column is lifted too. A 40' sits in both columns.
  3. Rehandles at p = lifted boxes not due at p. One unload plus reload is 1, for 20' and 40' alike.

Plan format (gzipped JSON): {"format", "file", "boxes": [...], "grids": [...]}.
  TR1 boxes are [o, d], grids[p][stack][tier] is a box id or null.
  TR4 boxes are [o, d, size(20|40), weight], grids[p][stack][tier] is [left, right]; a 40' has its
  id in both, a pair of 20's has two ids, an empty cell has [null, null].

Usage: check_kshift.py <instance-dir> <plan-dir> <out.json> [--limits G_ft]
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
from pathlib import Path

LCG_FT = 20.0
WEIGHTS = {"T": {"Light": 7, "Medium": 14, "Heavy": 21}, "F": {"Light": 10, "Medium": 20, "Heavy": 30}}


def read_instance(path: Path) -> dict:
    """Plain line scanner. Header lines are `Key: value`; a matrix follows its `Name:` line."""
    info, matrices, name = {}, {}, None
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.endswith(":"):
            name = s[:-1]
            matrices[name] = []
        elif ":" in s:
            key, value = s.split(":", 1)
            info[key.strip()] = value.strip()
            name = None
        elif name is not None:
            matrices[name].append(list(map(int, s.split())))
    return {"N": int(info["N"]), "B": int(info["B"]), "C": int(info["C"]), "R": int(info["R"]), "m": matrices}


class Bad(Exception):
    pass


def lifted_tr1(before: list, after: list, due, here: dict) -> set:
    lifted = set()
    for st, column in enumerate(before):
        lift = False
        for tier, box in enumerate(column):
            if box is None:
                continue
            if lift or due(box) or here.get(box) != (st, tier):
                lift = True
                lifted.add(box)
    return lifted


def check_tr1(inst: dict, plan: dict) -> dict:
    N, stacks, R = inst["N"], inst["B"] * inst["C"], inst["R"]
    F = inst["m"]["F"]
    boxes = plan["boxes"]
    grids = plan["grids"]
    if len(grids) != N - 1:
        raise Bad("need one grid per loading port")
    count = [[0] * N for _ in range(N)]
    for o, d in boxes:
        if not 0 <= o < d < N:
            raise Bad("bad box route")
        count[o][d] += 1
    if count != [[0 if j <= i else F[i][j] for j in range(N)] for i in range(N)]:
        raise Bad("boxes do not match the transport matrix")
    prev = [[None] * R for _ in range(stacks)]
    total, per_port, full = 0, [], True
    for p in range(N - 1):
        grid = grids[p]
        if len(grid) != stacks or any(len(c) != R for c in grid):
            raise Bad("grid shape")
        here = {}
        for st, col in enumerate(grid):
            gap = False
            for tier, b in enumerate(col):
                if b is None:
                    gap = True
                    continue
                if gap:
                    raise Bad(f"hanging box at port {p}")
                if b in here:
                    raise Bad(f"box {b} twice at port {p}")
                here[b] = (st, tier)
        want = {i for i, (o, d) in enumerate(boxes) if o <= p < d}
        if set(here) != want:
            raise Bad(f"wrong boxes on board leaving port {p}")
        if len(here) != stacks * R:
            full = False
        lifted = lifted_tr1(prev, grid, lambda b: boxes[b][1] == p, here)
        rh = sum(1 for b in lifted if boxes[b][1] != p)
        total += rh
        per_port.append(rh)
        prev = grid
    return {"ok": True, "rehandles": total, "per_port": per_port, "full_every_leg": full, "boxes": len(boxes)}


def check_tr4(inst: dict, plan: dict) -> dict:
    N, B, C, R = inst["N"], inst["B"], inst["C"], inst["R"]
    stacks = B * C
    m = inst["m"]
    want = {}
    for prefix, size in (("T", 20), ("F", 40)):
        for name, w in WEIGHTS[prefix].items():
            mat = m[f"{prefix}[{name}]"]
            for i in range(N):
                for j in range(N):
                    if mat[i][j]:
                        want[(i, j, size, w)] = want.get((i, j, size, w), 0) + mat[i][j]
    boxes = plan["boxes"]
    have = {}
    for o, d, size, w in boxes:
        if not 0 <= o < d < N:
            raise Bad("bad box route")
        have[(o, d, size, w)] = have.get((o, d, size, w), 0) + 1
    if have != want:
        raise Bad("boxes do not match the transport matrices")
    grids = plan["grids"]
    if len(grids) != N - 1:
        raise Bad("need one grid per loading port")
    prev = [[[None, None] for _ in range(R)] for _ in range(stacks)]
    total, per_port, lcg, vcg = 0, [], [], []
    for p in range(N - 1):
        grid = grids[p]
        if len(grid) != stacks or any(len(c) != R for c in grid):
            raise Bad("grid shape")
        here = {}
        mom_l = mom_v = weight = 0.0
        for st, col in enumerate(grid):
            bay = st // C  # stacks are laid out bay by bay
            d_bay = 40.0 * (bay - (B - 1) / 2.0)
            seen20 = True
            empty_below = False
            for tier, (a, b) in enumerate(col):
                if a is None or b is None:
                    if a is not None or b is not None:
                        raise Bad(f"half-full cell at port {p}")
                    empty_below = True
                    continue
                if empty_below:
                    raise Bad(f"hanging box at port {p}")
                arm = tier + 0.5 - R / 2.0
                if a == b:
                    if boxes[a][2] != 40:
                        raise Bad("20' in both slots")
                    here.setdefault(a, []).extend([(st, tier, 0), (st, tier, 1)])
                    seen20 = False
                    w = boxes[a][3]
                    mom_l += d_bay * w
                    mom_v += arm * w
                    weight += w
                else:
                    if boxes[a][2] != 20 or boxes[b][2] != 20:
                        raise Bad("a slot pair must be two 20'")
                    if not seen20:
                        raise Bad(f"20' on 40' at port {p}")
                    for x, q in enumerate((a, b)):
                        here.setdefault(q, []).append((st, tier, x))
                        w = boxes[q][3]
                        mom_l += (d_bay + (x - 0.5) * 20.0) * w
                        mom_v += arm * w
                        weight += w
        for q, slots in here.items():
            if len(slots) != (2 if boxes[q][2] == 40 else 1):
                raise Bad(f"box {q} in several places at port {p}")
        onboard = {i for i, bx in enumerate(boxes) if bx[0] <= p < bx[1]}
        if set(here) != onboard:
            raise Bad(f"wrong boxes on board leaving port {p}")

        # Lifts: a column-level closure, repeated because a 40' links the two columns.
        before = {}
        for st, col in enumerate(prev):
            for tier, cell in enumerate(col):
                for x in (0, 1):
                    if cell[x] is not None:
                        before.setdefault(cell[x], []).append((st, tier, x))
        lifted = {q for q, slots in before.items() if boxes[q][1] == p or sorted(here.get(q, [])) != sorted(slots)}
        changed = True
        while changed:
            changed = False
            for st, col in enumerate(prev):
                for x in (0, 1):
                    up = False
                    for cell in col:
                        q = cell[x]
                        if q is None:
                            continue
                        if up and q not in lifted:
                            lifted.add(q)
                            changed = True
                        if q in lifted:
                            up = True
        rh = sum(1 for q in lifted if boxes[q][1] != p)
        total += rh
        per_port.append(rh)
        lcg.append(mom_l / weight if weight else 0.0)
        vcg.append(mom_v / weight if weight else 0.0)
        prev = grid
    return {
        "ok": True,
        "rehandles": total,
        "per_port": per_port,
        "max_abs_lcg_ft": max(abs(v) for v in lcg),
        "max_vcg_tiers": max(vcg),
        "min_vcg_tiers": min(vcg),
        "lcg_ok": all(abs(v) <= LCG_FT + 1e-9 for v in lcg),
        "vcg_ok": all(v <= 1e-9 for v in vcg),
        "boxes": len(boxes),
    }


def main() -> None:
    inst_dir, plan_dir, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    results = json.loads(out.read_text()) if out.exists() else {}
    for path in sorted(plan_dir.glob("*.json.gz")):
        key = path.name[: -len(".json.gz")]
        if key in results and results[key].get("mtime") == path.stat().st_mtime:
            continue
        with gzip.open(path, "rt") as fh:
            plan = json.load(fh)
        inst = read_instance(inst_dir / plan["file"])
        try:
            res = check_tr1(inst, plan) if plan["format"] == "tr1" else check_tr4(inst, plan)
        except Bad as exc:
            res = {"ok": False, "error": str(exc)}
        res["mtime"] = path.stat().st_mtime
        results[key] = res
    out.write_text(json.dumps(results, indent=0))
    bad = [k for k, v in results.items() if not v["ok"]]
    print(f"checked {len(results)}, failed {len(bad)}, total rehandles {sum(v.get('rehandles', 0) for v in results.values())}")
    for k in bad[:10]:
        print("FAIL", k, results[k]["error"])


if __name__ == "__main__":
    main()
