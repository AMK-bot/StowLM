"""Exact booking-mix expectations for the random generator's first route.

The source is task 2.5's ``generate.cargo`` sampling formula and the supplied
``params/cargo`` profile: choose equipment and POD by their normalized shares,
then empty/full. Loaded gross mass is triangular, with POD multiplying payload
(gross minus tare), clipped to the configured loaded minimum/maximum and rating.
APPROXIMATION: D60 conditions each equipment/POD loaded distribution on gross
mass <= the synthetic securing limit, retaining the original triangular shape
and payload/POD transformation. Its mean is integrated over that surviving
support and normalized by its probability, not computed from a rescaled or
clamped triangular distribution. Equipment/POD shares and CSC ratings remain
the source values. Cargo/completion rejection and TEU stopping can further
change the accepted population; neither is included here.

Two-point Gauss integration is exact for each polynomial piece: the triangular
density is linear and clipped transformed mass is piecewise linear. Splitting
at the triangular mode and clipping thresholds therefore integrates a quadratic
exactly, apart from floating-point roundoff. No cargo samples are drawn.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
import math

from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG, triangular_cdf


def _value(record, key: str) -> float:
    value = float(record[key]["value"])
    if not math.isfinite(value):
        raise ValueError(f"{key} must be finite")
    return value


def _shares(rows):
    weights = [_value(row, "share") for row in rows]
    total = sum(weights)
    if not rows or any(weight < 0 for weight in weights) or total <= 0:
        raise ValueError("shares must be nonnegative with a positive total")
    return [weight / total for weight in weights]


def _loaded_mean(low, mode, high, tare, rating, multiplier):
    if not math.isfinite(multiplier) or multiplier < 0:
        raise ValueError("payload multiplier must be finite and nonnegative")
    ceiling = min(high, rating)

    def gross(x):
        return min(ceiling, max(low, tare + (x - tare) * multiplier))

    if low == high:
        answer = gross(low)
        if answer > MAX_GENERATED_GROSS_MASS_KG:
            raise ValueError("D60 loaded distribution has no mass within the securing limit")
        return answer
    # APPROXIMATION: D60 discards the old transformed tail, including any
    # clipping atom above the limit. Keep the source density's original high
    # and mode; divide by the exact surviving probability instead of changing
    # its triangular parameters or adding an artificial atom at the cap.
    end = high
    probability = 1.
    if ceiling > MAX_GENERATED_GROSS_MASS_KG:
        if low > MAX_GENERATED_GROSS_MASS_KG:
            raise ValueError("D60 loaded distribution has no mass within the securing limit")
        if multiplier > 0:
            end = min(high, tare + (MAX_GENERATED_GROSS_MASS_KG - tare) / multiplier)
            probability = triangular_cdf(end, low, mode, high)
            if probability <= 0:
                raise ValueError("D60 loaded distribution has no mass within the securing limit")
    cuts = {low, end}
    if low < mode < end:
        cuts.add(mode)
    if multiplier != 0:
        for bound in (low, ceiling):
            threshold = tare + (bound - tare) / multiplier
            if low < threshold < end:
                cuts.add(threshold)
    cuts = sorted(cuts)
    integral = 0.0
    for left, right in zip(cuts, cuts[1:]):
        mid, half = (left + right) / 2, (right - left) / 2

        def density(x):
            if x < mode:
                return 2 * (x - low) / ((high - low) * (mode - low))
            return 2 * (high - x) / ((high - low) * (high - mode))

        offset = half / math.sqrt(3)
        integral += half * sum(gross(x) * density(x) for x in (mid - offset, mid + offset))
    return integral / probability


def expected_cargo_mix(params: Mapping) -> tuple[float, float]:
    """Return ``(mean_teu_per_box, mean_gross_kg_per_box)`` for route zero.

    Uses the same wrapped numeric parameter records as task 2.5. Equipment and
    first-route POD shares are normalized independently; route-selection shares
    are irrelevant because the random generator fixes the first configured route.
    Empty gross uses ``empty_weight_kg.value`` exactly as the cargo provider does.
    """
    equipment = params["equipment"]
    pods = params["rotations"][0]["pods"]
    equipment_weights, pod_weights = _shares(equipment), _shares(pods)
    mean_teu = mean_mass = 0.0
    for equipment_weight, item in zip(equipment_weights, equipment):
        tare, rating = _value(item, "tare_kg"), _value(item, "max_gross_kg")
        empty_share = _value(item, "empty_share")
        empty_mass = _value(item["empty_weight_kg"], "value")
        weights = item["loaded_weight_kg"]
        low, mode, high = (_value(weights, key) for key in ("min", "mode", "max"))
        if (weights["family"] != "triangular" or not 0 <= empty_share <= 1
                or not 0 <= tare <= low <= mode <= high <= rating
                or not tare <= empty_mass <= rating
                or (empty_share > 0 and empty_mass > MAX_GENERATED_GROSS_MASS_KG)):
            raise ValueError("invalid cargo equipment weight parameters")
        loaded = (sum(share * _loaded_mean(low, mode, high, tare, rating,
                                          _value(pod, "payload_weight_multiplier"))
                      for share, pod in zip(pod_weights, pods))
                  if empty_share < 1 else 0.)
        mean_mass += equipment_weight * (empty_share * empty_mass + (1 - empty_share) * loaded)
        mean_teu += equipment_weight * {"2": 1.0, "4": 2.0, "L": 2.25}[item["iso_size_type"][0]]
    return mean_teu, mean_mass


@dataclass(frozen=True)
class ExportPolicy:
    """Synthetic full-ship export budget, in kg and actual available TEU.

    APPROXIMATION: aim at 85% TEU and 93% design displacement. Reserve at least
    2% of ballast tank mass capacity, and never exceed that capacity. Ballast is
    distributed at one common fill fraction (symmetric source tank pairs); the
    ballast finisher must still verify trim, strength and stability. Cargo mass
    may use at most 90% of DWT minus consumables, stores and planned ballast.
    This is a scenario initializer, not a checked departure/ballast plan.

    D64 rev 3.1 (rule freeze ON only): the plan's departure ballast in port
    keeps the anti-heeling pair total at its 50% arrival fill. That mass
    (``mandatory_ballast_kg``) is always part of departure ballast, and the
    pair's unused half can't hold operating ballast: operating capacity is
    ``ballast_capacity_kg - mandatory_ballast_capacity_kg + mandatory_ballast_kg``.
    The 2% floor is met by the mandatory pair mass when that is larger.
    Defaults (no pair) reproduce the original policy exactly.
    """
    capacity_teu: int
    design_displacement_kg: float
    lightship_kg: float
    consumables_kg: float
    stores_kg: float
    ballast_capacity_kg: float
    mean_teu: float
    mean_mass_kg: float
    mandatory_ballast_kg: float = 0.
    mandatory_ballast_capacity_kg: float = 0.
    # D77 (rule freeze ON): aim two SD inside the screen's [0.75, 0.90] cargo
    # band instead of its top edge, and never book less departure ballast than
    # the ship's own lightest C-condition carries. Defaults reproduce D37.
    target_cargo_fraction: float | None = None
    minimum_departure_ballast_kg: float = 0.

    @property
    def base_kg(self):
        return self.lightship_kg + self.consumables_kg + self.stores_kg

    @property
    def d64_reserved(self):
        return self.mandatory_ballast_capacity_kg > 0

    @property
    def operating_ballast_capacity_kg(self):
        if not self.d64_reserved:
            return self.ballast_capacity_kg
        return self.ballast_capacity_kg - self.mandatory_ballast_capacity_kg + self.mandatory_ballast_kg

    def ballast_kg(self, cargo_mass_kg):
        if not self.d64_reserved:
            return min(self.ballast_capacity_kg, max(0.02 * self.ballast_capacity_kg,
                0.93 * self.design_displacement_kg - self.base_kg - cargo_mass_kg))
        floor = max(self.mandatory_ballast_kg, 0.02 * self.ballast_capacity_kg,
                    self.minimum_departure_ballast_kg)
        return min(self.operating_ballast_capacity_kg, max(floor,
            0.93 * self.design_displacement_kg - self.base_kg - cargo_mass_kg))

    def cargo_budget_kg(self, cargo_mass_kg):
        return self.design_displacement_kg - self.base_kg - self.ballast_kg(cargo_mass_kg)

    @property
    def target_count(self):
        teu_count = math.floor(0.85 * self.capacity_teu / self.mean_teu)
        budget = self.cargo_budget_kg(teu_count * self.mean_mass_kg)
        # D77 replaces D37's 0.90 with f (two SD inside the screen's band).
        fraction = 0.90 if self.target_cargo_fraction is None else self.target_cargo_fraction
        return min(teu_count, math.floor(fraction * budget / self.mean_mass_kg))

    @property
    def mass_limited(self):
        return self.target_count * self.mean_teu < 0.75 * self.capacity_teu

    def is_export_count(self, count):
        # Small explicit quantities remain diagnostic profiles; never pretend
        # they are loaded export departures or silently increase their demand.
        return count >= math.floor((0.90 * self.target_count if self.mass_limited else
                                   0.75 * self.capacity_teu / self.mean_teu))

    def audit(self, cargo_mass_kg, teu):
        ballast = self.ballast_kg(cargo_mass_kg)
        budget = self.cargo_budget_kg(cargo_mass_kg)
        displacement = self.base_kg + cargo_mass_kg + ballast
        result = dict(design_displacement_kg=self.design_displacement_kg,
                    departure_displacement_kg=displacement,
                    design_displacement_fraction=displacement / self.design_displacement_kg,
                    cargo_mass_kg=cargo_mass_kg, cargo_mass_budget_kg=budget,
                    cargo_mass_fraction=cargo_mass_kg / budget,
                    ballast_reserve_kg=ballast, capacity_teu=self.capacity_teu,
                    teu_fraction=teu / self.capacity_teu, mass_limited=self.mass_limited)
        if self.d64_reserved:
            # Keys appear only for a D64 policy, so OFF audits stay byte-identical.
            result.update(mandatory_ballast_reserve_kg=self.mandatory_ballast_kg,
                          operating_ballast_capacity_kg=self.operating_ballast_capacity_kg,
                          minimum_departure_ballast_kg=self.minimum_departure_ballast_kg,
                          target_cargo_fraction=self.target_cargo_fraction)
        return result


@lru_cache(maxsize=3)
def _fleet_design(content: bytes):
    """Cache small design records by their exact source bytes (D30)."""
    import tomllib
    source = tomllib.loads(content.decode("utf-8"))
    return (frozenset(c["id"] for c in source["cells"]),
            source["derived_hydrostatics"]["displacement_kg"],
            source["hull"]["lightship_mass_kg"], source["tanks"],
            tuple(source.get("tank_pairs", ())))


def full_export_policy(vessel, params) -> ExportPolicy | None:
    """Read the declared whole-ship design; return None for a true sub-ship.

    Geometry-only diagnostics read the same authoritative fleet parameters,
    without inventing a HullModel. Their mass-budget arithmetic is not physical
    verification. A full-physics Instance must carry actual tanks and hull.
    """
    from stowage.params import params_dir
    from stowage.vessel.fleet import list_fleet
    from stowage.vessel.fleet_conditions import slice_condition

    from stowage.conventions import canonical_vessel_id

    root = canonical_vessel_id(vessel.slice_of or vessel.id)
    if root not in list_fleet():
        return None
    content = (params_dir("fleet") / f"{root}.toml").read_bytes()
    cells, source_design, source_lightship, source_tanks, source_pairs = _fleet_design(content)
    if {c.id for c in vessel.cells} != cells:
        return None
    info = slice_condition(vessel)
    design = (info["design_displacement_kg"] if info else
              source_design)
    tanks = vessel.tanks or source_tanks
    def value(tank, key):
        return tank[key] if isinstance(tank, dict) else getattr(tank, key)
    consumables = sum(0.97 * value(t, "capacity_m3") * value(t, "content_density_kg_m3")
                      for t in tanks if not value(t, "adjustable"))
    ballast = sum(value(t, "capacity_m3") * value(t, "content_density_kg_m3")
                  for t in tanks if value(t, "adjustable"))
    mean_teu, mean_mass = expected_cargo_mix(params)
    reserve = {}
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    if rule_freeze_v1_enabled():
        # D64 rev 3.1: the plan's in-port departure ballast keeps the pair at
        # its 50% arrival total. Read the same tanks/pairs the capacity uses.
        pairs = vessel.tank_pairs if vessel.tanks else source_pairs
        mandatory = _anti_heeling_tanks(pairs)
        held = [t for t in tanks if value(t, "id") in mandatory]
        if len(held) != len(mandatory) or any(not value(t, "adjustable") for t in held):
            raise ValueError("D64 anti-heeling pair must name adjustable ballast tanks")
        capacity = sum(value(t, "capacity_m3") * value(t, "content_density_kg_m3") for t in held)
        if capacity > 0:
            reserve = dict(mandatory_ballast_kg=.5 * capacity, mandatory_ballast_capacity_kg=capacity)
        # D77: APPROXIMATION (synthetic booking policy). The lightest C1-C3
        # loading-condition ballast of this ship is the departure-ballast floor;
        # 0.815 is the middle of D77's f = 0.81-0.82 band target.
        reserve.update(target_cargo_fraction=D77_TARGET_CARGO_FRACTION,
                       minimum_departure_ballast_kg=c_condition_ballast_floor_kg(root))
    return ExportPolicy(len(vessel.cells), design,
        vessel.hull.lightship_mass_kg if vessel.hull else source_lightship,
        consumables, sum(load.mass_kg for load in vessel.fixed_loads), ballast, mean_teu, mean_mass,
        **reserve)


D77_TARGET_CARGO_FRACTION = 0.815


def c_condition_ballast_floor_kg(root: str) -> float:
    """Lightest adjustable-ballast mass over the ship's C1-C3 loading conditions.

    Rebuilt on the current tanks by ``fleet_manual.standard_manual_conditions``
    (ON: the requalified D64 operating refits, which preserve ballast mass); a
    stale witness raises rather than guessing. Cached by parameter bytes (D30).
    """
    from stowage.vessel.fleet import _parameter_bytes
    from stowage.rules.catalogue import rule_freeze_v1_enabled
    return _c_condition_ballast_floor(root, _parameter_bytes(root), rule_freeze_v1_enabled())


@lru_cache(maxsize=6)
def _c_condition_ballast_floor(root: str, payload: bytes, activated: bool) -> float:
    import tomllib
    from stowage.vessel.fleet import load_vessel
    from stowage.vessel.fleet_manual import standard_manual_conditions

    vessel = load_vessel(root)
    adjustable = {tank.id for tank in vessel.tanks if tank.adjustable}
    recipes = standard_manual_conditions(vessel, tomllib.loads(payload.decode("utf-8")))
    masses = [sum(mass for tid, mass in recipe["departure_tank_masses_kg"].items() if tid in adjustable)
              for recipe in recipes]
    if not masses:
        raise ValueError(f"D77 needs the {root} C-conditions for its departure-ballast floor")
    return min(masses)


def _anti_heeling_tanks(pairs) -> set[str]:
    def value(pair, key):
        return pair[key] if isinstance(pair, dict) else getattr(pair, key)
    return {value(pair, side) for pair in pairs if value(pair, "role") == "anti_heeling"
            for side in ("port_tank", "starboard_tank")}


def booking_tank_masses(vessel, boxes, params):
    """C3 consumables plus bounded export ballast; small/slice profiles unchanged."""
    from stowage.vessel.fleet_conditions import departure_tank_masses
    masses = departure_tank_masses(vessel)
    policy = full_export_policy(vessel, params)
    if policy and policy.is_export_count(len(boxes)):
        ballast = policy.ballast_kg(sum(box.gross_mass_kg for box in boxes))
        held = set()
        if getattr(policy, "d64_reserved", False):
            from stowage.rules.catalogue import rule_freeze_v1_enabled
            if rule_freeze_v1_enabled():
                # D64 rev 3.1: the pair keeps its half-full arrival masses
                # (departure_tank_masses); only other ballast takes the rest.
                held = _anti_heeling_tanks(getattr(vessel, "tank_pairs", ()))
                if not held:
                    raise ValueError("D64 reserved export ballast requires an explicit anti-heeling TankPair")
                ballast -= policy.mandatory_ballast_kg
                capacity = policy.ballast_capacity_kg - policy.mandatory_ballast_capacity_kg
        if held:
            fraction = ballast / capacity if capacity else 0.0
        else:
            fraction = ballast / policy.ballast_capacity_kg if policy.ballast_capacity_kg else 0.0
        for tank in vessel.tanks:
            if tank.adjustable and tank.id not in held:
                masses[tank.id] = fraction * tank.capacity_m3 * tank.content_density_kg_m3
    return masses


def export_rejections(vessel, boxes, params, *, full_vessel=None, minimum_utilisation=True):
    """Reject export mass/TEU extremes; D36 scales the same bounds to slices.

    The normalised design-displacement calculation is a reference booking
    screen, not the sliced hull's actual departure. D29 and the full checker
    separately evaluate that actual departure with its fixed outside cargo.
    """
    audit = export_reference_audit(vessel, boxes, params, full_vessel=full_vessel)
    if audit is None:
        return []
    reasons = {"reference_teu_fraction": "expert.physical.slot_occupancy",
               "reference_cargo_mass_fraction": "iscode.loading.displacement",
               "reference_design_displacement_fraction": "iscode.loading.displacement"}
    failures = list(audit["failure_components"])
    if not minimum_utilisation:
        # ASSUMPTION: the opt-in underbooked service does not follow the dense
        # export distribution's synthetic lower utilisation band. Retain upper
        # bounds; actual departure physics still belongs to the full checker.
        for key, field, lower in (("reference_teu_fraction", "teu_fraction", .75),
                ("reference_cargo_mass_fraction", "cargo_mass_fraction", .75),
                ("reference_design_displacement_fraction", "design_displacement_fraction", .90)):
            if key in failures and audit[field] < lower:
                failures.remove(key)
    return sorted({reasons[key] for key in failures})


def export_reference_audit(vessel, boxes, params, *, full_vessel=None):
    """The export screen's whole-ship reference audit, with failure attribution.

    Returns None when the screen doesn't apply. A region booking is scaled by
    region/whole-ship TEU (D36) and compared with the unchanged bands; the
    result is a reference, so ``actual_ship_displacement_checked`` is False:
    the sliced hull's real departure is checked by D29 and the full checker.
    Gaps are in reference kg (positive = outside the band by that much).
    """
    policy = full_export_policy(full_vessel if full_vessel is not None else vessel, params)
    if policy is None:
        return None
    scale = len(vessel.cells) / policy.capacity_teu if full_vessel is not None else 1.0
    # An explicit D36 reference is always a region-sized export, even when
    # integer rounding makes a tiny booking fall below the legacy count gate.
    if full_vessel is None and not policy.is_export_count(len(boxes) / scale):
        return None
    actual_mass = sum(b.gross_mass_kg for b in boxes)
    audit = policy.audit(actual_mass / scale, sum(b.length_ft / 20 for b in boxes) / scale)
    failures = []
    if (not policy.mass_limited and audit["teu_fraction"] < 0.75) or audit["teu_fraction"] > 0.90:
        failures.append("reference_teu_fraction")
    if not 0.75 <= audit["cargo_mass_fraction"] <= 0.90:
        failures.append("reference_cargo_mass_fraction")
    if not 0.90 <= audit["design_displacement_fraction"] <= 1.0:
        failures.append("reference_design_displacement_fraction")
    budget, cargo = audit["cargo_mass_budget_kg"], audit["cargo_mass_kg"]
    design, displacement = policy.design_displacement_kg, audit["departure_displacement_kg"]
    audit.update(reference_scale=scale, actual_booking_cargo_mass_kg=actual_mass,
                 failure_components=failures,
                 cargo_mass_upper_gap_kg=cargo - 0.90 * budget,
                 cargo_mass_lower_gap_kg=0.75 * budget - cargo,
                 displacement_upper_gap_kg=displacement - design,
                 displacement_lower_gap_kg=0.90 * design - displacement,
                 actual_ship_displacement_checked=False)
    return audit
