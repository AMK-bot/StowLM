"""Read-only attribution of frozen G1 visibility; no altered acceptance rules.

Exact roof/face clipping mirrors checker.visibility, and public metrics are
asserted. Height excess is vertical ship-coordinate roof lowering needed to
reach the V/22 distance boundary at the current clipped roof vertices. It is a
diagnostic of this fixed attitude, not a recomputed loading certificate.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
from itertools import product
from pathlib import Path
import time

for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[key] = '1'

import numpy as np
from stowage.checker.visibility import (_all_cargo_geometry, _cargo_catalogs,
    _fixed_loads, _loading_condition, _clip, _merge, check_visibility)
from stowage.conventions import parse_slot

HERE = Path(__file__).resolve().parent


def obstacles(instance, ship):
    result = []
    lengths = {20: 6.058, 40: 12.192, 45: 13.716}
    for record in _all_cargo_geometry(instance, ship):
        p, b = record.placement, record.container
        if not record.background and parse_slot(p.slot).tier < 80:
            continue
        extra = b.oog
        fore, aft, port, starboard = ((extra.over_length_fore_m, extra.over_length_aft_m,
            extra.over_width_port_m, extra.over_width_starboard_m) if extra else (0., 0., 0., 0.))
        result.append(dict(source='background' if record.background else 'planned',
            box=b.id, slot=p.slot, bay=parse_slot(p.slot).bay,
            stacks=sorted({cell.stack_id for cell in record.physical}),
            mass_kg=b.gross_mass_kg, tier=parse_slot(p.slot).tier,
            x=record.x_m+(fore-aft)/2, y=record.y_m+(port-starboard)/2,
            bottom=record.bottom, top=record.top,
            length=lengths[b.length_ft]+fore+aft, width=2.438+port+starboard))
    for index, load in enumerate(_fixed_loads(instance.vessel)):
        if load.kind == 'cargo':
            continue
        e = load.silhouette
        result.append(dict(source='noncargo', box=f'fixed-load-{index}', slot=None,
            bay=None, stacks=[], x=(e.x_min_m+e.x_max_m)/2, y=(e.y_min_m+e.y_max_m)/2,
            bottom=e.z_min_m, top=e.z_max_m, length=e.x_max_m-e.x_min_m,
            width=e.y_max_m-e.y_min_m))
    cells, stacks = _cargo_catalogs(instance.vessel)
    for stack in stacks.values():
        if stack.on_deck and stack.hatch_cover_id and stack.cell_ids:
            cell = cells[stack.cell_ids[0]]
            result.append(dict(source='cover', box=stack.hatch_cover_id, slot=None,
                bay=parse_slot(cell.id).bay, stacks=[stack.id], x=cell.lcg_m, y=cell.tcg_m,
                bottom=stack.base_z_m, top=stack.base_z_m, length=6.058, width=2.438))
    return result


def diagnose(instance, ship, *, condition=None):
    c = condition or _loading_condition(instance, ship)
    bridge, hull = instance.vessel.bridge, instance.vessel.hull
    n = c.geometry.waterplane_normal(c.t_aft_m, c.t_fwd_m, c.heel_rad)
    along = np.array([1., 0., 0.])-n*n[0]
    along /= np.linalg.norm(along)
    across = np.cross(n, along)
    eye = np.array([bridge.x_m, 0., bridge.eye_z_m])
    height = float(n@eye-n[2]*c.t_aft_m)
    bow_z = c.t_aft_m+(c.t_fwd_m-c.t_aft_m)*bridge.bow_x_m/hull.lpp_m
    bow = float(along@(np.array([bridge.bow_x_m, 0., bow_z])-eye))
    limit = min(2*hull.loa_m, 500.)
    boundary = bow+limit
    wedge = math.tan(math.radians(10))
    all_sectors, records = [], []
    blind = 0.
    for raw in obstacles(instance, ship):
        x, y, bottom, top, length, width = (raw[k] for k in ('x', 'y', 'bottom', 'top', 'length', 'width'))
        def transform(xx, yy, zz):
            p = np.array([xx, yy, zz])-eye
            return np.array([along@p, across@p, n@p+height])
        roof = [transform(xx, yy, top) for xx, yy in
            ((x-length/2, y-width/2), (x+length/2, y-width/2),
             (x+length/2, y+width/2), (x-length/2, y+width/2))]
        forward = _clip(roof, lambda p: p[0]-1e-9)
        cone = _clip(_clip(forward, lambda p: wedge*p[0]-p[1]), lambda p: wedge*p[0]+p[1])
        distances = [math.inf if p[2] >= height-1e-10 else height*p[0]/(height-p[2])-bow
            for p in cone if p[2] > 0]
        distance = max(distances, default=0.)
        blind = max(blind, distance)
        def beyond(p):
            return height*p[0]+boundary*(p[2]-height)
        derivative = height*along[2]+boundary*n[2]
        cone_excess = max((beyond(p)/derivative for p in cone), default=0.)
        global_excess = max((beyond(p)/derivative for p in forward), default=0.)
        vertices = [transform(xx, yy, zz) for xx, yy, zz in product(
            (x-length/2, x+length/2), (y-width/2, y+width/2), (bottom, top))]
        candidates = []
        for indices in ((0,2,3,1), (4,5,7,6), (0,1,5,4), (2,6,7,3), (0,4,6,2), (1,3,7,5)):
            face = _clip([vertices[i] for i in indices], lambda p: p[0]-1e-9)
            face = _clip(face, lambda p: p[2])
            if face and max(beyond(p) for p in face) > 1e-8:
                candidates.extend(_clip(face, beyond))
        angles = sorted(math.atan2(p[1], p[0]) for p in candidates)
        sectors = []
        if angles:
            wrapped = angles+[angles[0]+2*math.pi]
            gi = max(range(len(angles)), key=lambda i: wrapped[i+1]-wrapped[i])
            gap = wrapped[gi+1]-wrapped[gi]
            lo, hi = wrapped[gi+1], wrapped[gi]+2*math.pi
            if gap < math.pi-1e-10:
                lo, hi = -math.pi, math.pi
            for turn in (-2,-1,0,1):
                left, right = max(-math.pi/2, lo+turn*2*math.pi), min(math.pi/2, hi+turn*2*math.pi)
                if left < right:
                    sectors.append((left, right))
        all_sectors.extend(sectors)
        records.append(dict(**raw, blind_distance_m=distance, distance_clearance_m=limit-distance,
            cone_roof_height_excess_m=max(0., cone_excess),
            all_bearings_roof_height_excess_m=max(0., global_excess),
            sectors_deg=[[math.degrees(lo), math.degrees(hi)] for lo, hi in sectors]))
    merged = _merge(all_sectors)
    maximum = max((hi-lo for lo,hi in merged), default=0.)
    total = sum(hi-lo for lo,hi in merged)
    clear = min((b[0]-a[1] for a,b in zip(merged,merged[1:])), default=math.pi)
    metrics = dict(blind_distance_m=blind, limit_m=limit,
        draught_aft_m=c.t_aft_m, draught_fwd_m=c.t_fwd_m, trim_m=c.t_aft_m-c.t_fwd_m,
        list_rad=c.heel_rad, max_blind_sector_rad=maximum, total_blind_sector_rad=total,
        min_clear_sector_rad=clear)
    failures = []
    if blind > limit+1e-8:
        failures.append(dict(kind='distance', measured=blind, limit=limit,
            boxes=[r['box'] for r in records if r['blind_distance_m'] > limit+1e-8]))
    for lo,hi in merged:
        sector_limit = math.radians(5 if lo < math.radians(10) and hi > -math.radians(10) else 10)
        if hi-lo > sector_limit+1e-10:
            failures.append(dict(kind='individual_sector', measured_deg=math.degrees(hi-lo),
                limit_deg=math.degrees(sector_limit), sector_deg=[math.degrees(lo), math.degrees(hi)],
                boxes=[r['box'] for r in records if any(max(lo, math.radians(a)) < min(hi, math.radians(b))
                    for a,b in r['sectors_deg'])]))
    if total > math.radians(20)+1e-10:
        failures.append(dict(kind='total_sectors', measured_deg=math.degrees(total), limit_deg=20,
            boxes=[r['box'] for r in records if r['sectors_deg']]))
    if clear < math.radians(5)-1e-10:
        failures.append(dict(kind='clear_gap', measured_deg=math.degrees(clear), limit_deg=5,
            boxes=[r['box'] for r in records if r['sectors_deg']]))
    return dict(metrics=metrics, failures=failures,
        merged_sectors_deg=[[math.degrees(a), math.degrees(b)] for a,b in merged],
        obstacles=records, equilibrium_gm_m=c.gm_m), c


def verified(instance, ship):
    data, condition = diagnose(instance, ship)
    public = check_visibility(instance, ship, call_index=instance.call_index)
    assert public.ran, public.error
    for key, value in data['metrics'].items():
        assert math.isclose(value, public.metrics[key], rel_tol=1e-10, abs_tol=1e-9), (key,value,public.metrics[key])
    assert len(data['failures']) == len(public.violations)
    data['public_report'] = public.model_dump(mode='json')
    return data, condition


def main():
    from small_stow_common import cases, load_case, background_only, source_identity
    start = time.monotonic()
    identity_start = source_identity()
    archived = {row['case']: row['departure']['parts']['visibility']
        for row in json.loads((HERE.parent/'ballast/review_fix/diagnostics.json').read_text())}
    rows = []
    for case in cases():
        t = time.monotonic()
        instance, plan, ship = load_case(case)
        before = instance.model_dump_json(), plan.model_dump_json(), ship.model_dump_json()
        full, condition = verified(instance, ship)
        for key, value in full['metrics'].items():
            assert math.isclose(value, archived[case]['metrics'][key], rel_tol=1e-10, abs_tol=1e-9), (case,key)
        full['matches_archived_initial_report'] = True
        empty = background_only(ship)
        background, _ = verified(instance, empty)
        fixed, _ = diagnose(instance, empty, condition=condition)
        # Diagnostic causal intervention: remove D42 cargo aggregates and their
        # box geometry together, never double-count or reclassify their mass.
        # This is not a valid revised booking or an approved sailing condition.
        planned_vessel = instance.vessel.model_copy(deep=True, update={'fixed_loads': [
            load for load in _fixed_loads(instance.vessel) if load.kind != 'cargo']})
        planned_instance = instance.model_copy(deep=True, update={'vessel': planned_vessel})
        planned_only, _ = verified(planned_instance, ship)
        assert before == (instance.model_dump_json(), plan.model_dump_json(), ship.model_dump_json())
        row = dict(case=case, full=full, background_only=background,
            background_at_full_attitude=fixed, planned_only=planned_only,
            wall_s=time.monotonic()-t)
        rows.append(row)
        print(json.dumps(dict(case=case, wall_s=row['wall_s'], full_failures=full['failures'],
            background_failures=background['failures'])), flush=True)
        result = dict(rows=rows, source_identity_start=identity_start,
            source_identity_end=source_identity(), wall_s=time.monotonic()-start,
            diagnostic_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        # Concurrent executives may edit unrelated checker modules. Pin every
        # numerical/geometry dependency used here, while preserving the complete
        # source snapshots so unrelated drift remains visible to the reader.
        ignored = {'src/stowage/checker/'+name for name in (
            'rules.py', 'strength.py', 'stability.py', 'stability_geometry.py',
            'stability_hydro.py', 'stability_kernel.py', 'stability_reference.py',
            'stability_reporting.py', 'stability_weather.py')}
        before_files = identity_start['files']
        after_files = result['source_identity_end']['files']
        result['source_drift'] = {key: [value, after_files.get(key)]
            for key,value in before_files.items() if after_files.get(key) != value}
        assert not (result['source_drift'].keys()-ignored), result['source_drift']
        (HERE/'small-stow-visibility.json').write_text(json.dumps(result, indent=2, allow_nan=True)+'\n')


if __name__ == '__main__':
    main()
