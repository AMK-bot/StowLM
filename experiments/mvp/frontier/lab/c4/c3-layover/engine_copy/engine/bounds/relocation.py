# Copied from experiments/mvp/frontier/lab/c1/fx-fvs/exact.py; engine integration changes are local.
"""Exact one-relocation relaxation, with explicit replay-certification scope.

Ship precedence is retained even for a relocated box. Every original yard
above/below pair is a conditional arc: upper -> lower unless upper is relocated.
Deleting an entire vertex (ordinary DFVS) would incorrectly delete ship arcs.

This is an exact minimum for unlimited private relocation buffers and monotone
(load each box once, no ship restows) fixed-stow loading. It is a lower bound for
the same monotone family with the real finite yard, crossing and handling rules.
A real replay attaining it proves the minimum in that family. It is NOT a bound
on yard relocations if arbitrary ship restows are permitted instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from time import monotonic

from ortools.sat.python import cp_model


@dataclass(frozen=True)
class BoundResult:
    lower_bound: int
    relaxation_value: int | None
    optimal: bool
    relocated: frozenset[str]
    mandatory_nonloads: frozenset[str]
    order: tuple[str, ...]
    ship_edges: tuple[tuple[str, str], ...]
    yard_edges: tuple[tuple[str, str], ...]
    status: str
    seconds: float

    def certifies(self, replay_relocations: int, *, restow_lifts: int = 0) -> bool:
        """Replay legality/checker admission is the caller's responsibility."""
        return restow_lifts == 0 and replay_relocations == self.lower_bound


def solve_graph(vertices, ship_edges, yard_stacks, *, time_limit_s=10.0, seed=0):
    """Solve the relaxation; yard stacks are sequences ordered bottom to top.

    Nonload inventory above a requested load is mandatory relocated inventory.
    Rank variables need no all-different: strict arcs are feasible exactly when
    the graph is acyclic. All original yard pairs, rather than just adjacent
    pairs, are needed after some outgoing yard arcs have been switched off.
    """
    start = monotonic()
    vertices = tuple(sorted(set(vertices)))
    vertex_set = set(vertices)
    ship_edges = tuple(sorted(set(tuple(edge) for edge in ship_edges)))
    if any(a not in vertex_set or b not in vertex_set for a, b in ship_edges):
        raise ValueError('ship edge endpoint is not a load vertex')
    yard_edges, mandatory = set(), set()
    for stack in yard_stacks:
        stack = tuple(stack)
        for i, lower in enumerate(stack):
            if lower not in vertex_set:
                continue
            for upper in stack[i + 1:]:
                if upper in vertex_set:
                    yard_edges.add((upper, lower))
                else:
                    mandatory.add(upper)
    yard_edges = tuple(sorted(yard_edges))
    mandatory = frozenset(mandatory)
    if not vertices:
        return BoundResult(0, 0, True, frozenset(), mandatory, (), ship_edges,
                           yard_edges, 'OPTIMAL', monotonic() - start)
    model = cp_model.CpModel()
    rank = {box: model.NewIntVar(0, len(vertices) - 1, 'rank_' + box)
            for box in vertices}
    cuts = {box: model.NewBoolVar('relocate_' + box)
            for box in sorted({upper for upper, _ in yard_edges})}
    for before, after in ship_edges:
        model.Add(rank[before] < rank[after])
    for upper, lower in yard_edges:
        model.Add(rank[upper] < rank[lower]).OnlyEnforceIf(cuts[upper].Not())
    model.Minimize(sum(cuts.values()) + len(mandatory))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.001, float(time_limit_s))
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(seed)
    status = solver.Solve(model)
    name = solver.StatusName(status)
    # Mandatory inventory is a valid bound even before CP-SAT finds a solution.
    lb = max(len(mandatory), ceil(solver.BestObjectiveBound() - 1e-7))
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return BoundResult(lb, None, False, frozenset(), mandatory, (),
                           ship_edges, yard_edges, name, monotonic() - start)
    relocated = frozenset(box for box, variable in cuts.items() if solver.Value(variable))
    # Kahn's algorithm yields a valid total order even if several ranks tie.
    predecessors = {box: set() for box in vertices}
    for before, after in ship_edges:
        predecessors[after].add(before)
    for upper, lower in yard_edges:
        if upper not in relocated:
            predecessors[lower].add(upper)
    done, order = set(), []
    while len(order) < len(vertices):
        ready = [box for box in vertices if box not in done and predecessors[box] <= done]
        if not ready:
            raise AssertionError('CP-SAT rank witness contains a directed cycle')
        # Prefer fewer still-pending original blockers among admissible choices.
        box = min(ready, key=lambda b: (sum(u not in done for u, v in yard_edges if v == b),
                                       solver.Value(rank[b]), b))
        order.append(box)
        done.add(box)
    return BoundResult(lb, int(round(solver.ObjectiveValue())), status == cp_model.OPTIMAL,
                       relocated, mandatory, tuple(order), ship_edges, yard_edges,
                       name, monotonic() - start)


def solve_relaxation(problem, assignment, *, time_limit_s=10.0, seed=0):
    """Build the graph with Frontier's unmodified physical ship precedences."""
    from stowage.actions.space import Place
    from stowage.solver.gold_warmstart_search import precedence_sets

    actions = [Place(box, slot) for box, slot in assignment.items()]
    predecessors = precedence_sets(problem.instance, actions)
    edges = [(actions[j].box, actions[i].box)
             for i, pred in enumerate(predecessors) for j in pred]
    return solve_graph(assignment, edges,
                       (stack.boxes for stack in problem.instance.yard.stacks),
                       time_limit_s=time_limit_s, seed=seed)


def witness_actions(assignment, result):
    from stowage.actions.space import Place
    return tuple(Place(box, assignment[box]) for box in result.order)


@dataclass(frozen=True)
class ReplaySearchResult:
    actions: tuple
    relocations: int | None
    lower_bound: int
    optimal: bool
    exhausted: bool
    states: int
    seconds: float


def search_replay(problem, assignment, *, incumbent_actions=None,
                  time_limit_s=10.0, lower_bound=0, max_states=200_000):
    """Bounded exact order search under the real deterministic Expander policy.

    Every branch is a ship-precedence-ready Place action. Memoization compares
    identical complete yard/cover states, never just loaded subsets. A smaller
    relocation cost dominates only because this search optimizes relocations;
    hatch/other D11 components still require the caller's full replay/checker.
    Exhaustion or attainment of a separately valid lower bound proves minimum
    within monotone Place-only orders and the unchanged relocation policy.
    """
    from stowage.actions.expander import Expander
    from stowage.actions.space import Place
    from stowage.moves import apply_plan, apply_move, IllegalMoveError
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    from stowage.solver.gold_warmstart_search import precedence_sets

    start = monotonic()
    validate_prefix = not rule_freeze_v1_enabled()
    deadline = start + max(0.0, time_limit_s)
    actions = tuple(Place(box, slot) for box, slot in assignment.items())
    pred = precedence_sets(problem.instance, actions)
    pred_masks = [sum(1 << j for j in ps) for ps in pred]
    all_done = (1 << len(actions)) - 1
    best, best_actions = None, ()
    if incumbent_actions and (len(incumbent_actions) == len(assignment)
                              and {getattr(a, 'box', None): getattr(a, 'slot', None)
                                   for a in incumbent_actions} == assignment):
        scored = problem.score(incumbent_actions)
        if scored is not None:
            replay = apply_plan(problem.instance, scored.plan)
            if replay.legal and replay.count.restow_lifts == 0:
                best = replay.count.relocations
                best_actions = tuple(incumbent_actions)
    # Reachability identifies forced pair inversions in each remaining yard.
    successor = {a.box: set() for a in actions}
    for i, ps in enumerate(pred):
        for j in ps:
            successor[actions[j].box].add(actions[i].box)
    for _ in actions:
        changed = False
        for box, found in successor.items():
            extended = found | set().union(*(successor[b] for b in found)) if found else found
            if extended != found:
                successor[box] = extended
                changed = True
        if not changed:
            break
    seen, states, stopped = {}, 0, False

    def forced(ex, done):
        pending = {a.box for i, a in enumerate(actions) if not done & (1 << i)}
        moved = set()
        for stack in ex.yard.values():
            for i, lower in enumerate(stack):
                if lower in pending:
                    moved.update(upper for upper in stack[i + 1:]
                                 if upper not in pending or upper in successor[lower])
        return len(moved)

    def visit(ex, done, cost, order):
        nonlocal states, stopped, best, best_actions
        if best is not None and best <= lower_bound:
            return
        if monotonic() >= deadline or states >= max_states:
            stopped = True
            return
        states += 1
        if best is not None and max(lower_bound, cost + forced(ex, done)) >= best:
            return
        if done == all_done:
            completed = tuple(actions[i] for i in order)
            # Closing covers and future-port projection can still reject a
            # physically expandable prefix. Only admit a complete real replay.
            scored = problem.score(completed)
            if scored is not None:
                replay = apply_plan(problem.instance, scored.plan)
                if replay.legal and replay.count.restow_lifts == 0:
                    best, best_actions = replay.count.relocations, completed
            return
        key = (done, tuple(sorted((key, tuple(stack)) for key, stack in ex.yard.items())),
               tuple(sorted(ex.ylen.items())), tuple(sorted(ex.foot.items())),
               tuple(sorted(ex.covers_off.items())), tuple(ex.buffer))
        old = seen.get(key)
        if old is not None and old <= cost:
            return
        seen[key] = cost
        children = []
        for i, action in enumerate(actions):
            if done & (1 << i) or pred_masks[i] & ~done:
                continue
            trial = ex.copy()
            try:
                moves = trial.step(action)
                if validate_prefix:
                    state = ex.world_state()
                    for move in moves:
                        state = apply_move(problem.instance, state, move)
            except (ValueError, KeyError, TypeError, IllegalMoveError):
                continue
            if any(move.kind.startswith('restow') for move in moves):
                continue
            extra = sum(move.kind == 'relocate' for move in moves)
            children.append((extra, action.box, i, trial))
        for extra, _, i, trial in sorted(children, key=lambda child: child[:2]):
            if best is None or cost + extra < best:
                visit(trial, done | (1 << i), cost + extra, (*order, i))
            if stopped or (best is not None and best <= lower_bound):
                return

    visit(Expander(problem.instance), 0, 0, ())
    exhausted = not stopped and not (best is not None and best <= lower_bound)
    proven = best is not None and (exhausted or best <= lower_bound)
    return ReplaySearchResult(best_actions, best, best if proven else lower_bound,
                              proven, exhausted, states, monotonic() - start)
