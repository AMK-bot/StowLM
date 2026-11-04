"""Current-pool greedy seeds with securing checked after every appended box.

Adapted from cgscale's native first_fit and C3's low-KG first_fit. Complete
and partial columns enter the original pattern gates, including near-capacity
patterns the conservatively rounded pricing DP may omit. No checker changes.
"""
from collections import defaultdict
from time import monotonic


def first_fit(model, boxes, stacks, rng=None, *, low_kg=False,
              kind_order=('long', '20'), special20_first=False, rarity_first=False):
    T = model.T
    free = set(stacks)
    opened = defaultdict(list)
    pages = sorted(range(len(model.pages)), key=lambda k: (
        model.pages[k].deck, -len(model.pages[k].slots), k))

    def persist():
        result = []
        for k, values in opened.items():
            if values:
                model.add(k, values)
                index = model.pool.get((k, tuple(values)))
                if index is not None and index not in model.banned:
                    result.append(index)
        return result

    order = [b for b in model.order if b in boxes]
    rarity = {}
    if rarity_first:
        rarity = {b: sum(any(model.static[b, s] for s in page.slots)
                         for page in model.pages
                         if (T.units[b] == 1) == (page.kind == '20')) for b in order}
    early = [b for b in order if special20_first and T.units[b] == 1 and b in model.special]
    groups = [('20', early)] if early else []
    early_set = set(early)
    groups += [(wanted, [b for b in order if b not in early_set and
                          (T.units[b] == 1) == (wanted == '20')]) for wanted in kind_order]
    for wanted, kept in groups:
        for b in sorted(kept, key=lambda b: (b not in model.special, rarity.get(b, 0))):
            if monotonic() >= model.deadline:
                persist()
                return None
            best = None
            for k in pages:
                page = model.pages[k]
                values = opened[k]
                if (page.kind != wanted or len(values) >= len(page.slots)
                        or not values and not set(page.stacks) <= free):
                    continue
                trial = values+[b]
                if not model.legal(page, trial) or not model.lash_ok(page, trial):
                    continue
                room = page.limit_kg-sum(float(T.share[q]) for q in trial)
                jitter = rng.uniform(0., 3000.) if rng is not None else 0.
                if low_kg:
                    height = (float(T.stack_base_z[page.stacks[0]])+
                              sum(float(T.height[q]) for q in values)+float(T.height[b])/2.)
                    rank = (page.deck and bool(model.heavy[b]),
                            float(T.mass[b])*height+jitter, not values, room, k)
                else:
                    rank = (page.deck == bool(model.heavy[b]), not values, room+jitter, k)
                if best is None or rank < best[0]:
                    best = rank, k
            if best is None:
                persist()
                model.last_refused = ('incremental_securing_fit', T.box_ids[b], wanted)
                return None
            k = best[1]
            if not opened[k]:
                free.difference_update(model.pages[k].stacks)
            opened[k].append(b)
    chosen = persist()
    covered = {b for k in chosen for b in model.columns[k].boxes}
    return chosen if covered == set(boxes) else None
