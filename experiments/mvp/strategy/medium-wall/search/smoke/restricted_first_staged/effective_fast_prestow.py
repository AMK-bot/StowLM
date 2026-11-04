def _fast_prestow(canonical, root, *, beam_width, deadline):
    """Yard-blind greedy carrier with bounded alternatives at each prefix.

    APPROXIMATION: keep at most ``4 * beam_width`` slots per chosen box. This
    bounded backtracking search can miss a completion; exhaustion is reported
    as a search failure, never as a proof of infeasibility. Exact local/C1/C2
    queries come from the two-step adapter's cached per-prefix box domains.
    Cargo moments and POD order rank candidates, not certify departure physics.
    """
    boxes = {b.id: b for b in canonical.containers}
    load = set(canonical.load_list)
    original = {p.container_id: p.slot for p in canonical.onboard.placements
                if p.container_id not in canonical.discharge_list}
    cells = {c.id: c for c in canonical.vessel.cells}
    stacks = {s.id: s for s in canonical.vessel.stacks}
    cover_station = {h.id: tuple(sorted({stacks[sid].bay for sid in h.hold_stack_ids+h.deck_stack_ids if sid in stacks})) for h in canonical.vessel.hatch_covers}
    def station(covers): return tuple(sorted({cover_station.get(c, ()) for c in covers}))
    hull = canonical.vessel.hull
    center = hull.lpp_m / 2 if hull is not None else 0.
    pods = {cid: _pod(canonical, box) for cid, box in boxes.items()}
    cargo = {cid: _cargo_key(canonical, box) for cid, box in boxes.items()}
    ranks = {cid: {"H": 0, "M": 1, "L": 2}.get(category_of(box, canonical)[3], 3)
             for cid, box in boxes.items()}
    # Static geometry is shared across every prefix and every candidate score.
    geometry = {}
    def shape(slot):
        if slot not in geometry:
            footprint = [cells[c] for c in cells_of_slot(slot)]
            js = tuple(c.stack_id for c in footprint)
            geometry[slot] = (js, tuple({stacks[j].hatch_cover_id for j in js} - {None}),
                sum(c.lcg_m for c in footprint) / len(footprint) - center,
                sum(c.tcg_m for c in footprint) / len(footprint),
                sum(c.vcg_base_m for c in footprint) / len(footprint),
                parse_slot(slot).on_deck)
        return geometry[slot]

    reasons = set()
    slot_limit = max(4, 4 * beam_width)
    space = get_action_space("boxslot")

    def choices(engine, expander):
        # All inputs are canonical: no original yard position or retrieval cost
        # can affect the carrier's choices, including its tie-breaks.
        pending = [cid for cid in load | set(original)
                   if expander.where.get(cid, (None,))[0] != "ship"]
        def priority(cid):
            b = boxes[cid]
            k = cargo[cid]
            return (cid in load, not bool(b.dg or b.reefer or b.oog or b.special_stowage),
                    ranks[cid],
                    b.length_ft == 20, *k[1:])
        pending.sort(key=priority)
        discharges = (engine.legal_discharges() if canonical.discharge_list
                      and callable(getattr(engine, "legal_discharges", None)) else ())
        fallback = None
        if not callable(getattr(engine, "legal_slots_for_box", None)):
            fallback = tuple(engine.legal_actions())
            discharges = tuple(a for a in fallback if isinstance(a, Discharge))
        yield from sorted(discharges, key=space.sort_key)

        # Prefix aggregates make a slot score O(its footprint), rather than
        # rescanning every earlier cargo placement for each candidate slot.
        mx = my = mass = 0.
        stack_pod, hatch_pod = {}, {}
        station_fill = {}
        for cid, location in expander.where.items():
            if location[0] != "ship":
                continue
            js, covers, x, y, _, deck = shape(location[1])
            station_fill[station(covers)] = station_fill.get(station(covers), 0) + (not deck)
            m = boxes[cid].gross_mass_kg
            mx += m * x
            my += m * y
            mass += m
            for j in js:
                stack_pod[j] = min(stack_pod.get(j, math.inf), pods[cid])
            if not deck:
                for cover in covers:
                    hatch_pod[cover] = min(hatch_pod.get(cover, math.inf), pods[cid])

        domains = {}
        def ranked(cid, slots):
            b = boxes[cid]
            def score(slot):
                js, covers, x, y, height, deck = shape(slot)
                overstows = sum(pods[cid] > stack_pod.get(j, math.inf) for j in js)
                if deck:
                    overstows += sum(pods[cid] > hatch_pod.get(c, math.inf) for c in covers)
                action = Place(cid, slot)
                restow = (bool(engine.forces_restow(action))
                          if callable(getattr(engine, "forces_restow", None)) else False)
                m = b.gross_mass_kg
                return (restow, deck, -station_fill.get(station(covers), 0), height, overstows,
                        abs(my + m*y) / max(mass+m, 1.),
                        abs(mx + m*x) / max(mass+m, 1.), slot)
            accepted = 0
            for slot in sorted(slots, key=score):
                if monotonic() >= deadline:
                    return
                action = Place(cid, slot)
                reason = securing_geometry_violation(canonical, expander, action)
                if reason:
                    reasons.add(reason)
                    continue
                yield action
                accepted += 1
                if accepted >= slot_limit:
                    break

        # Complete the holds before putting any cargo over their covers. A
        # deck-only H booking must wait for a medium under-deck booking too;
        # otherwise later hold access needlessly unloads an entire deck prefix.
        # Both phases reuse the same exact cached box domains at this prefix.
        has_hold_frontier = getattr(engine, "has_open_hold_slots", None)
        phases = ((False, True) if not callable(has_hold_frontier) or has_hold_frontier()
                  else (True,))
        # A geometrically full hold cannot accept any legal placement. Skip
        # scanning the remaining manifest for hold domains at every deck lift;
        # the optional structural hook never certifies an action as legal.
        for deck in phases:
            for cid in pending:
                if monotonic() >= deadline:
                    return
                if cid not in domains:
                    slots = (engine.legal_slots_for_box(cid) if fallback is None else
                             tuple(a.slot for a in fallback if isinstance(a, Place) and a.box == cid))
                    if cid not in load:
                        slots = tuple(s for s in slots if s == original[cid])
                    domains[cid] = slots
                slots = tuple(s for s in domains[cid] if shape(s)[-1] == deck)
                if slots:
                    yield from ranked(cid, slots)

    # Retain only bounded candidate iterators, not full manifest × slot masks.
    nodes = [(root, (), Expander(canonical), None)]
    seen = set()
    while nodes:
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Bounded cargo-only pre-stow time limit")
        engine, trace, expander, iterator = nodes[-1]
        capture(canonical, engine, trace, expander)
        if engine.is_complete():
            violations = securing_state_violations(canonical, expander)
            if violations:
                reasons.update(violations)
                nodes.pop()
                continue
            from stowage.checker.rules import e6_completion_possible
            ship = canonical.onboard.model_copy(update={"placements": [
                Placement(container_id=cid, slot=location[1])
                for cid, location in expander.where.items() if location[0] == "ship"]})
            if not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index):
                reasons.add("Departure has compatible empty hold space for deck H [expert.e6.heavy_under_deck]")
                nodes.pop()
                continue
            places = tuple(Place(cid, expander.location(cid)[1]) for cid in sorted(load))
            return PreStowPlan(tuple(SlotCategory(a.slot, category_of(boxes[a.box], canonical), a.box)
                                     for a in places),
                               tuple(a for a in trace if isinstance(a, Place) and a.box in load))
        if iterator is None:
            iterator = iter(choices(engine, expander))
            nodes[-1] = engine, trace, expander, iterator
        for action in iterator:
            if monotonic() >= deadline:
                return PreStowPlan(status="timeout", message="Bounded cargo-only pre-stow time limit")
            # Queries are cached on this unchanged prefix; push remains the
            # authoritative legality guard, including for protocol-only doubles.
            child = engine.copy()
            child.push(action)
            child_expander = expander.copy()
            child_expander.step(action)
            signature = (tuple(sorted(child_expander.where.items())),
                         tuple(sorted(child_expander.covers_off.items())), tuple(child_expander.buffer))
            if signature in seen:
                continue
            seen.add(signature)
            nodes.append((child, trace + (action,), child_expander, None))
            break
        else:
            exhausted(engine)
            nodes.pop()
    detail = ": " + "; ".join(sorted(reasons)) if reasons else ""
    return PreStowPlan(status="dead_end", message="Bounded cargo-only pre-stow exhausted its candidates" + detail)
