"""Planner-side reader for the Parreno-Torres TR1 to TR4 files, and stack-to-box plan expansion.

The checker has its own parser on purpose, so the two never share a bug. Files stay outside the repo.
"""

from __future__ import annotations

import gzip
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

S20, S40 = 0, 1


@dataclass
class Problem:
    name: str
    n: int  # ports
    stacks: int
    tiers: int
    t: dict  # size -> n x n matrix of boxes
    sizes: tuple = (S40,)
    empty: list = field(default_factory=list)  # empty cells per leg


CLASS_NAMES = {"A": "Authentic", "L": "Long", "M": "Mixed", "R": "Required", "S": "Short"}


def parse_name(path: Path) -> dict:
    m = re.match(r"RB[A-Z]*_([ALMRS])-n(\d+)r(\d+)c(\d+)\.(\d+)", path.name)
    return {"cls": CLASS_NAMES[m[1]], "n": int(m[2]), "r": int(m[3]), "c": int(m[4]), "seed": int(m[5])}


def read_file(path: Path) -> dict:
    head, mats, cur = {}, {}, None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z_()]+):\s*(\S+)?$", line)
        if m and m[2] is not None and not m[2][0].isdigit() and "[" not in m[1]:
            head[m[1]] = m[2]
            continue
        m2 = re.match(r"^([A-Za-z]+)(\[[A-Za-z]+\])?:\s*$", line)
        if m2:
            cur = m2[1] + (m2[2] or "")
            mats[cur] = []
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            head[k.strip()] = v.strip()
            continue
        if cur is not None:
            mats[cur].append([int(x) for x in line.split()])
    return {"head": head, "mats": mats}


def add(a: list, b: list) -> list:
    return [[x + y for x, y in zip(r, s)] for r, s in zip(a, b)]


def load_tr1(path: Path) -> Problem:
    d = read_file(path)
    h = d["head"]
    n, b, c, r = int(h["N"]), int(h["B"]), int(h["C"]), int(h["R"])
    return Problem(path.name, n, b * c, r, {S40: d["mats"]["F"]})


def load_tr4(path: Path) -> tuple:
    d = read_file(path)
    h, mats = d["head"], d["mats"]
    n, b, c, r = int(h["N"]), int(h["B"]), int(h["C"]), int(h["R"])
    weights = {"Light": 0, "Medium": 1, "Heavy": 2}
    by_class = {S20: {}, S40: {}}
    for key, m in mats.items():
        k = re.match(r"([TF])\[(\w+)\]", key)
        if k:
            by_class[S20 if k[1] == "T" else S40][weights[k[2]]] = m
    zero = [[0] * n for _ in range(n)]
    t = {S20: zero, S40: zero}
    for s in (S20, S40):
        tot = zero
        for w in range(3):
            tot = add(tot, by_class[s][w])
        t[s] = tot
    cells = b * c * r
    empty = []
    for leg in range(n - 1):
        on20 = sum(t[S20][i][j] for i in range(leg + 1) for j in range(leg + 1, n))
        on40 = sum(t[S40][i][j] for i in range(leg + 1) for j in range(leg + 1, n))
        e2 = 2 * cells - on20 - 2 * on40
        assert e2 >= 0 and e2 % 2 == 0, (path.name, leg, e2)
        empty.append(e2 // 2)
    prob = Problem(path.name, n, b * c, r, t, sizes=(S20, S40), empty=empty)
    return prob, by_class, {"bays": b, "rows": c}


def write_plan(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", compresslevel=5) as fh:
        json.dump(payload, fh, separators=(",", ":"))


def expand_tr1(prob: Problem, sol: dict) -> dict:
    """Turn stacks plus box flow into per-port grids of box ids, bottom to top."""
    n = prob.n
    stacks = []
    for pat, cnt in sol["patterns"].items():
        stacks.extend([pat] * cnt)
    assert len(stacks) == prob.stacks
    starts = defaultdict(list)  # (i, j) -> pieces (stack, tier)
    for st, pat in enumerate(stacks):
        for _s, i, j, tier, _c in pat:
            starts[(i, j)].append((st, tier))
    boxes = []  # id -> [origin, final destination]
    pool = defaultdict(list)  # d -> ids waiting at this port
    held = {}  # (stack, tier, i, j) -> id
    t = prob.t[S40]
    g = sol["g"]
    grids = []
    ending = defaultdict(list)  # port -> ids of boxes lifted there and continuing
    for i in range(n - 1):
        for d in range(i + 1, n):
            for _ in range(t[i][d]):
                pool[d].append(len(boxes))
                boxes.append([i, d])
        for bid in ending[i]:
            pool[boxes[bid][1]].append(bid)
        for j in range(i + 1, n):
            pieces = sorted(starts[(i, j)])
            k = 0
            for d in range(j, n):
                cnt = g.get((S40, i, j, d), 0)
                for _ in range(cnt):
                    st, tier = pieces[k]
                    k += 1
                    bid = pool[d].pop()
                    held[(st, tier, i, j)] = bid
                    if d > j:
                        ending[j].append(bid)
            assert k == len(pieces), (i, j, k, len(pieces))
        for d in pool:
            assert not pool[d], (i, d, len(pool[d]))
        grid = [[None] * prob.tiers for _ in stacks]
        for st, pat in enumerate(stacks):
            for _s, a, b, tier, _c in pat:
                if a <= i < b:
                    grid[st][tier] = held[(st, tier, a, b)]
        grids.append(grid)
    return {"format": "tr1", "file": prob.name, "boxes": boxes, "grids": grids}


W = {S20: [7, 14, 21], S40: [10, 20, 30]}


def expand_tr4(prob: Problem, sol: dict, by_class: dict, layout: dict, rng) -> dict:
    """Boxes onto pieces (heavy low within a route), then stacks into bays (balance LCG).

    Pieces are (size, i, j, tier, col); col is -1 for a 40', else the slot column.
    """
    n, R = prob.n, prob.tiers
    stacks = []
    for pat, cnt in sol["patterns"].items():
        stacks.extend([pat] * cnt)
    assert len(stacks) == prob.stacks
    starts = defaultdict(list)
    for st, pat in enumerate(stacks):
        for s, i, j, tier, col in pat:
            starts[(s, i, j)].append((tier, st, col))
    boxes = []
    pool = defaultdict(list)
    held = {}
    empties = set()
    g = sol["g"]
    ending = defaultdict(list)
    size_ft = {S20: 20, S40: 40}
    for i in range(n - 1):
        for s in prob.sizes:
            for w in range(3):
                row = by_class[s][w][i]
                for d in range(i + 1, n):
                    for _ in range(row[d]):
                        pool[(s, d)].append(len(boxes))
                        boxes.append([i, d, size_ft[s], W[s][w]])
        for bid in ending[i]:
            b = boxes[bid]
            pool[(S20 if b[2] == 20 else S40, b[1])].append(bid)
        for s in prob.sizes:
            targets = defaultdict(list)  # d -> pieces
            for j in range(i + 1, n):
                pieces = sorted(starts[(s, i, j)])
                if s == S40 and j == i + 1 and prob.empty[i]:
                    # Empty cells are one-leg 40' pieces, always above other one-leg 40' pieces.
                    cut = len(pieces) - prob.empty[i]
                    for q in pieces[cut:]:
                        empties.add((s, i, j, q[0], q[1], q[2]))
                    pieces = pieces[:cut]
                k = 0
                for d in range(j, n):
                    cnt = g.get((s, i, j, d), 0)
                    for _ in range(cnt):
                        targets[d].append((pieces[k], j))
                        k += 1
                assert k == len(pieces), (s, i, j, k, len(pieces))
            for d, lst in targets.items():
                lst.sort()
                ids = sorted(pool[(s, d)], key=lambda q: -boxes[q][3])
                assert len(ids) == len(lst), (s, i, d, len(ids), len(lst))
                pool[(s, d)] = []
                for (piece, j), bid in zip(lst, ids):
                    tier, st, col = piece
                    held[(s, i, j, tier, st, col)] = bid
                    if d > j:
                        ending[j].append(bid)

    # Stack weight and own moment per leg, for the bay assignment.
    S = len(stacks)
    wv = [[0.0] * (n - 1) for _ in range(S)]
    mv = [[0.0] * (n - 1) for _ in range(S)]
    for st, pat in enumerate(stacks):
        for s, a, b, tier, col in pat:
            bid = held.get((s, a, b, tier, st, col))
            if bid is None:
                continue
            w = boxes[bid][3]
            arm = 0.0 if col < 0 else (col - 0.5) * 20.0
            for leg in range(a, b):
                wv[st][leg] += w
                mv[st][leg] += arm * w
    order = assign_bays(wv, mv, layout["bays"], layout["rows"], rng)
    grids = []
    for i in range(n - 1):
        grid = [[[None, None] for _ in range(R)] for _ in range(S)]
        for pos, st in enumerate(order):
            for s, a, b, tier, col in stacks[st]:
                if a <= i < b:
                    bid = held.get((s, a, b, tier, st, col))
                    if bid is None:
                        continue
                    if col < 0:
                        grid[pos][tier] = [bid, bid]
                    else:
                        grid[pos][tier][col] = bid
        grids.append(grid)
    return {"format": "tr4", "file": prob.name, "boxes": boxes, "grids": grids}


def assign_bays(wv: list, mv: list, bays: int, rows: int, rng) -> list:
    """Place stacks in bays so the LCG stays near midships on every leg. Local search on swaps."""
    import numpy as np

    W_ = np.array(wv)
    M_ = np.array(mv)
    S, legs = W_.shape
    d = np.array([40.0 * (b - (bays - 1) / 2.0) for b in range(bays) for _ in range(rows)])
    total = np.maximum(W_.sum(axis=0), 1.0)
    idx = np.argsort(-W_.sum(axis=1))
    # Deal heaviest stacks to the middle pair first, balancing fore and aft.
    pos_order = np.argsort(np.abs(d) + np.arange(S) * 1e-6)
    perm = np.empty(S, dtype=int)
    perm[pos_order] = idx
    base = M_.sum(axis=0)

    def score(moment):
        return float(np.max(np.abs(moment) / total))

    moment = (d[:, None] * W_[perm]).sum(axis=0) + base
    cur = score(moment)
    for it in range(60000):
        if cur < 2.0 and it > 2000:
            break
        a, b = rng.randrange(S), rng.randrange(S)
        if a == b or d[a] == d[b]:
            continue
        delta = (d[a] - d[b]) * (W_[perm[b]] - W_[perm[a]])
        cand = moment + delta
        s2 = score(cand)
        if s2 <= cur:
            perm[a], perm[b] = perm[b], perm[a]
            moment, cur = cand, s2
    return [int(v) for v in perm]
