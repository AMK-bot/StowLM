"""Intact stability of the declared polygon hull (IMO MSC.267(85), A2/B3.1).

A hull floats when displaced water balances both its mass and mass moments.
GM measures its initial restoring stiffness; GZ is the finite-angle restoring
lever. Free surfaces correct those two quantities separately under B3.1.
Production selects Part B3.1.9.1: actual liquid-transfer moments interpolated
from qualified per-tank fill/heel/trim tables, with direct geometry outside their
coverage. Physical liquid LCG shifts enter free-trim equilibrium. The distinct
upright-inertia method .2 remains selectable for reporting by setting
STOWAGE_STABILITY_FREE_SURFACE_METHOD=2; its explicit interpretation is a
virtual KG rise FSC and righting-lever reduction FSC*sin(heel). Both retain
upright inertia for GM and the conservative small-tank exemption using
rho*I0/lightship_mass<0.01m. Precomputed exact sounding polynomials provide
upright liquid centroids and inertia. Only the selected production method
belongs in the stability booklet (IS Code B3.1.11).
Free-trim curves retain physical KG in longitudinal balance. The direct
reference clips the real hull at each heel; an optional prevalidated on-disk
state table interpolates its buoyancy states before solving the same balances.
Missing/uncovered tables and near-limit results use the direct reference.
Numerical kernels retain explicit finite iteration/evaluation caps. Production
does not use wall time or cache-sensitive poll quotas to decide physical results.
STOWAGE_STABILITY_TIME_BUDGET_S remains accepted and validated for legacy
configuration compatibility, without imposing a wall cutoff. External watchdog
interruptions are unavailable checks, never completed passes. Optional poll
limits are diagnostic only and are not selected by this production entry point.

Metrics (SI): displacement_kg, draught_mean_m, draught_aft_m, draught_fwd_m,
trim_m, list_rad, kg_m, gm_solid_m, fsc_m, gm_m, area_0_30_mrad,
area_0_40_mrad, area_30_40_mrad, gz_max_m, angle_gz_max_rad, weather_ratio.
free_surface_method records the selected B3.1.9 method (1 or2);
free_surface_small_tanks_omitted counts the individually exempt tanks and
free_surface_exemption_displacement_kg records the conservative denominator.
geometry_table_used records use of a validated cubic buoyancy-state table;
geometry_table_limit_fallback records a direct recheck near a hard boundary.
geometry_loading_equilibrium_direct marks exact refinement of loading limits
while retaining the validated interpolated GZ curve.
Margins are positive within limits (measured minus lower limits, upper limits
minus measured). Additional criteria diagnostics have descriptive SI suffixes.

Solver scaled residual tolerance is 2e-6, inherited from task 2.1. IACS UR L5
rev4 §5.2 Table1 validation tolerances are displacement2%, draught/GM max(1%,
0.05m), GZ max(5%,0.05m), areas max(5%,0.0012mrad), equilibrium heel1degree.
Those are validation tolerances, never extra allowances on regulatory limits.
The bounded finite curve uses two-degree numerical sampling and linear integration;
its quadrature convergence is tested separately from equilibrium convergence.
Under leader decision D21, Lpp > 100 m selects B 2.3 in place of A 2.2.1–3;
A 2.2.4 and A 2.3 remain active. Numeric ``criteria_*_active`` flags record
that selection. B total available area ends at flooding or vanishing stability.
Declared KCS openings bound the leeward curve; independent windward coverage
is retained for weather work. No near-90-degree tail is required.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import math
import numpy as np
from scipy.optimize import brentq

from stowage.frozen import frozen
from stowage.model import Instance, PartReport, ShipState, Violation
from stowage.checker.slots import cells_of_slot
from stowage.vessel.fleet import _parameter_bytes
from stowage.checker.stability_geometry import StabilityGeometry
from stowage.checker.stability_loading import loading_condition, curve_correction


@lru_cache(maxsize=12)
def _geometry(name, payload):
    # Payload participates in the key so a changed parameter file cannot reuse
    # stale geometry. The caller supplies freshly read raw parameters.
    import json
    import hashlib
    parameters = json.loads(payload)
    result = StabilityGeometry(parameters)
    result._stability_vessel_hash = hashlib.sha256(payload.encode()).hexdigest()
    # The payload is an exact immutable parameter identity. Canonicalizing its
    # large tank trees once preserves D30 disk keys without repeating that work
    # for every loading condition. Edits to the parameter bytes create a fresh
    # geometry and metadata together.
    from stowage.checker.stability_liquid import metadata
    result._stability_liquid_metadata = metadata(parameters)
    return result


@lru_cache(maxsize=12)
def _parsed_parameter_payload(payload):
    """Cache TOML parsing by exact bytes, never a rounded/stat-only identity.

    The fleet loader currently parses these bytes directly with tomllib too.
    Reading through its byte helper retains allowed-name/path validation. A
    change of any byte invalidates parsing and canonical geometry identity,
    including edits which preserve file length and modification timestamp.
    Parser implementation is fixed by the imported Python module for a process;
    restarting after code changes naturally clears this process-local cache.
    This parsed-parameter LRU is shared by check and check_reference; the six
    optional-cache bypass does not disable it.
    """
    import json
    import tomllib
    params = tomllib.loads(payload.decode('utf-8'))
    return params,json.dumps(params,sort_keys=True)


def _context(vessel):
    import json
    name = vessel.slice_of or vessel.id
    _,payload = _parsed_parameter_payload(_parameter_bytes(name))
    # Preserve the fleet loader's fresh-tree contract. A private consumer that
    # edits a returned tree must not contaminate the next loading condition.
    # The cached canonical JSON reconstructs the same values in C, avoiding a
    # Python deepcopy of the large nested fleet/tank tree on every check.
    params = json.loads(payload)
    geometry = _geometry(name, payload)
    h = vessel.hull
    for field in ('lpp_m', 'beam_m', 'depth_m'):
        if not math.isclose(getattr(geometry, field), getattr(h, field), abs_tol=1e-8):
            raise ValueError('vessel hull does not match its declared geometry parameters')
    return geometry, params


_CANONICAL_POLICY_FIELDS = ('min_trim_m', 'max_trim_m', 'min_displacement_kg',
                            'max_displacement_kg', 'bilge_keel_area_m2')


@lru_cache(maxsize=12)
def _canonical_policy_baseline(root, parent_digest, construction_identity, providers):
    """Immutable manual values; only successful content identities are cached.

    The builder fingerprint is part of the key, including its source/model,
    library/version and auxiliary cargo/port inputs. Their current content and
    the actual pinned hull offsets are checked by the caller on every use.
    No mutable vessel, loading state, criterion result or failure is cached.
    Both check and check_reference share this baseline and canonical projection.
    Paired full reports do not independently verify that projection: its five
    fields are compared separately against the real public fleet builder.
    """
    from stowage.vessel import fleet
    from stowage.checker.stability_policy import provider_identity, projected_policy
    if providers != provider_identity():
        raise ValueError('canonical fleet policy providers changed during resolution')
    projected = projected_policy(root, parent_digest, construction_identity, providers)
    if projected is not None:
        if providers != provider_identity():
            raise ValueError('canonical fleet policy providers changed during resolution')
        return projected
    canonical = fleet.load_vessel(root)
    if canonical.id != root or canonical.slice_of is not None or canonical.hull is None:
        raise ValueError('canonical vessel resolver returned an inconsistent root or missing hull')
    expected = f'params/fleet/{root}.toml sha256:{parent_digest}'
    records = [source for source in canonical.sources if source.startswith('params/fleet/')]
    if records != [expected]:
        raise ValueError('canonical fleet parameter source disagrees with current parameter bytes')
    values = []
    for field in _CANONICAL_POLICY_FIELDS:
        value = _hull_policy_number(canonical.hull, field)
        if value is None:
            raise ValueError(f'canonical vessel.hull.{field} is missing')
        values.append(value)
    if providers != provider_identity():
        raise ValueError('canonical fleet policy providers changed during resolution')
    return tuple(values)


def _canonical_fleet_policy(vessel):
    """Validate each embedded fleet manual against a content-keyed baseline."""
    from stowage.vessel import fleet
    from stowage.conventions import canonical_vessel_id
    from stowage.vessel.fleet_conditions import _verify_parent_parameter_sources
    import hashlib
    try:
        names = set(fleet.list_fleet())
        root = canonical_vessel_id(vessel.slice_of or vessel.id)
        sources = vessel.sources
        if not isinstance(sources,(list,tuple)) or any(not isinstance(source,str) for source in sources):
            raise ValueError('fleet parameter source records must be strings')
        records = [source for source in sources if source.startswith('params/fleet/')]
        identity = canonical_vessel_id(vessel.id)
        identities = [name for name in names if identity == name or identity.startswith(name+'.')]
        if any(name != root for name in identities):
            raise ValueError('known vessel identity disagrees with its declared slice root')
        if root not in names:
            if identities or records:
                raise ValueError(f'canonical fleet root {root!r} is unavailable')
            # Foreign hulls retain their explicit independent data contract;
            # _context still requires an available physical geometry provider.
            return
        # IS Code A2.1.6, PDF p.12: booklet information must enable operation
        # "in compliance with the applicable requirements". Under D40 the
        # public vessel manual is authoritative; instance text cannot widen it.
        payload = fleet._parameter_bytes(root)
        digest = hashlib.sha256(payload).hexdigest()
        if len(records) != 1 or '.toml sha256:' not in records[0]:
            raise ValueError('fleet parameter source must contain exactly one current record')
        # D33 admits the historical label only with its exact old raw digest
        # and a complete match of every nonlabel parameter against its pin.
        try:
            _verify_parent_parameter_sources(vessel, root, payload)
        except ValueError as exc:
            raise ValueError(f'fleet parameter source invalid: {exc}') from exc
        # D30: a memoized manual is never evidence that its source still exists.
        # Mirror the public builder's offset check on every hit, using its
        # exact-byte parsed parameters rather than reparsing the large TOML.
        params, _ = _parsed_parameter_payload(payload)
        form = params.get('hull_form', {})
        if form.get('family') == 'stowlm_offsets_v1':
            from stowage.vessel.hull_offsets import verified_offset_bytes
            verified_offset_bytes(form['offsets_path'], expected_sha256=form['offsets_sha256'])
        # Reuse the builder's complete construction fingerprint so changed or
        # unavailable auxiliary inputs cannot hide behind a policy cache hit.
        # This reads/hashes inputs without the builder's repeated TOML/model parse.
        from stowage.vessel.fleet_cache import _cache_key
        identity = _cache_key(root, 'full', True, payload)
        from stowage.checker.stability_policy import provider_identity
        baseline_values = _canonical_policy_baseline(root, digest, identity, provider_identity())
        for field, baseline in zip(_CANONICAL_POLICY_FIELDS, baseline_values):
            actual = _hull_policy_number(vessel.hull,field)
            if actual is None or actual != baseline:
                raise ValueError(f'vessel.hull.{field} differs from canonical fleet policy; use a tighter instance override')
    except Exception as exc:
        # Unavailable/corrupt public input cannot turn into an unevaluated
        # exception or permission to trust an instance-authored replacement.
        raise ValueError(f'canonical fleet policy: {exc}') from exc


def _deck_edge_angle(geometry, angles, drafts, *, draft_solver=None):
    """First weather-deck-edge immersion, including heel sinkage/free trim.

    The actual section deck breadths and station coordinates are retained.
    A supplied free-trim solver refines the initial angular-grid bracket;
    interpolation alone is useful for independent closed-form tests.
    """
    # These section/deck intersections depend only on the declared hull.
    # Cache their exact values, not a sampled immersion angle.
    direct = getattr(geometry, 'direct', geometry)
    if not hasattr(direct, '_stability_deck_edges'):
        widths,stations = [],[]
        for x,section in zip(geometry.x_m,geometry.sections):
            polygon=section[0]
            following=np.roll(polygon,-1,axis=0)
            mask=((np.minimum(polygon[:,1],following[:,1])-1e-9 <= geometry.depth_m) &
                  (geometry.depth_m <= np.maximum(polygon[:,1],following[:,1])+1e-9))
            a,b=polygon[mask],following[mask]
            if len(a):
                flat=np.abs(a[:,1]-b[:,1]) < 1e-12
                intersections=a[:,0]+np.divide((geometry.depth_m-a[:,1])*(b[:,0]-a[:,0]),
                    b[:,1]-a[:,1],out=np.zeros(len(a)),where=~flat)
                intersections=np.r_[intersections,b[flat,0]]
                stations.append(x)
                widths.append(float(np.max(np.abs(intersections))))
        if not widths:
            raise ValueError('no hull weather-deck edge in declared geometry')
        direct._stability_deck_edges = np.asarray(widths),np.asarray(stations)
    half_breadths,x_m = direct._stability_deck_edges
    def clearance(phi):
        ta, tf = (draft_solver(phi) if draft_solver else
                  (np.interp(phi, angles, drafts[:, 0]), np.interp(phi, angles, drafts[:, 1])))
        return float(np.min(geometry.depth_m-ta-(tf-ta)*x_m/geometry.lpp_m
                            -half_breadths*np.tan(phi)))
    if clearance(0.) <= 0:
        return 0.
    previous = 0.
    for phi in angles[1:]:
        # Grid values bracket the first immersion; only root refinement needs
        # additional hull solves when an exact draft solver is supplied.
        ta, tf = np.interp(phi, angles, drafts[:, 0]), np.interp(phi, angles, drafts[:, 1])
        wet = np.min(geometry.depth_m-ta-(tf-ta)*x_m/geometry.lpp_m
                     -half_breadths*np.tan(phi)) <= 0
        if wet:
            return float(brentq(clearance, previous, float(phi), xtol=1e-9))
        previous = float(phi)
    if draft_solver is not None:
        for phi in np.arange(previous+math.radians(5), math.radians(80)+1e-10, math.radians(5)):
            if clearance(float(phi)) <= 0:
                return float(brentq(clearance,previous,float(phi),xtol=1e-9))
            previous = float(phi)
    raise ValueError('deck-edge immersion not covered by the free-trim curve')


def _refine_peak(angles, gz, gz_solver):
    """Resolve the physical peak near the 25-degree regulatory boundary."""
    imax = int(np.argmax(gz))
    if not (0 < imax < len(angles)-1 and
            abs(angles[imax]-math.radians(25)) <= math.radians(3)):
        return None, None
    from scipy.optimize import minimize_scalar
    optimum = minimize_scalar(lambda phi: -gz_solver(float(phi)),
        bounds=(float(angles[imax-1]),float(angles[imax+1])), method='bounded',
        options={'xatol':1e-8})
    if not optimum.success:
        raise ValueError('maximum-GZ angle refinement did not converge')
    phi = float(optimum.x)
    return phi, float(gz_solver(phi))


def _background_deck_z(vessel, load):
    """Local weather deck or hatch/pedestal top, in baseline coordinates."""
    # Stack.base_z_m is the declared top of its hatch cover/pedestal (Vessel
    # contract). Cells are physical 20-foot supports; use their ISO footprint
    # to locate that surface, rather than a cover's steel mass centroid.
    decks = {s.id: s.base_z_m for s in vessel.stacks if s.on_deck}
    heights = [decks[c.stack_id] for c in vessel.cells if c.stack_id in decks
               and abs(c.lcg_m-load.x_m) <= 6.096/2+1e-8
               and abs(c.tcg_m-load.y_m) <= 2.438/2+1e-8]
    return min(heights) if heights else vessel.hull.depth_m


def _missing_background_silhouette(vessel, load):
    # ASSUMPTION: without an occupied envelope, a background point mass below
    # its local weather-deck/hatch surface denotes enclosed hold cargo (D30).
    # A point at or above that surface denotes exposed cargo and needs geometry.
    return (vessel.slice_of and getattr(load, 'kind', None) == 'cargo'
            and load.mass_kg > 0 and load.silhouette is None
            and load.z_m >= _background_deck_z(vessel, load))


def _windage(instance, ship, params, ta, tf):
    """Exact union of declared lateral silhouettes, clipped by actual waterline.

    IS A2.3.2 explicitly permits underwater lateral centre at half mean draught.
    Cargo silhouettes use actual stack heights; overlapping rows count once.
    Fixed-load envelopes describe the background cargo of a vessel slice;
    they share the same projection, waterline clipping and overlap union.
    """
    rects = [dict(r) for r in params['windage']['projected_rectangles']]
    L = instance.vessel.hull.lpp_m
    for r in rects:
        if r.get('id') == 'hull_freeboard':
            r['z_min_m'] = -1e6
    for load in instance.vessel.fixed_loads:
        envelope = getattr(load, 'silhouette', None)
        if envelope is not None:
            rectangle = {key: getattr(envelope, key) for key in
                         ('x_min_m', 'x_max_m', 'z_min_m', 'z_max_m')}
            if getattr(load, 'kind', None) == 'cargo':
                rectangle['z_min_m'] = max(rectangle['z_min_m'],
                                           _background_deck_z(instance.vessel, load))
                if rectangle['z_max_m'] <= rectangle['z_min_m']:
                    continue  # Enclosed hold cargo has no weather windage.
            rects.append(rectangle)
        elif _missing_background_silhouette(instance.vessel, load):
            raise ValueError('slice background cargo is missing its windage silhouette')
    cells = {c.id: c for c in instance.vessel.cells}
    boxes = {b.id: b for b in instance.containers}
    occupied = {cid: boxes[p.container_id] for p in ship.placements for cid in cells_of_slot(p.slot)}
    floors = {}
    for stack in instance.vessel.stacks:
        z = stack.base_z_m
        for cid in stack.cell_ids:
            floors[cid] = z
            if cid in occupied:
                z += occupied[cid].height_m
    for p in ship.placements:
        ids = cells_of_slot(p.slot); box = boxes[p.container_id]
        x = sum(cells[c].lcg_m for c in ids)/len(ids)
        z = sum(floors[c] for c in ids)/len(ids)
        rects.append(dict(x_min_m=x-box.length_ft*.3048/2, x_max_m=x+box.length_ft*.3048/2,
                          z_min_m=z, z_max_m=z+box.height_m))
    # Coincident port/starboard cargo silhouettes have the same union. Remove
    # duplicate rectangles before the sweep, preserving exactly the same area.
    coordinates = ('x_min_m','x_max_m','z_min_m','z_max_m')
    rects = [dict(zip(coordinates,key)) for key in
             dict.fromkeys(tuple(r[k] for k in coordinates) for r in rects)]
    breaks = {r[k] for r in rects for k in ('x_min_m','x_max_m')}
    slope = (tf-ta)/L
    if slope:
        for r in rects:
            for key in ('z_min_m', 'z_max_m'):
                x = (r[key]-ta)/slope
                if r['x_min_m'] < x < r['x_max_m']:
                    breaks.add(x)
    area = moment = 0.
    for left, right in zip(sorted(breaks), sorted(breaks)[1:]):
        active = [r for r in rects if r['x_min_m'] < (left+right)/2 < r['x_max_m']]
        for u, w in zip((-math.sqrt(3/5),0.,math.sqrt(3/5)),(5/9,8/9,5/9)):
            x = (left+right)/2+u*(right-left)/2
            intervals = sorted((max(r['z_min_m'],ta+slope*x),r['z_max_m']) for r in active)
            union = []
            for lo, hi in intervals:
                if hi <= lo: continue
                if union and lo <= union[-1][1]: union[-1][1] = max(union[-1][1],hi)
                else: union.append([lo,hi])
            area += w*(right-left)/2*sum(hi-lo for lo,hi in union)
            moment += w*(right-left)/2*sum((hi*hi-lo*lo)/2 for lo,hi in union)
    if area <= 0: raise ValueError('no windage silhouette above water')
    return area, moment/area-(ta+tf)/4


def _minimum_loading_gm(instance):
    """Retain the statutory and vessel-manual GM floors under instance policy."""
    from numbers import Real

    def finite(value, label):
        try:
            valid = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f'{label} must be finite nonboolean numeric policy data')
        return float(value)

    baseline = finite(instance.vessel.hull.min_gm_m, 'vessel.hull.min_gm_m')
    overrides = instance.rule_overrides
    if not isinstance(overrides, Mapping):
        raise ValueError('rule_overrides: GM policy must be a mapping')
    policy = overrides.get('iscode.loading.gm', {})
    if not isinstance(policy, Mapping):
        raise ValueError('iscode.loading.gm: GM policy must be a mapping')
    proposed = (finite(policy['minimum_gm'], 'iscode.loading.gm.minimum_gm')
                if 'minimum_gm' in policy else baseline)
    # IS Code A2.2.4, PDF p.13: "shall not be less than 0.15 m";
    # A2.1.6, PDF p.12: the booklet enables operation "in compliance with
    # the applicable requirements". A finite instance request cannot erase
    # the vessel's stricter manual minimum; retain the strongest floor.
    return max(.15, baseline, proposed)


def _hull_policy_number(hull, field):
    """Read a declared hull value with explicit source/assumption evidence."""
    from numbers import Real
    value = getattr(hull, field, None)
    if value is None:
        return None
    try:
        valid = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f'vessel.hull.{field} must be finite nonboolean numeric policy data')
    evidence = getattr(hull, field+'_provenance', None)
    texts = [evidence.get(key) if isinstance(evidence, Mapping) else getattr(evidence, key, None)
             for key in ('source', 'assumption')]
    if (not any(isinstance(text, str) and text.strip() for text in texts)
            or any(text is not None and (not isinstance(text, str) or not text.strip()) for text in texts)):
        raise ValueError(f'vessel.hull.{field}_provenance requires a nonblank source or assumption')
    return float(value)


def _weather_keel_area(instance):
    """Apply weather-area tightening only against an evidenced vessel baseline."""
    from numbers import Real
    baseline = _hull_policy_number(instance.vessel.hull, 'bilge_keel_area_m2')
    if baseline is None:
        raise ValueError('weather criterion needs total bilge/bar keel area Ak')
    if baseline < 0:
        raise ValueError('bilge/bar keel area must be finite and nonnegative')
    rule = 'iscode.a2_3.weather'
    key = 'bilge_keel_area_m2'
    if not isinstance(instance.rule_overrides, Mapping):
        raise ValueError('rule_overrides: weather policy must be a mapping')
    policy = instance.rule_overrides.get(rule, {})
    if not isinstance(policy, Mapping):
        raise ValueError(f'{rule}: weather policy must be a mapping')
    if key not in policy:
        return baseline
    value = policy[key]
    try:
        valid = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid or value < 0:
        raise ValueError(f'{rule}.{key} must be finite nonboolean nonnegative area')
    # IS Code A2.3.4, PDF p15: Ak is the "overall area of bilge keels";
    # table2.3.4-3, PDF p16, assigns decreasing k as Ak increases. Under D40
    # and the catalogue, an override may reduce credited area (increasing
    # assumed roll), never grant more damping than the evidenced vessel.
    if value > baseline:
        raise ValueError(f'{rule}.{key} must not weaken vessel.hull.{key}')
    return float(value)


def _loading_policy(instance):
    """Independent loading-manual bounds, never hydrostatic-table coverage."""
    # IS Code A2.1.6, printed p.11: "contains sufficient information ... to
    # enable the master to operate the ship in compliance"; A2.1.7, p.11:
    # "full range of operational trims". The catalogue supplies the project
    # contract: independent signed trim and displacement bounds. Under D40 the
    # evidenced vessel manual is the baseline; instance limits may only tighten.
    # These synthetic/public manual limits are not statutory numeric defaults.
    overrides = instance.rule_overrides
    if not isinstance(overrides, Mapping):
        raise ValueError('rule_overrides: loading policy must be a mapping')

    def finite(rule, key, value):
        from numbers import Real
        try:
            valid = isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f'{rule}.{key}: explicit finite numeric loading policy is required')
        return float(value)

    def interval(rule, minimum_field, maximum_field, *, positive=False):
        policy = overrides.get(rule, {})
        if not isinstance(policy, Mapping):
            raise ValueError(f'{rule}: loading policy must be a mapping')
        baseline = [_hull_policy_number(instance.vessel.hull, field)
                    for field in (minimum_field, maximum_field)]
        for field, value in zip((minimum_field, maximum_field), baseline):
            if value is None:
                raise ValueError(f'{rule}: missing vessel.hull.{field}; instance overrides require a known vessel baseline')
        if positive and any(value <= 0 for value in baseline):
            raise ValueError(f'{rule}: vessel.hull displacement bounds must be positive')
        if baseline[0] > baseline[1]:
            raise ValueError(f'{rule}: vessel.hull minimum must not exceed maximum')
        effective = []
        for key, field, original, lower in zip(('minimum', 'maximum'),
                (minimum_field, maximum_field), baseline, (True, False)):
            if key in policy:
                value = finite(rule, key, policy[key])
                if positive and value <= 0:
                    raise ValueError(f'{rule}.{key}: displacement policy must be positive')
                if (lower and value < original) or (not lower and value > original):
                    raise ValueError(f'{rule}.{key} must not weaken vessel.hull.{field}')
            else:
                value = original
            # D40: every endpoint must come from the evidenced vessel manual;
            # an instance cannot establish tightening against an unknown bound.
            effective.append(value)
        if effective[0] > effective[1]:
            raise ValueError(f'{rule}: minimum must not exceed maximum')
        return effective

    return (*interval('iscode.loading.trim', 'min_trim_m', 'max_trim_m'),
            *interval('iscode.loading.displacement', 'min_displacement_kg', 'max_displacement_kg', positive=True))


@frozen('checker.check_stability', owner='3.1', implemented=True)
def check_stability(instance: Instance, ship: ShipState, *, call_index: int,
                    in_port: bool = False) -> PartReport:
    """Return hard catalogue violations for invalid/uncomputable conditions.

    A previously validated disk table may accelerate geometry queries. Set
    STOWAGE_STABILITY_DIRECT=1 to force the unchanged numerical reference.
    Missing tables, uncovered conditions and near-limit results use that reference.
    Production uses B3.1.9.1 actual transfer, with qualified liquid tables and
    direct fallback. STOWAGE_STABILITY_FREE_SURFACE_METHOD=2 selects the separate
    upright-inertia comparison. STOWAGE_STABILITY_LIQUID_DIRECT=1 disables only
    liquid interpolation; STOWAGE_STABILITY_DIRECT=1 disables both interpolations.
    Only the selected production method belongs in a ship stability booklet.
    ``in_port=True`` explicitly selects D64's equilibrium-only cargo-operation
    criteria P1--P6. The production caller owns activation and lift replay;
    this primitive never changes tank fills or transfers HEEL ballast.
    P5 implies the D40 maximum-displacement bound only at water density
    <=1025 kg/m3. A necessary mask must adapt that row to the actual density
    or omit it at denser ports; this primitive adds no displacement criterion.
    """
    import os
    from stowage.checker.stability_budget import budget, StabilityTimeout
    seconds = 30.
    try:
        seconds = float(os.environ.get('STOWAGE_STABILITY_TIME_BUDGET_S','30'))
        with budget(seconds):
            result = _check_stability(instance,ship,call_index=call_index,
                _force_direct=os.environ.get('STOWAGE_STABILITY_DIRECT') == '1',
                in_port=in_port)
        return result
    except (StabilityTimeout, ValueError) as exc:
        message = str(exc)
        return PartReport(name='stability',ran=False,error=message,
            metrics={'computation_failed':1.,
                     'diagnostic_work_limit_exceeded':float(isinstance(exc,StabilityTimeout))},
            violations=[Violation(rule_id='checker.internal_error',severity='hard',
                                  call_index=call_index,message=message)])


def _check_stability(instance, ship, *, call_index, _force_direct=False, in_port=False):
    from stowage.checker.stability_criteria import evaluate_criteria
    violations = []
    def violation(rule, measured=None, limit=None, unit=None, message=''):
        violations.append(Violation(rule_id=rule, severity='hard', call_index=call_index,
                                    measured=measured, limit=limit, unit=unit, message=message))
    def failed(message, rule='checker.missing_vessel_data'):
        violation(rule, message=message)
        return PartReport(name='stability', ran=False, error=message, violations=violations)
    if instance.vessel.hull is None:
        return failed('Hull/lightship data is missing')
    if ship.vessel_id != instance.vessel.id:
        return failed('Ship state vessel does not match instance')
    if not 0 <= call_index < len(instance.rotation.calls):
        return failed('Call index outside rotation')
    if instance.rotation.calls[call_index].locode != instance.port.locode:
        return failed('Port density/profile for requested call is unavailable')
    if ship.covers_off and not in_port:
        return failed('Open hatch invalidates the secured watertight buoyancy envelope')
    if not in_port and any(_missing_background_silhouette(instance.vessel, load)
           for load in instance.vessel.fixed_loads):
        return failed('Slice background cargo requires silhouette geometry for weather windage')
    phase = 'data'
    try:
        _canonical_fleet_policy(instance.vessel)
        if not in_port:
            minimum_trim, maximum_trim, minimum_mass, maximum_mass = _loading_policy(instance)
            min_gm = _minimum_loading_gm(instance)
            weather_keel_area = _weather_keel_area(instance)
        import os
        method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD','1'))
        if method not in (1,2):
            raise ValueError('free-surface method must be 1 or 2')
        geometry, params = _context(instance.vessel)
        if in_port and ship.covers_off:
            coamings = {c['id']:c for c in params.get('envelope',{}).get('hatch_coamings',[])}
            for cover_id in ship.covers_off:
                coaming = coamings.get(cover_id)
                if coaming is None:
                    raise ValueError(f'open hatch {cover_id} lacks coaming geometry')
                if not all(math.isfinite(float(coaming[key])) for key in
                           ('x_min_m','x_max_m','y_min_m','y_max_m','z_max_m')):
                    raise ValueError(f'open hatch {cover_id} has nonfinite coaming geometry')
        phase = 'loading'
        condition = loading_condition(instance, ship, geometry=geometry, ship_params=params,
                                      free_surface_method=method,precomputed_soundings=True,
                                      small_tank_exemption=True)
        if condition.errors:
            return failed('; '.join(condition.errors), 'iscode.b3_1.free_surface')
        if method == 1 and not _force_direct and os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') != '1':
            from stowage.checker.stability_liquid import attach_tables
            condition = attach_tables(condition,params,
                vessel_hash=getattr(geometry,'_stability_vessel_hash',None),
                _metadata_value=getattr(geometry,'_stability_liquid_metadata',None))
        h = instance.vessel.hull
        rho = instance.port.water_density_kg_m3
        using_table = False
        if not _force_direct:
            from stowage.checker.stability_tables import cached_geometry
            from zipfile import BadZipFile
            try:
                accelerated = cached_geometry(geometry,params,rho)
            except (OSError, ValueError, KeyError, TypeError, BadZipFile):
                # Disk caches are optional acceleration, never required vessel
                # data. A damaged archive must retain the direct checker path.
                accelerated = None
            if accelerated is not None:
                geometry = accelerated
                using_table = True
        # Production retains actual liquid redistribution in the equilibrium
        # iteration, interpolated only inside independently qualified grids.
        from stowage.checker.stability_loading import liquid_cg
        phase = 'equilibrium'
        def solve_floating(solver):
            if method == 2:
                return solver.equilibrium(condition.mass_kg,condition.lcg_m,
                    condition.tcg_m,condition.kg_m+condition.fsc_m,rho)
            from stowage.checker.stability_equilibrium import floating_equilibrium
            return floating_equilibrium(solver,condition,rho,h.lpp_m,liquid_cg)
        floating = solve_floating(geometry)
        exact_loading_equilibrium = False
        if using_table and not in_port and _floating_near_limit(instance,floating):
            # A draft/trim/list boundary needs only the actual equilibrium,
            # not a new direct GZ curve. Evaluate its mass and moment balances
            # directly and retain the independently validated curve table.
            floating = solve_floating(geometry.direct)
            exact_loading_equilibrium = True
        ta,tf = floating.t_aft_m,floating.t_fwd_m
        if in_port:
            metrics,margins,broken = _in_port_criteria(instance,ship,geometry,params,
                condition,floating,method)
            # The existing conservative guards trigger exact geometry, never
            # an allowance on a port limit. Refine GM and P4 as well as drafts.
            if (not _force_direct and (using_table or condition.liquid_tables)
                    and _near_table_limit(metrics,margins)):
                result = _check_stability(instance,ship,call_index=call_index,
                    _force_direct=True,in_port=True)
                result.metrics['geometry_table_limit_fallback'] = 1.
                return result
            for value,limit,unit,message in broken:
                violation('port.stability.cargo_operations',value,limit,unit,message)
            metrics['geometry_table_used'] = float(using_table)
            return PartReport(name='stability',metrics=metrics,margins=margins,
                              violations=violations)
        phase = 'curve'
        from stowage.checker.stability_curve import build_stability_curve
        curve = build_stability_curve(geometry,condition,rho,initial_drafts=(ta,tf),
                                      correction_solver=lambda c,a,t:curve_correction(c,a,t,method=method),
                                      free_surface_method=method)
        angles,gz,negative_gz,drafts = curve.angles,curve.gz,curve.negative_gz,curve.drafts
        phi_f,at_angle = curve.flooding_angle,curve.at_angle
        # APPROXIMATION: use the conservative worst-side curve from geometric
        # upright rather than discarding its negative work below equilibrium list.
        peak_angle,peak_gz = _refine_peak(angles,gz,lambda phi:at_angle(phi)[0])
        upright = geometry.full_submerged(*drafts[0],0.,rho)
        normal = geometry.waterplane_normal(*drafts[0],0.)
        gm_solid = float(np.dot(np.asarray(upright.centroid_m)-
            [condition.lcg_m,0.,condition.kg_m],normal))+upright.bm_t_m
        gm = gm_solid-condition.fsc_m
        phase = 'deck_edge'
        deck_edge = _deck_edge_angle(geometry,angles,drafts,
                                    draft_solver=lambda phi:at_angle(float(phi))[1])
        if peak_angle is not None and peak_angle > phi_f:
            peak_angle = peak_gz = None
        phase = 'data'
        area, lever = _windage(instance,ship,params,ta,tf)
        metrics,margins,broken = evaluate_criteria(angles,gz,gm_m=gm,hull=h,geometry=geometry,
            displacement_kg=condition.mass_kg,kg_m=condition.kg_m+condition.fsc_m,draught_m=floating.mean_draught_m,
            phi_f_rad=phi_f,windage_area_m2=area,windage_lever_m=lever,hull_params=params,negative_gz=negative_gz,
            weather_bilge_keel_area_m2=weather_keel_area,
            draught_aft_m=ta,draught_fwd_m=tf,rule_overrides=instance.rule_overrides,
            deck_edge_angle_rad=deck_edge,peak_angle_rad=peak_angle,peak_gz_m=peak_gz)
        for args in broken: violation(*args)
        metrics.update(displacement_kg=condition.mass_kg,draught_mean_m=floating.mean_draught_m,
            draught_aft_m=ta,draught_fwd_m=tf,trim_m=floating.trim_m,list_rad=floating.heel_rad,
            kg_m=condition.kg_m,gm_solid_m=gm_solid,fsc_m=condition.fsc_m,gm_m=gm,
            equilibrium_residual=floating.residual_norm,angle_flooding_rad=phi_f,
            windage_area_m2=area,windage_lever_m=lever,
            curve_max_heel_rad=float(angles[-1]),flooding_openings_declared=float(bool(geometry.openings)),
            free_surface_method=float(method),free_surface_small_tanks_omitted=float(len(condition.omitted_tanks)),
            free_surface_exemption_displacement_kg=h.lightship_mass_kg,
            liquid_table_used=float(bool(condition.liquid_usage.get('table',0))),
            liquid_table_queries=float(condition.liquid_usage.get('table',0)),
            liquid_direct_queries=float(condition.liquid_usage.get('direct',0)),
            liquid_table_partial_fallback=float(bool(condition.liquid_usage.get('table',0) and condition.liquid_usage.get('direct',0))))
        # D40's lightship displacement minimum is a consistency bound, not an
        # operational/service minimum: modeled lightship plus nonnegative loads
        # cannot cross it. Retain the independent bound; do not invent a service condition.
        limits = [('gm_m',gm,min_gm,True,'iscode.loading.gm','m'),
            ('trim_m',floating.trim_m,maximum_trim,False,'iscode.loading.trim','m'),
            ('trim_min_m',floating.trim_m,minimum_trim,True,'iscode.loading.trim','m'),
            ('list_rad',abs(floating.heel_rad),h.max_list_rad,False,'iscode.loading.list','rad'),
            ('draught_aft_m',ta,h.max_draught_m,False,'iscode.loading.draft','m'),
            ('draught_fwd_m',tf,h.max_draught_m,False,'iscode.loading.draft','m'),
            ('draught_fwd_min_m',tf,h.min_draught_fwd_m,True,'iscode.loading.draft','m'),
            ('displacement_minimum_kg',condition.mass_kg,minimum_mass,True,'iscode.loading.displacement','kg'),
            ('displacement_maximum_kg',condition.mass_kg,maximum_mass,False,'iscode.loading.displacement','kg')]
        if instance.port.max_draught_m is not None:
            limits.append(('port_draught_m',max(ta,tf),instance.port.max_draught_m,False,'port.call.draft','m'))
        for key,value,limit,lower,rule,unit in limits:
            margin = value-limit if lower else limit-value
            margins[key] = margin
            if margin < -1e-10: violation(rule,value,limit,unit,f'{key} exceeds permissible loading limit')
        metrics['geometry_table_used'] = float(using_table)
        metrics['geometry_loading_equilibrium_direct'] = float(exact_loading_equilibrium)
        guarded_margins = ({key:value for key,value in margins.items()
                           if not _is_equilibrium_loading_margin(key)}
                          if exact_loading_equilibrium else margins)
        if using_table and _near_table_limit(metrics,guarded_margins):
            result = _check_stability(instance,ship,call_index=call_index,_force_direct=True)
            result.metrics['geometry_table_limit_fallback'] = 1.
            return result
        return PartReport(name='stability',metrics=metrics,margins=margins,
                          violations=sorted(violations,key=lambda v:v.rule_id))
    except (ValueError, ArithmeticError, KeyError, TypeError, RuntimeError) as exc:
        if not _force_direct and locals().get('using_table',False):
            # Any inability to represent a condition conservatively restarts
            # the entire check with direct geometry, including its equilibrium.
            return _check_stability(instance,ship,call_index=call_index,
                                    _force_direct=True,in_port=in_port)
        numerical = phase in {'loading','equilibrium','curve','openings','deck_edge'}
        return failed(f'{phase}: {exc}', 'checker.internal_error' if numerical else 'checker.missing_vessel_data')


def _in_port_criteria(instance,ship,geometry,params,condition,floating,method):
    """D64 P1--P6 from floating and upright equilibrium; no seagoing curve.

    The one zero-heel free-trim solve uses the same upright GM convention as
    the seagoing checker. It does not compute finite-angle GZ, flooding curves,
    windage, weather, or any seagoing loading-manual minimum.
    """
    from stowage.checker.stability_loading import liquid_cg
    h = instance.vessel.hull
    rho = instance.port.water_density_kg_m3
    ta,tf = floating.t_aft_m,floating.t_fwd_m
    if (not np.isfinite([ta,tf,floating.heel_rad,floating.residual_norm]).all()
            or floating.residual_norm > 2e-6):
        raise ValueError('no converged floating equilibrium for cargo operations')
    callback = ((lambda phi,trim:(condition.lcg_m,0.,condition.kg_m+condition.fsc_m))
                if method == 2 else
                (lambda phi,trim:liquid_cg(condition,phi,trim)))
    _,drafts = geometry.free_trim_curve([0.],condition.mass_kg,condition.lcg_m,
        condition.kg_m,rho,cg_callback=callback,initial_drafts=(ta,tf))
    if np.asarray(drafts).shape != (1,2) or not np.isfinite(drafts).all():
        raise ValueError('nonfinite or unavailable upright equilibrium draughts')
    upright = geometry.full_submerged(*drafts[0],0.,rho)
    normal = geometry.waterplane_normal(*drafts[0],0.)
    gm_solid = float(np.dot(np.asarray(upright.centroid_m)-
        [condition.lcg_m,0.,condition.kg_m],normal))+upright.bm_t_m
    gm = gm_solid-condition.fsc_m
    # APPROXIMATION: D64 carries the 0.15 m IS Code GM floor into port and
    # borrows crane/cell-guide limits of max_list_rad and one degree of trim.
    trim_limit = h.lpp_m*math.tan(math.radians(1.))
    limits = [('gm_m',gm,.15,True,'m'),
        ('list_rad',abs(floating.heel_rad),h.max_list_rad,False,'rad'),
        ('trim_m',abs(floating.trim_m),trim_limit,False,'m'),
        ('draught_aft_m',ta,h.max_draught_m,False,'m'),
        ('draught_fwd_m',tf,h.max_draught_m,False,'m')]
    if instance.port.max_draught_m is not None:
        limits.append(('port_draught_m',max(ta,tf),instance.port.max_draught_m,False,'m'))
    metrics = dict(in_port=1.,displacement_kg=condition.mass_kg,
        draught_mean_m=floating.mean_draught_m,draught_aft_m=ta,draught_fwd_m=tf,
        trim_m=floating.trim_m,list_rad=floating.heel_rad,kg_m=condition.kg_m,
        gm_solid_m=gm_solid,fsc_m=condition.fsc_m,gm_m=gm,
        equilibrium_residual=floating.residual_norm,free_surface_method=float(method),
        free_surface_small_tanks_omitted=float(len(condition.omitted_tanks)),
        free_surface_exemption_displacement_kg=h.lightship_mass_kg)
    strict = set()
    if ship.covers_off:
        # APPROXIMATION: D64 uses first deck/open-coaming immersion beyond
        # the list limit as its harbour open-hatch criterion. Hold equilibrium
        # trim fixed and solve the simultaneous mass/contact equations; the
        # contact-plane substitution below exactly eliminates the heave root.
        deck_edge,immersion = _in_port_immersion_angles(geometry,params,
            ship.covers_off,condition.mass_kg,rho,floating.trim_m)
        metrics.update(angle_deck_edge_rad=deck_edge,angle_open_hatch_immersion_rad=immersion)
        limits.append(('open_hatch_immersion_angle_rad',immersion,h.max_list_rad,True,'rad'))
        strict.add('open_hatch_immersion_angle_rad')
    margins,broken = {},[]
    for key,value,limit,lower,unit in limits:
        if not math.isfinite(value) or not math.isfinite(limit):
            raise ValueError(f'nonfinite in-port {key}')
        margin = value-limit if lower else limit-value
        margins[key] = margin
        outside = margin <= 0 if key in strict else margin < -1e-10
        if outside:
            broken.append((value,limit,unit,f'{key} exceeds permissible cargo-operation limit'))
    metrics.update(liquid_table_used=float(bool(condition.liquid_usage.get('table',0))),
        liquid_table_queries=float(condition.liquid_usage.get('table',0)),
        liquid_direct_queries=float(condition.liquid_usage.get('direct',0)),
        liquid_table_partial_fallback=float(bool(condition.liquid_usage.get('table',0)
            and condition.liquid_usage.get('direct',0))))
    return metrics,margins,broken


def _in_port_immersion_angles(geometry,params,covers_off,mass,rho,trim):
    """First deck/open-lip immersion on either side, at fixed trim and mass.

    P4 uses exact polygon buoyancy even when floating equilibrium used a
    qualified table. At first contact the mean draught is fixed by the lowest
    deck/lip point. Substituting that contact draught into the mass equation
    eliminates the inner heave root: displacement is monotone in heave, so its
    zero gives the same simultaneous mass-and-contact solution. Angular search
    trials are contact planes, not claimed mass-balanced floating states. No
    moment balance, GZ, liquid-transfer envelope or seagoing curve is run.
    """
    from stowage.checker.stability_budget import check_budget
    geometry = getattr(geometry,'direct',geometry)
    if not hasattr(geometry,'_stability_port_immersion_geometry'):
        vertices,deck = [],[]
        for x,section in zip(geometry.x_m,geometry.sections):
            for polygon in section:
                vertices.extend((x,y,z) for y,z in polygon)
            polygon = section[0]
            following = np.roll(polygon,-1,axis=0)
            mask = ((np.minimum(polygon[:,1],following[:,1])-1e-9 <= geometry.depth_m)
                    & (geometry.depth_m <= np.maximum(polygon[:,1],following[:,1])+1e-9))
            a,b = polygon[mask],following[mask]
            if len(a):
                flat = np.abs(a[:,1]-b[:,1]) < 1e-12
                intersections = a[:,0]+np.divide((geometry.depth_m-a[:,1])*(b[:,0]-a[:,0]),
                    b[:,1]-a[:,1],out=np.zeros(len(a)),where=~flat)
                deck.extend((x,y,geometry.depth_m) for y in np.r_[intersections,b[flat,0]])
        if not deck:
            raise ValueError('no hull weather-deck edge in declared geometry')
        geometry._stability_port_immersion_geometry = np.asarray(vertices),np.asarray(deck)
    vertices,deck = geometry._stability_port_immersion_geometry
    coamings = {c['id']:c for c in params.get('envelope',{}).get('hatch_coamings',[])}
    lips = []
    for cover_id in covers_off:
        if cover_id not in coamings:
            raise ValueError(f'open hatch {cover_id} lacks coaming geometry')
        c = coamings[cover_id]
        lips.extend((x,y,c['z_max_m']) for x in (c['x_min_m'],c['x_max_m'])
                    for y in (c['y_min_m'],c['y_max_m']))
    points = np.vstack((deck,np.asarray(lips).reshape(-1,3)))
    point_base = points[:,2]+trim*points[:,0]/geometry.lpp_m-trim/2
    clearance_cache = {}
    def clearance(angle):
        angle = float(angle)
        if angle not in clearance_cache:
            check_budget('in-port fixed-trim mass/contact balance')
            tangent = math.tan(angle)
            contact_means = point_base-points[:,1]*tangent
            if not np.isfinite(contact_means).all():
                raise ValueError('nonfinite in-port immersion contact draught')
            means = (float(np.min(contact_means[:len(deck)])),float(np.min(contact_means)))
            values = {}
            for mean in means:
                if mean in values:
                    continue  # Deck and combined first-contact planes coincide.
                value = geometry.submerged(mean+trim/2,mean-trim/2,angle,rho).displacement_kg-mass
                if not math.isfinite(value):
                    raise ValueError('nonfinite in-port displacement balance')
                values[mean] = value
            # Positive displacement reserve means the mass-balanced waterline
            # is below first contact; nonpositive reserve brackets immersion.
            clearance_cache[angle] = tuple(values[mean] for mean in means)
        return clearance_cache[angle]
    zero = clearance(0.)
    result = [math.inf,math.inf]
    # Bracket from upright outwards, independently on both sides. A root for
    # an open lip can precede deck immersion; retain each first crossing.
    for sign in (-1.,1.):
        previous = 0.
        found = [value <= 0 for value in zero]
        for k,wet in enumerate(found):
            if wet:
                result[k] = 0.
        for magnitude in np.radians(np.r_[np.arange(2.,90.,2.),89.9]):
            angle = sign*float(magnitude)
            values = clearance(angle)
            for k in (0,1):
                if not found[k] and values[k] <= 0:
                    root = brentq(lambda phi:clearance(sign*phi)[k],abs(previous),abs(angle),xtol=1e-10)
                    result[k] = min(result[k],root)
                    found[k] = True
            if all(found):
                break
            previous = angle
        if not all(found):
            raise ValueError('in-port immersion not covered before 89.9 degrees')
    return tuple(result)


def _near_table_limit(metrics,margins):
    """Recheck boundary cases with direct geometry, without relaxing a limit.

    APPROXIMATION: interpolation has finite error. Conservative guard bands
    exceed the requested one-fifth L5 validation targets; they trigger a direct
    recomputation rather than creating any regulatory allowance.
    """
    for key,margin in margins.items():
        if key == 'weather_table_applicability':
            continue  # discrete applicability flag, not a numerical clearance
        if 'area' in key:
            tolerance = max(.00024,.01*abs(metrics.get(key.replace('containership_',''),0.)))
        elif 'gm' in key:
            tolerance = max(.01,.002*abs(metrics.get('gm_m',0.)))
        elif 'gz' in key and 'angle' not in key:
            tolerance = max(.01,.01*abs(metrics.get(key.replace('containership_',''),0.)))
        elif 'draught' in key or 'trim' in key:
            tolerance = .02
        elif 'weather' in key:
            tolerance = .05
        elif 'angle' in key or 'list' in key or 'flooding' in key:
            tolerance = math.radians(.2)
        else:
            continue
        if abs(margin) <= tolerance:
            return True
    return False


def _is_equilibrium_loading_margin(key):
    """Only these margins are exact after a direct floating-state refinement."""
    return key in {'trim_m','trim_min_m','list_rad','draught_aft_m','draught_fwd_m',
                   'draught_fwd_min_m','port_draught_m'}


def _floating_near_limit(instance,floating):
    """Use direct equilibrium near a draft/trim/list limit, never an allowance."""
    hull=instance.vessel.hull
    try:
        minimum_trim, maximum_trim, _, _ = _loading_policy(instance)
    except (ValueError, TypeError, AttributeError):
        # Solver probes also call this boolean numerical guard. Unavailable
        # policy requires direct evaluation; the public checker independently
        # rejects it before physics. Do not invent a bound or raise in a probe.
        return True
    clearance=[hull.max_draught_m-floating.t_aft_m,
               hull.max_draught_m-floating.t_fwd_m,
               floating.t_fwd_m-hull.min_draught_fwd_m,
               maximum_trim-floating.trim_m,
               floating.trim_m-minimum_trim]
    if instance.port.max_draught_m is not None:
        clearance.append(instance.port.max_draught_m-max(floating.t_aft_m,floating.t_fwd_m))
    return (any(abs(value) <= .02 for value in clearance) or
            abs(hull.max_list_rad-abs(floating.heel_rad)) <= math.radians(.2))
