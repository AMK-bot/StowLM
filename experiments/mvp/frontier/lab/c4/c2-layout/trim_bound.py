"""Necessary loaded-state trim/list moment envelopes, never sampled fits.

Exact rational polygon clipping bounds the direct hull.  Active native tensor
cubic tables require a separate Bernstein enclosure: their weights can be
negative.  No numerical job is performed on import.
"""
from __future__ import annotations

from bisect import bisect_left
from fractions import Fraction as F
from itertools import product
from math import comb
import hashlib
import importlib.util
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('c4_trim_physics_helpers', HERE/'physics_bound.py')
physics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(physics)
helpers = physics.envelopes


def gamma(operations):
    """Standard absolute-sum IEEE binary64 error factor n*u/(1-n*u)."""
    amount = F(int(operations),2**53)
    if amount >= 1:
        raise ValueError('Arithmetic operation budget is not admissible')
    return amount/(1-amount)


def multiply(a, b):
    values = [x*y for x in a for y in b]
    return min(values), max(values)


def divide(a, b):
    if b[0] <= 0:
        raise ValueError('Positive denominator interval required')
    return multiply(a, (1/b[1], 1/b[0]))


def centroid_range(xs, volume_lower, volume_upper, total_lower, total_upper):
    """Exact extrema of sum(x*v)/sum(v), bounded component/total volumes.

    For fixed volume, fill the lowest coordinates first to minimize moment.
    Between cumulative-capacity breakpoints the ratio is linear-fractional,
    hence monotone or constant; checking breakpoints and endpoints is exact.
    """
    xs, lo, hi = [list(map(F, values)) for values in (xs, volume_lower, volume_upper)]
    if len(xs) != len(lo) or len(xs) != len(hi) or any(a < 0 or b < a for a,b in zip(lo,hi)):
        raise ValueError('Invalid nonnegative component volume intervals')
    total_lower, total_upper = max(F(total_lower), sum(lo)), min(F(total_upper), sum(hi))
    if total_upper < total_lower or total_upper <= 0:
        raise ValueError('Empty positive displacement envelope')
    total_lower = max(total_lower, F(1, 10**12))

    def extreme(reverse):
        order = sorted(range(len(xs)), key=lambda i:xs[i], reverse=reverse)
        baseline = sum(lo)
        base_moment = sum(x*v for x,v in zip(xs,lo))
        knots = {total_lower, total_upper}
        running = baseline
        for i in order:
            running += hi[i]-lo[i]
            if total_lower <= running <= total_upper:
                knots.add(running)
        candidates = []
        for total in knots:
            remaining, moment = total-baseline, base_moment
            for i in order:
                amount = min(remaining, hi[i]-lo[i])
                moment += xs[i]*amount
                remaining -= amount
            assert remaining == 0
            candidates.append(moment/total)
        return (max if reverse else min)(candidates)

    return extreme(False), extreme(True)


def basis_bernstein(nodes, lower, upper):
    """Exact cubic Lagrange-to-Bernstein matrix on a closed subinterval.

    Result[a][j] is Bernstein coefficient a of nodal basis j.  Signed nodal
    weights are retained. Bernstein convex-hull enclosure is used only after
    transforming the complete polynomial, not its signed interpolation nodes.
    """
    nodes, lower, upper = list(map(F,nodes)), F(lower), F(upper)
    if len(nodes) != 4 or len(set(nodes)) != 4 or lower > upper:
        raise ValueError('Four distinct nodes and nonempty interval required')
    matrix = [[F() for _ in range(4)] for _ in range(4)]
    for j in range(4):
        power = [F(1)]
        for k in range(4):
            if j == k:
                continue
            a, b = (lower-nodes[k])/(nodes[j]-nodes[k]), (upper-lower)/(nodes[j]-nodes[k])
            nxt = [F()]*(len(power)+1)
            for p,value in enumerate(power):
                nxt[p] += a*value
                nxt[p+1] += b*value
            power = nxt
        for a in range(4):
            matrix[a][j] = sum(power[p]*F(comb(a,p),comb(3,p)) for p in range(a+1))
    return matrix


def tensor_bernstein(values, matrices):
    """Transform a 4x4x4 scalar nodal block using three exact matrices."""
    a = [[[F(values[i][j][k]) for k in range(4)] for j in range(4)] for i in range(4)]
    for axis, matrix in enumerate(matrices):
        b = [[[F() for _ in range(4)] for _ in range(4)] for _ in range(4)]
        for out in product(range(4), repeat=3):
            for j in range(4):
                src = list(out)
                src[axis] = j
                b[out[0]][out[1]][out[2]] += matrix[out[axis]][j]*a[src[0]][src[1]][src[2]]
        a = b
    return [a[i][j][k] for i,j,k in product(range(4), repeat=3)]


def _segments(axis, interval):
    axis = list(map(F,axis))
    lower, upper = max(F(interval[0]),axis[0]), min(F(interval[1]),axis[-1])
    if lower > upper:
        return []
    knots = sorted({lower, upper, *(v for v in axis if lower < v < upper)})
    spans = list(zip(knots,knots[1:])) if lower < upper else [(lower,upper)]
    result = []
    for lo,hi in spans:
        start = min(max(bisect_left(axis,(lo+hi)/2)-2,0),len(axis)-4)
        result.append((start, basis_bernstein(axis[start:start+4],lo,hi)))
    return result


def direct_enclosure(geometry, *, draft_upper, forward_draft_lower,
                     trim_lower, trim_upper, list_upper, volume_lower, volume_upper,
                     mean_draft_lower=None):
    """Outward polygon section-volume enclosure over every legal listed pose."""
    L, D = F(float(geometry.lpp_m)), F(float(geometry.depth_m))
    a, b, t = F(trim_lower), F(trim_upper), F(list_upper)
    if not 0 <= t < F(1,2):
        raise ValueError('List bound must be below 0.5 radians')
    if max(abs(a),abs(b))/L > F(1,4):
        raise ValueError('Small legal trim slope not established')
    # sin(t)<=t, cos(t)>=1-t^2/2, hence tan(t)<=this rational ratio.
    tangent = t/(1-t*t/2)
    draft_upper, forward_draft_lower = F(draft_upper), F(forward_draft_lower)
    xs, lower, upper = [], [], []
    transverse_moment_upper = F()
    arithmetic_total = F()
    coordinate_scale = F(1)
    zlo, zhi = None, None
    for x,w,polygons in zip(geometry.x_m,geometry.integration_weights_m,geometry.sections):
        x, w = F(float(x)), F(float(w))
        arm = 1-x/L
        base_lo = forward_draft_lower+min(arm*a,arm*b)
        if mean_draft_lower is not None:
            mean_arm = F(1,2)-x/L
            base_lo = max(base_lo,F(mean_draft_lower)+min(mean_arm*a,mean_arm*b))
        # Inside AP/FP the mean of two individually capped end draughts is
        # capped too. Outside their interval use the full signed interpolation.
        ta_lo, tf_lo = forward_draft_lower+a, forward_draft_lower
        u,v = 1-x/L,x/L
        base_hi = max(u*ta_lo,u*draft_upper)+max(v*tf_lo,v*draft_upper)
        ymin = min(F(float(y)) for p in polygons for y,z in p)
        ymax = max(F(float(y)) for p in polygons for y,z in p)
        Y = max(abs(ymin),abs(ymax))
        water_lo, water_hi = base_lo-tangent*Y, base_hi+tangent*Y
        vlo = sum((helpers._clipped_area(p,water_lo) for p in polygons),F())*w
        vhi = sum((helpers._clipped_area(p,water_hi) for p in polygons),F())*w
        # Protect translation to the native ordinary floating integration.
        scale = max(F(1),abs(x),abs(water_lo),abs(water_hi),Y,
                    *(abs(F(float(z))) for p in polygons for y,z in p))
        edges = sum(len(p) for p in polygons)
        if edges > 100000 or scale > 10**6:
            raise ValueError('Polygon scale/count outside arithmetic guard scope')
        arithmetic = max(F(1,10**8)*max(F(1),w)*edges*scale*scale,
                         gamma(128*edges+64)*256*edges*max(F(1),w)*scale*scale)
        arithmetic_total += arithmetic
        coordinate_scale = max(coordinate_scale,scale)
        xs.append(x); lower.append(max(F(),vlo-arithmetic)); upper.append(vhi+arithmetic)
        pzlo = min(F(float(z)) for p in polygons for y,z in p)
        pzhi = min(water_hi,max(F(float(z)) for p in polygons for y,z in p))
        zlo = pzlo if zlo is None else min(zlo,pzlo)
        zhi = pzhi if zhi is None else max(zhi,pzhi)
        # Bilateral-section shell bound, proved in trim_bound_notes.md.
        transverse_moment_upper += w*F(2,3)*tangent*len(polygons)*Y**3
    xb = centroid_range(xs,lower,upper,
                        max(F(1,10**12),F(volume_lower)-arithmetic_total),
                        F(volume_upper)+arithmetic_total)
    # Very small negative floating section areas may be clipped to zero in
    # the nonnegative volume relaxation. Credit their entire bounded moment.
    centroid_guard = 4*coordinate_scale*arithmetic_total/F(volume_lower)+F(1,10**5)
    from stowage.checker.stability_geometry import _bilateral_sections
    if _bilateral_sections(geometry.sections):
        y = transverse_moment_upper/F(volume_lower)+F(1,10**5)
    else:
        y = max(abs(F(float(y))) for ps in geometry.sections for p in ps for y,z in p)
    y += centroid_guard
    return dict(x=(xb[0]-centroid_guard,xb[1]+centroid_guard),y=(-y,y),
                z=(zlo-centroid_guard,zhi+centroid_guard),provider='direct polygon enclosure',
                arithmetic_volume_guard_m3=helpers._up(arithmetic_total),
                arithmetic_centroid_guard_m=helpers._up(centroid_guard))


class EnvelopeWorkLimit(RuntimeError):
    pass


def table_enclosure(table, *, immersion_interval, trim_interval, list_upper,
                    volume_lower, volume_upper, max_blocks=256):
    """Exact tensor-cubic polynomial bounds on all intersecting table cells."""
    for axis in table.grid.values():
        coordinates = list(map(F,axis))
        if (max(map(abs,coordinates)) > 10**6 or
                min(b-a for a,b in zip(coordinates,coordinates[1:])) < F(1,10**9)):
            raise ValueError('Table scale/spacing outside arithmetic guard scope')
    segments = [_segments(table.grid[name], interval) for name,interval in zip(
        ('immersion_m','trim_m','heel_rad'),
        (immersion_interval,trim_interval,(0,list_upper)))]
    blocks_required = math.prod(map(len,segments))
    if blocks_required > max_blocks:
        raise EnvelopeWorkLimit(f'{blocks_required} table boxes exceed explicit work limit{max_blocks}')
    ranges = []
    blocks = 0
    xref = F(float(table.lpp_m))/2
    zref = F(float(table.depth_m))/2
    for qa,ta,pa in product(*segments):
        starts, matrices = zip(qa,ta,pa)
        coefficients = []
        field_guards = []
        weight_bounds = [[max(abs(matrix[a][j]) for a in range(4))+F(1,10**12)
                          for j in range(4)] for matrix in matrices]
        for field in range(4):
            values = [[[F(float(table.values[starts[0]+i,starts[1]+j,starts[2]+k,field]))
                        for k in range(4)] for j in range(4)] for i in range(4)]
            if max(abs(values[i][j][k]) for i,j,k in product(range(4),repeat=3)) > 10**100:
                raise ValueError('Table field magnitude outside arithmetic guard scope')
            coefficients.append(tensor_bernstein(values,matrices))
            absolute_sum = sum(abs(values[i][j][k])*weight_bounds[0][i]
                               *weight_bounds[1][j]*weight_bounds[2][k]
                               for i,j,k in product(range(4),repeat=3))
            field_guards.append(F(1,10**10)*max(F(1),absolute_sum))
        vb,mxb,myb,mzb = coefficients
        guard = field_guards[0]
        vlo, vhi = max(F(volume_lower),min(vb)-guard), min(F(volume_upper),max(vb)+guard)
        if vlo > vhi or vhi <= 0:
            continue
        moments = ([v-xref*u for v,u in zip(mxb,vb)],myb,
                   [v-zref*u for v,u in zip(mzb,vb)])
        one = []
        for values,reference,field_guard in zip(moments,(xref,F(),zref),field_guards[1:]):
            g = field_guard+abs(reference)*field_guards[0]
            lo,hi = divide((min(values)-g,max(values)+g),(vlo,vhi))
            one.append((reference+lo-F(1,10**5),reference+hi+F(1,10**5)))
        # Negative heel reflects My, while V/Mx/Mz remain unchanged.
        yabs = max(map(abs,one[1]))
        one[1] = (-yabs,yabs)
        ranges.append(one)
        blocks += 1
    if not ranges:
        return None
    return dict(x=(min(r[0][0] for r in ranges),max(r[0][1] for r in ranges)),
                y=(min(r[1][0] for r in ranges),max(r[1][1] for r in ranges)),
                z=(min(r[2][0] for r in ranges),max(r[2][1] for r in ranges)),
                provider='exact tensor-cubic Bernstein enclosure',blocks=blocks)


def necessary_envelope(instance, *, max_table_blocks=256):
    """Necessary CGX/CGY range for the native departure loading criteria.

    Active table bytes and source hashes are returned as certificate provenance.
    Recompute this envelope if native dispatch/source/cache inputs change.
    Empty closed arrival/no future loads are the accompanying bound's scope.
    """
    from stowage.checker import (stability, stability_tables, stability_geometry,
                                stability_equilibrium, stability_loading)
    from stowage.vessel import hydrostatics
    from stowage.checker.visibility import _fixed_loads
    from stowage.frozen import active_stubs
    if active_stubs():
        raise ValueError('Frozen-interface overrides are outside this proof')
    geometry,params = stability._context(instance.vessel)
    p = helpers.physical_bounds(instance)
    h = instance.vessel.hull
    guard = F(1,10**7)
    dlo = F(h.min_draught_fwd_m)-guard
    dhi = F(p['draft_stability_upper_m'])+guard
    tlo,thi = F(p['trim_lower_m'])-guard,F(p['trim_upper_m'])+guard
    phi = F(h.max_list_rad)+guard
    if not 0 <= phi < F(1,2):
        raise ValueError('Small legal-list envelope not established')
    rho = F(instance.port.water_density_kg_m3)
    constant = F(p['fixed_mass_kg'])+F(p['cargo_mass_kg'])
    mass_lo = max(constant,F(p['mass_lower_kg']))
    mass_hi = min(constant+sum(F(t['capacity_kg']) for t in p['tanks']),F(p['mass_upper_kg']))
    # Both native equilibrium solvers accept normalized volume residual2e-6.
    vlo, vhi = mass_lo/rho*(1-F(3,10**6)),mass_hi/rho*(1+F(3,10**6))
    direct = direct_enclosure(geometry,draft_upper=dhi,forward_draft_lower=dlo,
        trim_lower=tlo,trim_upper=thi,list_upper=phi,volume_lower=vlo,volume_upper=vhi,
        mean_draft_lower=F(p['draft_stability_lower_m'])-guard)
    providers = [direct]
    table = stability_tables.cached_geometry(geometry,params,float(rho))
    table_path = table_sha = None
    if table is not None:
        mean_lo = max(F(p['draft_stability_lower_m'])-guard,dlo+tlo/2)
        qlo = min(mean_lo,mean_lo*(1-phi*phi/2))
        qhi = dhi
        try:
            enclosed = table_enclosure(table,immersion_interval=(qlo,qhi),
                trim_interval=(tlo,thi),list_upper=phi,volume_lower=vlo,volume_upper=vhi,
                max_blocks=max_table_blocks)
        except EnvelopeWorkLimit as exc:
            return dict(instance_id=instance.id,status='unknown',certificate_eligible=False,
                        reason=str(exc),scope='no trim/list cut admitted after work-limit refusal')
        if enclosed is not None:
            providers.append(enclosed)
        table_path = str(table.cache_path)
        table_sha = hashlib.sha256(table.cache_path.read_bytes()).hexdigest()
    cb = {axis:(min(v[axis][0] for v in providers),max(v[axis][1] for v in providers))
          for axis in ('x','y','z')}
    fixed = _fixed_loads(instance.vessel)
    # Liquid motion remains inside the declared tank solid. Method2's virtual
    # KG is separately enclosed by weather applicability.
    zpoints = [F(h.lightship_vcg_m),*(F(q.z_m) for q in fixed if q.mass_kg)]
    zpoints.extend(F(s.base_z_m) for s in instance.vessel.stacks)
    zpoints.extend(F(s.base_z_m)+F(s.max_height_m) for s in instance.vessel.stacks)
    zpoints.extend(F(t['z_lower_m']) for t in p['tanks'])
    zpoints.extend(F(t['z_upper_m']) for t in p['tanks'])
    cgz = min(zpoints),max(*zpoints,F(p['physical_kg_upper_m'])+guard)
    L,B = F(h.lpp_m),F(h.beam_m)
    delta = tlo/L,thi/L
    dz = cgz[0]-cb['z'][1],cgz[1]-cb['z'][0]
    offset = multiply(delta,dz)
    dmax = max(map(abs,delta))
    longitudinal_error = F(3,10**6)*L*helpers._sqrt_upper(1+dmax*dmax)+F(1,10**5)
    cgx = cb['x'][0]+offset[0]-longitudinal_error,cb['x'][1]+offset[1]+longitudinal_error
    tangent = phi/(1-phi*phi/2)
    transverse_error = F(3,10**6)*B/(1-phi*phi/2)+dmax*tangent*longitudinal_error+F(1,10**5)
    yoffset = tangent*max(map(abs,dz))
    cgy = cb['y'][0]-yoffset-transverse_error,cb['y'][1]+yoffset+transverse_error
    import inspect
    source_paths = [Path(module.__file__) for module in (stability,stability_tables,
        stability_geometry,stability_equilibrium,stability_loading,hydrostatics)]
    source_paths.append(Path(inspect.getfile(type(geometry))))
    return dict(instance_id=instance.id,
        status='uncertified_source_algebra',certificate_eligible=False,
        reason='direct floating clipping perturbation proof pending independent review',
        cg_x_lower_m=helpers._down(cgx[0]),cg_x_upper_m=helpers._up(cgx[1]),
        cg_y_lower_m=helpers._down(cgy[0]),cg_y_upper_m=helpers._up(cgy[1]),
        cg_z_enclosure_m=[helpers._down(cgz[0]),helpers._up(cgz[1])],
        buoyancy_centroid_enclosure_m={k:[helpers._down(v[0]),helpers._up(v[1])] for k,v in cb.items()},
        providers=[v['provider'] for v in providers],table_blocks=sum(v.get('blocks',0) for v in providers),
        table_path=table_path,table_sha256=table_sha,
        dispatch_snapshot='recompute if native source or active qualified table changes',
        source_sha256={str(q):hashlib.sha256(q.read_bytes()).hexdigest() for q in source_paths},
        scope='necessary native loaded-state trim/list moment envelope; finite-angle GZ area relaxed')


def attach_moment_rows(model, instance, T, allocations, *, envelope_result=None):
    """Attach four necessary X/Y rows to supporting-stack CP allocations.

    The allocation API is the existing physics_bound.attach API. Arm extrema
    over each physical stack relax tier identity; moment coefficients take
    minima over every group member. Actual liquid mass maps to ceil kg with
    the same signed-coefficient guard proved in physics_bound_notes.md.
    """
    from stowage.checker import stability
    from stowage.checker.visibility import _fixed_loads
    env = necessary_envelope(instance) if envelope_result is None else envelope_result
    if env['instance_id'] != instance.id:
        raise ValueError('Envelope instance mismatch')
    if env.get('certificate_eligible') is not True:
        raise ValueError('Uncertified trim envelope cannot enter a reported global bound')
    for path,digest in env.get('source_sha256',{}).items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('Envelope native source provenance changed')
    if env.get('table_path') is not None:
        if hashlib.sha256(Path(env['table_path']).read_bytes()).hexdigest() != env['table_sha256']:
            raise ValueError('Envelope active table bytes changed')
    _, params = stability._context(instance.vessel)
    raw_tanks = {t['id']:t for t in params.get('tanks',[])}
    def number(v):
        return F(float(v['value'] if isinstance(v,dict) else v))
    tanks = []
    for t in instance.vessel.tanks:
        g = raw_tanks[t.id].get('geometry',raw_tanks[t.id])
        tanks.append(dict(capacity=F(t.capacity_m3)*F(t.content_density_kg_m3),
                          x=(number(g['x_min_m']),number(g['x_max_m'])),
                          y=(number(g['y_min_m']),number(g['y_max_m']))))
    us = [model.new_int_var(0,physics.ceil(t['capacity']),f'trim_list_tank_{k}')
          for k,t in enumerate(tanks)]
    boxes = {b.id:b for b in instance.containers}
    fixed = _fixed_loads(instance.vessel)
    h = instance.vessel.hull
    fixed_mass = F(h.lightship_mass_kg)+sum((F(q.mass_kg) for q in fixed),F())
    constant_mass = fixed_mass+sum((F(boxes[bid].gross_mass_kg) for bid in instance.load_list),F())
    cell_objects = {c.id:c for c in instance.vessel.cells}
    for axis in ('x','y'):
        arm_name = 'lcg_m' if axis == 'x' else 'tcg_m'
        lightship_arm = h.lightship_lcg_m if axis == 'x' else h.lightship_tcg_m
        fixed_moment = F(h.lightship_mass_kg)*F(lightship_arm)+sum(
            (F(q.mass_kg)*F(q.x_m if axis == 'x' else q.y_m) for q in fixed),F())
        stack_arms = {}
        for j in range(len(T.stack_ids)):
            arms = [F(getattr(cell_objects[T.cell_ids[c]],arm_name)) for c in T.stack_cells[j]]
            if arms:
                stack_arms[j] = min(arms),max(arms)
        for upper in (False,True):
            bound = F(env[f'cg_{axis}_{"upper" if upper else "lower"}_m'])
            tank_raw = [(t[axis][0]-bound if upper else bound-t[axis][1]) for t in tanks]
            coeffs,_,rounding_guard = physics.rounded_tank_row(tank_raw,[t['capacity'] for t in tanks])
            terms = []
            for allocation in allocations:
                x,members,sig = allocation[:3]
                units = sum(count for _,count in sig)
                arm = sum((stack_arms[j][0 if upper else 1]*count for j,count in sig),F())/units
                coefficient = min(F(boxes[bid].gross_mass_kg)*(arm if upper else -arm) for bid in members)
                terms.append(physics.floor(coefficient)*x)
            rhs = (bound*constant_mass-fixed_moment if upper else fixed_moment-bound*constant_mass)
            model.add(sum(terms)+sum(c*u for c,u in zip(coeffs,us))
                      <= physics.ceil(rhs)+rounding_guard+1)
    return dict(envelope=env,moment_rows=4,tank_variables=len(us),
                relaxation='per-stack arm extrema, tank solid arm extrema, integer-ceil liquid masses')


def main():
    import argparse
    import json
    from time import monotonic
    from catalog import load_set
    from stowage.io import load_instance
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',required=True)
    parser.add_argument('--out',required=True)
    parser.add_argument('--max-table-blocks',type=int,default=256)
    args = parser.parse_args()
    rows = []
    for path in load_set(args.list):
        started = monotonic()
        row = necessary_envelope(load_instance(path),max_table_blocks=args.max_table_blocks)
        row.update(input=path,input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                   wall_s=monotonic()-started)
        rows.append(row)
        print(json.dumps(row),flush=True)
        Path(args.out).write_text(json.dumps(rows,indent=2)+'\n')


if __name__ == '__main__':
    main()
