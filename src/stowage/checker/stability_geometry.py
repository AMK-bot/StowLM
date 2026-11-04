"""Batched evaluation of task 2.1's polygon hull, without changing its geometry.

Clipping retains the wet interval of each original polygon edge. Translating
the section origin onto the waterline gives its closing segments zero shoelace
cross, so their area and moments integrate without constructing a clipped vertex
array. This is an exact rearrangement of the same polygon formula; batching
only replaces Python loops. Waterplane quantities delegate to HullGeometry when
needed. Source: the polygon quadrature documented in vessel/hydrostatics.py.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import numpy as np
from stowage.vessel.hydrostatics import HullGeometry, Submerged
from stowage.checker.stability_budget import check_budget


def _contour_cycle(polygon):
    """Exact contour identity, independent of start vertex and orientation.

    Consecutive identical vertices describe zero-length edges and are removed;
    every other vertex and its cyclic adjacency remain unchanged. No rounding
    or geometric tolerance can turn an asymmetric contour into a mirror pair.
    """
    points=np.asarray(polygon)
    if points.ndim!=2 or points.shape[1]!=2 or not np.isfinite(points).all():
        return None
    points=points[np.any(points!=np.roll(points,1,axis=0),axis=1)]
    if len(points)<3:
        return None
    vertices=tuple(map(tuple,points))
    def rotation(sequence):
        first=min(sequence)
        return min(sequence[i:]+sequence[:i] for i,p in enumerate(sequence) if p==first)
    return min(rotation(vertices),rotation(vertices[::-1]))


def _bilateral_sections(sections):
    """Prove reflection of each section's entire contour multiset exactly."""
    from collections import Counter
    for section in sections:
        contours=[_contour_cycle(polygon) for polygon in section]
        mirrored=[_contour_cycle(np.asarray(polygon)*[-1.,1.]) for polygon in section]
        if any(contour is None for contour in contours+mirrored):
            return False
        if Counter(contours)!=Counter(mirrored):
            return False
    return True


class StabilityGeometry(HullGeometry):
    _STATE_CACHE_LIMIT = 2048
    _FULL_STATE_CACHE_LIMIT = 64

    def __init__(self, params, **kwargs):
        super().__init__(params, **kwargs)
        # The direct weather branch may reflect a positive free-trim solution
        # only after proving the hull/coaming contours mirror exactly. Actual
        # liquid LCG and VCG must independently agree on both sides in the
        # curve consumer; a bilateral hull alone cannot establish that.
        self._stability_table_bilateral=_bilateral_sections(self.sections)
        def corners(p):
            # Offsets may repeat a contour vertex. Remove duplicate nodes before
            # testing collinearity, or both copies of a genuine corner vanish.
            p = p[np.any(p != np.roll(p, 1, axis=0), axis=1)]
            before = p-np.roll(p, 1, axis=0)
            after = np.roll(p, -1, axis=0)-p
            cross = before[:, 0]*after[:, 1]-before[:, 1]*after[:, 0]
            return p[cross != 0.]  # Retain every curved offsets vertex.
        polygons = [(x, w, corners(p)) for x, w, ps in zip(self.x_m, self.integration_weights_m, self.sections) for p in ps]
        n = max(len(p) for _, _, p in polygons)
        self._p = np.array([np.concatenate((p, np.repeat(p[-1:], n-len(p), axis=0))) for _, _, p in polygons])
        self._q = np.roll(self._p, -1, axis=1)
        self._px = np.array([x for x, _, _ in polygons])
        self._pw = np.array([w for _, w, _ in polygons])
        self._prepare_edges()

    def _prepare_edges(self):
        """Prepare exact edge differences for fleet and independent test solids."""
        # Geometry replacement through this preparation boundary invalidates
        # every memoized physical state before even attempting the rebuild.
        self._edge_generation = getattr(self,'_edge_generation',0)+1
        self._state_cache = OrderedDict()
        self._full_state_cache = OrderedDict()
        self._py, self._pz = self._p[...,0], self._p[...,1]
        self._dy = self._q[...,0]-self._py
        self._dz = self._q[...,1]-self._pz
        self._edge_cross = self._py*self._q[...,1]-self._q[...,0]*self._pz
        # Padding repeats the last vertex of short contours (notably four-edge
        # coamings). Such edges have exactly zero length and zero contribution.
        # Compact only those exact zeros; retain every genuine offsets edge.
        active = (self._dy != 0.) | (self._dz != 0.)
        counts = np.count_nonzero(active, axis=1)
        if np.any(counts == 0):
            raise ValueError('hull contour has no nonzero edges')
        self._edge_starts = np.r_[0, np.cumsum(counts)[:-1]]
        self._edge_polygon = np.repeat(np.arange(len(counts)), counts)
        self._edge_next = np.arange(int(counts.sum()))+1
        self._edge_next[np.cumsum(counts)-1] = self._edge_starts
        self._flat_py, self._flat_pz = self._py[active], self._pz[active]
        self._flat_dy, self._flat_dz = self._dy[active], self._dz[active]
        self._flat_cross = self._edge_cross[active]

    def _state_key(self, ta, tf, heel, rho):
        # Instance-local, full-precision physical identity. No mass, callback,
        # rounded coordinates or checker report participates in this cache.
        return self._edge_generation,self.lpp_m,float(ta),float(tf),float(heel),float(rho)

    @staticmethod
    def _copy_state(value):
        # Submerged is frozen, but its arrays are mutable. Cached arrays remain
        # private; a caller can freely edit its own copies without cache taint.
        return replace(value,x_m=value.x_m.copy(),sectional_areas_m2=value.sectional_areas_m2.copy(),
                       integration_weights_m=value.integration_weights_m.copy())

    def _store_state(self, cache, key, value, limit):
        cached = self._copy_state(value)
        for array in (cached.x_m,cached.sectional_areas_m2,cached.integration_weights_m):
            array.flags.writeable = False
        cache[key] = cached
        cache.move_to_end(key)
        while len(cache) > limit:
            cache.popitem(last=False)
        return cached

    def submerged(self, t_aft_m, t_fwd_m, heel_rad=0., density_kg_m3=None):
        check_budget("direct hull integration")
        rho = self.density_kg_m3 if density_kg_m3 is None else density_kg_m3
        if not np.isfinite(rho) or rho <= 0:
            raise ValueError('positive finite water density required')
        normal, _, _ = self._axes(t_aft_m, t_fwd_m, heel_rad)
        key = self._state_key(t_aft_m,t_fwd_m,heel_rad,rho)
        if key in self._state_cache:
            self._state_cache.move_to_end(key)
            return self._copy_state(self._state_cache[key])
        vol,cg = self._submerged_integrals(normal,np.asarray(t_aft_m))
        value = self._store_state(self._state_cache,key,self._integral_result(vol,cg,rho),self._STATE_CACHE_LIMIT)
        return self._copy_state(value)

    def _submerged_many(self, drafts, heel, density):
        """Integrate exact finite-difference trial attitudes in one edge batch."""
        check_budget('batched direct hull integration')
        if not np.isfinite(density) or density <= 0:
            raise ValueError('positive finite water density required')
        drafts = np.asarray(drafts,dtype=float)
        if drafts.ndim != 2 or drafts.shape[1] != 2:
            raise ValueError('batched draughts must have shape (states, 2)')
        if not len(drafts):
            return []
        normals = np.asarray([self._axes(ta,tf,heel)[0] for ta,tf in drafts])
        keys = [self._state_key(ta,tf,heel,density) for ta,tf in drafts]
        answers,missing = {},{}
        for index,key in enumerate(keys):
            if key in self._state_cache:
                self._state_cache.move_to_end(key)
                answers[key] = self._state_cache[key]
            elif key not in missing:
                missing[key] = index
        if missing:
            indices = list(missing.values())
            volumes,centres = self._submerged_integrals(normals[indices],drafts[indices,0])
            for key,volume,centre in zip(missing,volumes,centres):
                answers[key] = self._store_state(self._state_cache,key,
                    self._integral_result(volume,centre,density),self._STATE_CACHE_LIMIT)
        # answers also retains entries evicted by an unusually large batch.
        return [self._copy_state(answers[key]) for key in keys]

    def _submerged_integrals(self, normal, ta):
        """Same edge quadrature for one attitude or leading attitude batches."""
        h = normal[...,2,None]*ta[...,None]-normal[...,0,None]*self._px
        d = self._flat_py*normal[...,1,None]+self._flat_pz*normal[...,2,None]-h[...,self._edge_polygon]
        dq = d[...,self._edge_next]
        inside, next_inside = d<=0, dq<=0
        crossing = inside != next_inside
        fraction = np.divide(d,d-dq,out=np.zeros_like(d),where=crossing)
        # Clip each original edge by its scalar interval [a,b]. Translating
        # the origin onto the waterline makes every closing waterline edge's
        # shoelace cross exactly zero mathematically. Therefore the unchanged
        # clipped-polygon area and first moments need no vertex compaction or
        # indirect successor gathering, even for concave/multiple contours.
        a = np.where(inside,0.,fraction)
        b = np.where(next_inside,1.,fraction)
        origin_scale = h/(normal[...,1,None]**2+normal[...,2,None]**2)
        oy,oz = origin_scale*normal[...,1,None],origin_scale*normal[...,2,None]
        edge_oy, edge_oz = oy[...,self._edge_polygon], oz[...,self._edge_polygon]
        cross = (b-a)*(self._flat_cross-edge_oy*self._flat_dz+edge_oz*self._flat_dy)
        # Segmented sums retain each original contour's area and moments before
        # applying its unchanged longitudinal integration weight.
        area = np.add.reduceat(cross,self._edge_starts,axis=-1)/2
        endpoint_sum = a+b
        my = (np.add.reduceat((2*(self._flat_py-edge_oy)+endpoint_sum*self._flat_dy)*cross,self._edge_starts,axis=-1)/6
              +oy*area)
        mz = (np.add.reduceat((2*(self._flat_pz-edge_oz)+endpoint_sum*self._flat_dz)*cross,self._edge_starts,axis=-1)/6
              +oz*area)
        px,pw=self._px,self._pw
        vol = area @ pw
        moments = np.stack([(area*px) @ pw,my @ pw,mz @ pw],axis=-1)
        cg = np.divide(moments,vol[...,None],out=np.zeros_like(moments),where=vol[...,None] > 1e-12)
        return vol,cg

    def _integral_result(self, vol, cg, rho):
        # Solver residuals only need volume and centroid. Use the base implementation
        # explicitly for GM/waterplane data after convergence.
        return Submerged(float(vol), float(rho*vol), *map(float, cg), 0., self.lpp_m/2, 0., 0., self.x_m,
                         np.array([]), self.integration_weights_m)

    def full_submerged(self, ta, tf, heel=0., density=None):
        check_budget("direct hydrostatic properties")
        rho = self.density_kg_m3 if density is None else density
        if not np.isfinite(rho) or rho <= 0:
            raise ValueError('positive finite water density required')
        self._axes(ta,tf,heel)
        key = self._state_key(ta,tf,heel,rho)
        if key in self._full_state_cache:
            self._full_state_cache.move_to_end(key)
            return self._copy_state(self._full_state_cache[key])
        value = self._store_state(self._full_state_cache,key,
            super().submerged(ta,tf,heel,rho),self._FULL_STATE_CACHE_LIMIT)
        return self._copy_state(value)

    def free_trim_curve(self, angles, mass, lcg, kg, density, cg_callback=None,
                        initial_drafts=None, stop_when=None):
        """Continue free-trim equilibrium, retaining the physical longitudinal KG.

        Drafts are waterline intercepts and grow as 1/cos(heel) near 90 degrees.
        Solve instead for mean intercept times cos(heel), a finite immersion
        coordinate, and the aft-minus-forward draft difference. This is an
        algebraic coordinate change, not a hull or moment approximation. Roots
        meet task 2.1's scaled 2e-6 volume/longitudinal equilibrium tolerance.
        ``initial_drafts`` allows callers to continue successive curve segments.
        ``stop_when(phi, gz, drafts)`` can end evaluation after a flooding or
        vanishing-angle bracket has been collected, without solving later heels.
        Exactly 90 degrees is outside HullGeometry's attitude parametrisation.
        """
        from scipy.optimize import root, least_squares, brentq
        guess = np.asarray(initial_drafts if initial_drafts is not None else
                           [self.design_draught_m, self.design_draught_m], dtype=float)
        if guess.shape != (2,) or not np.isfinite(guess).all():
            raise ValueError('initial_drafts must contain two finite draughts')
        gz, conditions = [], []
        for phi in angles:
            cosine = np.cos(phi)
            # Validate the angle before any division by its cosine.
            self._axes(*guess, phi)
            def drafts(v):
                mean = v[0]/cosine
                return np.array([mean+v[1]/2, mean-v[1]/2])
            # MINPACK repeats its initial point and the accepted root; later
            # convergence/fallback checks repeat them again. Reuse only exact
            # waterline identities within this heel, density and fixed
            # loading callback. No rounded state or cross-condition cache exists.
            states = {}
            def remember(ta,tf,s):
                _, along, _ = self._axes(ta, tf, phi)
                cg = (cg_callback(phi, np.arctan((ta-tf)/self.lpp_m))
                      if cg_callback else [lcg, 0., kg])
                value = [(s.displacement_kg-mass)/mass,
                         np.dot(np.asarray(s.centroid_m)-cg, along)/self.lpp_m]
                states[ta,tf] = value,s
                return value
            def residual(v):
                check_budget('direct free-trim residual')
                ta, tf = drafts(v)
                key = ta,tf
                if key not in states:
                    remember(ta,tf,self.submerged(ta,tf,phi,density))
                return states[key][0]
            jacobians = {}
            def jacobian(v):
                check_budget('direct free-trim finite differences')
                key = tuple(v)
                if key not in jacobians:
                    # Match MINPACK's forward-difference scale for eps=1e-8.
                    # The same exact edge integral evaluates both perturbed
                    # attitudes together; acceptance still checks actual residual.
                    step = 1e-4*np.abs(v)
                    step[step == 0.] = 1e-4
                    trials = np.repeat(np.asarray(v)[None,:],2,axis=0)
                    trials[np.arange(2),np.arange(2)] += step
                    pairs = [drafts(trial) for trial in trials]
                    missing = list(dict.fromkeys(tuple(pair) for pair in pairs if tuple(pair) not in states))
                    if missing:
                        integrated = self._submerged_many(missing,phi,density)
                        for (ta,tf),state in zip(missing,integrated):
                            remember(ta,tf,state)
                    jacobians[key] = (np.asarray([states[tuple(pair)][0] for pair in pairs])-residual(v)).T/step
                return jacobians[key]
            seed = np.array([np.mean(guess)*cosine, guess[0]-guess[1]])
            solved = root(residual, seed, jac=jacobian, tol=1e-8)
            if not np.isfinite(solved.x).all() or np.linalg.norm(residual(solved.x)) > 2e-6:
                solved = least_squares(residual, seed, diff_step=1e-4,
                    xtol=2e-10,ftol=2e-10,gtol=2e-10,max_nfev=100)
            if np.linalg.norm(residual(solved.x)) > 2e-6:
                def at_trim(trim):
                    # In scaled immersion coordinates this bracket stays finite
                    # even when the centreline waterline intercept diverges.
                    limit = self.max_z_m+2*self.beam_m+abs(trim*cosine)
                    immersion = brentq(lambda q:residual([q, trim])[0],
                                       -limit,limit,xtol=1e-9)
                    return np.array([immersion, trim])
                for extent in (.05,.2,.5,1.,2.,10.):
                    lo,hi = -self.lpp_m*extent,self.lpp_m*extent
                    if residual(at_trim(lo))[1]*residual(at_trim(hi))[1] <= 0:
                        trim = brentq(lambda trim:residual(at_trim(trim))[1],lo,hi,xtol=1e-7)
                        solved.x = at_trim(trim)
                        break
            if np.linalg.norm(residual(solved.x)) > 2e-6:
                raise ValueError(f'free-trim curve equilibrium did not converge at {np.degrees(phi):.4g} degrees')
            guess = drafts(solved.x)
            s = states[tuple(guess)][1]
            _, _, transverse = self._axes(*guess, phi)
            gz.append(float(np.dot(np.asarray(s.centroid_m)-[lcg, 0., kg], transverse)))
            conditions.append(tuple(guess))
            if stop_when is not None and stop_when(phi, gz[-1], conditions[-1]):
                break
        return np.asarray(gz), np.asarray(conditions).reshape(-1, 2)
