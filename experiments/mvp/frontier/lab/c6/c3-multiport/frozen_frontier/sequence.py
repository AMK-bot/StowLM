"""Mask-checked load order for a fixed assignment: at each step, the shallowest legal box whose dependencies are done.

The cheap order in ``alns.Sequencer`` ignores rules that depend on loading order, such as crane reach over deck stacks. This
one asks the feasibility engine at every step, so a plan it returns is mask-legal by construction. It costs more, so it is
used to finish a large plan, not inside the search loop.
"""
from __future__ import annotations

from stowage.actions.space import Place
from stowage.solver.gold_warmstart_search import precedence_sets


def order_legal(problem, assignment):
    """Return (ordered actions, None) or (partial actions, details of the stuck state)."""
    p, engine = problem, problem.engine
    actions = [Place(box, slot) for box, slot in assignment.items()]
    predecessors = precedence_sets(p.instance, actions)
    pending, done, out, taken = set(range(len(actions))), set(), [], set()
    pushed = 0

    def blockers(box):
        stack = p.instance_yard.get(p.yard_stack.get(box), ())
        if box not in stack:
            return 0
        return sum(1 for b in stack[stack.index(box)+1:] if b not in taken)

    try:
        while pending:
            ready = sorted((i for i in pending if predecessors[i] <= done),
                           key=lambda i: (blockers(actions[i].box), -abs(p.cells_tcg(actions[i].slot)), actions[i].box))
            chosen = next((i for i in ready if engine.is_legal(actions[i])), None)
            if chosen is None:
                return out, dict(placed=len(out), left=len(pending), ready=[(actions[i].box, actions[i].slot) for i in ready[:6]])
            engine.push(actions[chosen])
            pushed += 1
            pending.discard(chosen)
            done.add(chosen)
            taken.add(actions[chosen].box)
            out.append(actions[chosen])
        return tuple(out), None
    finally:
        for _ in range(pushed):
            engine.pop()
