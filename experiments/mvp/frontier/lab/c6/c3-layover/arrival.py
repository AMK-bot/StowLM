"""Cold arrival preparation: legal discharge/restow closures, then original replay.

Preparation is adapted from the frozen C4 layover method. Its temporary Instance
is only a constructor view; every returned action is expanded against the input.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from stowage.actions.expander import Expander
from stowage.actions.space import Discharge, Place, RestowOff
from stowage.conventions import cells_of_slot, parse_slot


@dataclass
class Branch:
    original: object
    working: object
    prefix: tuple
    expander: object
    label: str
    roots: tuple

    @property
    def fixed(self):
        return {p.container_id: p.slot for p in self.working.onboard.placements}

    @property
    def required(self):
        return set(self.working.load_list) | set(self.fixed)

    @property
    def metadata(self):
        return dict(branch=self.label, roots=list(self.roots), fixed_boxes=len(self.fixed),
                    free_boxes=len(self.working.load_list),
                    prefix_restow_off_lifts=sum(m.kind == 'restow_off' for m in self.expander.moves),
                    prefix_cover_lifts=sum(m.kind.startswith('hatch_cover') for m in self.expander.moves))


def prepare(instance, roots=(), *, label='keep'):
    """Remove due cargo before optional through-cargo roots, with exact closure."""
    ex, actions = Expander(instance), []
    due = set(instance.discharge_list)
    while due:
        aboard = [b for b in due if ex.location(b) and ex.location(b)[0] == 'ship']
        if not aboard:
            raise ValueError('discharge target is no longer aboard')
        # Deck/top first avoids moving another due box into the restow buffer.
        box = max(aboard, key=lambda b: (parse_slot(ex.location(b)[1]).on_deck,
                                       parse_slot(ex.location(b)[1]).tier, b))
        action = Discharge(box)
        ex.step(action); actions.append(action); due.remove(box)
    roots = tuple(dict.fromkeys(roots))
    for box in roots:
        if ex.location(box) and ex.location(box)[0] == 'ship':
            action = RestowOff(box)
            ex.step(action); actions.append(action)
    state = ex.world_state()
    buffered = [b for b in state.yard.quay_buffer if b not in instance.discharge_list]
    working = instance.model_copy(update={'onboard': state.ship, 'yard': state.yard,
                                          'discharge_list': [],
                                          'load_list': list(dict.fromkeys(instance.load_list+buffered))})
    return Branch(instance, working, tuple(actions), ex, label, roots)


def restow_options(instance):
    """Keep, clear all, clear deck, then individual covers; fixed finite work.

    Selecting a lowest retained box buys removal of its actual upward and cover
    closure. Lift prices are computed by the expander, never inferred by counts.
    """
    cells = {c.id:c for c in instance.vessel.cells}
    stacks = {s.id:s for s in instance.vessel.stacks}
    cargo = defaultdict(list)
    for placement in instance.onboard.placements:
        if placement.container_id not in instance.discharge_list:
            cargo[cells[cells_of_slot(placement.slot)[0]].stack_id].append(placement)
    all_roots, deck_roots, covers = [], [], defaultdict(list)
    for stack, found in sorted(cargo.items()):
        root = min(found, key=lambda p:parse_slot(p.slot).tier).container_id
        all_roots.append(root)
        covers[stacks[stack].hatch_cover_id].append(root)
        if stacks[stack].on_deck:
            deck_roots.append(root)
    out, seen = [('keep', ())], {frozenset()}
    proposed = [('all', all_roots), ('deck', deck_roots)]
    proposed += [('cover:'+str(c), roots) for c, roots in sorted(covers.items(), key=lambda q:str(q[0]))]
    for label, roots in proposed:
        key = frozenset(roots)
        if key and key not in seen:
            seen.add(key); out.append((label, tuple(roots)))
    return out


def replay_actions(branch, assignment, problem=None):
    """Order full departure assignment; unchanged fixed placements cost no lift."""
    from alns import Problem, Sequencer
    p = problem or Problem(branch.working)
    if set(assignment) != branch.required:
        return None
    if any(assignment.get(b) != slot for b, slot in branch.fixed.items()):
        return None
    ordered = Sequencer(p).order(assignment)
    if ordered is None:
        return None
    replay, out = branch.expander.copy(), list(branch.prefix)
    try:
        for action in ordered:
            if replay.location(action.box) == ('ship', action.slot):
                continue
            replay.step(action); out.append(action)
        replay.finish()
        final = replay.world_state()
        aboard = {p.container_id:p.slot for p in final.ship.placements}
        if set(aboard) != branch.required or final.yard.quay_buffer:
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return tuple(out)


def arrival_condition(instance):
    """Native hydrostatics of given cargo/tanks; no stored recipe is read."""
    from stowage.checker.visibility import _loading_condition
    condition = _loading_condition(instance, instance.onboard)
    hull = instance.vessel.hull
    volume = condition.geometry.submerged(hull.scantling_draught_m, hull.scantling_draught_m).volume_m3
    return dict(gm=float(condition.gm_m), draught=float((condition.t_aft_m+condition.t_fwd_m)/2),
                draught_aft_m=float(condition.t_aft_m), draught_fwd_m=float(condition.t_fwd_m),
                list_rad=float(condition.heel_rad),
                cb=float(volume/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)))
