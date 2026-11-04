"""Discrepancy-inspired rounding of a fixed page-model LP point.

This is a heuristic transfer, not an implementation of a discrepancy theorem.
Cover and stack rows never get dropped. Physical rows can be released by a
Beck--Fiala-style floating-variable walk; CP-SAT closes the integer obstruction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import heapq
from time import monotonic

import numpy as np
from scipy.linalg import qr
from ortools.sat.python import cp_model

_EPS = 1e-7


def _feature_array(model, use):
    return np.asarray([model.features(model.columns[k]) for k in use], dtype=float).T


def _kernel(rows, n):
    """An orthonormal kernel basis; numerical rank uses scaled row coefficients."""
    if not rows:
        return np.eye(n)
    a = np.asarray(rows, dtype=float)
    norm = np.linalg.norm(a, axis=1)
    a = a[norm > 1e-12] / norm[norm > 1e-12, None]
    if not len(a):
        return np.eye(n)
    q, r, _ = qr(a.T, mode='full', pivoting=True, check_finite=False)
    diag = np.abs(np.diag(r))
    rank = int(np.sum(diag > max(1e-10, (diag.max() if len(diag) else 0.)*1e-9)))
    return q[:, rank:]


def _pin_basis(basis, i):
    """Intersect the current kernel with the plane d[i] = 0, without another SVD."""
    row = basis[i]
    if np.linalg.norm(row) < 1e-10:
        return basis
    q, _ = qr(row[:, None], mode='full', check_finite=False)
    return basis @ q[:, 1:]


def _floating_walk(model, root, *, deadline, seed, variant):
    use = [k for k, v in root['x'].items() if v > _EPS]
    x = np.array([root['x'][k] for k in use], dtype=float)
    info = dict(steps=0, dropped=[], protected=list(model.feature_names))
    if not use or root.get('uncovered', 0.) > 1e-5:
        info['reason'] = 'fractional cover has uncovered slack'
        return dict(root['x']), info
    boxes = {b: i for i, b in enumerate(model.boxes)}
    stacks = {j: i for i, j in enumerate(model.stacks)}
    cover = np.zeros((len(boxes), len(use)))
    load = np.zeros((len(stacks), len(use)))
    for col, k in enumerate(use):
        c = model.columns[k]
        for b in c.boxes:
            cover[boxes[b], col] = 1.
        for j in model.pages[c.page].stacks:
            load[stacks[j], col] = 1.
    if np.max(np.abs(cover @ x - 1.)) > 1e-5 or np.max(load @ x) > 1.+1e-5:
        info['reason'] = 'root does not satisfy exact structural rows'
        return dict(root['x']), info
    # Stack inequalities become exact equations through nonnegative unused-space
    # coordinates. Their coefficients and cover incidence remain exact throughout.
    z = np.r_[x, np.clip(1.-load @ x, 0., 1.)]
    structural = np.block([[cover, np.zeros((len(boxes), len(stacks)))],
                           [load, np.eye(len(stacks))]])
    f = _feature_array(model, use)
    side = np.c_[f, np.zeros((len(f), len(stacks)))]
    scale = np.maximum(np.max(np.abs(f), axis=1), 1e-9)
    target = f @ x
    protected = list(range(len(f)))
    rng = np.random.default_rng(seed)
    while monotonic() < deadline:
        fractional = np.flatnonzero((z > _EPS) & (z < 1.-_EPS))
        if not len(fractional):
            break
        if len(fractional) > 900:
            info['reason'] = 'kernel dimension guard; CP-SAT completion'
            break
        rows = list(structural[:, fractional])
        rows += list(side[protected][:, fractional])
        basis_small = _kernel(rows, len(fractional))
        if not basis_small.shape[1]:
            if not protected:
                info['reason'] = 'fractional structural vertex; integer completion needed'
                break
            # Beck--Fiala's transferable idea is to abandon a side equation only
            # when it touches few remaining floating coordinates. No cargo or
            # set-system discrepancy bound is asserted here.
            release = min(protected, key=lambda j: (np.count_nonzero(np.abs(side[j, fractional]) > 1e-10), j))
            protected.remove(release)
            info['dropped'].append(model.feature_names[release])
            continue
        basis = np.zeros((len(z), basis_small.shape[1]))
        basis[fractional] = basis_small
        while basis.shape[1] and monotonic() < deadline:
            if variant == 'walk':
                direction = basis @ rng.normal(size=basis.shape[1])
            else:
                # A fixed direction makes this the deterministic edge-walk arm.
                direction = basis[:, 0]
            moving = np.flatnonzero(np.abs(direction) > 1e-10)
            if not len(moving):
                break
            d = direction[moving]
            current = z[moving]
            upper = np.where(d > 0., (1.-current)/np.maximum(d, 1e-300), -current/np.minimum(d, -1e-300))
            lower = np.where(d > 0., -current/np.maximum(d, 1e-300), (1.-current)/np.minimum(d, -1e-300))
            hi, lo = float(np.min(upper)), float(np.max(lower))
            if hi < -1e-6 or lo > 1e-6:
                info['reason'] = 'numerical box intersection'
                return dict(zip(use, z[:len(use)])), info
            if variant == 'walk':
                # Mean-zero endpoint choice: this is a projected random walk,
                # inspired by partial colouring rather than its theorem's walk.
                step = hi if rng.random() < -lo/max(1e-15, hi-lo) else lo
            else:
                a = (f @ (z[:len(use)] + hi*direction[:len(use)])-target)/scale
                b = (f @ (z[:len(use)] + lo*direction[:len(use)])-target)/scale
                step = hi if (np.max(np.abs(a)), np.sum(np.abs(a))) <= (np.max(np.abs(b)), np.sum(np.abs(b))) else lo
            z += step*direction
            z[np.abs(z) < _EPS] = 0.
            z[np.abs(z-1.) < _EPS] = 1.
            hits = [i for i in moving if z[i] <= _EPS or z[i] >= 1.-_EPS]
            if not hits:
                break
            for i in hits:
                basis = _pin_basis(basis, i)
            info['steps'] += 1
    info['protected'] = [model.feature_names[j] for j in protected]
    info['fractional_remaining'] = int(np.sum((z[:len(use)] > _EPS) & (z[:len(use)] < 1.-_EPS)))
    info['walk_drift'] = dict(zip(model.feature_names, (f @ z[:len(use)]-target).tolist()))
    return {k: float(v) for k, v in zip(use, z[:len(use)]) if v > _EPS}, info


def _complete(model, root, hint, *, deadline, seed):
    """Pooled exact cover minimizing maximum normalized physical discrepancy."""
    witness = list(getattr(model, 'rounding_witness', None) or [])
    witness_boxes = [b for k in witness for b in model.columns[k].boxes]
    witness_stacks = [j for k in witness for j in model.pages[model.columns[k].page].stacks]
    if (sorted(witness_boxes) != sorted(model.boxes)
            or len(set(witness_stacks)) != len(witness_stacks)
            or any(k in model.banned for k in witness)):
        witness = []
    mandatory = set(root['x']) | set(witness)
    candidates = [k for k in range(len(model.columns)) if k not in model.banned and k not in mandatory]
    # Preserve every root-support and witness column. Fill the capped remainder
    # with inexpensive alternatives having affinity to the root on their page.
    affinity = defaultdict(float)
    for k, value in root['x'].items():
        c = model.columns[k]
        for b in c.boxes:
            affinity[c.page, b] += value
    rng = np.random.default_rng(seed)
    jitter = {k: rng.random() for k in candidates}
    def ranking(k):
        c = model.columns[k]
        overlap = sum(affinity[c.page, b] for b in c.boxes)/max(1, len(c.boxes))
        cost = model.column_cost(c, 3) if hasattr(model, 'column_cost') else c.cost
        return (-overlap, cost, jitter[k])
    candidates.sort(key=ranking)
    pool_cap = int(getattr(model, 'rounding_pool_cap', 2000))
    use = sorted(mandatory | set(candidates[:max(0, pool_cap-len(mandatory))]))
    if not use:
        return None, dict(reason='empty completion pool', fallback_witness=False)
    f = _feature_array(model, use)
    maximum = np.maximum(np.max(np.abs(f), axis=1), 1e-9)
    coeff = np.rint(1000.*f/maximum[:, None]).astype(np.int64)
    index = {k: i for i, k in enumerate(use)}
    target = np.rint(sum((coeff[:, index[k]]*v for k, v in root['x'].items() if k in index), np.zeros(len(f)))).astype(np.int64)
    sat = cp_model.CpModel()
    variables = {k: sat.NewBoolVar(f'p{k}') for k in use}
    cover, stacks = defaultdict(list), defaultdict(list)
    for k in use:
        column = model.columns[k]
        for b in column.boxes:
            cover[b].append(variables[k])
        for j in model.pages[column.page].stacks:
            stacks[j].append(variables[k])
    for b in model.boxes:
        if not cover[b]:
            return None, dict(reason='box absent from fixed pool', box=int(b))
        sat.AddExactlyOne(cover[b])
    for terms in stacks.values():
        sat.AddAtMostOne(terms)
    # Coefficients are normalized only for numerical conditioning and objective
    # comparability; final diagnostics use the unrounded checker-derived rows.
    bound = max(1, 2000*len(model.boxes))
    deviations = []
    for j in range(len(f)):
        deviation = sat.NewIntVar(0, bound, f'd{j}')
        expression = sum(int(coeff[j, i])*variables[k] for i, k in enumerate(use) if coeff[j, i])
        sat.AddAbsEquality(deviation, expression-int(target[j]))
        deviations.append(deviation)
    worst = sat.NewIntVar(0, bound, 'worst')
    sat.AddMaxEquality(worst, deviations)
    # Prefer the walk's coordinates only after physical discrepancy. This makes
    # the rounding arm observable while keeping the LP's ship moments foremost.
    tie = sum(int(round(100*(1.-2.*hint.get(k, 0.))))*variables[k] for k in use)
    tie_bound = max(1, 200*len(model.boxes))
    sat.Minimize((tie_bound+1)*(1000*worst+sum(deviations))+tie)
    witness_set = set(witness)
    for k in use:
        sat.AddHint(variables[k], int(k in witness_set) if witness else int(hint.get(k, 0.) > .5))
    if witness:
        # Supply auxiliary values too: the entire original CP model now has a
        # feasible hint, rather than a partial placement hint needing repair.
        witness_values = [int(abs(sum(int(coeff[j, index[k]]) for k in witness)-int(target[j])))
                          for j in range(len(f))]
        for deviation, value in zip(deviations, witness_values):
            sat.AddHint(deviation, value)
        sat.AddHint(worst, max(witness_values))
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = int(seed % 2147483647)
    solver.parameters.max_presolve_iterations = 3
    solver.parameters.max_time_in_seconds = max(.001, deadline-monotonic())
    did_solve = monotonic() < deadline
    status = solver.Solve(sat) if did_solve else cp_model.UNKNOWN
    info = dict(status=solver.StatusName(status), pool_columns=len(use), pool_cap=pool_cap,
                original_pool_columns=len(model.columns), root_support_columns=len(root['x']),
                witness_columns=len(witness), cp_s=solver.WallTime() if did_solve else 0.,
                fallback_witness=False)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        if not witness:
            return None, info
        chosen = witness
        info['fallback_witness'] = True
    else:
        chosen = [k for k in use if solver.Value(variables[k])]
    target_real = sum((np.asarray(model.features(model.columns[k]))*v for k, v in root['x'].items()), np.zeros(len(f)))
    actual = sum((np.asarray(model.features(model.columns[k])) for k in chosen), np.zeros(len(f)))
    info.update(drift=dict(zip(model.feature_names, (actual-target_real).tolist())),
                max_normalized_drift=float(np.max(np.abs(actual-target_real)/maximum)),
                quantization='1000 units per largest column coefficient')
    return chosen, info


def round_lp(model, root, *, deadline, seed=0, variant='iterated'):
    """Return (column indices or None, diagnostics) from the identical root LP.

    ``iterated`` drops low-support physical side equations and follows kernel
    edges; ``walk`` chooses random projected directions and mean-zero endpoints;
    ``cp`` is an ablation omitting the walk. All use the same fixed pool closure.
    """
    if variant not in ('iterated', 'walk', 'cp'):
        raise ValueError(f'unknown discrepancy variant {variant}')
    started = monotonic()
    if variant == 'cp':
        hint, walk = root['x'], dict(steps=0)
    else:
        walk_deadline = min(deadline, started+min(12., max(0., deadline-started)*.2))
        hint, walk = _floating_walk(model, root, deadline=walk_deadline, seed=seed, variant=variant)
    chosen, completion = _complete(model, root, hint, deadline=deadline, seed=seed)
    return chosen, dict(variant=variant, walk=walk, completion=completion, seconds=monotonic()-started,
                        repricing=False)


def _kk_signs(values):
    """Karmarkar--Karp largest differencing, with the partition recovered."""
    heap = [(-abs(float(v)), i, i) for i, v in enumerate(values)]
    heapq.heapify(heap)
    counter = len(heap)
    while len(heap) > 1:
        na, _, a = heapq.heappop(heap)
        nb, _, b = heapq.heappop(heap)
        heapq.heappush(heap, (na-nb, counter, (a, b)))
        counter += 1
    signs = {}
    def recover(tree, sign):
        if isinstance(tree, int):
            signs[tree] = sign
        else:
            recover(tree[0], sign)
            recover(tree[1], -sign)
    if heap:
        recover(heap[0][2], 1)
    return signs


def balance_kk(model, chosen, *, deadline, seed=0):
    """Differencing chooses disjoint same-bay mirror swaps; keep other rows exact."""
    from stowage.conventions import parse_slot
    chosen = list(chosen)
    before = float(model.moments(chosen))
    used = {model.columns[k].page: i for i, k in enumerate(chosen)}
    heel_rows = [j for j, name in enumerate(model.feature_names) if 'heel' in name.lower() or 'tcg' in name.lower()]
    if not heel_rows:
        return chosen, before, dict(reason='heel feature unidentified', pairs=0)
    other_rows = [j for j in range(len(model.feature_names)) if j not in heel_rows]
    groups = defaultdict(list)
    for page_index in used:
        page = model.pages[page_index]
        if not page.deck:
            continue
        slot = model.T.slot_ids[page.slots[0]]
        bay = parse_slot(slot).bay
        tcg = model.p.cells_tcg(slot)
        groups[(model.type_of[page_index], bay, round(abs(tcg), 6))].append(page_index)
    pairs = []
    for pages in groups.values():
        left = sorted(k for k in pages if model.p.cells_tcg(model.T.slot_ids[model.pages[k].slots[0]]) < -1e-6)
        right = sorted(k for k in pages if model.p.cells_tcg(model.T.slot_ids[model.pages[k].slots[0]]) > 1e-6)
        for one, two in zip(left, right):
            if monotonic() >= deadline:
                break
            i, j = used[one], used[two]
            a, b = model.columns[chosen[i]], model.columns[chosen[j]]
            if not model.legal(model.pages[two], a.boxes) or not model.legal(model.pages[one], b.boxes):
                continue
            if not model.lash_ok(model.pages[two], a.boxes) or not model.lash_ok(model.pages[one], b.boxes):
                continue
            old = np.asarray(model.features(a))+np.asarray(model.features(b))
            new = np.asarray(model.features(replace(a, page=two)))+np.asarray(model.features(replace(b, page=one)))
            if np.any(np.abs((new-old)[other_rows]) > 1e-6):
                continue
            h = heel_rows[0]
            pairs.append((i, j, one, two, float(old[h]), float(new[h])))
    center = before-sum(a for *_, a, b in pairs)+sum((a+b)/2. for *_, a, b in pairs)
    deltas = [(a-b)/2. for *_, a, b in pairs]
    signs = _kk_signs([*deltas, center])
    anchor = signs.get(len(deltas), 1)*(1 if center >= 0. else -1)
    orientations = [signs[i]*anchor*(1 if d >= 0. else -1) for i, d in enumerate(deltas)]
    predicted = center+sum(o*d for o, d in zip(orientations, deltas))
    swapped = 0
    if abs(predicted) < abs(before)-1e-7:
        for orientation, (i, j, one, two, _, _) in zip(orientations, pairs):
            if orientation < 0:
                chosen[i], chosen[j] = model._moved(chosen[i], two), model._moved(chosen[j], one)
                swapped += 1
    after = float(model.moments(chosen))
    return chosen, after, dict(pairs=len(pairs), swaps=swapped, before=before, after=after,
                               preserves='all nonheel feature rows within 1e-6')
