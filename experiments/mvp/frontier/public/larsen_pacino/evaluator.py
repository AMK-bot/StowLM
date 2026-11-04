"""Independent evaluator for Larsen & Pacino (2021) stowage plans.

Takes the raw benchmark text files and a plan file, and recomputes the objective and
every hard constraint from the plan alone. It shares no code with the C++ search
(alns.cpp): the parser, KPI rules and stability maths here are written separately, on
purpose, so that a bug in one is unlikely to repeat in the other.

Plan file: one line per loaded box, `box_index bay stack tier slot`, where box_index is
the zero-based position of the box in the instance's container list. Lines starting with
`#` are ignored. Load boxes that do not appear are unloaded (UL).

Conventions (all justified in VALIDATION.md, section "Interpretations"):
  stack      one section: the deck part and the hold part of a bay position are two stacks.
  cell       one 40-foot position. Data slot 1 = Aft, slot 2 = Fore; a 40' sits in slot 1.
  OV         counted per box; x overstows a lower y in its stack when d_y < d_x and the two
             overlap physically (x or y is 40', or both are 20' in the same slot).
  HO         per cell: Eq. (15) a deck cell holding a box that leaves later than some box
             under the hatch; Eq. (16) a hold cell holding a LOAD box under a hatch with
             release on it.
  MK         max over adjacent bay pairs of LOAD boxes (release does not count).
  NR         per cell: cell with a plug holding at least one non-reefer (release counts).
  FB         sum of (end port + 1) below deck, all boxes: 0-based ports cannot reach Table 2.
  VM         lightship plus every box at its tier: vcg_s + position*2.61 + 1.3. Eq. (27) prints
             the stack base only; that gives 1.2e6 on instance 1 against the published 1.52e6.
  BP, ES     over all boxes on board.
  bending    physical lever about the station: sum_{b'<=b} (lcg_b' - lcg_b) * net_b'. Eq. (33) as
             printed exceeds the limits by multiples on any plan; see VALIDATION.md.
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import json
import math
import os
from collections import Counter, defaultdict

C_LOAD, C_HATCH, C_OV, C_MK, C_GM, C_PURITY, C_EMPTY, C_REEFER, C_BELOW = (
    1000.0, 100.0, 100.0, 1.0, 0.0001, 20.0, 10.0, 5.0, 0.5)

# Calibrated from the release stacks (see VALIDATION.md); nominal 8'6"/9'6" are looser.
H_STD = 2.62
H_HC = 2.93
CELL_PITCH = 2.61      # vertical spacing of cells in the vessel files
BOX_CENTRE = 1.3       # centre of a box above its cell floor
AFT, FORE = 1, 2
EPS = 1e-6


def objective(UL, HO, OV, MK, VM, BP, ES, NR, FB):
    """Printed cost vector, with the -10*ES reading that fits Table 2."""
    return (C_LOAD * UL + C_HATCH * HO + C_OV * OV + C_MK * MK + C_GM * VM
            + C_PURITY * BP - C_EMPTY * ES + C_REEFER * NR - C_BELOW * FB)


@dc.dataclass
class Section:
    bay: int
    stack: int
    deck: bool
    block_id: int
    hmax: float
    w20: float
    w40: float
    vcg: float
    tcg: float
    tiers: list  # ascending
    plugs: dict  # tier -> plug count

    @property
    def hatch(self):
        return (self.bay, (self.block_id + 1) // 2)


@dc.dataclass
class Bay:
    idx: int
    lcg: float
    min_shear: float
    max_shear: float
    max_bend: float
    const_w: float
    const_vcg: float
    buoy: list


@dc.dataclass
class Vessel:
    tcg_tol: float
    hydro: list  # (disp, lcg_min, lcg_max, metacentre)
    bays: list
    sections: list
    by_cell: dict  # (bay, stack, tier) -> Section


@dc.dataclass
class Box:
    idx: int
    end: int
    length: int
    weight: float
    kind: str
    pos: tuple | None  # (bay, stack, tier, slot) for release

    @property
    def reefer(self):
        return self.kind in ("RC", "HR")

    @property
    def height(self):
        return H_HC if self.kind in ("HC", "HR") else H_STD


@dc.dataclass
class Instance:
    boxes: list

    @property
    def release(self):
        return [b for b in self.boxes if b.pos is not None]

    @property
    def load(self):
        return [b for b in self.boxes if b.pos is None]


def read_vessel(path) -> Vessel:
    hydro, bays, sections = [], [], []
    tol = None
    mode = None
    bay = None
    stack = tcg = None
    sec = None
    with open(path) as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                mode = s.lstrip("# ").split(":")[0]
                continue
            f = s.split()
            if mode == "Ship":
                tol = float(f[3])
            elif mode == "HydroPoints":
                hydro.append(tuple(map(float, f)))
            elif mode == "Bay":
                bay = Bay(int(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[4]),
                          float(f[5]), float(f[6]), [])
                bays.append(bay)
            elif mode == "BuoyancyPoints":
                bay.buoy.append(float(f[0]))
            elif mode == "Stack":
                stack, tcg = int(f[0]), float(f[1])
            elif mode in ("AboveDeck", "BelowDeck"):
                sec = Section(bay.idx, stack, mode == "AboveDeck", int(f[0]), float(f[1]),
                              float(f[2]), float(f[3]), float(f[4]), tcg, [], {})
                sections.append(sec)
            elif mode == "Cell":
                sec.tiers.append(int(f[0]))
                sec.plugs[int(f[0])] = int(f[1])
    by_cell = {}
    for x in sections:
        x.tiers.sort()
        for t in x.tiers:
            by_cell[(x.bay, x.stack, t)] = x
    return Vessel(tol, hydro, bays, sections, by_cell)


def read_instance(path) -> Instance:
    types = {}
    boxes = []
    mode = None
    with open(path) as fh:
        for raw in fh:
            s = raw.strip()
            if not s:
                continue
            if s.startswith("#"):
                mode = s
                continue
            f = s.split()
            if mode.startswith("# Parameters"):
                continue
            if mode.startswith("# Transport"):
                types[int(f[0])] = (int(f[1]), float(f[2]), f[3])
            else:
                length, weight, kind = types[int(f[2])]
                pos = tuple(map(int, f[3:7])) if len(f) > 3 else None
                boxes.append(Box(len(boxes), int(f[1]), length, weight, kind, pos))
    return Instance(boxes)


def read_plan(path):
    plan = []
    with open(path) as fh:
        for raw in fh:
            s = raw.strip()
            if s and not s.startswith("#"):
                f = list(map(int, s.split()))
                plan.append((f[0], (f[1], f[2], f[3], f[4])))
    return plan


def physically_overlap(x: Box, y: Box, x_slot: int, y_slot: int) -> bool:
    return x.length == 40 or y.length == 40 or x_slot == y_slot


def hydro_bracket(hydro, w):
    """Index o with disp[o-1] <= w <= disp[o], or None when outside the table."""
    for o in range(1, len(hydro)):
        if hydro[o - 1][0] <= w <= hydro[o][0]:
            return o
    return None


def stability(v: Vessel, placed, *, bending="physical", lcg_shift=0.0):
    """Eqs (21)-(33). `placed` yields (Box, Section, tier). Returns values and violations."""
    cargo = defaultdict(float)
    vm = sum(b.const_vcg * b.const_w for b in v.bays)
    tm = 0.0
    for box, sec, tier in placed:
        cargo[sec.bay] += box.weight
        vm += (sec.vcg + sec.tiers.index(tier) * CELL_PITCH + BOX_CENTRE) * box.weight
        tm += sec.tcg * box.weight
    disp = sum(cargo[b.idx] + b.const_w for b in v.bays)
    lm = sum(b.lcg * (cargo[b.idx] + b.const_w) for b in v.bays)
    out = dict(disp=disp, lm=lm, tm=tm, vm=vm, violations=[], lcg=lm / disp, tcg=tm / disp)
    o = hydro_bracket(v.hydro, disp)
    if o is None:
        out["violations"].append(("displacement", disp))
        return out
    lo, hi = v.hydro[o - 1], v.hydro[o]
    # lcg_shift is a diagnostic: it moves the longitudinal bounds forward, in metres
    lm_lo, lm_hi = (hi[1] + lcg_shift) * hi[0], (hi[2] + lcg_shift) * hi[0]
    out.update(lm_lo=lm_lo, lm_hi=lm_hi)
    if lm < lm_lo - EPS:
        out["violations"].append(("lcg_min", lm_lo - lm))
    if lm > lm_hi + EPS:
        out["violations"].append(("lcg_max", lm - lm_hi))
    # Eq (29) prints disp_o; the published |TCG| never exceed 0.10, so the actual
    # displacement is used, which is stricter.
    if abs(tm) > v.tcg_tol * disp + EPS:
        out["violations"].append(("tcg", abs(tm) - v.tcg_tol * disp))
    frac = (disp - lo[0]) / (hi[0] - lo[0])
    shear = 0.0
    bend_printed = 0.0
    profile = []
    nets = []
    for i, b in enumerate(v.bays):
        by = (1 - frac) * b.buoy[o - 1] + frac * b.buoy[o]
        net = cargo[b.idx] + b.const_w - by
        nets.append(net)
        shear += net
        bend_printed += abs(v.bays[0].lcg - b.lcg) * net
        physical = sum((v.bays[j].lcg - b.lcg) * nets[j] for j in range(i + 1))
        profile.append((shear, bend_printed, physical))
        if shear > b.max_shear + EPS:
            out["violations"].append(("shear_max", b.idx, shear - b.max_shear))
        if shear < b.min_shear - EPS:
            out["violations"].append(("shear_min", b.idx, b.min_shear - shear))
        lever_shear = abs(v.bays[0].lcg - b.lcg) * shear
        val = {"printed": lever_shear, "cumulative": bend_printed, "physical": physical}[bending]
        if val > b.max_bend + EPS:
            out["violations"].append(("bending", b.idx, val - b.max_bend))
    out["profile"] = profile
    return out


def evaluate(v: Vessel, inst: Instance, plan, *, bending="physical", lcg_shift=0.0):
    """Recompute KPIs and hard checks. `plan` is [(box_index, (bay, stack, tier, slot)), ...]."""
    viol = Counter()
    notes = Counter()
    cells = defaultdict(dict)  # (bay, stack, tier) -> slot -> (Box, is_load)
    release_cells = set()
    for b in inst.release:
        bay, stack, tier, slot = b.pos
        if (bay, stack, tier) not in v.by_cell:
            notes["release_off_grid"] += 1
            continue
        if slot in cells[(bay, stack, tier)]:
            notes["release_double"] += 1
        cells[(bay, stack, tier)][slot] = (b, False)
        release_cells.add((bay, stack, tier))
    seen = set()
    for idx, (bay, stack, tier, slot) in plan:
        if idx < 0 or idx >= len(inst.boxes):
            viol["bad_box_index"] += 1
            continue
        b = inst.boxes[idx]
        if b.pos is not None:
            viol["moved_release_eq12"] += 1
            continue
        if idx in seen:
            viol["box_placed_twice_eq6"] += 1
            continue
        seen.add(idx)
        if (bay, stack, tier) not in v.by_cell:
            viol["no_such_cell"] += 1
            continue
        if slot in cells[(bay, stack, tier)]:
            viol["slot_taken_eq2_3"] += 1
            continue
        if b.length == 40 and slot != AFT:
            viol["forty_in_fore_eq4"] += 1
        cells[(bay, stack, tier)][slot] = (b, True)
    # cell-level rules
    for c, slots in cells.items():
        sec = v.by_cell[c]
        boxes = [e[0] for e in slots.values()]
        has_load = any(e[1] for e in slots.values())
        forty = [b for b in boxes if b.length == 40]
        if forty and len(boxes) > 1 and has_load:
            viol["forty_shares_cell_eq2_4"] += 1
        twenties = [b for b in boxes if b.length == 20]
        if len(twenties) == 1 and not forty:
            if has_load:
                viol["lone_twenty_eq5"] += 1
            else:
                notes["release_lone_twenty"] += 1
        nreef = sum(b.reefer for b in boxes)
        if nreef > sec.plugs[c[2]]:
            if has_load:
                viol["reefer_without_plug_eq10"] += 1
            else:
                notes["release_reefer_no_plug"] += 1
    # stack-level rules
    stacks = defaultdict(list)
    for c, slots in cells.items():
        stacks[id(v.by_cell[c])].append((c[2], slots))
    placed = []
    OV = 0
    strict_eq11 = 0
    for sec in v.sections:
        lst = sorted(stacks.get(id(sec), []), key=lambda e: e[0])
        if not lst:
            continue
        w40 = w20f = w20a = 0.0
        h = 0.0
        any_load = False
        for tier, slots in lst:
            for slot, (b, is_load) in slots.items():
                any_load |= is_load
                placed.append((b, sec, tier))
                if b.length == 40:
                    w40 += b.weight
                    h += b.height
                else:
                    h += b.height / 2
                    if slot == FORE:
                        w20f += b.weight
                    else:
                        w20a += b.weight
        if any_load:
            if 0.5 * (w20f + w20a) + w40 > sec.w40 + EPS:
                viol["stack_weight40_eq7"] += 1
            if max(w20f, w20a) + 0.5 * w40 > sec.w20 + EPS:
                viol["stack_weight20_eq8"] += 1
            if h > sec.hmax + EPS:
                viol["stack_height_eq9"] += 1
        # support and weight order
        for i, (tier, slots) in enumerate(lst):
            idx_tier = sec.tiers.index(tier)
            below = None
            for j in range(i):
                if lst[j][0] == sec.tiers[idx_tier - 1] and idx_tier > 0:
                    below = lst[j][1]
            this_load = any(e[1] for e in slots.values())
            if idx_tier > 0 and below is None:
                if this_load:
                    viol["floating_box_eq11"] += 1
                continue
            if below is None:
                continue
            for slot, (b, is_load) in slots.items():
                pair_load = is_load or any(e[1] for e in below.values())
                if not pair_load:
                    continue
                below40 = [e[0] for e in below.values() if e[0].length == 40]
                if b.length == 20 and below40:
                    viol["twenty_on_forty_eq11"] += 1
                elif b.length == 20:
                    if slot in below and below[slot][0].weight < b.weight - EPS:
                        viol["heavier_on_lighter_eq11"] += 1
                    elif slot not in below:
                        viol["floating_box_eq11"] += 1
                elif below40:
                    if below40[0].weight < b.weight - EPS:
                        viol["heavier_on_lighter_eq11"] += 1
                else:  # 40' on a pair of 20': compared with the pair's sum, which the
                    # 6,031 release cases obey without exception (Eq. 11 prints per slot)
                    if len(below) < 2:
                        viol["floating_box_eq11"] += 1
                    elif sum(e[0].weight for e in below.values()) < b.weight - EPS:
                        viol["heavier_on_lighter_eq11"] += 1
                    if AFT in below and below[AFT][0].weight < b.weight - EPS:
                        strict_eq11 += 1
        # overstowage inside the stack
        flat = [(tier, slot, b) for tier, slots in lst for slot, (b, _) in slots.items()]
        for tier, slot, b in flat:
            for t2, s2, y in flat:
                if t2 < tier and y.end < b.end and physically_overlap(b, y, slot, s2):
                    OV += 1
                    break
    # hatch overstowage, Eqs (15)-(16)
    hatches = defaultdict(lambda: ([], []))
    for sec in v.sections:
        hatches[sec.hatch][0 if sec.deck else 1].append(sec)
    HO = 0
    for key, (deck, hold) in hatches.items():
        hold_cells = []
        for sec in hold:
            for tier, slots in stacks.get(id(sec), []):
                hold_cells.append(slots)
        deck_cells = []
        release_on_deck = False
        for sec in deck:
            for tier, slots in stacks.get(id(sec), []):
                deck_cells.append(slots)
                release_on_deck |= any(not e[1] for e in slots.values())
        if hold_cells:
            first_out = min(e[0].end for slots in hold_cells for e in slots.values())
            for slots in deck_cells:
                if any(e[0].end > first_out for e in slots.values()):
                    HO += 1
            if release_on_deck:
                for slots in hold_cells:
                    if any(e[1] for e in slots.values()):
                        HO += 1
    # remaining KPIs
    load_per_bay = Counter()
    blocks = defaultdict(set)
    used = set()
    NR = FB = 0
    for c, slots in cells.items():
        sec = v.by_cell[c]
        used.add(id(sec))
        for b, is_load in slots.values():
            if is_load:
                load_per_bay[c[0]] += 1
            blocks[(sec.bay, sec.block_id)].add(b.end)
            if not sec.deck:
                FB += b.end + 1
        if sec.plugs[c[2]] > 0 and any(not e[0].reefer for e in slots.values()):
            NR += 1
    MK = max(load_per_bay[i] + load_per_bay[i + 1] for i in range(len(v.bays) - 1))
    BP = sum(len(p) for p in blocks.values())
    ES = len(v.sections) - len(used)
    UL = len(inst.load) - len(seen)
    st = stability(v, placed, bending=bending, lcg_shift=lcg_shift)
    for name in (e[0] for e in st["violations"]):
        viol["stability_" + name] += 1
    VM = st["vm"]
    return dict(obj=objective(UL, HO, OV, MK, VM, BP, ES, NR, FB), UL=UL, HO=HO, OV=OV, MK=MK,
                BP=BP, NR=NR, ES=ES, FB=FB, VM=VM, feasible=not viol, violations=dict(viol),
                notes=dict(notes), strict_eq11_40on20=strict_eq11, disp=st["disp"],
                lcg=st["lcg"], tcg=st["tcg"], lm=st["lm"], tm=st["tm"],
                lm_bounds=(st.get("lm_lo"), st.get("lm_hi")),
                stability_margin=st["violations"], profile=st.get("profile"))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("vessel")
    p.add_argument("instance")
    p.add_argument("plan")
    p.add_argument("--bending", default="physical", choices=["printed", "cumulative", "physical"])
    a = p.parse_args()
    r = evaluate(read_vessel(a.vessel), read_instance(a.instance), read_plan(a.plan), bending=a.bending)
    r.pop("profile", None)
    print(json.dumps(r, indent=1, default=str))


if __name__ == "__main__":
    main()
