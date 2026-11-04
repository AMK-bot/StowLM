# Copied from experiments/mvp/frontier/lab/c5/c5-peel/assignment_vendor/c4/c2-layout/pages_balanced.py; frozen body unchanged.
"""Optional exact-moment whole-page balancing, without changing frozen pricing.

This is a constructor search operator; native replay/full physics still decide
acceptance. Import this Model only in a separately measured method version.
"""
from math import fsum
from time import monotonic

from pages import Model as MomentModel, Page, Pattern


class Model(MomentModel):
    def balance(self, chosen, rng, rounds=1200, *, seconds=1.5, passes=3,
                vertical_slack_m=.01, lcg_slack_m=.03):
        """Reduce exact cargo transverse moment by legal whole-column moves.

        Up to ``rounds`` candidate operations and ``passes`` accepted moves;
        wall time is capped at two seconds. Fixed columns and physical stacks
        are protected. The returned heel is the exact selected moment in t m.
        """
        chosen = list(chosen)
        started = monotonic()
        deadline = started+min(2., max(0., seconds))
        limit = max(0, int(rounds))
        boxes, used_stacks = set(), set()
        for k in chosen:
            c = self.columns[k]
            p = self.pages[c.page]
            if boxes.intersection(c.boxes) or used_stacks.intersection(p.stacks):
                raise ValueError('balance requires disjoint concrete-box pages')
            boxes.update(c.boxes); used_stacks.update(p.stacks)
        if boxes != set(self.boxes):
            raise ValueError('balance requires complete concrete-box coverage')
        total = [fsum(self.column_moments(self.columns[k])[j] for k in chosen) for j in range(4)]
        mass = total[0]
        if mass <= 0.:
            return chosen, total[3]
        target_min, target_max = self.tcg_min, self.tcg_max
        if target_min is not None and target_max is not None:
            target_tcg = (target_min+target_max)/2.
        elif target_min is not None:
            target_tcg = max(0., target_min)
        elif target_max is not None:
            target_tcg = min(0., target_max)
        else:
            target_tcg = 0.
        target = mass*target_tcg
        # A soft-master incumbent may already lie outside the target interval.
        # Keep its current violation available, with only explicit small slack;
        # never silently widen to an unrelated vessel-wide interval.
        vertical_limit = max(total[1], self.cargo_vmoment_max if self.cargo_vmoment_max is not None else total[1])
        vertical_limit += mass*max(0., vertical_slack_m)
        initial_lcg = total[2]/mass
        lower = min(initial_lcg, self.lcg_min if self.lcg_min is not None else initial_lcg)-max(0., lcg_slack_m)
        upper = max(initial_lcg, self.lcg_max if self.lcg_max is not None else initial_lcg)+max(0., lcg_slack_m)
        fixed = set(getattr(self, 'fixed_columns', ()))
        fixed_stacks = {j for k in fixed for j in self.pages[self.columns[k].page].stacks}
        examined, accepted = 0, 0
        for _ in range(max(0, int(passes))):
            if examined >= limit or monotonic() >= deadline:
                break
            movable = [i for i, k in enumerate(chosen) if k not in fixed and
                       not fixed_stacks.intersection(self.pages[self.columns[k].page].stacks)]
            rng.shuffle(movable)
            candidates = []
            current_pages = {self.columns[k].page for k in chosen}
            used_stacks = {s for k in chosen for s in self.pages[self.columns[k].page].stacks}
            for n, i in enumerate(movable):
                if monotonic() >= deadline:
                    break
                source = self.pages[self.columns[chosen[i]].page]
                for j in movable[n+1:]:
                    if source.kind == self.pages[self.columns[chosen[j]].page].kind:
                        candidates.append((i, j, None))
                for destination, page in enumerate(self.pages):
                    if (destination not in current_pages and page.kind == source.kind
                            and not fixed_stacks.intersection(page.stacks)
                            and not (set(page.stacks) & (used_stacks-set(source.stacks)))):
                        candidates.append((i, None, destination))
            rng.shuffle(candidates)
            best = None
            before_distance = abs(total[3]-target)
            for i, j, destination in candidates:
                if examined >= limit or monotonic() >= deadline:
                    break
                examined += 1
                a = self.columns[chosen[i]]
                if j is not None:
                    b = self.columns[chosen[j]]
                    proposed = ((i, a, Pattern(b.page, a.boxes, a.cost)),
                                (j, b, Pattern(a.page, b.boxes, b.cost)))
                else:
                    proposed = ((i, a, Pattern(destination, a.boxes, a.cost)),)
                if any(len(new.boxes) > len(self.pages[new.page].slots)
                       or self.pool.get((new.page, new.boxes)) in self.banned
                       for _, _, new in proposed):
                    continue
                changed = list(total)
                for _, old, new in proposed:
                    old_g, new_g = self.column_moments(old), self.column_moments(new)
                    for component in range(4):
                        changed[component] += new_g[component]-old_g[component]
                gain = before_distance-abs(changed[3]-target)
                if (gain <= 1e-7 or changed[1] > vertical_limit+1e-7
                        or changed[2] < mass*lower-1e-7 or changed[2] > mass*upper+1e-7):
                    continue
                if any(not self.legal(self.pages[new.page], new.boxes)
                       or not self.lash_ok(self.pages[new.page], new.boxes)
                       for _, _, new in proposed):
                    continue
                rank = (gain, total[1]-changed[1], -abs(changed[2]-total[2]))
                if best is None or rank > best[0]:
                    best = (rank, proposed, changed)
            if best is None:
                break
            _, proposed, changed = best
            for i, _, new in proposed:
                self.add(new.page, new.boxes)
                index = self.pool.get((new.page, new.boxes))
                if index is None or index in self.banned:
                    raise RuntimeError('checked balancing pattern could not enter pool')
                chosen[i] = index
            total = changed
            accepted += 1
        self.balance_metadata = dict(examined=examined, accepted=accepted,
                                     seconds=monotonic()-started,
                                     transverse_target_tm=target,
                                     transverse_final_tm=total[3],
                                     scope='heuristic pure-page moment balancing; native checker required')
        return chosen, total[3]
