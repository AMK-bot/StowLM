from pathlib import Path
p=Path('src/stowage/checker/lashing.py').read_text()
p=p.replace('from collections.abc import Mapping\n','').replace('from numbers import Real\n','')
p=p.replace("vessel's section from rated geometry at D56's declared maximum GM with a 10% margin; area 0.003 m² and", "vessel's section from rated geometry with a 10% margin; area 0.003 m² and")
a=p.index('def _lashing_design_gm('); b=p.index('def _end_response(',a)
old='''def derive_cell_guide_design(vessel) -> CellGuideDesign:
    """D54 deterministic scantlings from rated geometry, never occupied cargo.

    APPROXIMATION: synthetic design, not class-approved scantlings. There is
    no maximum-GM field in HullModel. Instead bound every permitted waterplane
    geometrically, using the *independent* signed loading-manual trim bounds.
    This conservative upper bound need not be an attainable loading condition.
    No hydrostatic-table maximum or particular background stow defines it.
    """
    from stowage.checker.visibility import _cargo_catalogs, _fixed_loads, _geometry

    cells, stacks = _cargo_catalogs(vessel)
    holds = sorted((s for s in stacks.values() if not s.on_deck), key=lambda s: s.id)
    if not holds:
        return CellGuideDesign(CELL_GUIDE_SECTION_MODULUS_M3, CELL_GUIDE_AREA_M2,
                               0., '', 0., 0., 0, 'D54: no rated hold stacks.')
    hull = vessel.hull
    if hull is None or not hull.hull_offsets_ref:
        raise ValueError('D54 guide design requires referenced hull geometry')
    if hull.min_trim_m is None or hull.max_trim_m is None:
        raise ValueError('D54 guide design requires both signed loading-manual trim bounds')
    if not 0 <= hull.max_list_rad < math.pi/2:
        raise ValueError('D54 guide design requires finite list limit below 90 degrees')
    ref = PurePosixPath(hull.hull_offsets_ref.split('#')[0])
    if str(ref.parent) != 'params/fleet' or ref.suffix != '.toml':
        raise ValueError('D54 unsupported hull reference; expected params/fleet/<name>.toml#hull_form')
    geometry, params = _geometry(ref.stem)
    if not np.allclose([geometry.lpp_m, geometry.beam_m, geometry.depth_m],
                       [hull.lpp_m, hull.beam_m, hull.depth_m], rtol=0., atol=1e-7):
        raise ValueError('D54 hull reference disagrees with principal dimensions')
    points = np.concatenate([np.column_stack((np.full(len(p), x), p))
                             for x, section in zip(geometry.x_m, geometry.sections)
                             for p in section])
    xmin, ymin, zmin = np.min(points, axis=0)
    xmax, ymax, zmax = np.max(points, axis=0)
    phi = hull.max_list_rad
    trim = (hull.min_trim_m, hull.max_trim_m)
    slope = max(abs(t) for t in trim)/hull.lpp_m
    # For z=Tf+(1-x/L)*trim+y*tan(list), this horizontal plane lies
    # below every permitted waterplane. Its volume is therefore a positive,
    # density-independent lower bound, including fresh-water departures.
    hmin = hull.min_draught_fwd_m + min((1-x/hull.lpp_m)*t
                                      for x in (xmin, xmax) for t in trim)
    hmin -= max(abs(ymin), abs(ymax))*math.tan(phi)
    volume_min = geometry.submerged(hmin, hmin).volume_m3
    if not math.isfinite(volume_min) or volume_min <= 0:
        raise ValueError('D54 loading envelope has no positive certified immersed-volume lower bound')
    # Section chords at any allowed heel fit in each polygon's y interval;
    # their physical length is <= interval/cos(phi). The x-to-waterplane
    # Jacobian is <=sqrt(1+slope²). Popoviciu's variance inequality then
    # gives Iwp <= area * transverse_span² / 4 about its own centroid.
    area_max = sum(float(w)*sum(float(np.ptp(p[:, 0])) for p in section)
                   for w, section in zip(geometry.integration_weights_m, geometry.sections))
    area_max *= math.sqrt(1+slope*slope)/math.cos(phi)
    span_max = (xmax-xmin)*slope*math.sin(phi)+(ymax-ymin)+(zmax-zmin)*math.sin(phi)
    from stowage.vessel.tanks import TankGeometry
    raw_tanks = {t['id']: t for t in params.get('tanks', [])}
    tank_floors = []
    for tank in vessel.tanks:
        if tank.id not in raw_tanks:
            raise ValueError(f'D54 guide design lacks physical tank geometry: {tank.id}')
        solid = TankGeometry.from_params(raw_tanks[tank.id], ship_params=params)
        tank_floors.append(solid.z_min_m)
    # A tilted partly-filled tank's CG can be below every nonempty upright
    # capacity-table centroid; only the solid's physical floor is a bound.
    kg_min = min([0., hull.lightship_vcg_m]+[s.base_z_m for s in stacks.values()]+tank_floors)
    # All cargo CGs are inside their declared supported envelopes. Explicit
    # non-cargo constants are part of the vessel, and can be below baseline.
    kg_min = min([kg_min]+[float(q.z_m) for q in _fixed_loads(vessel)
                          if q.kind == 'non_cargo'])
    gm = float(zmax+area_max*span_max**2/(4*volume_min)-kg_min)
    if not math.isfinite(gm) or gm <= 0:
        raise ValueError('D54 guide design has no finite positive GM bound')
'''
# The rest of the rated-geometry contact envelope and IRS formulas were unchanged.
x=p.index('    volume_sc = geometry.submerged',a); y=p.index('def _end_response(',x)
rest=p[x:y].replace('GM up to the declared D56 design GM.', 'GM up to the certified upper bound.').replace('D54/D56 synthetic rated-vessel guide design','D54 synthetic rated-vessel guide design').replace('declared lashing design GM {gm:.9g}', 'GM upper bound {gm:.9g}')
p=p[:a]+old+rest+p[b:]
a=p.index('    design_gm = _lashing_design_gm(hull)'); b=p.index('    draught = ',a)
p=p[:a]+'    condition = _loading_condition(instance, ship)\n'+p[b:]
p=p.replace('    guide_design = derive_cell_guide_design(instance.vessel)\n', "    guide_design = derive_cell_guide_design(instance.vessel)\n    if guide_design.gm_m > 0 and condition.gm_m > guide_design.gm_m+1e-8:\n        raise ValueError('loading-condition GM exceeds certified D54 design envelope bound')\n")
p=p.replace("                               'guide_design_gm_m': guide_design.gm_m,\n                               'max_gm_lashing_m': guide_design.gm_m,\n                               # Backward-compatible key: now the declared D56 validity cap.\n",'')
Path('/tmp/lashing-pre-d56.py').write_text(p)
compile(p,'/tmp/lashing-pre-d56.py','exec')
print('Restored original touched portions to /tmp/lashing-pre-d56.py')
