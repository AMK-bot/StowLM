"""Descriptive geometry only; no equilibrium solve, finisher, or causal test.

Inputs are the audit's already-chosen compacted plans. Coordinates are the
production vessel coordinates: x forward, y port, z above baseline.
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations
from math import ceil, fsum, prod

from stowage.checker.visibility import _all_cargo_geometry, _cargo_catalogs
from stowage.conventions import parse_slot
from stowage.moves import apply_plan


def mean(values):
    values = [v for v in values if v is not None]
    return fsum(values)/len(values) if values else None


def p90(values):
    """Nearest-rank empirical 90th percentile; empty population -> None."""
    values = sorted(values)
    return values[ceil(.9*len(values))-1] if values else None


def _features(records):
    records = list(records)
    mass = fsum(r.container.gross_mass_kg for r in records)
    tiers = [parse_slot(r.placement.slot).tier for r in records]
    deck = [r for r in records if parse_slot(r.placement.slot).on_deck]
    # A box's geometric midpoint is a descriptive mass location, matching the
    # production planned-box geometry; explicit background CGs are honoured.
    coordinates = [(r, r.cg_m or (r.x_m, r.y_m, (r.bottom+r.top)/2)) for r in records]
    cg = [fsum(r.container.gross_mass_kg*xyz[j] for r, xyz in coordinates)/mass
          if mass else None for j in range(3)]
    return dict(boxes=len(records), mass_kg=mass, tier_counts=dict(sorted(Counter(tiers).items())),
                hold_boxes=len(records)-len(deck), deck_boxes=len(deck),
                max_tier=max(tiers, default=None),
                # Tier identifiers jump from hold to deck; geometric mean is
                # included literally, not interpreted as a physical height.
                geometric_mean_tier=prod(t**(1/len(tiers)) for t in tiers) if tiers else None,
                max_deck_tier=max((parse_slot(r.placement.slot).tier for r in deck), default=None),
                lcg_m=cg[0], tcg_m=cg[1], abs_tcg_m=abs(cg[1]) if cg[1] is not None else None,
                vcg_m=cg[2],
                deck_mass_fraction=fsum(r.container.gross_mass_kg for r in deck)/mass if mass else None,
                high_deck_box_fraction=sum(parse_slot(r.placement.slot).tier >= 86 for r in records)/len(records) if records else None,
                high_deck_mass_fraction=fsum(r.container.gross_mass_kg for r in records
                    if parse_slot(r.placement.slot).tier >= 86)/mass if mass else None)


def _profiles(vessel, records):
    cells, stacks = _cargo_catalogs(vessel)
    bridge = vessel.bridge
    if bridge is None or bridge.bow_x_m <= bridge.x_m:
        return dict(available=False, reason='bridge/bow geometry unavailable')
    length = bridge.bow_x_m-bridge.x_m
    # Fixed support catalogs participate; empty deck stacks remain at zero.
    deck = {sid:s for sid,s in stacks.items() if s.on_deck and s.cell_ids}
    xs = {sid:cells[s.cell_ids[0]].lcg_m for sid,s in deck.items()}
    roof = {sid:s.base_z_m for sid,s in deck.items()}
    occupied = set()
    for record in records:
        for cell in record.physical:
            if cell.stack_id in deck:
                roof[cell.stack_id] = max(roof[cell.stack_id], record.top)
                occupied.add(cell.stack_id)
    output = []
    for quarter in range(4):
        lo, hi = bridge.x_m+quarter*length/4, bridge.x_m+(quarter+1)*length/4
        ids = [sid for sid,x in xs.items() if lo <= x and (x < hi or quarter == 3 and x == hi)]
        heights = [roof[sid]-deck[sid].base_z_m for sid in ids]
        output.append(dict(quarter=quarter+1, x_min_m=lo, x_max_m=hi,
                           stacks=len(ids), occupied_stacks=sum(sid in occupied for sid in ids),
                           mean_height_m=mean(heights), p90_height_m=p90(heights),
                           max_height_m=max(heights,default=None),
                           max_roof_z_m=max((roof[sid] for sid in ids),default=None)))
    return dict(available=True, bridge_x_m=bridge.x_m, bow_x_m=bridge.bow_x_m,
                quarters=output, primary=output[0])


def describe(instance, chosenplan):
    """Return plain JSON-compatible metrics, requiring a complete legal replay."""
    replay = apply_plan(instance, chosenplan)
    if not replay.legal:
        raise ValueError('descriptive plan replay is illegal')
    ship = replay.final_state.ship
    if set(instance.load_list)-{p.container_id for p in ship.placements}:
        raise ValueError('descriptive plan is incomplete')
    records = _all_cargo_geometry(instance, ship)
    planned = [r for r in records if not r.background]
    background = [r for r in records if r.background]
    return dict(instance_id=instance.id, planned=_features(planned), background=_features(background),
                profile_all_cargo=_profiles(instance.vessel, records),
                profile_planned_only=_profiles(instance.vessel, planned))


METRICS = {
    'Forward quarter mean stack height (m)':('profile_all_cargo','primary','mean_height_m'),
    'Forward quarter P90 stack height (m)':('profile_all_cargo','primary','p90_height_m'),
    'Forward quarter max roof z (m)':('profile_all_cargo','primary','max_roof_z_m'),
    'Planned deck boxes':('planned','deck_boxes'),
    'Planned max deck tier':('planned','max_deck_tier'),
    'Planned cargo LCG (m)':('planned','lcg_m'),
    'Planned cargo absolute TCG (m)':('planned','abs_tcg_m'),
    'Planned cargo VCG (m)':('planned','vcg_m'),
    'Planned deck mass fraction':('planned','deck_mass_fraction'),
    'Planned tier ≥86 mass fraction':('planned','high_deck_mass_fraction'),
}


def _get(row, path):
    value = row.get('description', row.get('metrics', row))
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def render_description(rows):
    """Render sample-0 rows with arm, instance_id, description and sight_clear.

    Missing descriptions are excluded, never substituted with zero geometry.
    """
    rows = [r for r in rows if r.get('sample',0) == 0
            and isinstance(r.get('description',r.get('metrics')),dict)
            and 'planned' in r.get('description',r.get('metrics',{}))]
    arms = [a for a in ('8.2','null','K1r') if any(r['arm']==a for r in rows)]
    by = {a:{r['instance_id']:r for r in rows if r['arm']==a} for a in arms}
    shared = set.intersection(*(set(by[a]) for a in arms)) if arms else set()
    fmt = lambda n: '—' if n is None else f'{n:.3f}'
    def with_n(values):
        values = [value for value in values if value is not None]
        return f'{fmt(mean(values))} (n={len(values)})'
    lines = ['### Small-map sample-0 plan descriptions', '',
        'Descriptive only. Plans are the same compacted choices used by the audit, before finishing. '
        'Each map has equal weight. Primary arm comparisons use maps with complete legal plans in all three arms; '
        'pairwise differences use each pair’s shared maps. Different own-arm cohorts are not controlled comparisons.', '',
        'Production cargo geometry supplies actual support heights (including high cubes and OOG). '
        'x increases forward. The primary near-bridge region is the first quarter of bridge-front-to-bow distance; '
        'the other three forward quarters are sensitivity profiles. Stack means and nearest-rank P90 include empty '
        'deck support stacks at zero height; a spanning box contributes to both physical support stacks. '
        'Roof z is above baseline; height is above each stack base. All-cargo profiles include fixed background boxes. '
        'Planned-only features count each box once; their mass moments exclude background, tanks, and lightship. '
        'Tier ≥86 (third standard deck tier) is a descriptive threshold, not a legal criterion. '
        'Raw tier identifiers distinguish hold (<80) from deck (≥80); their geometric mean has no physical height interpretation.', '',
        'Every metric cell gives its own available-map count. Empty regions have no stack-height metric; '
        'a plan with zero deck boxes has an unavailable maximum deck tier (None), not tier zero. '
        'Empty physical deck stacks inside an available region still contribute zero height. '
        'Pairwise n counts maps with both feature values available; shared-plan map counts remain separate.', '',
        f'Complete descriptive plans: '+', '.join(f'{a} {len(by[a])}' for a in arms)+f'; shared all-arm maps: {len(shared)}.', '',
        '| Metric; all-arm shared maps | '+' | '.join(arms)+' |',
        '|---|'+ '|'.join('---:' for _ in arms)+'|']
    for title,path in METRICS.items():
        lines.append('| '+title+' | '+' | '.join(with_n(_get(by[a][i],path) for i in shared) for a in arms)+' |')
    lines += ['', '| Pair (A − B) | Shared maps | '+' | '.join(METRICS)+' |',
              '|---|---:|'+ '|'.join('---:' for _ in METRICS)+'|']
    for a,b in combinations(arms,2):
        ids = set(by[a])&set(by[b])
        differences = []
        for path in METRICS.values():
            differences.append(with_n(_get(by[a][i],path)-_get(by[b][i],path) for i in ids
                if _get(by[a][i],path) is not None and _get(by[b][i],path) is not None))
        lines.append(f'| {a} − {b} | {len(ids)} | '+' | '.join(differences)+' |')
    lines += ['', '| Arm/cohort | Plans | Forward-quarter mean stack height (m) | Cargo VCG (m) | Deck mass fraction |',
              '|---|---:|---:|---:|---:|']
    for a in arms:
        for label, cohort in [('own complete',list(by[a].values())),
                              ('gate clear',[r for r in by[a].values() if r.get('sight_clear') is True]),
                              ('gate refused',[r for r in by[a].values() if r.get('sight_clear') is False])]:
            paths = [METRICS[k] for k in ('Forward quarter mean stack height (m)','Planned cargo VCG (m)','Planned deck mass fraction')]
            lines.append(f'| {a}: {label} | {len(cohort)} | '+' | '.join(with_n(_get(r,p) for r in cohort) for p in paths)+' |')
    lines += ['', '| Arm; all-arm shared maps | Quarter 1 mean height | Quarter 2 | Quarter 3 | Quarter 4 |',
              '|---|---:|---:|---:|---:|']
    for a in arms:
        values = [with_n(by[a][i].get('description',by[a][i].get('metrics',{})).get('profile_all_cargo',{}).get('quarters',[{}]*4)[q].get('mean_height_m') for i in shared) for q in range(4)]
        lines.append(f'| {a} | '+' | '.join(values)+' |')
    lines += ['', 'Full per-plan JSON retains tier histograms, planned/background box and mass totals, '
              'planned-only profiles, and per-quarter occupied/available stack counts. No feature establishes why a gate clears.', '']
    return '\n'.join(lines)
