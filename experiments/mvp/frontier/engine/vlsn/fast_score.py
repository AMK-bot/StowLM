# Provenance: copied from experiments/mvp/frontier/lab/c1/fx-vlsn/fast_score.py.
"""Exact cached scoring for pure-load stows under the legacy move policy.

This is an experimental accelerator, not a checker. A number means exactly
Problem.score(actions).objective in the supported domain; None means reject or
fall back to the owner engine. Full feasibility and final checking remain the
caller's responsibility. No source owner is monkeypatched.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from stowage.actions.space import Place
from stowage.conventions import cells_of_slot, parse_slot, parse_yard
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.frozen import active_stubs


class FastScorer:
    def __init__(self, problem):
        self.p = problem
        ins = problem.instance
        self.instance = ins
        self.supported = not (active_stubs() or rule_freeze_v1_enabled() or ins.onboard.placements
                              or ins.onboard.covers_off or ins.yard.quay_buffer
                              or ins.discharge_list)
        self.loads = frozenset(ins.load_list)
        self.boxes = problem.boxes
        self.cells = problem.cells
        self.stacks = problem.stacks
        self.positions = {c: (s.id, i) for s in ins.vessel.stacks for i, c in enumerate(s.cell_ids)}
        self.covers = {c.id: tuple(c.deck_stack_ids) for c in ins.vessel.hatch_covers}
        self.first_call = {}
        for i in range(ins.call_index + 1, len(ins.rotation.calls)):
            self.first_call.setdefault(ins.rotation.calls[i].locode, i)
        self.destination = {b: self.first_call.get(x.pod, len(ins.rotation.calls))
                            for b, x in self.boxes.items()}
        self.blocks = {b.id: b for b in ins.port.yard.blocks}
        equipment = {e.id: e for e in ins.port.equipment}
        self.limits = {b.id: min(b.max_tiers, ins.port.yard_max_tiers,
                                 equipment[b.equipment_id].stack_tiers)
                       for b in ins.port.yard.blocks}
        self.initial_yard = {s.key: tuple(s.boxes) for s in ins.yard.stacks}
        self.initial_lengths = {s.key: s.length_ft for s in ins.yard.stacks}
        self.initial_where = {cid: s.key for s in ins.yard.stacks for cid in s.boxes}
        self.supported &= self.loads <= self.initial_where.keys()
        self._candidate_cache = {}
        self._slot_cache = {}
        self._yard_cost = lru_cache(maxsize=8192)(self._yard_cost_uncached)

    def _slot(self, box, slot):
        key = (self.boxes[box].length_ft, slot)
        if key in self._slot_cache:
            return self._slot_cache[key]
        try:
            addr = parse_slot(slot)
            footprint = cells_of_slot(slot)
            length = key[0]
            if (length == 20) != addr.is_20ft_bay:
                return None
            found = []
            for cell in footprint:
                if cell not in self.cells or not getattr(self.cells[cell], f'accepts_{length}'):
                    return None
                sid, index = self.positions[cell]
                stack = self.stacks[sid]
                if not stack.on_deck and stack.hatch_cover_id is None:
                    return None
                if stack.on_deck and index + 1 > self.instance.port.quay_crane.max_on_deck_tiers:
                    return None
                found.append((cell, sid, index, stack.hatch_cover_id, stack.on_deck))
            value = (addr.tier, tuple(found))
            self._slot_cache[key] = value
            return value
        except (ValueError, KeyError, AttributeError):
            return None

    def _candidates(self, cid, src):
        box = self.boxes[cid]
        source = parse_yard(src + '-01')
        block = self.blocks[source.block]
        reefer_row = box.reefer and source.row in block.reefer_rows
        cache_key = (src, box.length_ft, reefer_row)
        if cache_key in self._candidate_cache:
            return self._candidate_cache[cache_key]
        out = []
        width = 1 if box.length_ft == 20 else 2
        if box.length_ft in block.allowed_lengths_ft:
            for row in range(1, block.n_rows + 1):
                if reefer_row and row not in block.reefer_rows:
                    continue
                for bay in range(1, block.n_bays - width + 2):
                    key = f'{block.id}-{bay:03d}-{row:02d}'
                    if key != src:
                        dist = (int(bay != source.bay), abs(bay-source.bay), abs(row-source.row))
                        foot = tuple((block.id, bay + o, row) for o in range(width))
                        out.append((key, dist, (block.id, bay, row), foot, self.limits[block.id]))
        out.sort(key=lambda c: (c[1], c[2]))
        self._candidate_cache[cache_key] = out
        return out

    def _yard_cost_uncached(self, order):
        yard = {k: list(v) for k, v in self.initial_yard.items()}
        lengths = dict(self.initial_lengths)
        where = dict(self.initial_where)
        pending = {k: sum(b in self.loads for b in v) for k, v in yard.items()}
        foot = {}
        for key, length in lengths.items():
            a = parse_yard(key + '-01')
            for o in range(1 if length == 20 else 2):
                foot[(a.block, a.bay+o, a.row)] = key
        relocations = 0

        def pop(key):
            cid = yard[key].pop()
            pending[key] -= cid in self.loads
            del where[cid]
            if not yard[key]:
                a = parse_yard(key + '-01')
                for o in range(1 if lengths[key] == 20 else 2):
                    del foot[(a.block, a.bay+o, a.row)]
                del yard[key], lengths[key], pending[key]
            return cid

        for cid in order:
            src = where.get(cid)
            if src is None:
                return None
            a = parse_yard(src + '-01')
            if len(yard[src]) > self.limits[a.block]:
                # A lower target can only become reachable by lifting an
                # initially unreachable blocker, which replay also rejects.
                return None
            while yard[src][-1] != cid:
                top = yard[src][-1]
                length = self.boxes[top].length_ft
                best = target = target_foot = None
                for key, dist, tail, footprint, limit in self._candidates(top, src):
                    # Candidates are in static distance order. Once a clean
                    # target is found only equal-distance targets can beat it
                    # (an existing stack wins the next preference over fresh).
                    if best is not None and best[0] == 0 and dist > best[2]:
                        break
                    if key in yard:
                        if lengths[key] != length or len(yard[key]) >= limit:
                            continue
                        fresh, pend = 0, pending[key]
                    else:
                        if any(cell in foot for cell in footprint):
                            continue
                        fresh, pend = 1, 0
                    value = (int(pend > 0), pend, dist, fresh, tail)
                    if best is None or value < best:
                        best, target, target_foot = value, key, footprint
                if target is None:
                    return None
                pop(src)
                if target not in yard:
                    yard[target], lengths[target], pending[target] = [], length, 0
                    for cell in target_foot:
                        foot[cell] = target
                yard[target].append(top)
                pending[target] += top in self.loads
                where[top] = target
                relocations += 1
            pop(src)
        return relocations

    def score(self, actions):
        """Return exact objective in the guarded domain, else None.

        No feasibility mask is run here; this matches the *physical replay*
        contract of Problem.score, not the full stowage rules.
        """
        if not self.supported or active_stubs() or rule_freeze_v1_enabled() or actions is None:
            return None
        actions = tuple(actions)
        if (len(actions) != len(self.loads) or any(not isinstance(a, Place) for a in actions)
                or {a.box for a in actions} != self.loads):
            return None
        occupied = {}
        columns = defaultdict(list)
        opened = set()
        loaded_deck = set()
        hatch_lifts = 0
        order = []
        for action in actions:
            found = self._slot(action.box, action.slot)
            if found is None:
                return None
            tier, footprint = found
            need = {}
            for cell, sid, index, cover, deck in footprint:
                if cell in occupied:
                    return None
                stack = self.stacks[sid]
                for below in stack.cell_ids[:index]:
                    if below not in occupied:
                        return None
                if index and self.boxes[action.box].length_ft == 20:
                    if self.boxes[occupied[stack.cell_ids[index-1]]].length_ft != 20:
                        return None
                if any(c in occupied for c in stack.cell_ids[index+1:]):
                    return None
                if cover is not None:
                    if cover in need and need[cover] == deck:
                        return None
                    need[cover] = not deck
            for cover, want_off in need.items():
                if want_off and cover not in opened:
                    # Opening a loaded deck makes unique-load-only actions
                    # incomplete: these boxes have no remaining Place action.
                    if any(sid in loaded_deck for sid in self.covers[cover]):
                        return None
                    opened.add(cover)
                    hatch_lifts += 1
                elif not want_off and cover in opened:
                    opened.remove(cover)
                    hatch_lifts += 1
            for cell, sid, index, cover, deck in footprint:
                occupied[cell] = action.box
                columns[sid].append((tier, action.box))
                if deck:
                    loaded_deck.add(sid)
            order.append(action.box)
        hatch_lifts += len(opened)
        relocations = self._yard_cost(tuple(order))
        if relocations is None:
            return None
        # D15 rebuilds upward links after each call: spanning boxes connect
        # the two columns, and an upward closure counts each blocker once.
        active = set(self.loads)
        future_restows = future_hatch = 0
        for call in range(self.instance.call_index+1, len(self.instance.rotation.calls)):
            targets = {b for b in active if self.destination[b] == call}
            if not targets:
                continue
            above = defaultdict(set)
            remaining = {}
            for sid, members in columns.items():
                members = [b for _, b in members if b in active]
                remaining[sid] = members
                for low, high in zip(members, members[1:]):
                    above[low].add(high)
            covers = {self.stacks[sid].hatch_cover_id for sid, members in remaining.items()
                      if not self.stacks[sid].on_deck and any(b in targets for b in members)}
            required = set(targets)
            for cover in covers:
                for sid in self.covers[cover]:
                    required.update(remaining.get(sid, ()))
            todo = list(required)
            while todo:
                for b in above[todo.pop()]:
                    if b not in required:
                        required.add(b)
                        todo.append(b)
            future_restows += len(required-targets)
            future_hatch += 2*len(covers)
            active -= targets
        obj = self.instance.objective
        return float(obj.relocate*relocations + obj.hatch_cover*hatch_lifts
                     + obj.future_restow*obj.future_restow_lifts_per_box*future_restows
                     + obj.future_hatch_cover*future_hatch)
