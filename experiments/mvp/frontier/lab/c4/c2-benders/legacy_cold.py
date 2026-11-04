"""Original C1 cold plain/reference/physics sequence, without stored starts.

Unique imports preserve C1's own Model and one_stow, independently of the C2
RMP changes. Every motion reference and LCG target comes from this invocation's
plain stow; tank recipes and archived assignments are never read.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import os
from pathlib import Path
import sys
from time import monotonic

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parents[3]


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _single_thread(module):
    if getattr(module, '_legacy_single_thread', False):
        return
    original = module.linprog
    def solve(*args, **kwargs):
        kwargs['options'] = {**kwargs.get('options', {}), 'threads': 1}
        return original(*args, **kwargs)
    module.linprog = solve
    module._legacy_single_thread = True


def _runtime():
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[key] = '1'
    sys.path[:0] = [str(ROOT/'src'), str(LAB.parent), str(LAB/'c1/fx-scout'),
                   str(LAB/'c1/leader')]
    _load('_c4_legacy_e9', LAB/'c1/leader/e9fix.py').install()
    pages = _load('_c4_legacy_original_pages', LAB/'c1/fx-scout/pages.py')
    lash = _load('_c4_legacy_original_lash', LAB/'c1/fx-scout/lash_oracle.py')
    # The old method uses ordinary module names. Bind those imports to its own
    # source while loading, then restore the embedding process's names.
    names = {'pages': pages, 'lash_oracle': lash}
    old = {name: sys.modules.get(name) for name in names}
    try:
        sys.modules.update(names)
        method = _load('_c4_legacy_original_method', LAB/'c1/fx-scout/method.py')
    finally:
        for name, module in old.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    _single_thread(pages)
    import alns
    return pages, method, lash, alns


def _ship_metrics(instance, scored):
    from stowage.conventions import cells_of_slot
    from stowage.moves import apply_plan
    replay = apply_plan(instance, scored.plan)
    if not replay.legal:
        raise ValueError('original C1 candidate failed ordinary replay')
    ship = replay.final_state.ship
    if {p.container_id for p in ship.placements} != set(instance.load_list):
        raise ValueError('original C1 candidate did not load the complete list')
    boxes = {b.id: b for b in instance.containers}
    cells = {c.id: c for c in instance.vessel.cells}
    occupied = {c: boxes[p.container_id] for p in ship.placements for c in cells_of_slot(p.slot)}
    floor = {}
    for stack in instance.vessel.stacks:
        z = stack.base_z_m
        for c in stack.cell_ids:
            floor[c] = z
            if c in occupied:
                z += occupied[c].height_m
    mass = vertical = longitudinal = master_longitudinal = 0.
    for p in ship.placements:
        b = boxes[p.container_id]; support = cells_of_slot(p.slot)
        mass += b.gross_mass_kg
        vertical += b.gross_mass_kg*(math.fsum(floor[c] for c in support)/len(support)+b.height_m/2.)
        longitudinal += b.gross_mass_kg*math.fsum(cells[c].lcg_m for c in support)/len(support)
        # C1's unmodified lcg_moment prices the first support cell, rather
        # than a long box's physical centre. Search targets must match it.
        master_longitudinal += b.gross_mass_kg*cells[support[0]].lcg_m
    return ship, dict(cargo_mass_kg=mass, cargo_kg_m=vertical/mass,
                     cargo_lcg_m=longitudinal/mass, loaded=len(ship.placements),
                     c1_master_cargo_lcg_m=master_longitudinal/mass,
                     cargo_lcg_convention='physical mean of placement support cells',
                     c1_master_lcg_convention='first placement support cell, matching original C1 moment row',
                     required=len(instance.load_list), complete_replay_legal=True,
                     cargo_kg_convention='ordinary native cargo half-height; no OOG overheight',
                     lifts=scored.objective, parts=replay.count.model_dump(mode='json'))


def _warm_columns(model, previous, *, deadline, limit=4000):
    """Import suggestions through the destination's original geometry/rule gates."""
    where = {(page.kind, tuple(page.stacks)): k for k, page in enumerate(model.pages)}
    useful = list(getattr(previous, '_warm_indices', ()))
    useful += list(range(max(0, len(previous.columns)-limit), len(previous.columns)))
    added, examined, seen = 0, 0, set()
    for index in useful:
        if monotonic() >= deadline or examined >= limit:
            break
        if index in seen or not 0 <= index < len(previous.columns):
            continue
        seen.add(index); examined += 1
        old = previous.columns[index]
        source = previous.pages[old.page]
        page_index = where.get((source.kind, tuple(source.stacks)))
        if page_index is None:
            continue
        page = model.pages[page_index]
        if (len(old.boxes) <= len(page.slots) and model.legal(page, old.boxes)
                and model.lash_ok(page, old.boxes)):
            added += int(bool(model.add(page_index, old.boxes)))
    return dict(examined=examined, added=added, limit=limit,
                gates='destination page capacity, original legal and lash_ok')


def legacy_candidates(instance, seed=0, budget_s=235., context=None):
    """Return (records, metadata), with a Scored under each record['scored']."""
    started = monotonic(); deadline = started+max(0., float(budget_s))
    context = context if context is not None else {}
    meta = dict(entry='legacy_cold.py:legacy_candidates', cold_start=True,
                stored_data_consulted=False, seed=seed, budget_s=budget_s,
                complete_stows=0, full_checker_passes=None, variants=[], numerical_threads=1,
                target_policy='fresh C1 first-support-cell plain baseline; zero offset')
    context.setdefault('method_metadata', {})['legacy_cold'] = meta
    if budget_s <= 0 or instance.onboard.placements:
        meta.update(status='budget_or_nonempty_arrival', wall_s=monotonic()-started)
        return [], meta
    pages, method, lash, alns = _runtime()
    problem = alns.Problem(instance)
    context['problem'] = problem
    records = []

    def remember(scored, row, kind):
        row.update(kind=kind, full_checker_passed=None, lp_is_physical_certificate=False)
        row.pop('lp_proved', None)
        meta['variants'].append(row)
        if scored is None:
            return None, None
        try:
            ship, metrics = _ship_metrics(instance, scored)
        except ValueError as error:
            row.update(status='replay_or_coverage_rejected', error=str(error))
            return None, None
        row.update(metrics, candidate_id=f'legacy-{len(records):03d}')
        records.append(dict(scored=scored, candidate_id=row['candidate_id'], metadata=dict(row)))
        return ship, metrics

    plain_model = pages.Model(problem)
    first_stop = min(deadline, started+max(15., .30*budget_s))
    first, row = method.one_stow(problem, plain_model, seed=seed, deadline=first_stop)
    ship, initial = remember(first, row, 'legacy_plain')
    if ship is not None and monotonic() < deadline:
        # Use the same coordinate as C1's moment row. The zero-offset fallback
        # is a search policy; physical mean LCG remains separately reported.
        target = initial['c1_master_cargo_lcg_m']
        oracle = lash.Fast(instance, ship)
        reserve_profile = len(instance.load_list) <= 150
        previous = None
        profiles = ((1., 5), (.97, 6), (.985, 7)) if reserve_profile else ((1., 5),)
        retry_midpoint = False
        for cap, offset in profiles:
            if cap == .985 and not retry_midpoint:
                continue
            if monotonic() >= deadline-1.:
                break
            settings = dict(deck_tiers=(4, 5, 5), lash_limit=cap, lcg_target=target)
            model = pages.Model(problem, oracle=oracle, **settings)
            stop = min(deadline, started+.70*budget_s) if cap == 1. and reserve_profile else deadline
            warmed = dict(examined=0, added=0)
            if previous is not None:
                warmed = _warm_columns(model, previous, deadline=min(stop, monotonic()+2.))
            if monotonic() >= stop:
                meta['variants'].append(dict(kind='legacy_native_physics', settings=settings,
                    construction_seed=seed+offset, status='stage_budget_exhausted',
                    full_checker_passed=None, warmed_columns=warmed))
                previous = model
                continue
            scored, row = method.one_stow(problem, model, seed=seed+offset, deadline=stop)
            row.update(settings=settings, construction_seed=seed+offset,
                       securing_search_cap=cap, warmed_columns=warmed,
                       configured_stop_s=stop-started,
                       reference_source='this invocation original-C1 plain at native instance tanks',
                       reference_cargo_lcg_m=initial['cargo_lcg_m'],
                       reference_c1_master_cargo_lcg_m=initial['c1_master_cargo_lcg_m'],
                       lcg_target_offset_m=0., lcg_target_policy=meta['target_policy'])
            accepted_ship, _ = remember(scored, row, 'legacy_native_physics')
            if cap == .97:
                # One generic search bracket, never a physical infeasibility
                # inference. A complete 1.0 candidate remains retained.
                retry_midpoint = accepted_ship is None and deadline-monotonic() >= 20.
                meta['midpoint_retry'] = dict(enabled=retry_midpoint,
                    trigger='0.97 incomplete and at least 20 seconds remain',
                    cap=.985, construction_seed=seed+7,
                    physical_infeasibility_claim=False)
            previous = model
    meta.update(status='complete' if records else 'no_complete_stow',
                complete_stows=len(records), wall_s=monotonic()-started)
    return records, meta
