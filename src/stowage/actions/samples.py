"""Seeded tiny instances and random action sequences for the 3.8 property tests and the brute-force study
(``experiments/action-space``). Built from the 1.1 fixture vessel/port (``stowage.model.fixtures``), so they are
contract-valid synthetic data, not checker-approved loading cases. Replace with 3.5's generator once it lands.
"""

from __future__ import annotations

import random

from stowage.actions.expander import Expander
from stowage.actions.space import Action, Discharge, Place, Relocate, RestowOff
from stowage.conventions import SYNTHETIC_OWNER_CODES, cells_of_slot, make_container_id, parse_slot
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.moves import IllegalMoveError, apply_move, initial_state
from stowage.rules.catalogue import rule_freeze_v1_enabled


def tiny_instance(seed: int, *, n_load: int = 5, n_other: int = 3, p40: float = 0.2, layover: bool = False,
                  max_tiers: int = 3) -> Instance:
    """A random yard on the fixture port (blocks A, B: 6 bays × 2 rows) holding ``n_load`` load-list boxes and
    ``n_other`` boxes for other ships, stacked at random (so some load boxes are blocked). ``layover`` keeps the
    fixture's two boxes aboard (one to discharge here, under a through box)."""
    rng = random.Random(seed)
    base = build_instance(layover=layover).model_dump()
    owner = SYNTHETIC_OWNER_CODES[1]
    pods = ["THBKK", "THSGZ"]
    onboard = [p["container_id"] for p in base["onboard"]["placements"]]
    containers = [c for c in base["containers"] if c["id"] in onboard]
    boxes = []
    for i in range(n_load + n_other):
        length = 40 if rng.random() < p40 else 20
        heavy = rng.random() < 0.5
        cid = make_container_id(owner, 100 * seed % 900_000 + i + 1)
        boxes.append(cid)
        containers.append(dict(
            id=cid, iso_type="42G1" if length == 40 else "22G1", length_ft=length, height_m=2.591,
            tare_kg=3800 if length == 40 else 2200, gross_mass_kg=(24000 if heavy else 9000) + rng.randrange(0, 2000),
            pol="THLCH", pod=rng.choice(pods)))
    load = boxes[:n_load]
    order = boxes[:]
    rng.shuffle(order)
    lengths = {c["id"]: c["length_ft"] for c in containers}
    stacks: dict[str, list[str]] = {}
    slen: dict[str, int] = {}
    foot: dict[tuple[str, int, int], str] = {}
    for cid in order:
        length = lengths[cid]
        width = 1 if length == 20 else 2
        options = [k for k, v in stacks.items() if slen[k] == length and len(v) < max_tiers]
        ground = [(blk, bay, row) for blk in ("A", "B") for bay in range(1, 8 - width) for row in (1, 2)
                  if all((blk, bay + o, row) not in foot for o in range(width))]
        if options and (rng.random() < 0.7 or not ground):
            key = rng.choice(sorted(options))
        else:
            blk, bay, row = rng.choice(ground)
            key = f"{blk}-{bay:03d}-{row:02d}"
            stacks[key], slen[key] = [], length
            for o in range(width):
                foot[(blk, bay + o, row)] = key
        stacks[key].append(cid)
    base["id"] = f"tiny_{'b' if layover else 'a'}_{seed}"
    base["seed"] = seed
    base["containers"] = containers
    base["load_list"] = load
    base["yard"]["stacks"] = [dict(key=k, length_ft=slen[k], boxes=v) for k, v in sorted(stacks.items())]
    base["port"]["yard_max_tiers"] = max_tiers
    for blk in base["port"]["yard"]["blocks"]:
        blk["max_tiers"] = max_tiers
    base["port"]["equipment"][0]["stack_tiers"] = max_tiers
    return Instance.model_validate(base)


def legal_slots(instance: Instance, ex: Expander, box: str) -> list[str]:
    """Slots where ``box`` could physically go next (empty cells, supported, length fits); covers are the
    expander's business. Over-approximate on 20-on-40 and crane reach: callers validate with the move engine."""
    b = ex.boxes[box]
    out = []
    for cell in instance.vessel.cells:
        a = parse_slot(cell.id)
        bays = [a.bay] if b.length_ft == 20 else [a.bay + 1]
        for bay in bays:
            slot = f"{bay:03d}{a.row:02d}{a.tier:02d}"
            try:
                cells = cells_of_slot(slot)
            except ValueError:
                continue
            if any(c not in ex.cells or c in ex.occ for c in cells):
                continue
            if all(ex.cell_pos[c][1] == 0 or ex.stacks[ex.cell_pos[c][0]].cell_ids[ex.cell_pos[c][1] - 1] in ex.occ
                   for c in cells) and slot not in out:
                out.append(slot)
    return sorted(out)


def random_actions(instance: Instance, seed: int, *, p_relocate: float = 0.15, max_tries: int = 200) -> list[Action]:
    """A random action sequence whose expansion the move engine accepts lift by lift and which completes the call
    (all loads aboard, discharges done, quay buffer empty). Mixes in voluntary ``Relocate``s, explicit discharge
    targets and, in layovers, ``RestowOff`` + re-``Place``. Raises RuntimeError if it gets stuck."""
    # APPROXIMATION: direct-delivery trucks are available at lift time; class-1
    # boxes may load anywhere in the sequence, optimistically ignoring truck timing.
    rng = random.Random(seed)
    ex = Expander(instance)
    state = initial_state(instance)
    freeze_enabled = rule_freeze_v1_enabled()
    acts: list[Action] = []

    def attempt(action: Action) -> bool:
        nonlocal ex, state
        trial = ex.copy()
        try:
            moves = trial.step(action)
            st = state
            for m in moves:
                st = apply_move(instance, st, m)
        except (ValueError, IllegalMoveError):
            return False
        ex, state = trial, st
        acts.append(action)
        return True

    for _ in range(max_tries):
        todo_load = [b for b in instance.load_list if ex.where.get(b, ("",))[0] == "yard"
                     or (freeze_enabled and ex.boxes[b].direct_delivery and b not in ex.where)]
        todo_dis = [b for b in instance.discharge_list if ex.where.get(b, ("",))[0] == "ship"]
        if not (todo_load or todo_dis or ex.buffer):
            break
        r = rng.random()
        if r < p_relocate and (ex.yard or not freeze_enabled):
            tops = sorted(v[-1] for v in ex.yard.values())
            box = rng.choice(tops)
            keys = sorted(set(ex.yard) | {f"{blk}-{bay:03d}-{row:02d}" for blk in "AB" for bay in range(1, 7)
                                          for row in (1, 2)})
            attempt(Relocate(box, rng.choice(keys)))
            continue
        if todo_dis and (r < 0.5 or not (todo_load or ex.buffer)):
            box = rng.choice(todo_dis)
            to = None
            if (not freeze_enabled or (ex.yard and not ex.boxes[box].direct_delivery)) and rng.random() < 0.3:
                to = rng.choice(sorted(ex.yard))
            attempt(Discharge(box, to))
            continue
        if instance.discharge_list and rng.random() < 0.1:
            aboard = sorted(cid for cid, loc in ex.where.items() if loc[0] == "ship")
            if aboard:
                attempt(RestowOff(rng.choice(aboard)))
                continue
        pool = ex.buffer or todo_load
        if not pool:
            continue
        box = rng.choice(sorted(pool))
        slots = legal_slots(instance, ex, box)
        rng.shuffle(slots)
        for slot in slots[:6]:
            if attempt(Place(box, slot)):
                break
    else:
        raise RuntimeError(f"random_actions stuck on {instance.id} seed {seed}")
    return acts
