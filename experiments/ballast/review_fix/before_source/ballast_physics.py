"""Fast exact loading-condition probes for successive ballast corrections.

Cargo and fixed liquids do not change during ballast search, so their mass
moments are accumulated once. Adjustable tank moments and free surfaces are
recomputed at every fill, including the discontinuity between slack and pressed
full tanks. The hull is the checker's actual polygon hull. Finite-angle liquid
redistribution is iterated with hull equilibrium; upright projected GM uses the
same free-trim calculation as the linear model's exact calibration reference.
The default method .1 uses finite liquid transfer, rather than a small-angle
list or virtual-KG equilibrium surrogate. Explicit method .2 uses the same
Code-permitted virtual-KG policy as the production checker.

Probes follow the production checker's selected free-surface method, including
its lightship-based small-tank exemption (IS Code B3.1.12). They reuse only the
checker's qualified hull/liquid tables, with direct fallback outside coverage
and at loading boundaries. These numerical search probes never authorize a
candidate: the full checker remains the acceptance gate.

Only loading limits are evaluated here. GZ areas, weather, strength, visibility
and lashing still require the full checker before accepting any candidate.
Source: IMO MSC.267(85), Part B 3.1 (separate initial-GM and finite-angle liquid
corrections), implemented in checker.stability_loading and linear.physics.
"""
from __future__ import annotations

import math
import json
import os
import time
from dataclasses import replace
from functools import lru_cache

import numpy as np

from stowage.checker import stability
from stowage.checker.stability_loading import (
    CONSUMABLE_KINDS, CorrectionTank, liquid_cg,
    loading_condition,
)
from stowage.solver.ballast_deadline import ExactDeadline, exact_budget
from stowage.vessel.tanks import TankGeometry


@lru_cache(maxsize=4)
def _adjustable_solids(parameter_payload, tank_ids):
    """Reuse immutable solids only for identical complete parameter content.

    The exact canonical JSON, rather than a file timestamp, vessel name or
    component version, binds both explicit sections and hull-derived bounds.
    The section provider itself returns immutable arrays. This small cache
    avoids rebuilding/hashing every solid between stows on the same ship.
    """
    params = json.loads(parameter_payload)
    raw = {tank['id']: tank for tank in params.get('tanks', [])}
    return tuple(TankGeometry.from_params(raw[tid], ship_params=params)
                 for tid in tank_ids)


class ExactContext:
    """One fixed cargo stow, with adjustable tanks ordered by tank identifier."""

    def __init__(self, instance, ship, deadline):
        self.instance, self.ship, self.deadline = instance, ship, deadline
        with exact_budget(deadline):
            self.geometry, self.params = stability._context(instance.vessel)
            self.direct_geometry = self.geometry
            self.method = int(os.environ.get('STOWAGE_STABILITY_FREE_SURFACE_METHOD', '1'))
            if self.method not in (1, 2):
                raise ValueError('free-surface method must be 1 or 2')
            self.tanks = sorted((t for t in instance.vessel.tanks if t.adjustable),
                                key=lambda t: t.id)
            self.capacity = np.array([t.capacity_m3*t.content_density_kg_m3 for t in self.tanks])
            adjustable = {t.id for t in self.tanks}
            self.fixed_ship = ship.model_copy(update={'tank_masses_kg': {
                tid: mass for tid, mass in ship.tank_masses_kg.items()
                if tid not in adjustable}})
            self.base = loading_condition(instance, self.fixed_ship,
                                          geometry=self.geometry, ship_params=self.params,
                                          free_surface_method=self.method,
                                          precomputed_soundings=True, small_tank_exemption=True)
            if self.base.errors:
                raise ValueError('; '.join(self.base.errors))
            ids = tuple(tank.id for tank in self.tanks)
            self.solids = dict(zip(ids, _adjustable_solids(
                json.dumps(self.params, sort_keys=True), ids)))
            # Compute the same canonical qualification identity once from this
            # context's exact parameter tree. In particular do not borrow a
            # hull identity when a caller has supplied different tank params.
            from stowage.checker.stability_liquid import metadata
            self.liquid_metadata = metadata(self.params)
            self.soundings = {}
            self.base_moments = self.base.mass_kg*np.array(
                [self.base.lcg_m, self.base.tcg_m, self.base.kg_m])
            # Adjustable consumable kinds change which fixed tanks need their
            # all-filling-range envelope. Retain the checker's complete builder
            # for that uncommon model rather than freezing its carried-kind set.
            self.variable_consumables = any(t.kind in CONSUMABLE_KINDS for t in self.tanks)
            self.rho = instance.port.water_density_kg_m3
            from stowage.checker.stability_tables import cached_geometry
            from zipfile import BadZipFile
            try:
                accelerated = cached_geometry(self.geometry, self.params, self.rho)
            except (OSError, ValueError, KeyError, TypeError, BadZipFile):
                accelerated = None
            if accelerated is not None:
                self.geometry = accelerated

    def _attach_tables(self, condition):
        if self.method == 1 and os.environ.get('STOWAGE_STABILITY_LIQUID_DIRECT') != '1':
            from stowage.checker.stability_liquid import attach_tables
            return attach_tables(condition, self.params,
                vessel_hash=getattr(self.direct_geometry, '_stability_vessel_hash', None),
                _metadata_value=self.liquid_metadata)
        return condition

    def _check_clock(self):
        if time.monotonic() >= self.deadline:
            raise ExactDeadline

    def _upright(self, mass, cg):
        if self.geometry is not self.direct_geometry:
            try:
                return self.geometry._condition(mass, cg, self.rho, 0.)[0]
            except ValueError:
                pass
        return self.direct_geometry._solve(mass, cg[0], 0., cg[2], self.rho, 0.)

    def _floating(self, mass, cg):
        try:
            result = self.geometry.equilibrium(mass, *cg, self.rho)
        except ValueError:
            if self.geometry is self.direct_geometry:
                raise
            return self.direct_geometry.equilibrium(mass, *cg, self.rho)
        if (self.geometry is not self.direct_geometry and
                stability._floating_near_limit(self.instance, result)):
            return self.direct_geometry.equilibrium(mass, *cg, self.rho)
        return result

    def condition(self, fractions):
        """Exact mass and free-surface bookkeeping with cached fixed cargo."""
        self._check_clock()
        fractions = np.asarray(fractions, dtype=float)
        if fractions.shape != self.capacity.shape or not np.isfinite(fractions).all():
            raise ValueError('invalid adjustable tank fractions')
        fractions = np.clip(fractions, 0., 1.)
        masses = fractions*self.capacity
        if self.variable_consumables:
            candidate = self.ship.model_copy(update={'tank_masses_kg': {
                **self.fixed_ship.tank_masses_kg,
                **{tank.id: float(mass) for tank, mass in zip(self.tanks, masses)}}})
            result = loading_condition(self.instance, candidate, geometry=self.geometry,
                                       ship_params=self.params, free_surface_method=self.method,
                                       precomputed_soundings=True, small_tank_exemption=True)
            if result.errors:
                raise ValueError('; '.join(result.errors))
            return self._attach_tables(result)
        mass = self.base.mass_kg+float(masses.sum())
        moments = self.base_moments.copy()
        corrections, actual = list(self.base.tanks), list(self.base.actual_tanks)
        omitted = list(self.base.omitted_tanks)
        for tank, tank_mass in zip(self.tanks, masses):
            self._check_clock()
            solid = self.solids[tank.id]
            volume = min(tank_mass/tank.content_density_kg_m3, solid.capacity_m3)
            from stowage.checker.stability_sounding import sounding
            key = (tank.id, float(volume))
            row = self.soundings.get(key)
            if row is None:
                row = sounding(solid, volume)
                if len(self.soundings) >= 512:
                    self.soundings.clear()
                self.soundings[key] = row
            moments += tank_mass*np.array([row.lcg_m, row.tcg_m, row.vcg_m])
            if tank_mass > 0:
                actual.append(CorrectionTank(tank.id, tank.content_density_kg_m3,
                                             volume, 0., solid))
            # Same 98%-pressed-up convention and actual slack inertia as 3.1.
            if 0 < volume < .98*tank.capacity_m3:
                fsm = row.fsm_m4*tank.content_density_kg_m3
                # Identical B3.1.12 exemption to the production checker. The
                # denominator is lightship, never the current loaded mass.
                if fsm/self.instance.vessel.hull.lightship_mass_kg < .01:
                    omitted.append(tank.id)
                    continue
                corrections.append(CorrectionTank(tank.id, tank.content_density_kg_m3,
                    volume, fsm, solid))
        fsm = sum(tank.fsm_kgm for tank in corrections)
        x, y, z = map(float, moments/mass)
        condition = replace(self.base, mass_kg=mass, lcg_m=x, tcg_m=y, kg_m=z,
            fsc_m=fsm/mass, fsm_kgm=fsm, tanks=tuple(corrections),
            actual_tanks=tuple(actual), omitted_tanks=tuple(omitted),
            liquid_tables=None, liquid_usage={}, upright_cache={}, cg_cache={},
            liquid_cg_groups=None)
        return self._attach_tables(condition)

    def _condition_equilibrium(self, condition):
        """Actual finite-liquid equilibrium, retaining signed trim and list."""
        cg = np.array([condition.lcg_m, condition.tcg_m, condition.kg_m+
            (condition.fsc_m if self.method == 2 else 0.)])
        for _ in range(20):
            self._check_clock()
            eq = self._floating(condition.mass_kg, cg)
            if self.method == 2:
                return eq
            updated = np.array(liquid_cg(condition, eq.heel_rad,
                                         math.atan(eq.trim_m/self.geometry.lpp_m)))
            if np.linalg.norm(updated-cg) < 1e-7:
                return eq
            cg = updated
        raise ValueError('finite-liquid reference equilibrium did not converge')

    def equilibrium(self, fractions):
        """Return the signed exact loading equilibrium for a search probe.

        This skips upright GM and stability curves, but includes the same slack
        free surfaces and finite-liquid transfer as ``margins``. It never
        certifies a plan; callers must still pass the complete checker.
        """
        with exact_budget(self.deadline):
            return self._condition_equilibrium(self.condition(fractions))

    def margins(self, fractions, active_keys=None):
        """Signed loading margins; positive means passing, never acceptance.

        ``active_keys`` avoids upright GM work when only trim/draft/list need
        correction. List clearance is divided by the ship's maximum list;
        the remaining reported clearances are in meters. GM is measured first:
        its negative margin guides recovery even when an unstable list solution
        is unavailable. Failed floating solves never create draft/list values.
        """
        with exact_budget(self.deadline):
            condition = self.condition(fractions)
            geometry, hull = self.geometry, self.instance.vessel.hull
            requested = None if active_keys is None else set(active_keys)
            result = {}
            if (requested is None or 'stability/gm_m' in requested or
                    'stability/weather_period' in requested):
                # Upright initial stiffness is defined even if an unstable
                # floating/list solution cannot be found. Compute it first so
                # ballast search can recover from negative GM without inventing
                # draught or list values for a nonexistent equilibrium.
                upright_cg = np.array([condition.lcg_m, 0., condition.kg_m+
                    (condition.fsc_m if self.method == 2 else 0.)])
                for _ in range(20):
                    self._check_clock()
                    upright = self._upright(condition.mass_kg, upright_cg)
                    if self.method == 2:
                        break
                    updated = np.array(liquid_cg(condition, 0.,
                                                 math.atan(upright.trim_m/geometry.lpp_m)))
                    if np.linalg.norm(updated[[0, 2]]-upright_cg[[0, 2]]) < 1e-7:
                        break
                    upright_cg = updated
                else:
                    raise ValueError('upright liquid reference equilibrium did not converge')
                submerged = geometry.full_submerged(upright.t_aft_m, upright.t_fwd_m, 0., self.rho)
                normal = geometry.waterplane_normal(upright.t_aft_m, upright.t_fwd_m, 0.)
                gm = float(np.dot(np.asarray(submerged.centroid_m)-
                    [condition.lcg_m, 0., condition.kg_m], normal))+submerged.bm_t_m-condition.fsc_m
                overrides = self.instance.rule_overrides
                loading_minimum = float(overrides.get('iscode.loading.gm', {}).get(
                    'minimum_gm', hull.min_gm_m))
                code_minimum = float(overrides.get('iscode.a2_2.initial_gm', {}).get('minimum', .15))
                if not math.isfinite(loading_minimum) or not math.isfinite(code_minimum):
                    raise ValueError('nonfinite stability loading override')
                result['stability/gm_m'] = gm-max(loading_minimum, code_minimum)
                if requested == {'stability/gm_m'}:
                    return result
                if requested is None and result['stability/gm_m'] < 0.:
                    # This exact violated prerequisite already forbids the
                    # candidate. Repair it before spending time finding a
                    # possibly unstable heeled equilibrium or checking curves.
                    return result
            try:
                eq = self._condition_equilibrium(condition)
            except ValueError:
                if 'stability/gm_m' not in result:
                    raise
                if result['stability/gm_m'] >= 0.:
                    # An unavailable equilibrium is a search failure flag,
                    # never a physical margin or an infeasibility certificate.
                    result['stability/equilibrium_unavailable'] = -1.
                return result
            result.update({
                'stability/trim_m': hull.max_trim_m-abs(eq.trim_m),
                'stability/list_rad': (hull.max_list_rad-abs(eq.heel_rad))/max(hull.max_list_rad, 1e-8),
                'stability/draught_aft_m': hull.max_draught_m-eq.t_aft_m,
                'stability/draught_fwd_m': hull.max_draught_m-eq.t_fwd_m,
                'stability/draught_fwd_min_m': eq.t_fwd_m-hull.min_draught_fwd_m,
            })
            if self.instance.port.max_draught_m is not None:
                result['stability/port_draught_m'] = self.instance.port.max_draught_m-max(eq.t_aft_m, eq.t_fwd_m)
            if requested is not None and any(key.startswith('stability/weather_') for key in requested):
                # These continuous applicability prerequisites help repair a
                # failed discrete weather-table flag. They do not replace the
                # full weather area/steady-equilibrium/coverage calculation.
                draught, beam = eq.mean_draught_m, hull.beam_m
                effective_kg = condition.kg_m+condition.fsc_m
                result.update({
                    'stability/weather_beam_draught': 1.-beam/(3.5*draught)-1e-8,
                    'stability/weather_kg_lower': effective_kg/draught-.7,
                    'stability/weather_kg_upper': 1.5-effective_kg/draught,
                    'stability/weather_r': .73+.6*(effective_kg-draught)/draught,
                })
                if 'stability/weather_period' in requested:
                    from stowage.checker.stability_criteria import _waterline_length
                    lwl = _waterline_length(geometry, eq.t_aft_m, eq.t_fwd_m, hull.lpp_m)
                    # APPROXIMATION: the Code-permitted rolling-period estimate
                    # is identical to the full checker (A 2.3.4), not measured T.
                    coefficient = .373+.023*beam/draught-.043*lwl/100
                    period = 2.*coefficient*beam/math.sqrt(gm) if gm > 0 else math.inf
                    result['stability/weather_period'] = 1.-period/20.-1e-8
            return result if requested is None else {k: v for k, v in result.items() if k in requested}
