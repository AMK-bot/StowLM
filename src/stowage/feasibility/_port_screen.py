"""Shared move/expander probes for the activated feasibility path.

Target domains deliberately include unopened, unsupported and occupied slots.
They are an overestimate of eventual targets, so D69 can prove an obligation
only when every possible alternative crosses the newly raised stack.
"""
from __future__ import annotations

import numpy as np


def record_rejection(state, rule_id, *, pairs=()):
    """Count distinct rejected (box, slot) pairs per rule at this prefix.

    Prefix failures without a candidate still contribute an active rule ID,
    but cannot manufacture a rejected-pair count. Lazy and eager probes share
    this ledger; hatch-prefix ledgers merge by identity rather than totals.
    """
    state.sp.setdefault('port_screen_rule_ids', set()).add(rule_id)
    seen = state.sp.setdefault('screen_rejection_pairs', {}).setdefault(rule_id, set())
    seen.update(pairs)
    if seen:
        state.sp.setdefault('screen_rejections', {})[rule_id] = len(seen)


def pending_target_domains(tables, state):
    """Exhaustive static domains, cached on the immutable prefix."""
    key = "pending_load_targets"
    if key not in state.sp:
        state.sp[key] = {
            tables.box_ids[int(b)]: tuple(tables.slot_ids[int(s)]
                                         for s in np.flatnonzero(tables.static[b]))
            for b in np.flatnonzero(state.rem)
        }
    return state.sp[key]


def port_action_allowed(instance, tables, state, action, *, physical_checked=False):
    """Delegate crossing and D69; replay D64 only after its checker gate opens."""
    pair = (action.box, getattr(action, 'slot', state.ex.where.get(action.box, (None, None))[1]))
    if not physical_checked and not state.ex.can_expand(action, pending_load_targets=pending_target_domains(tables, state)):
        record_rejection(state, _expansion_rule(state.ex, action), pairs=(pair,))
        return False
    from .in_port import in_port_screen_enabled, InPortPrefixScreen

    if not in_port_screen_enabled():
        return True
    from stowage.moves import apply_move

    world = state.ex.world_state()
    if 'in_port_prefix_screen' not in state.sp:
        state.sp['in_port_prefix_screen'] = InPortPrefixScreen(instance, world.ship)
    screen = state.sp['in_port_prefix_screen']
    trial = state.ex.copy()
    for move in trial.step(action):
        world = apply_move(instance, world, move)
        verdict = screen.evaluate(world.ship)
        if verdict.status == "reject":
            record_rejection(state, verdict.rule_id, pairs=(pair,))
            return False
    return True


def _expansion_rule(ex, action):
    """Recover the shared expander's reason; a pure prospective cut is D69."""
    try:
        ex.copy().step(action)
    except ValueError as exc:
        text = str(exc)
        if text.startswith('unexpandable: '):
            return text.split(': ', 2)[1]
        return 'expert.moves.top_access'
    return 'port.e3.quay_crossing'


def port_actions_allowed(instance, tables, state, boxes, slots):
    """Factor source work per box and shared physical probes per slot column.

    Used after the engine's exact open-cell/footprint/local matrix. Yard
    retrieval is prepared by the existing expander and validated through the
    public GateOut replay (same source/lane crossing as Load). GateOut is only
    a pure probe; plans still contain the original productive load. A shared
    can_expand then validates ship access and D69 for each equivalence class.
    Forced deck restows, discharges and D64 retain actual per-action replay.
    No crossing geometry or threshold is independently implemented here.
    """
    from stowage.actions.space import Place
    from stowage.conventions import cells_of_slot
    from stowage.model import GateOut
    from stowage.moves import apply_move
    from .in_port import in_port_screen_enabled

    ex = state.ex
    sources = state.sp.setdefault('port_sources', {})
    source_rules = state.sp.setdefault('port_source_rules', {})
    yard_proofs = state.sp.setdefault('port_yard_proofs', {})
    columns = state.sp.setdefault('port_columns', {})
    column_rules = state.sp.setdefault('port_column_rules', {})
    counts = state.sp.setdefault('port_probe_counts', dict(yard_sources=0, ship_columns=0, fallback=0))
    domains = None
    h = instance.port.max_crane_height_tiers
    d64 = in_port_screen_enabled()
    out = np.ones(len(boxes), dtype=bool)
    for k, (b, s) in enumerate(zip(boxes.tolist(), slots.tolist())):
        cid, slot = tables.box_ids[b], tables.slot_ids[s]
        action = Place(cid, slot)
        needs = ex._needs(cells_of_slot(slot))
        forced = any(off and cover not in ex.covers_off and any(
            c in ex.occ for sid in ex.covers[cover].deck_stack_ids for c in ex.stacks[sid].cell_ids)
            for cover, off in needs.items())
        location = ex.where.get(cid, ('truck', 'truck'))[0]
        if forced or location == 'ship':
            counts['fallback'] += 1
            out[k] = port_action_allowed(instance, tables, state, action)
            continue
        if b not in sources:
            if location == 'yard':
                if b not in yard_proofs:
                    counts['yard_sources'] += 1
                    prepared = ex.copy()
                    start = len(prepared.moves)
                    try:
                        _, src = prepared._dig_out(cid, slot)
                        if 'port_world' not in state.sp:
                            state.sp['port_world'] = ex.world_state()
                        # The same indexed transactional replay backs public
                        # apply_move. Avoid materialising its discarded world
                        # after every pure yard-source probe.
                        from stowage.moves._physical import Replay
                        replay = Replay(instance, state.sp['port_world'])
                        relocations = tuple(prepared.moves[start:])
                        for move in relocations:
                            replay.move(move)
                        replay.move(GateOut(call_index=instance.call_index, container_id=cid, src=src))
                        yard_proofs[b] = (prepared, relocations)
                    except ValueError as exc:
                        rule = getattr(getattr(exc, 'violation', None), 'rule_id', 'expert.moves.top_access')
                        source_rules[b] = rule
                        yard_proofs[b] = None
                proof = yard_proofs[b]
                if proof is None:
                    sources[b] = None
                else:
                    yard, relocations = proof
                    prepared = ex.copy()
                    prepared.yard, prepared.ylen, prepared.foot = yard.yard, yard.ylen, yard.foot
                    prepared.pending = yard.pending
                    prepared.where.update({c: loc for c, loc in yard.where.items() if loc[0] == 'yard'})
                    prepared.moves.extend(relocations)
                    sources[b] = prepared
            else:
                sources[b] = ex
        prepared = sources[b]
        if prepared is None:
            record_rejection(state, source_rules.get(b, 'expert.moves.top_access'), pairs=((cid, slot),))
            out[k] = False
            continue
        # D69 only runs for a deck footprint that reaches H. The pending-box
        # domain class belongs in this key: removing different alternatives
        # can change which farther-row obligations remain mandatory.
        at_h = h is not None and bool(tables.slot_deck[s]) and int(tables.slot_k[s].max()) + 1 >= h
        if at_h:
            if domains is None:
                domains = pending_target_domains(tables, state)
            domain_class = domains.get(cid, ())
        else:
            domain_class = None
        key = (cid, int(tables.length[b]), location, s, domain_class)
        if key not in columns:
            counts['ship_columns'] += 1
            allowed = prepared.can_expand(action, pending_load_targets=domains if at_h else None)
            columns[key] = allowed
            if not allowed:
                column_rules[key] = _expansion_rule(prepared, action)
        out[k] = columns[key]
        if not out[k]:
            record_rejection(state, column_rules[key], pairs=((cid, slot),))
        if out[k] and d64:
            out[k] = port_action_allowed(instance, tables, state, action, physical_checked=True)
    return out
