def _choices(instance, node, future, rng, metrics, attempt, deadline=float('inf'), *,
             heavy_first_ordering=False):
    """Rank actual legal pairs using future-domain scarcity and reservation."""
    from stowage.solver.scalable import _moment_preference
    if time.monotonic() >= deadline:
        return []
    ex, engine, t = node.ex, node.engine, future.t
    discharges = [Discharge(cid) for cid in instance.discharge_list
                  if ex.where.get(cid, (None,))[0] == 'ship']
    if discharges:
        return [a for a in discharges if engine.is_legal(a)]
    remaining = [cid for cid in instance.load_list if ex.where.get(cid, (None,))[0] != 'ship']
    remaining += [cid for cid in ex.buffer if cid not in remaining]
    if not remaining:
        return []
    indices, domains = future.evaluate(ex, remaining)
    potential_counts = domains.sum(axis=1)
    groups = Counter(future.signatures[c] for c in remaining)
    # Group domains also contain mass-specific restrictions. Union rather than
    # intersection keeps this count optimistic for unequal masses of one class.
    union = {}
    for ri, cid in enumerate(remaining):
        sig = future.signatures[cid]
        if sig not in union:
            union[sig] = domains[ri].copy()
        else:
            union[sig] |= domains[ri]
    deficits = [dict(boxes=groups[sig], slots=int(mask.sum()))
                for sig, mask in union.items() if groups[sig] > int(mask.sum())]
    # APPROXIMATION: keep placements fixed and decline forced-restow successors;
    # this is a search-domain restriction, not a physical infeasibility claim.
    monotone = True
    deficits.extend(future.pools.evaluate(indices, domains))
    if (deficits or future.last_capacity['demand'] > future.last_capacity['capacity']) and monotone:
        metrics['v4_reservation_prunes'] = metrics.get('v4_reservation_prunes', 0) + 1
        metrics['v4_last_deficits'] = deficits[:8]
        metrics['v4_visibility_capacity'] = dict(future.last_capacity)
        return []
    if time.monotonic() >= deadline:
        return []
    box_indices, slot_indices = engine.legal_pairs()
    metrics['candidate_probes'] += len(box_indices)
    metrics['legal_candidates'] += len(box_indices)
    counts = np.bincount(box_indices, minlength=len(t.box_ids))
    by_box = {cid: slot_indices[box_indices == t.box_index[cid]]
              for cid in remaining if counts[t.box_index[cid]]}
    if not by_box:
        return []
    row = {cid: ri for ri, cid in enumerate(remaining)}
    # Full physical/booking facts define representatives; coarse reservation
    # groups above deliberately use unions and are never equivalence proofs.
    def blockers(cid):
        location, key = ex.where.get(cid, ('', ''))
        return len(ex.yard[key])-ex.yard[key].index(cid)-1 if location == 'yard' else 0
    def box_key(cid):
        ri, bi = row[cid], t.box_index[cid]
        demand = groups[future.signatures[cid]]
        scarcity = potential_counts[ri] / demand
        box = ex.boxes[cid]
        # Heavy supports precede lighter bookings when space is plentiful;
        # truly scarce domains take priority over that preference.
        no_hold = not np.any(~t.slot_deck[by_box[cid]])
        common = (int(no_hold), int(scarcity > 2.0))
        profiles = ((-int(t.rank[bi]), -float(t.height[bi]), -box.length_ft, scarcity),
                    (-float(t.height[bi]), -int(t.rank[bi]), -box.length_ft, scarcity),
                    (-box.length_ft, -int(t.rank[bi]), -float(t.height[bi]), scarcity),
                    (scarcity, -int(t.rank[bi]), -float(t.height[bi]), -box.length_ft))
        # APPROXIMATION: when heavy-first ordering is opted in, use exact mass to order
        # similarly abundant bookings heavy first. This is search steering,
        # never a substitute for the unchanged force or feasibility checks.
        heavy_first = (-float(t.mass[bi]),) if heavy_first_ordering else ()
        return (*common, *heavy_first, *profiles[attempt % 4], len(by_box[cid]), blockers(cid), cid)

    ordered = sorted(by_box, key=box_key)
    reps, used = [], set()
    for cid in ordered:
        sig = future.representative_signatures[cid]
        if sig not in used:
            reps.append(cid)
            used.add(sig)
    # APPROXIMATION: branch on at most three fully distinct box profiles per
    # state. Bounded restarts diversify ordering; this is not exhaustive search.
    selected = reps[:min(12, len(reps))]
    # A slot needed by many scarce classes is expensive to consume. 20-foot
    # footprints count once; long footprints naturally reserve both cells.
    weights = np.array([1. / max(1., float(n)) for n in potential_counts])
    slot_pressure = weights @ domains
    cell_pressure = future.by_cell.T @ slot_pressure
    actions = []
    for order, cid in enumerate(selected):
        bi = t.box_index[cid]
        ranked = []
        for si in by_box[cid]:
            if time.monotonic() >= deadline:
                break
            si = int(si)
            action = Place(cid, t.slot_ids[si])
            addr = future.address[si]
            if engine.forces_restow(action):
                continue
            pressure = sum(cell_pressure[t.cell_index[c]] for c in future.parts[si])
            # Prefer filling low/middle constrained-height columns before
            # consuming columns that can still support the tallest bookings.
            js = t.slot_stacks[si]
            tightness = min(future.visibility[j] for j in js if j >= 0)
            lost = 0
            for j in js[js >= 0]:
                after = min(len(t.stack_cells[j])-future.last_fill[j]-1,
                            max(0, int(np.floor((future.visibility[j]-future.last_heights[j]-t.height[bi])/future.minimum_height+1e-9))))
                lost += max(0, int(future.last_cap[j])-after-1)

            # Holds preserve future deck/hatch access and reduce E6 pressure.
            key = (int(addr.on_deck), lost, pressure, tightness, addr.tier, _moment_preference(node, cid, future.parts[si]), rng.random(), action.slot)
            if attempt % 3 == 2:
                key = (int(addr.on_deck), lost, tightness, pressure, addr.tier, _moment_preference(node, cid, future.parts[si]), rng.random(), action.slot)
            ranked.append((key, action))
        ranked.sort(key=lambda item: item[0])
        actions.extend((order, key, action) for key, action in ranked)
    actions.sort(key=lambda item: (item[1][0], item[0], item[1]))
    return [a for _, _, a in actions]
