"""Run the page model on one map and print what the LP, the integer master and the mask say."""
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[6]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'experiments/mvp/frontier'), str(Path(__file__).parent)]

from alns import Problem  # noqa: E402
from pages import Model  # noqa: E402
from sequence import order_legal  # noqa: E402
from stowage.io import load_instance  # noqa: E402


def main(path, seconds=60.):
    instance = load_instance(ROOT/path)
    problem = Problem(instance)
    started = monotonic()
    model = Model(problem)
    print(instance.id, 'boxes', len(model.boxes), 'pages', len(model.pages), 'types', len(model.types),
          'stacks', len(model.stacks), 'setup', round(monotonic()-started, 2), flush=True)
    result = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks),
                            deadline=monotonic()+seconds, log=lambda m: print(' ', m, flush=True))
    print('LP', {k: v for k, v in result.items() if k != 'x'}, 'time', round(monotonic()-started, 1), flush=True)
    packed = model.generate(active_boxes=set(model.boxes), active_stacks=set(model.stacks), phase=3,
                            deadline=monotonic()+seconds)
    print('LP fewest stacks', round(packed['objective'], 2), 'proved', packed['proved'], 'of', len(model.stacks),
          'columns', len(model.columns), 'time', round(monotonic()-started, 1), flush=True)
    print('fractional', sum(1 for v in result['x'].values() if v < .999), 'of', len(result['x']))
    chosen, info = model.dive(deadline=monotonic()+60, log=lambda m: print(' ', m, flush=True))
    print('dive', info, flush=True)
    if chosen:
        report(problem, model, chosen)


def report(problem, model, chosen):
    assignment = model.assignment(chosen)
    print(' assigned', len(assignment), 'mixes', problem.mixes_hold_stack(assignment), 'empty hold cells', model.empty_hold_cells(chosen))
    T = model.T
    heavy_deck = sum(1 for b, s in assignment.items() if T.mass[T.box_index[b]] >= 20000 and '8' <= s[5] <= '9')
    print(' heavy on deck', heavy_deck)
    from stowage.actions.space import Place
    from stowage.solver.gold_warmstart_search import precedence_sets
    actions, stuck = order_legal(problem, assignment)
    print(' order', len(actions), 'stuck', stuck, flush=True)
    if stuck is None:
        print(' score', getattr(problem.score(actions), 'objective', None), flush=True)
        return
    engine = problem.engine
    n = 0
    try:
        for a in actions:
            engine.push(a); n += 1
        print(' dead_end', engine.dead_end())
        print(' completion', engine.completion_check())
        left = [b for b in assignment if b not in {a.box for a in actions}]
        for b in left:
            print('  left', b, assignment[b], T.mass[T.box_index[b]], T.length[T.box_index[b]], bool(T.is_dg[T.box_index[b]]), bool(T.fallback[T.box_index[b]]), 'legal slots', len(engine.legal_slots_for_box(b)))
    finally:
        for _ in range(n):
            engine.pop()


if __name__ == '__main__':
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 60.)
