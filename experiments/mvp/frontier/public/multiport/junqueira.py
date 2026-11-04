"""Junqueira et al. 2020 (arXiv 2006.06795) heuristics HR1 and HR2, re-implemented from the paper.

The problem: one ship visits ports 1..P. Port o has a yard of stacks. Every box in every yard must
be loaded, only stack tops are retrieved (so yard relocations are 0 by construction), and ship
stacks have no in-ship shifts: a blocking box is unloaded to a temporary area and reloaded. One
such unload + reload is one relocation. Boxes above the height cap ceil(theta_o / C) (Eq. 33) are
moved too, each counting one move. The paper's objective is the number of relocations.

The paper leaves several things open, so every reading is a `Reading` field and `experiments` in
`run_junqueira.py` tries them. The defaults are the literal reading that fits Table 7 best: the
height cap counts as a full stack, and every tie takes the first listed.

Data files are read-only and live outside the repo. Run this with `python -I`.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy.io as sio


@dataclass(frozen=True)
class Reading:
    # 'phys': loading ignores the height cap, then a repair step moves what is above it.
    # 'branch1': the first branch of Eq. 34 obeys the cap, the second only the physical height.
    # 'both': the cap counts as "full" in both branches. Only boxes already aboard can then exceed
    # it, when the cap falls from one port to the next, and the repair step still moves those.
    cap: str = "both"
    # When several yard tops share the farthest destination: lowest 'stack' index or lowest 'id'.
    yard_tie: str = "stack"
    # Ship stack ties in Eq. 34: 'first' or 'last'.
    ship_tie: str = "first"
    # Temp_Yard reload order: 'far' (farthest destination first), 'lifo' or 'fifo'.
    temp: str = "far"
    # Does a box due at this port, lifted to reach a lower box due here, count as a move?
    # The pseudocode's line 35 removes it from Unload and counts it. The objective (Eq. 10) does not.
    count_same_port: bool = False
    # Reload Temp_Yard before the port's yard (paper), or merge them into one pool of tops.
    temp_first: bool = True
    # Which box of Unload is treated first, which sets the Temp_Yard order: by 'stack' or by 'id'.
    unload: str = "stack"
    # When the height repair runs ('after' loading, as in the paper, or 'before' it), and in which
    # order boxes above the cap are taken: 'stack', 'stack_rev', or 'tier' (highest tier first).
    repair_when: str = "after"
    repair_order: str = "stack"
    # Height limits on the Temp_Yard reload: 'same' as loading, 'branch1' (cap on the first branch
    # only) or 'phys' (physical height only, the repair then pays for any excess).
    temp_cap: str = "same"


@dataclass
class Instance:
    name: str
    ports: int
    tiers: int
    stacks: int
    n: int
    yards: list  # per port (0-based), list of yard stacks, each a list of (id) bottom to top
    dest: dict  # id -> destination port (1-based)
    # Set per port o: number of boxes on board leaving o, and so the height cap
    theta: list
    cap: list


def load_instance(path: Path) -> Instance:
    m = sio.loadmat(str(path), squeeze_me=False)
    patios = m["Patios"]
    P = patios.shape[0] + 1
    R = int(m["R"][0, 0])
    C = int(m["C"][0, 0])
    phi = m["phi"]
    tt = m["TT"].astype(int)
    dest: dict[int, int] = {}
    for o in range(phi.shape[0]):
        for d in range(phi.shape[1]):
            ids = np.asarray(phi[o, d]).ravel()
            for box in ids:
                dest[int(box)] = d + 1
    yards = []
    for o in range(P - 1):
        grid = np.asarray(patios[o, 0]).astype(int)
        stacks = []
        for c in range(grid.shape[1]):
            col = [int(v) for v in grid[::-1, c] if v != 0]  # bottom to top
            stacks.append(col)
        yards.append(stacks)
    n = sum(len(s) for y in yards for s in y)
    onboard = 0
    theta, cap = [], []
    for o in range(P - 1):
        onboard += int(tt[o, :].sum())
        onboard -= int(tt[:, o].sum()) if o > 0 else 0
        theta.append(onboard)
        cap.append(max(1, min(R, math.ceil(onboard / C))))
    return Instance(path.stem.replace("Instance_", ""), P, R, C, n, yards, dest, theta, cap)


def _choose(mins, heights, dest, h_limit, phys, C, reading, rng):
    """Eq. 34 / 35. mins[c] is P+1 when empty. Returns a stack, or -1 if none has room.

    `h_limit` is the height the first branch must respect. `phys` is the height the second may use.
    """
    best, best_v = -1, 1 << 30
    last = reading.ship_tie == "last"
    for c in range(C):
        v = mins[c]
        if v >= dest and heights[c] < h_limit:
            if v < best_v or (last and v == best_v):
                best, best_v = c, v
    if best >= 0:
        return best
    if rng is None:
        best, best_v = -1, -1
        for c in range(C):
            if heights[c] < phys:
                v = mins[c]
                if v > best_v or (last and v == best_v):
                    best, best_v = c, v
        return best
    avail = [c for c in range(C) if heights[c] < phys]
    return rng.choice(avail) if avail else -1


def simulate(inst: Instance, reading: Reading, rng: random.Random | None = None) -> dict:
    """One constructive run. rng=None is HR1; a Random gives HR2's random second branch."""
    P, R, C = inst.ports, inst.tiers, inst.stacks
    dest = inst.dest
    ship: list[list[int]] = [[] for _ in range(C)]
    mins = [P + 1] * C
    heights = [0] * C
    moves_rel = 0
    moves_cap = 0
    empty = P + 1

    for o in range(1, P):  # port o, 1-based; there is no yard at P
        cap_o = inst.cap[o - 1]
        h_first = R if reading.cap == "phys" else cap_o
        h_second = cap_o if reading.cap == "both" else R

        temp: list[int] = []
        if o > 1 and reading.unload == "id":
            where = {b: c for c in range(C) for b in ship[c]}
            for target in sorted(b for b in where if dest[b] == o):
                c = where[target]
                if target not in ship[c]:
                    continue
                st = ship[c]
                while True:
                    b = st.pop()
                    if dest[b] != o:
                        temp.append(b)
                        moves_rel += 1
                    if b == target:
                        break
                heights[c] = len(st)
                mins[c] = min((dest[b] for b in st), default=empty)
        elif o > 1:
            for c in range(C):
                st = ship[c]
                low = -1
                for i, b in enumerate(st):
                    if dest[b] == o:
                        low = i
                        break
                if low < 0:
                    continue
                kept = st[:low]
                for b in reversed(st[low:]):
                    if dest[b] != o:
                        temp.append(b)
                        moves_rel += 1
                    elif b != st[low] and reading.count_same_port:
                        moves_rel += 1
                ship[c] = kept
                heights[c] = len(kept)
                mins[c] = min((dest[b] for b in kept), default=empty)

        def repair() -> None:
            nonlocal moves_cap
            while True:
                over = [c for c in range(C) if heights[c] > cap_o]
                if not over:
                    return
                if reading.repair_order == "tier":
                    c = max(over, key=lambda k: (heights[k], -k))
                elif reading.repair_order == "stack_rev":
                    c = over[-1]
                else:
                    c = over[0]
                box = ship[c].pop()
                heights[c] -= 1
                mins[c] = min((dest[b] for b in ship[c]), default=empty)
                target = _choose(mins, heights, dest[box], cap_o, cap_o, C, reading, rng)
                if target < 0:  # cannot happen: cap_o * C >= theta_o always leaves room
                    raise RuntimeError("no stack below the height cap")
                ship[target].append(box)
                heights[target] += 1
                if dest[box] < mins[target]:
                    mins[target] = dest[box]
                moves_cap += 1

        # Build the retrieval pools. Temp_Yard is a flat pool: every box in it is a free "top".
        yard = [list(s) for s in inst.yards[o - 1]]
        if reading.temp == "far":
            ordered = sorted(enumerate(temp), key=lambda t: (-dest[t[1]], t[0]))
            temp = [b for _, b in ordered]
        elif reading.temp == "lifo":
            temp = temp[::-1]

        if reading.repair_when == "before":
            repair()

        def place(box: int) -> None:
            d = dest[box]
            c = _choose(mins, heights, d, h_first, h_second, C, reading, rng)
            if c < 0:
                c = _choose(mins, heights, d, R, R, C, reading, rng)
            ship[c].append(box)
            heights[c] += 1
            if d < mins[c]:
                mins[c] = d

        if reading.temp_first:
            saved = (h_first, h_second)
            if reading.temp_cap == "branch1":
                h_second = R
            elif reading.temp_cap == "phys":
                h_first, h_second = R, R
            for box in temp:
                place(box)
            h_first, h_second = saved
            pool_extra: list[int] = []
        else:
            pool_extra = list(temp)

        while True:
            best_i, best_d, best_id = -1, -1, 1 << 60
            for i, s in enumerate(yard):
                if not s:
                    continue
                b = s[-1]
                db = dest[b]
                if db > best_d or (db == best_d and reading.yard_tie == "id" and b < best_id):
                    best_i, best_d, best_id = i, db, b
            if pool_extra:
                j = max(range(len(pool_extra)), key=lambda k: dest[pool_extra[k]])
                if dest[pool_extra[j]] > best_d:
                    place(pool_extra.pop(j))
                    continue
            if best_i < 0:
                break
            place(yard[best_i].pop())

        # Eq. 33 can bind on boxes already aboard: the cap falls when theta_o falls.
        if reading.repair_when == "after":
            repair()

    # Port P: unload only, nothing stays on board so there are no relocations.
    return {"of": moves_rel + moves_cap, "rel": moves_rel, "cap": moves_cap}
