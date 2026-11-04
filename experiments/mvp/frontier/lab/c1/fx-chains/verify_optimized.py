"""Check coverage and stack exclusion independently on tiny packing masters."""
from types import SimpleNamespace

from ortools.sat.python import cp_model
from optimized_patterns import _master


def fixture(patterns, demand, rarity):
    t = SimpleNamespace(stack_ids=['s0', 's1'], e6_fallback=False,
                        e6_threshold=None)
    return SimpleNamespace(T=t, freeze=False, patterns=[dict(counts=counts,
                           stacks=stacks, sequence=(), slots=())
                           for counts, stacks in patterns],
                           demand=demand, rarity=rarity)


def solve(p, **kwargs):
    model, variables, covered, scale = _master(p, [], [], **kwargs)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.max_time_in_seconds = 2
    status = solver.solve(model)
    assert status == cp_model.OPTIMAL
    return [int(solver.value(v)) for v in covered]


def main():
    p = fixture([({0: 1}, (0,)), ({1: 1}, (0,)), ({1: 1}, (1,))],
                [1, 1], [1, 100])
    assert solve(p) == [1, 1]
    p.patterns.pop()
    assert sum(solve(p)) == 1  # overlapping alternatives cannot both be used
    p = fixture([({0: 2}, (0,)), ({1: 1}, (0,))], [2, 1], [1, 100000])
    assert solve(p) == [2, 0]  # coverage strictly dominates rarity
    p = fixture([({0: 2}, (0,))], [1], [1])
    assert solve(p) == [0]  # no group may exceed manifest demand
    p = fixture([], [1, 1], [1, 1])
    assert solve(p) == [0, 0]  # empty pool is feasible and incomplete
    print('PASS: five exact tiny pattern-master checks')


if __name__ == '__main__':
    main()
