"""Cold securing LP with per-call port-order prices; no stored solution input."""
from contextlib import contextmanager

import frozen_env  # noqa: F401
from frozen_securing import method as original
from frozen_securing.stow import columns
from port_order import PortAssignment


class TierPortAssignment(PortAssignment):
    """After an E6-only miss, softly steer heavy cargo toward the hold."""
    def __init__(self, *args, e6_penalty=0., **kwargs):
        self.e6_penalty = float(e6_penalty)
        super().__init__(*args, **kwargs)
        self.port_metadata['e6_guidance_penalty'] = self.e6_penalty

    def cost(self):
        cost = super().cost()
        threshold = self.T.e6_threshold
        if threshold is not None and self.e6_penalty:
            for v, (b, q) in enumerate(self.pair):
                j, _ = self.slot_page[q][0]
                if self.m.pages[j].deck and float(self.T.mass[b]) >= threshold:
                    cost[v] += self.e6_penalty
        return cost


def stack_restows(model, boxes):
    """One off/on event for each distinct earlier POD below a later box."""
    below, events = set(), 0
    for b in boxes:
        events += sum(p < model.pod[b] for p in below)
        below.add(model.pod[b])
    return events


def priced_assignment(model, placed, solver):
    """Keep the LP's port order when securing permits; never silently resort it."""
    T = model.T
    out, illegal = {}, []
    for j, boxes in columns(model, placed, solver).items():
        page = model.pages[j]
        from frozen_securing.stow import resort
        options = [list(boxes), resort(model, boxes)]
        legal = [q for q in options if model.legal(page, tuple(q), any_port=True)]
        if legal:
            # Completion precedes lifts. Inside the securing allowance choose
            # fewer port-order events, retaining genuine negative row effects.
            ordered = min(legal, key=lambda q: (
                max(0., solver.stack_ratio(page, q)-solver.s.limit-.03),
                stack_restows(model, q), solver.stack_ratio(page, q)))
        else:
            ordered = list(boxes)
            illegal.append((j, [T.box_ids[b] for b in ordered]))
        for k, b in enumerate(ordered):
            out[T.box_ids[b]] = T.slot_ids[page.slots[k]]
    return out, illegal


@contextmanager
def port_prices(context):
    saved = original.Assignment, original.to_assignment, original.learn
    solvers = []
    guidance = dict(e6=0.)

    def create(*args, **kwargs):
        solver = TierPortAssignment(*args, e6_penalty=guidance['e6'], **kwargs)
        solvers.append(solver)
        return solver

    def learn(state, instance, report, gm, ratio):
        saved[2](state, instance, report, gm, ratio)
        hard = {v.rule_id for v in report.violations if v.severity == 'hard'}
        if hard == {'expert.e6.heavy_under_deck'}:
            guidance['e6'] = 25.
        elif hard - {'expert.e6.heavy_under_deck'}:
            guidance['e6'] = 0.

    original.Assignment = create
    original.to_assignment = priced_assignment
    original.learn = learn
    try:
        yield
    finally:
        original.Assignment, original.to_assignment, original.learn = saved
        context.setdefault('method_metadata', {})['port_order'] = [s.port_metadata for s in solvers]


def propose(instance, *, seed, budget_s, context):
    with port_prices(context):
        return original.propose(instance, seed=seed, budget_s=budget_s, context=context)


finish = original.finish
