# Copied from experiments/mvp/frontier/lab/c4/c2-hatch/bound_securing.py; engine integration changes are local.
"""Conservative ballast-independent securing bounds for one fixed cargo stow.

This is a necessary-condition diagnostic, never acceptance. It uses the pinned
checker's cargo graph and fixed scantling CB, and computes no equilibrium. A
bound below one says nothing about feasibility. See REVIEW.md for the proof.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path, PurePosixPath

import numpy as np


LASHING_SOURCE_SHA256 = '6bf6a1ba92cf17687716fbb6eb8ac7bf528971356614134b84d5bb89f1ecfdb8'


def _verified_rod_bound(forces, winds, heights, stiffness, *, cg_heights):
    """Cross-check the source matrix solve with positive chain compliance.

    The inverse of the unrodded, uniform grounded chain has entries
    min(i+1,j+1)/stiffness. A single positive rod diagonal gives the anchor
    displacement below. Every coefficient of equivalent force is positive.
    """
    from stowage.checker import lashing
    arrays = [np.asarray(values, dtype=float) for values in
              (forces, winds, heights, cg_heights)]
    forces, winds, heights, cg_heights = arrays
    n = len(forces)
    if (n == 0 or any(a.shape != (n,) or not np.isfinite(a).all() for a in arrays)
            or np.any(forces < 0.) or np.any(winds < 0.) or np.any(heights <= 0.)
            or np.any(cg_heights < 0.) or np.any(cg_heights > heights)
            or not math.isfinite(stiffness) or stiffness <= 0.):
        raise ValueError('invalid positive-chain inputs')
    fractions = cg_heights/heights
    equivalent = []
    for i in range(n):
        terms = [float(fractions[i]*forces[i]), .5*float(winds[i])]
        if i+1 < n:
            terms.extend([float((1-fractions[i+1])*forces[i+1]), .5*float(winds[i+1])])
        equivalent.append(math.fsum(terms))
    anchor = min(2, n)-1
    z = math.fsum(map(float, heights[:anchor+1]))
    length = math.hypot(lashing.WIDTH, z)
    cosine, sine = lashing.WIDTH/length, z/length
    rod_k = lashing._rod_stiffness(length, anchor+1)
    compliance = (anchor+1)/stiffness
    rod_diagonal = rod_k*cosine*cosine
    denominator = 1.+rod_diagonal*compliance
    weights = [min(j+1, anchor+1)/stiffness for j in range(n)]
    rod_coefficients = [rod_k*cosine*w/denominator for w in weights]
    if not all(math.isfinite(v) and v > 0. for v in
               (z, length, cosine, sine, rod_k, compliance, denominator,
                *weights, *rod_coefficients)):
        raise ValueError('nonpositive or nonfinite chain compliance')
    anchor_displacement = math.fsum(f*w for f, w in zip(equivalent, weights))/denominator
    closed_rod = rod_k*cosine*anchor_displacement
    rack, original_rod, original_cosine, original_sine, original_anchor = lashing._end_response(
        forces, winds, heights.tolist(), stiffness, cg_heights=cg_heights)
    if (original_anchor != anchor or not np.isfinite(rack).all()
            or not all(math.isfinite(v) and v >= 0. for v in (closed_rod, original_rod))
            or not math.isclose(cosine, original_cosine, rel_tol=1e-12, abs_tol=0.)
            or not math.isclose(sine, original_sine, rel_tol=1e-12, abs_tol=0.)):
        raise ValueError('invalid original rod response')
    relative_difference = abs(original_rod-closed_rod)/max(abs(original_rod), abs(closed_rod), 1e-300)
    # Recover displacements from the source rack=k*(u-previous_u), then verify
    # its equilibrium row residuals independently of the rod formula.
    displacement = [math.fsum(float(v)/stiffness for v in rack[:i+1]) for i in range(n)]
    residuals = []
    for i, value in enumerate(displacement):
        terms = [stiffness*(value-(displacement[i-1] if i else 0.))]
        if i+1 < n:
            terms.append(stiffness*(value-displacement[i+1]))
        if i == anchor:
            terms.append(rod_diagonal*value)
        residuals.append(math.fsum(terms)-equivalent[i])
    relative_residual = max(map(abs, residuals))/max(1., max(equivalent))
    if (not math.isfinite(relative_difference) or relative_difference > 1e-9
            or not math.isfinite(relative_residual) or relative_residual > 1e-9):
        raise ValueError('source/compliance rod cross-check failed')
    guarded_rod = min(original_rod, closed_rod)*(1.-1e-10)
    detail = dict(stiffness_n_m=float(stiffness), anchor_index=anchor,
        minimum_forces_n=forces.tolist(), fixed_winds_n=winds.tolist(),
        heights_m=heights.tolist(), cg_heights_m=cg_heights.tolist(),
        cg_height_fractions=fractions.tolist(), equivalent_forces_n=equivalent,
        rod_force_coefficients=rod_coefficients,
        rod_stiffness_n_m=rod_k, rod_cosine=original_cosine, rod_sine=original_sine,
        anchor_compliance_m_n=compliance, feedback_denominator=denominator,
        anchor_displacement_m=anchor_displacement,
        original_rod_n=float(original_rod), closed_form_rod_n=closed_rod,
        guarded_rod_n=guarded_rod, relative_rod_difference=relative_difference,
        source_equilibrium_residual_n=residuals,
        source_equilibrium_relative_residual=relative_residual,
        rod_limits_n=dict(horizontal=150000., vertical=225000., resultant=.85*lashing.ROD_SWL))
    return guarded_rod, original_cosine, original_sine, detail


class _MinimumMotions:
    def __init__(self, motions, hull, gravity):
        self.values = .7*abs(motions.surge), abs(motions.sway), abs(motions.heave)
        self.rotation_upper_m = hull.depth_m/2
        self.gravity = gravity
        self.longitudinal_angle_rad = motions.theta
        self.elevated_al = .7*math.hypot(motions.surge,
            math.sqrt(max(hull.lpp_m, 110.)/325)*gravity*math.sin(motions.theta))
        # Positive GM implies 0 < Tr <= 30 s. The source's unbounded-GM
        # limit is Tr -> 0, giving an upper phi; sin(phi) is monotone only
        # after independently establishing that the whole range is < pi/2.
        factor = 1. if hull.bilge_keel else 1.2
        lower = 9000*.5*factor/((hull.beam_m+75)*math.pi)
        upper = 9000*1.25*factor/((hull.beam_m+75)*math.pi)
        if hull.beam_m < 32.2:
            lower, upper = max(25., lower), max(25., upper)
        if not 0 <= lower <= upper < 90.-1e-10:
            raise ValueError('cannot establish monotone roll-angle range')
        self.roll_angle_floor_rad = math.radians(lower)
        self.roll_angle_upper_rad = math.radians(upper)
        self.elevated_states = 0

    def at(self, x, y, z):
        del x, y
        al, at, av = self.values
        lever = z-self.rotation_upper_m
        if lever >= 0.:
            # pitch >= 0 and rotation_z <= depth/2: the positive fixed
            # gravity/pitch term cannot be cancelled for this elevated CG.
            al = max(al, self.elevated_al)
            phi = self.roll_angle_floor_rad
            at = max(at, math.hypot(at, self.gravity*math.sin(phi)+
                phi*(2*math.pi/30.)**2*lever))
            self.elevated_states += 1
        return al, at, av


def securing_bound(instance, ship):
    """Return proved lower ratios and an optional fixed-stow contradiction.

    No ballast search, hull equilibrium, checker call or process is started.
    A single scantling submerged integration establishes exactly the same CB
    used by securing; guide sizing uses the checker's fixed design function.
    Unknown input/source/geometry never supplies a proof. Numerical guards are
    stricter than record()'s 1e-10 ratio tolerance.
    """
    from stowage.checker import lashing, visibility
    from stowage.frozen import active_stubs
    result = dict(status='unknown', proof=None, lower_ratios={}, witnesses={})
    if active_stubs():
        result['reason'] = 'active frozen-interface override'
        return result
    try:
        if hashlib.sha256(Path(lashing.__file__).read_bytes()).hexdigest() != LASHING_SOURCE_SHA256:
            raise ValueError('unreviewed lashing source')
        hull = instance.vessel.hull
        if hull is None or not hull.hull_offsets_ref:
            raise ValueError('missing hull offsets')
        if not all(math.isfinite(x) and x > 0 for x in
                   (hull.lpp_m, hull.beam_m, hull.depth_m, hull.scantling_draught_m)):
            raise ValueError('nonpositive hull dimensions')
        ref = PurePosixPath(hull.hull_offsets_ref.split('#')[0])
        if str(ref.parent) != 'params/fleet' or ref.suffix != '.toml':
            raise ValueError('unsupported hull reference')
        geometry, _ = visibility._geometry(ref.stem)
        import inspect
        dependencies = {'checker.lashing': lashing.__file__,
                        'checker.visibility': visibility.__file__,
                        'hull_geometry': inspect.getsourcefile(type(geometry))}
        dependency_sha256 = {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                             for name,path in dependencies.items() if path is not None}
        if not np.allclose([geometry.lpp_m, geometry.beam_m, geometry.depth_m],
                           [hull.lpp_m, hull.beam_m, hull.depth_m], rtol=1e-9):
            raise ValueError('hull dimensions disagree')
        volume = geometry.submerged(hull.scantling_draught_m,
                                    hull.scantling_draught_m).volume_m3
        cb = volume/(hull.lpp_m*hull.beam_m*hull.scantling_draught_m)
        # These three terms of _motions are independent of actual GM/draught.
        motions = lashing._motions(hull, hull.scantling_draught_m/4, 1., cb)
        if not 0 <= motions.theta < math.pi/2:
            raise ValueError('cannot establish nonnegative pitch-angle gravity')
        minimum = _MinimumMotions(motions, hull, lashing.G)
        if not all(math.isfinite(x) and x >= 0 for x in minimum.values):
            raise ValueError('nonfinite acceleration bound')
        loads = lashing._load_graph(instance, ship, minimum)
        chains = lashing._chains(loads)
        for q in loads.values():
            values = (q.mass, q.height, q.length, q.dx, q.dy, q.cg_height,
                      q.al, q.at, q.av, q.wind_t, q.wind_l)
            if not all(math.isfinite(x) for x in values):
                raise ValueError('nonfinite cargo graph')
            if q.mass < 0 or q.height <= 0 or q.length <= 0:
                raise ValueError('nonpositive cargo geometry')
        ratios, witnesses, witness_details = {}, {}, {}

        def record(key, value, slots, *, detail=None):
            value = float(value)
            if not math.isfinite(value):
                raise ValueError('nonfinite bound')
            if value > ratios.get(key, 0.):
                ratios[key] = value
                witnesses[key] = list(slots)
                if detail is not None:
                    witness_details[key] = {**detail, 'slots': list(slots), 'lower_ratio': value}
                else:
                    witness_details.pop(key, None)

        for q in loads.values():
            record('container_mass', q.container.gross_mass_kg/30500., [q.slot])

        # With nonnegative own corner-weight coefficients, greater av cannot
        # reduce an averaged +/- longitudinal compression reaction. Horizontal
        # dynamic moments cancel in that average, including propagated shear.
        positive_corners = all(
            abs(q.dx)/(2*q.length)+abs(q.dy)/(2*lashing.WIDTH) <= .25-1e-12
            for q in loads.values())
        if positive_corners:
            compression, _ = lashing._longitudinal_reactions(
                loads, 0., dynamic_vertical=True)
            for slot, forces in compression.items():
                maximum = max(0., float(np.max(forces)))
                record('max_corner_post_ratio', maximum/848000., [slot])
                record('max_fitting_ratio', maximum/(.85*lashing.LOCK_COMPRESSION_SWL), [slot])

        # With direction +1 every own horizontal force and propagated end
        # shear is nonnegative. This recurrence is componentwise monotone in
        # al; vertical acceleration and eccentric moments do not enter shear.
        _, longitudinal_shear = lashing._longitudinal_reactions(
            loads, 1., dynamic_vertical=True)
        if any(not np.isfinite(v).all() or np.any(v < 0.)
               for v in longitudinal_shear.values()):
            raise ValueError('cannot establish nonnegative longitudinal shear')
        guide = None
        for chain in chains:
            qs = [q for q, _ in chain]
            slots = list(dict.fromkeys(q.slot for q in qs))
            if qs[0].on_deck:
                shares = [.5+(-1 if end == 0 else 1)*q.dx/q.length
                          for q, end in chain]
                wind_shares = [.5 if getattr(q.container, 'background', False) else f
                               for q, f in zip(qs, shares)]
                trans = np.array([abs(q.mass*q.at*f) for q, f in zip(qs, shares)])
                wt = np.array([abs(q.wind_t*f) if q.on_deck else 0.
                               for q, f in zip(qs, wind_shares)])
                for i, q in enumerate(qs):
                    transverse = float(sum(trans[i:])+sum(wt[i:]))/2
                    longitudinal = longitudinal_shear[q.slot][chain[i][1]]/2
                    # The exact synthetic fitting checks both horizontal axes;
                    # rods do not relieve this shear_corner expression.
                    shear_corner = math.hypot(transverse, longitudinal)
                    record('max_fitting_ratio', shear_corner/(.85*lashing.LOCK_SHEAR_SWL),
                           slots)
                # Every multiplier in equivalent_l and its suffix sums must
                # be nonnegative before componentwise acceleration bounds apply.
                fractions = np.array([q.cg_height/q.height for q in qs])
                if np.any(fractions < 0.) or np.any(fractions > 1.):
                    continue
                # Grounded chain stiffness is SPD with nonpositive off-diagonal
                # entries, hence inverse-positive. Every equivalent-force
                # coefficient is nonnegative for these CG-height fractions.
                # Therefore the rod force is monotone in the minimum trans
                # forces; the exact fixed wind and both stiffness cases remain.
                heights = [q.height for q in qs]
                cg_heights = [q.cg_height for q in qs]
                for stiffness in (15.7e6, 3.7e6):
                    rod, cosine, sine, detail = _verified_rod_bound(
                        trans, wt, heights, stiffness, cg_heights=cg_heights)
                    record('max_lashing_ratio', max(rod*cosine/150000.,
                        rod*sine/225000., rod/(.85*lashing.ROD_SWL)), slots, detail=detail)
                longs = np.array([abs(q.mass*q.al*f) for q, f in zip(qs, shares)])
                wl = np.array([abs(q.wind_l*f) if q.on_deck else 0.
                               for q, f in zip(qs, wind_shares)])
                equivalent = fractions*longs+.5*wl
                equivalent[:-1] += (1-fractions[1:])*longs[1:]+.5*wl[1:]
                record('max_longitudinal_racking_ratio',
                       float(max(np.cumsum(equivalent[::-1])))/75000., slots)
            else:
                if guide is None:
                    guide = lashing.derive_cell_guide_design(instance.vessel)
                for i, q in enumerate(qs):
                    lower = np.array([q.mass*q.at, q.mass*q.al])/4
                    upper = (np.array([qs[i+1].mass*qs[i+1].at,
                                       qs[i+1].mass*qs[i+1].al])/4
                             if i+1 < len(qs) else np.zeros(2))
                    contact = float(np.linalg.norm(lower+upper))
                    record('max_cell_guide_ratio', lashing._cell_guide_ratio(
                        contact, guide.section_modulus_m3, guide.area_m2), slots)
                    record('max_fitting_ratio', max(float(np.linalg.norm(lower)),
                        float(np.linalg.norm(upper)))/(.85*lashing.LOCK_SHEAR_SWL), slots)

        result.update(status='bounded', lower_ratios=ratios, witnesses=witnesses,
                      witness_details=witness_details,
                      minimum_accelerations_m_s2=list(minimum.values),
                      elevated_cg_count=minimum.elevated_states,
                      elevated_al_floor_m_s2=minimum.elevated_al,
                      motion_rotation_upper_m=minimum.rotation_upper_m,
                      roll_angle_floor_rad=minimum.roll_angle_floor_rad,
                      roll_angle_upper_rad=minimum.roll_angle_upper_rad,
                      cb=cb, positive_corner_coefficients=positive_corners,
                      source_sha256=LASHING_SOURCE_SHA256, dependency_sha256=dependency_sha256)
        failed = sorted(key for key, value in ratios.items() if value > 1.+1e-8)
        if failed:
            result.update(status='infeasible_fixed_stow', proof=(
                'Every ballast condition has securing ratios at least: ' +
                ', '.join(f'{key}={ratios[key]:.12g}' for key in failed) +
                '. These exceed the unchanged securing limit independently '
                'of tank fills; another cargo stow is not ruled out.'))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError,
            IndexError, OSError, np.linalg.LinAlgError) as error:
        result['reason'] = f'{type(error).__name__}: {error}'
        return result


def selftest(seed=19620803):
    """Cheap deterministic rod/compliance and monotonicity checks; not a map run."""
    from stowage.checker import lashing
    rng = np.random.default_rng(seed)
    maximum_difference = maximum_residual = 0.
    cases = 0
    for n in range(1, 9):
        for stiffness in (15.7e6, 3.7e6):
            for _ in range(8):
                heights = rng.uniform(2., 4., n)
                fractions = rng.uniform(0., 1., n)
                fractions[0] = 0.
                if n > 1:
                    fractions[-1] = 1.
                cg_heights = fractions*heights
                forces = rng.uniform(0., 300000., n)
                winds = rng.uniform(0., 50000., n)
                bound, _, _, detail = _verified_rod_bound(forces, winds, heights,
                    stiffness, cg_heights=cg_heights)
                increments = rng.uniform(0., 200000., n)
                wind_increments = rng.uniform(0., 10000., n)
                _, increased, _, _, _ = lashing._end_response(forces+increments,
                    winds+wind_increments, heights.tolist(), stiffness, cg_heights=cg_heights)
                assert detail['original_rod_n'] <= increased*(1.+1e-12)+1e-8
                assert bound <= increased
                assert all(v > 0. for v in detail['rod_force_coefficients'])
                maximum_difference = max(maximum_difference, detail['relative_rod_difference'])
                maximum_residual = max(maximum_residual, detail['source_equilibrium_relative_residual'])
                cases += 1
    for kwargs in (
        dict(forces=[-1.], winds=[0.], heights=[2.], cg_heights=[1.]),
        dict(forces=[1.], winds=[0.], heights=[0.], cg_heights=[0.]),
        dict(forces=[1.], winds=[0.], heights=[2.], cg_heights=[3.]),
        dict(forces=[math.nan], winds=[0.], heights=[2.], cg_heights=[1.]),
    ):
        try:
            _verified_rod_bound(stiffness=3.7e6, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid rod input accepted')
    return dict(seed=seed, cases=cases, maximum_relative_rod_difference=maximum_difference,
                maximum_relative_equilibrium_residual=maximum_residual)
