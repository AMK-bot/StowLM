"""Pattern cover with equal-height paired twenty bases below long deck cargo.

Extends the frozen homogeneous grammar, without changing its implementation.
Patterns remain bounded proposals; native replay, E6, finishing and full
checking remain mandatory. Existing witnesses are not imported here.
"""
from collections import Counter
from time import monotonic

from ortools.sat.python import cp_model

from pattern_model import Patterns


class MixedPatterns(Patterns):
    def _remember_mixed(self, unit, sequence, slots):
        key = ('mixed', unit, tuple(zip(sequence, slots)))
        if not sequence or key in self.seen:
            return
        self.seen.add(key)
        self.patterns.append({'unit': unit, 'stacks': self.units[unit][0],
                              'slots': tuple(slots), 'sequence': tuple(sequence),
                              'counts': dict(Counter(sequence)), 'mixed_deck': True})

    def _sample_mixed(self, u, base_depth, attempt, deadline):
        T = self.T
        js, longslots, _ = self.units[u]
        sight = T.extra.get('stack_visibility_top')
        hmax = [float(T.stack_hmax[j]) for j in js]
        if sight is not None:
            hmax = [min(hmax[side], float(sight[j])-float(T.stack_base_z[j]))
                    for side, j in enumerate(js)]
        # Both envelopes apply when twenty-foot cargo occurs. Restrict even
        # the twenty-only prefix to the final mixed envelope: conservative.
        limits = [min(float(T.stack_lim_20[j]), float(T.stack_lim_mixed[j])) for j in js]
        masses, heights = [0., 0.], [0., 0.]
        minimum, rankmin = [float('inf')]*2, [1 << 20]*2
        nonheavy, closed = [False, False], [False, False]
        counts = Counter()
        sequence, chosen_slots = [], []
        preference = [self.rng.expovariate(1.) for _ in self.groups]

        def fits(g, side, width):
            if counts[g] >= self.demand[g] or closed[side]:
                return False
            if masses[side]+self.mass[g]/width > limits[side]+1e-6:
                return False
            if heights[side]+self.height[g] > hmax[side]+1e-9:
                return False
            if heights[side] > 0:
                if self.freeze:
                    if self.kg_tol is None or self.mass[g] > minimum[side]+self.kg_tol:
                        return False
                elif (self.rank[g] > rankmin[side]+int(T.e2_tol)
                      or self.heavy[g] and nonheavy[side]):
                    return False
            return True

        def value(g):
            mode = attempt % 5
            if mode == 0:
                return (-self.rank[g], -self.mass[g], -preference[g])
            if mode == 1:
                return (self.mass[g]*(.3+preference[g]), -self.rarity[g], -self.rank[g])
            return (-preference[g]*(.05+self.rarity[g])*(1+self.demand[g]),
                    -self.rank[g], self.mass[g])

        def put(g, slot, sides, width):
            counts[g] += 1
            sequence.append(g)
            chosen_slots.append(slot)
            for side in sides:
                masses[side] += self.mass[g]/width
                heights[side] += self.height[g]
                minimum[side] = min(minimum[side], self.mass[g])
                rankmin[side] = min(rankmin[side], self.rank[g])
                nonheavy[side] |= not self.heavy[g]
                closed[side] |= self.closes[g]

        # Each base level has two independently chosen twenties of identical
        # actual height. Thus the eventual common long support is level.
        for k in range(base_depth):
            if monotonic() >= deadline:
                return
            cells = T.slot_cells[longslots[k]]
            shortslots = [T.slot_index[T.cell_ids[int(c)]] for c in cells]
            candidates = []
            for side, slot in enumerate(shortslots):
                candidates.append([g for g, b in enumerate(self.rep)
                                   if T.units[b] == 1 and T.static[b, slot]
                                   and not self.closes[g] and fits(g, side, 1)])
            if not candidates[0] or not candidates[1]:
                return
            # Group right candidates by exact generated height, then sample
            # a few best left groups rather than scan the Cartesian product.
            right_by_height = {}
            for g in sorted(candidates[1], key=value):
                right_by_height.setdefault(self.height[g], []).append(g)
            left = sorted(candidates[0], key=value)
            choice = None
            for a in left:
                for b in right_by_height.get(self.height[a], ()):
                    if a == b and counts[a]+2 > self.demand[a]:
                        continue
                    choice = a, b
                    break
                if choice is not None:
                    break
            if choice is None:
                return
            put(choice[0], shortslots[0], (0,), 1)
            put(choice[1], shortslots[1], (1,), 1)
            self._remember_mixed(u, sequence, chosen_slots)

        # Every subsequent box spans both columns, consumes half its mass in
        # each and obeys every lower box's order independently on each side.
        for k in range(base_depth, len(longslots)):
            if monotonic() >= deadline:
                return
            slot = int(longslots[k])
            candidates = [g for g in self.allowed[u][k]
                          if fits(g, 0, 2) and fits(g, 1, 2)]
            if not candidates:
                return
            g = min(candidates, key=value)
            put(g, slot, (0, 1), 2)
            self._remember_mixed(u, sequence, chosen_slots)
            if self.closes[g]:
                return

    def generate(self, per_unit, deadline):
        started = monotonic()
        remaining = max(0., deadline-started)
        # Preserve homogeneous alternatives, using fewer per unit so later
        # pairs receive capacity. Mixed passes then visit every pair before
        # giving any pair its next sample.
        super().generate(max(4, min(16, per_unit//4)), started+remaining*.42)
        pairs = [u for u, (js, slots, width) in enumerate(self.units)
                 if width == 2 and len(slots) > 1 and all(self.T.stack_deck[j] for j in js)]
        self.rng.shuffle(pairs)
        for attempt in range(max(8, per_unit)):
            for u in pairs:
                if monotonic() >= deadline:
                    return
                length = len(self.units[u][1])
                # Exhaust the available transitions cyclically, then vary the
                # cargo choices on later passes; no mixed hold patterns exist.
                depth = 1+attempt % (length-1)
                self._sample_mixed(u, depth, attempt, deadline)


def construct(instance, problem, seed, budget_s):
    """Pattern master plus unchanged native validation; no physics bypass."""
    from chain_model import validate
    started = monotonic()
    deadline = started+max(0., budget_s)
    detail = {'model': 'paired_twenty_base_long_deck_pattern_cover', 'attempts': [],
              'restrictions': ['fixed long support pairs', 'group maximum mass',
                               'equal height at each paired twenty base level',
                               'bounded sampled patterns'],
              'omissions': ['physics and ballast', 'full voyage checker']}
    construct.last_diagnostics = detail
    if instance.onboard.placements or instance.discharge_list:
        detail['unsupported'] = 'nonempty arrival or discharges'
        return []
    if budget_s <= .1:
        return []
    patterns = MixedPatterns(problem, seed=seed)
    detail.update(groups=len(patterns.groups), units=len(patterns.units))
    T = patterns.T
    hold_cells = sum(len(cells) for j, cells in enumerate(T.stack_cells) if not T.stack_deck[j])
    full_hold = (not patterns.freeze and len(T.box_ids) > 60
                 and sum(int(n) for n in T.units) >= hold_cells)
    rounds = [(64, full_hold), (128, False), (256, False)]
    for arm, (samples, fill) in enumerate(rounds):
        remaining = deadline-monotonic()
        if remaining < .1:
            break
        patterns.generate(samples, min(deadline, monotonic()+max(.05, remaining*.25)))
        model, selected = patterns.master(full_hold=fill)
        row = {'samples_per_unit': samples, 'full_hold': fill, 'patterns': len(selected),
               'mixed_patterns': sum(bool(p.get('mixed_deck')) for p in patterns.patterns),
               'solutions': []}
        detail['attempts'].append(row)
        arm_end = min(deadline, monotonic()+max(.1, remaining/(len(rounds)-arm)))
        for repeat in range(8):
            seconds = arm_end-monotonic()
            if seconds < .05:
                break
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = seconds
            solver.parameters.random_seed = (seed+arm*31+repeat) % 2_147_483_647
            solver.parameters.stop_after_first_solution = True
            status = solver.solve(model)
            item = {'status': solver.status_name(status), 'solve_s': solver.wall_time,
                    'branches': solver.num_branches, 'conflicts': solver.num_conflicts}
            row['solutions'].append(item)
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                break
            assignment = patterns.assignment(solver, selected)
            good, validation = validate(problem, assignment)
            item['validation'] = validation
            if good:
                detail['wall_s'] = monotonic()-started
                return [assignment]
            chosen = [v for v in selected if solver.boolean_value(v)]
            model.add(sum(chosen) <= len(chosen)-1)
    detail['wall_s'] = monotonic()-started
    return []


def propose(instance, *, seed, budget_s, context):
    from alns import Problem
    problem = context.get('problem') or Problem(instance)
    out = construct(instance, problem, seed, budget_s)
    context.setdefault('method_metadata', {})['mixed_patterns'] = construct.last_diagnostics
    return out
