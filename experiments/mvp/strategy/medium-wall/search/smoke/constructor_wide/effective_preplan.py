def _preplan(instance, *, beam_width: int, deadline: float, feasibility_config=None, seed=0) -> PreStowPlan:
    if monotonic() >= deadline:
        return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
    canonical = _canonical_instance(instance)
    space = get_action_space("boxslot")
    kwargs = {"config": feasibility_config} if feasibility_config is not None else {}
    if _use_fast_prestow(instance):
        from stowage.feasibility import FeasibilityConfig
        if feasibility_config is None:
            feasibility_config = FeasibilityConfig(extra={"global_screen": 0})
        kwargs = {"config": feasibility_config}
        now = monotonic()
        greedy_budget = min(10, .9 * max(0., deadline - now))
        greedy_deadline = min(deadline, now + greedy_budget)
        # APPROXIMATION: greedy gets at most 10 seconds or 40% of the remaining
        # carrier budget. A fast failure gives all unused time to the MRV
        # constructor. This switches search only, never repairs a checker failure.
        stage = perf_counter()
        try:
            root = make_feasibility_engine(canonical, space, **kwargs)
            primary = _fast_prestow(canonical, root, beam_width=beam_width,
                                    deadline=greedy_deadline)
            if primary.status == "ok" and monotonic() >= greedy_deadline:
                primary = PreStowPlan(status="timeout", message="Greedy completion exceeded its initial slice")
        except Exception as exc:
            primary = PreStowPlan(status="error", message=f"Greedy carrier {type(exc).__name__}: {exc}")
        search_metrics = {
            "preplan_greedy_primary_status": primary.status,
            "preplan_greedy_primary_message": primary.message,
            "preplan_greedy_primary_time_s": perf_counter() - stage,
            "preplan_greedy_primary_budget_s": greedy_budget,
            "preplan_constructor_fallback_used": False,
            "constructor_status": "not_started", "constructor_shared_search_family": False,
            "effective_global_screen": feasibility_config.extra.get("global_screen", 1),
            "preplan_search_method": "bounded_greedy", "preplan_method": "bounded_greedy"}
        if primary.status == "ok":
            return replace(primary, search_metrics=search_metrics)
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Cargo-only greedy search exhausted pre-stow deadline",
                               search_metrics=search_metrics)
        from stowage.solver.twostep_construct import construct_preplan
        stage = perf_counter()
        fallback, constructor_metrics = construct_preplan(canonical, deadline=deadline,
            seed=seed, feasibility_config=feasibility_config)
        if fallback.status == "ok" and monotonic() >= deadline:
            fallback = PreStowPlan(status="timeout", message="Constructor completion exceeded pre-stow deadline")
        search_metrics.update(constructor_metrics)
        search_metrics.update(preplan_method="canonical_constructor_after_greedy",
            preplan_search_method="canonical_constructor_after_greedy",
            preplan_constructor_fallback_used=True, preplan_constructor_status=fallback.status,
            preplan_constructor_message=fallback.message,
            preplan_constructor_fallback_time_s=perf_counter() - stage)
        return replace(fallback, search_metrics=search_metrics)
    root = make_feasibility_engine(canonical, space, **kwargs)
    boxes = {b.id: b for b in canonical.containers}
    original = {p.container_id: p.slot for p in canonical.onboard.placements
                if p.container_id not in canonical.discharge_list}

    dead_reasons = set()
    model_unavailable = []
    cargo_keys = {cid: _cargo_key(canonical, box) for cid, box in boxes.items()}

    def choices(engine, trace, expander):
        # v2.3 exposes exactly the public legal Place set as flat indices. Group
        # array slices first, decoding only the box currently being explored;
        # this avoids millions of action objects on production-size ships.
        indexed = callable(getattr(engine, "legal_index", None))
        grouped = {}
        discharges = []
        if indexed:
            flat = engine.legal_index()
            n_slots = engine.n_slots
            slot_ids = engine.index_slots
            for bi, cid in enumerate(engine.index_boxes):
                lo = int(flat.searchsorted(bi * n_slots))
                hi = int(flat.searchsorted((bi + 1) * n_slots))
                if hi > lo:
                    indices = flat[lo:hi]
                    if cid not in canonical.load_list:
                        indices = indices[[slot_ids[int(i) % n_slots] == original.get(cid) for i in indices]]
                    if len(indices):
                        grouped[cid] = indices
            discharges = list(engine.legal_discharges())
        else:
            # Protocol-only oracles remain supported; the index API is additive.
            for action in engine.legal_actions():
                if isinstance(action, Discharge):
                    discharges.append(action)
                elif isinstance(action, Place):
                    if action.box not in canonical.load_list and original.get(action.box) != action.slot:
                        continue
                    grouped.setdefault(action.box, []).append(action)
        if not grouped and not discharges:
            dead = engine.dead_end()
            if dead is not None:
                dead_reasons.add(dead.reason + (f" [{', '.join(dead.rule_ids)}]" if dead.rule_ids else ""))
                if "checker.missing_vessel_data" in dead.rule_ids:
                    model_unavailable.append(dead.reason)
        def is_hold(value):
            slot = slot_ids[int(value) % n_slots] if indexed else value.slot
            return not parse_slot(slot).on_deck

        has_hold = {cid: any(is_hold(value) for value in values)
                    for cid, values in grouped.items()}
        yield from sorted(discharges, key=space.sort_key)
        def box_priority(cid):
            key = cargo_keys[cid]
            return (key[0], not has_hold[cid] if not key[0] else False, *key[1:])

        context = _slot_context(canonical, trace)
        if rule_freeze_v1_enabled():
            # Compare across boxes as well as slots: a mass/POD search hint
            # must never outrank actual moves or the checker's soft metrics.
            ranked = []
            for cid, values in grouped.items():
                for value in values:
                    if monotonic() >= deadline:
                        return
                    action = engine.decode_index(int(value)) if indexed else value
                    score = _carrier_action_score(canonical, expander, trace, action, context=context)
                    if score is not None:
                        ranked.append((score, space.sort_key(action), action))
            yield from (action for _, _, action in sorted(ranked))
            return
        for cid in sorted(grouped, key=box_priority):
            heavy = not cargo_keys[cid][0]
            values = grouped[cid]
            # A hold-compatible H must take hold before any of its deck choices.
            # Hard on-deck bookings have no compatible hold and retain their
            # legal deck choices. No rule is cloned: compatibility is the public
            # oracle's legal set, and departure E6 is checked below by 2.4.
            if (not rule_freeze_v1_enabled() and heavy and has_hold[cid]
                    and "on_deck" not in boxes[cid].special_stowage):
                values = [value for value in values if is_hold(value)]
            actions = (engine.decode_index(int(value)) for value in values) if indexed else values
            for action in sorted(actions, key=lambda a: (
                    bool(engine.forces_restow(a)) if callable(getattr(engine, "forces_restow", None)) else False,
                    _slot_score(canonical, trace, a, context=context))):
                yield action

    # Explicit DFS remembers untried alternatives at every depth. Unlike beam
    # pruning, a later dead end can return all the way to an earlier bad slot.
    stack = [(root, (), None, Expander(canonical))]
    seen = set()
    while stack:
        if monotonic() >= deadline:
            return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
        engine, trace, iterator, expander = stack[-1]
        if engine.is_complete():
            reasons = securing_state_violations(canonical, expander)
            if reasons:
                dead_reasons.update(reasons)
                stack.pop()
                continue
            from stowage.checker.rules import e6_completion_possible
            ship = canonical.onboard.model_copy(update={"placements": [
                Placement(container_id=cid, slot=location[1])
                for cid, location in expander.where.items() if location[0] == "ship"]})
            if (not rule_freeze_v1_enabled()
                    and not e6_completion_possible(canonical, ship, (), call_index=canonical.call_index)):
                dead_reasons.add("Departure has compatible empty hold space for deck H [expert.e6.heavy_under_deck]")
                stack.pop()
                continue
            places = tuple(Place(cid, expander.location(cid)[1]) for cid in sorted(instance.load_list))
            return PreStowPlan(tuple(SlotCategory(a.slot, category_of(boxes[a.box], canonical), a.box)
                                     for a in places),
                               tuple(a for a in trace if isinstance(a, Place) and a.box in instance.load_list))
        if iterator is None:
            iterator = iter(choices(engine, trace, expander))
            stack[-1] = engine, trace, iterator, expander
        advanced = False
        for action in iterator:
            if monotonic() >= deadline:
                return PreStowPlan(status="timeout", message="Cargo-only pre-stow time limit")
            if not engine.is_legal(action):
                continue
            if securing_geometry_violation(canonical, expander, action):
                dead_reasons.add("Unsupported cargo securing geometry [iacs.member_irs.container_securing]")
                continue
            child = engine.copy()
            child.push(action)
            child_trace = trace + (action,)
            child_expander = expander.copy()
            child_expander.step(action)
            signature = (tuple(sorted(child_expander.where.items())),
                         tuple((k, tuple(v)) for k, v in sorted(child_expander.yard.items())),
                         tuple(sorted(child_expander.covers_off.items())), tuple(child_expander.buffer))
            if signature in seen:
                continue
            seen.add(signature)
            stack.append((child, child_trace, None, child_expander))
            advanced = True
            break
        if not advanced:
            stack.pop()
    detail = ": " + "; ".join(sorted(dead_reasons)) if dead_reasons else ""
    if model_unavailable:
        return PreStowPlan(status="error", message="Public feasibility model unavailable" + detail)
    return PreStowPlan(status="dead_end", message="Cargo-only pre-stow search exhausted legal alternatives" + detail)
