"""One cumulative CP-SAT deterministic-work allowance, plus a wall safety guard."""
import math
from time import monotonic


class WorkExhausted(RuntimeError):
    pass


class SolverWork:
    def __init__(self, limit, *, wall_s, max_solves=1000):
        if (isinstance(limit, bool) or not math.isfinite(float(limit)) or limit <= 0
                or not math.isfinite(float(wall_s)) or wall_s <= 0):
            raise ValueError('work_limit and wall budget must be finite and positive')
        if isinstance(max_solves, bool) or not isinstance(max_solves, int) or max_solves <= 0:
            raise ValueError('max_solves must be a positive integer')
        self.max_solves = max_solves
        self.limit = float(limit)
        self.used = 0.
        self.solves = 0
        self.deadline = monotonic()+wall_s
        self.outer_wall_hit = False

    @property
    def remaining(self):
        return max(0., self.limit-self.used)

    def available(self):
        if monotonic() >= self.deadline:
            self.outer_wall_hit = True
            return False
        return self.remaining > 1.e-12 and self.solves < self.max_solves

    def configure(self, solver, *, cap=None):
        if not self.available():
            raise WorkExhausted('outer_wall_guard' if self.outer_wall_hit else
                                'solve_count_limit' if self.solves >= self.max_solves else
                                'deterministic_work_limit')
        allowance = self.remaining if cap is None else min(self.remaining, float(cap))
        if allowance <= 0:
            raise WorkExhausted('deterministic_work_limit')
        solver.parameters.max_deterministic_time = allowance
        solver.parameters.max_time_in_seconds = max(.001, self.deadline-monotonic())
        solver.parameters.num_search_workers = 1

    def record(self, solver):
        amount = float(solver.deterministic_time)
        if not math.isfinite(amount) or amount < 0:
            raise RuntimeError('solver returned invalid deterministic work')
        self.used += amount
        self.solves += 1
        if monotonic() >= self.deadline:
            self.outer_wall_hit = True
        return amount

    def metadata(self, *, stop_reason=None):
        self.available()
        return dict(unit='CP-SAT deterministic seconds', limit=self.limit, used=self.used,
                    solves=self.solves, max_solves=self.max_solves,
                    solve_limit_reached=self.solves >= self.max_solves,
                    limit_reached=self.remaining <= 1.e-12,
                    outer_wall_hit=self.outer_wall_hit,
                    work_complete=not self.outer_wall_hit and stop_reason != 'static_oracle_unknown',
                    stop_reason=('outer_wall_guard' if self.outer_wall_hit else stop_reason))
