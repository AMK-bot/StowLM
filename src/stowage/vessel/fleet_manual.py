"""Pure production recipes for D27's C1–C3 loading-manual conditions.

Port of the pinned convergence harness2.build/place recipes, without importing
experiment modules, installing spies, calling a solver, or loading a vessel.
APPROXIMATION: synthetic dry ISO20 designer conditions, not an approved manual.
C4 ballast-only conditions stay outside D27's verified envelope and this set.
"""
from __future__ import annotations

from math import floor, isfinite
from random import Random

from stowage.conventions import CONTAINER_HEIGHT_STD_M, VESSEL_ID_ALIASES, canonical_vessel_id
from stowage.params import load_param_file
from stowage.ports.profiles import load_port
from stowage.vessel.hydrostatics import HullGeometry
from stowage.vessel.fleet_manual_witness import CURRENT_WITNESSES

STANDARD_ROUTE = ("THLCH", "THBKK", "THSGZ")
_PORTS = {"THLCH":"laem_chabang_like", "THBKK":"small_feeder", "SGSIN":"singapore_like",
          "USLAX":"us_west_coast", "VNSGN":"cat_lai_like", "HKHKG":"hong_kong_like", "MYPKG":"port_klang_like"}
# Recipes are the existing historical witnesses, including the previously
# unfinished 1700 C2 first DB+HEEL candidate. Passing status never filters design.
_RECIPES = {
    "feeder_500": {
        "C1": ("DB+WING+FPT0.75","73d3328b594b2757897c5ac0f9edf0906d3562ce51899c851db9e421a4355e1b"),
        "C2": ("DB+HEEL+FPT0.38","785f58bf41bda474266def09ba23b9d0119d78453c633ff0a5006c7b99ca6999"),
        "C3": ("DB+HEEL+FPT0.50","f215a77f9838d08c8e365e282e40e7a061ec7b3914801142fb9fe97873b3d928"),
    },
    "feeder_800": {
        "C1": ("DB+HEEL+FPT0.75","8505a20a575e9e07a50cd77449288ec5430baa7985c5b8610bd909b89dc3cce8"),
        "C2": ("DB+WING+FPT0.88","3cc53fd5ca1a69a4eebb9e5ad2b8750cbd9f05bd5a7070242149fadad5dad1ad"),
        "C3": ("DB+HEEL+FPT0.88","68cc097c7611f25bb00436d69474a20b0800a5d8c4ac1b22b7e2bd6ce95b43cb"),
    },
    "feeder_1700": {
        "C1": ("DB+HEEL+FPT0.50","7574213ab3f2f89e5bdc0e64fa6cafb097f27cda734137497c30aab817757246"),
        "C2": ("DB+HEEL",None),
        "C3": ("DB+HEEL+FPT0.50","c4c3f1a808da529d7cfbe2faf97ebe216a0b4b0eb559f9efa8beef40770bafb1"),
    },
}


def manual_port(locode: str):
    """Resolve a declared D34 dock profile; unknown density never means seawater."""
    if locode not in _PORTS:
        raise ValueError(f"standard manual density/profile unavailable for {locode}")
    profile = load_port(_PORTS[locode])
    if profile.locode != locode:
        raise ValueError(f"standard manual density profile LOCODE mismatch for {locode}")
    return profile


def _fill_order(vessel):
    lcb = vessel.hull.lpp_m*.485
    cells = {cell.id:cell for cell in vessel.cells}
    stacks = {stack.id:stack for stack in vessel.stacks}
    entries = []
    for stack in vessel.stacks:
        for index,cid in enumerate(stack.cell_ids):
            cell = cells[cid]
            if cell.accepts_20:
                entries.append(((int(stack.on_deck),index,round(abs(cell.lcg_m-lcb),3),
                                 round(abs(cell.tcg_m),3),cell.tcg_m),stack.id,cid))
    return sorted(entries),stacks


def place_manual_cargo(vessel, masses):
    """Exact heavy-first, hold-first designer placement used by the D27 recipes."""
    entries,stacks = _fill_order(vessel)
    used = dict.fromkeys(stacks,0.)
    height = dict.fromkeys(stacks,0.)
    next_tier = dict.fromkeys(stacks,0)
    taken,placed,start,failed = set(),[],0,None
    for mass in sorted(masses,reverse=True):
        if failed is not None and mass >= failed:
            continue
        while start<len(entries) and entries[start][2] in taken:
            start += 1
        for key,sid,cid in entries[start:]:
            if cid in taken:
                continue
            stack = stacks[sid]
            if (stack.on_deck and mass>=25000.) or key[1]!=next_tier[sid]:
                continue
            limit = stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
            if used[sid]+mass>limit+1e-6 or height[sid]+CONTAINER_HEIGHT_STD_M>stack.max_height_m+1e-9:
                continue
            taken.add(cid);used[sid]+=mass;height[sid]+=CONTAINER_HEIGHT_STD_M;next_tier[sid]+=1
            placed.append((cid,float(mass)))
            break
        else:
            failed = mass
    return placed


def uniform_placement_count(vessel, mass):
    """Exact count for the uniform C2 scan, avoiding 282 repeated placements.

    Every requested box has identical mass and the request covers all cells.
    Each independent stack therefore fills its usable prefix up to the same
    height/weight cap; designer ordering only chooses their enumeration order.
    The winning mass is subsequently placed by the unchanged discrete routine.
    """
    cells={cell.id:cell for cell in vessel.cells}
    total=0
    for stack in vessel.stacks:
        if stack.on_deck and mass>=25000.:
            continue
        prefix=0
        for cid in stack.cell_ids:
            if not cells[cid].accepts_20:
                break
            prefix+=1
        height=floor((stack.max_height_m+1e-9)/CONTAINER_HEIGHT_STD_M)
        limit=stack.max_mass_20_kg if stack.max_mass_20_kg is not None else stack.max_mass_kg
        total+=max(0,min(prefix,height,floor((limit+1e-6)/mass)))
    return total


def manual_ballast_masses(vessel, label):
    """Recover exact binary peak fractions; map historical FPT onto both zones."""
    prefixes={"DB":("DB-",),"HEEL":("HEEL",),"WING-F":("WING-F",),"WING":("WING",)}
    pieces=label.split('+')
    full=[prefix for piece in pieces if piece in prefixes for prefix in prefixes[piece]]
    peaks={piece[:3]:round(float(piece[3:])*32)/32 for piece in pieces
           if piece[:3] in ('FPT','APT')}
    if 'FPT' in peaks:
        peaks['FWD-DEEP']=peaks['FPT']
    return {tank.id:tank.capacity_m3*tank.content_density_kg_m3*
            (1. if any(tank.id.startswith(prefix) for prefix in full) else
             next((fraction for prefix,fraction in peaks.items() if tank.id.startswith(prefix)),0.))
            for tank in vessel.tanks if tank.adjustable}


def standard_manual_conditions(vessel, ship_params):
    """Rebuild all C1–C3 cargo/ballast recipes on the supplied current vessel."""
    if vessel.hull is None:
        raise ValueError("D27 standard manual requires a physical hull")
    name=canonical_vessel_id(ship_params['id'])
    if name not in _RECIPES:
        raise ValueError(f"D27 standard loading-manual recipes unavailable for {name}")
    geometry=HullGeometry.from_params(ship_params)
    density=manual_port(STANDARD_ROUTE[0]).water_density_kg_m3
    target=geometry.submerged(vessel.hull.design_draught_m-.10,
                              vessel.hull.design_draught_m-.10,density_kg_m3=density).displacement_kg
    consumables={tank.id:(0. if tank.adjustable else .97*tank.capacity_m3*tank.content_density_kg_m3)
                 for tank in vessel.tanks}
    base=vessel.hull.lightship_mass_kg+sum(load.mass_kg for load in vessel.fixed_loads)+sum(consumables.values())
    count=sum(cell.accepts_20 for cell in vessel.cells)
    profile=load_param_file('cargo','feeder_export')
    equipment=next(item for item in profile['equipment'] if item.get('iso_size_type','').startswith('22G'))
    distribution=equipment['loaded_weight_kg']
    low,mode,high=(distribution[key]['value'] for key in ('min','mode','max'))
    results=[]
    for condition,(label,digest) in _RECIPES[name].items():
        ballast=manual_ballast_masses(vessel,label)
        budget=target-base-sum(ballast.values())
        uniform=None
        if condition=='C1':
            cargo=place_manual_cargo(vessel,[14000.]*max(0,min(count,int(budget//14000.))))
        elif condition=='C2':
            best_mass,best_total=None,-1.
            for mass in range(2300,30481,100):
                total=mass*uniform_placement_count(vessel,mass)
                if best_total<total<=budget:
                    best_mass,best_total=float(mass),total
            if best_mass is None:
                raise ValueError(f"D27 {name} C2 cargo budget cannot support its declared condition")
            uniform=best_mass
            cargo=place_manual_cargo(vessel,[best_mass]*count)
        else:
            rng=Random(7);masses=[];total=0.
            while len(masses)<count:
                mass=(2200. if rng.random()<equipment['empty_share']['value'] else
                      min(rng.triangular(low,high,mode),30480.))
                if total+mass>budget:
                    break
                masses.append(mass);total+=mass
            cargo=place_manual_cargo(vessel,masses)
        archive_name=next((old for old,new in VESSEL_ID_ALIASES.items() if new==name),name)
        source=(f"experiments/physics-review/convergence/post_d30/search_{archive_name}_{condition}.json sha256:{digest}"
                if digest else "experiments/physics-review/CONVERGENCE.md:1700 C2 first DB+HEEL candidate, no passing witness")
        correction = None
        if name == 'feeder_500' and condition == 'C2':
            # APPROXIMATION: a declared homogeneous 18t designer loading with
            # budget-limited count, not a requirement to fill every physical
            # cell. The old full-placement scan chose 516 x 8.8t and negative
            # departure/arrival GM. 252 x 18t lowers cargo KG while retaining
            # 99.894% of that cargo mass and all original consumables/ballast.
            # D27 permits correcting synthetic loading-manual design recipes;
            # no IS Code, securing, expert-rule or load-line limit is changed.
            uniform = 18000.
            cargo = place_manual_cargo(vessel, [uniform]*max(0,min(count,int(budget//uniform))))
            correction = ('D27 homogeneous 18t budget-limited count, hold-first placement; '
                          '97% consumables and legacy cargo-budget ballast reserve unchanged')
            source += ('; experiments/physics-review/rerun/fix-t2-vessel/restore/'
                       'diagnose_500_fullcheck.json sha256:00bc74dff0bf235cda8b089819ea844122e115fd1e94ecaf0103fa18ae0cbd9a: corrected homogeneous recipe verified at both phases')
        if name == 'feeder_1700' and condition == 'C2':
            # APPROXIMATION: D93 homogeneous synthetic designer loading. The
            # enlarged HEEL reserve made the uniform scan jump to 1,604 light
            # boxes, including 1,032 on deck. Retain the previously qualified
            # 25.8t mass with a budget-limited count and hold-first placement.
            # No checker limit or other loading condition is changed.
            uniform = 25800.
            cargo = place_manual_cargo(vessel, [uniform]*max(0,min(count,int(budget//uniform))))
            correction = ('D93 homogeneous 25.8t budget-limited count, hold-first placement; '
                          'restores the C2 regime after HEEL enlargement')
            source += '; experiments/mvp/solve47/physics/manual-regression/REPORT.md: D93 corrected synthetic recipe'
        current = CURRENT_WITNESSES.get(f'{name}.{condition}')
        if current is not None:
            # Preserve the legacy reserve used to construct cargo above. New
            # ballast is an operating witness, not a new cargo-budget input.
            declared = dict(current['tank_masses_kg'])
            actual = {tank.id:tank for tank in vessel.tanks if tank.adjustable}
            if declared.keys() != actual.keys() or any(
                    not isfinite(mass) or mass < 0 or mass > actual[tid].capacity_m3*actual[tid].content_density_kg_m3
                    for tid,mass in declared.items()):
                raise ValueError(f'D27 {name} {condition} declared witness requires requalification for actual tanks')
            ballast = declared
            source += '; current dual-phase full-checker witness: '+current['source']
        # A declared static witness must not be labelled as a new finisher run.
        operating_label = (current.get('ballast_label') or
            f'finisher_seed{current["seed"]}_{current["time_limit_s"]:g}s') if current else label
        results.append(dict(condition=condition,route=STANDARD_ROUTE,cargo_cells=cargo,
            departure_tank_masses_kg={**consumables,**ballast},ballast_label=operating_label,
            cargo_reserve_ballast_label=label, recipe_correction=correction,
            ballast_witness_provenance=current['provenance'] if current else None,
            recipe_source=source,historical_witness=digest is not None,
            target_draught_m=vessel.hull.design_draught_m-.10,cargo_budget_kg=budget,
            cargo_mass_kg=sum(mass for _,mass in cargo),boxes=len(cargo),uniform_mass_kg=uniform,
            seed=7 if condition=='C3' else None))
    return results
