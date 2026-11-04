"""Research-only LIST boundary algebra; no production physics or certificates.

Mass moments use kg m. An unsafe residual is affine outward capacity minus
exact outward capacity, independently for each heel direction. See the adjacent
design note for assumptions and why finite measurements are not uniform proofs.
"""
from dataclasses import dataclass
import math
import unittest


@dataclass(frozen=True)
class BoundaryObservation:
    sign: int
    limit_rad: float
    affine_restoring_kgm: float
    exact_boundary_my_kgm: float
    affine_gm_m: float

    def residual_kgm(self):
        if self.sign not in (-1, 1):
            raise ValueError("direction must be -1 or +1")
        if not all(math.isfinite(v) for v in (
            self.limit_rad, self.affine_restoring_kgm,
            self.exact_boundary_my_kgm, self.affine_gm_m)):
            raise ValueError("finite boundary observation required")
        if not 0 < self.limit_rad < math.pi/2:
            raise ValueError("positive small-angle boundary required")
        return (math.tan(self.limit_rad)*self.affine_restoring_kgm
                - self.sign*self.exact_boundary_my_kgm)


def measured_reserve(observations, *, sign, gm_min_m, transfer_kgm=0., factor=1.1):
    """Return an empirical boundary reserve; an empty eligible side fails closed."""
    if not math.isfinite(gm_min_m) or gm_min_m <= 0:
        raise ValueError("positive GM threshold required")
    if (not math.isfinite(transfer_kgm) or transfer_kgm < 0
            or not math.isfinite(factor) or factor < 1):
        raise ValueError("invalid transfer reserve or safety factor")
    eligible = [x.residual_kgm() for x in observations
                if x.sign == sign and x.affine_gm_m >= gm_min_m]
    if not eligible:
        raise ValueError("direction has no GM-eligible boundary measurements")
    return factor*(max(0., max(eligible))+transfer_kgm)


def affine_outward_demand(my_kgm, restoring_kgm, *, sign, limit_rad, reserve_kgm):
    return sign*my_kgm-math.tan(limit_rad)*restoring_kgm+reserve_kgm


class BoundaryAlgebraTests(unittest.TestCase):
    def test_direction_and_transfer(self):
        angle = math.atan(.1)
        observations = [BoundaryObservation(1, angle, 1000., 90., .2),
                        BoundaryObservation(-1, angle, 1000., -95., .2)]
        self.assertAlmostEqual(measured_reserve(observations, sign=1,
            gm_min_m=.15, transfer_kgm=2.), 13.2)
        self.assertAlmostEqual(measured_reserve(observations, sign=-1,
            gm_min_m=.15), 5.5)

    def test_already_conservative_capacity_has_no_unsafe_reserve(self):
        obs = [BoundaryObservation(1, math.atan(.1), 1000., 120., .2)]
        self.assertEqual(measured_reserve(obs, sign=1, gm_min_m=.15), 0.)

    def test_missing_or_gm_ineligible_side_fails_closed(self):
        obs = [BoundaryObservation(1, .03, 1000., 20., .1)]
        with self.assertRaises(ValueError):
            measured_reserve(obs, sign=1, gm_min_m=.15)

    def test_interior_safe_angle_gap_need_not_exhaust_boundary_row(self):
        # Synthetic exact response has greater stiffness and a lateral bias.
        # An interior predicted +1.8deg can correspond to exact -0.1deg,
        # producing a 1.9deg lower residual despite both conditions being safe.
        limit = math.radians(2.)
        restoring = 1000.
        my = restoring*math.tan(math.radians(1.8))
        exact_stiffness = 2000.
        bias = my-exact_stiffness*math.tan(math.radians(-.1))
        self.assertGreater(1.1*math.radians(1.9), limit)
        observations = [BoundaryObservation(s, limit, restoring,
            bias+s*exact_stiffness*math.tan(limit), .2) for s in (-1, 1)]
        # Both directional rows still admit this known-safe interior point.
        for s in (-1, 1):
            reserve = measured_reserve(observations, sign=s, gm_min_m=.15)
            self.assertLessEqual(affine_outward_demand(my, restoring, sign=s,
                limit_rad=limit, reserve_kgm=reserve), 0.)

    def test_holdout_breach_is_detectable(self):
        angle = math.atan(.1)
        calibration = [BoundaryObservation(1, angle, 1000., 90., .2)]
        reserve = measured_reserve(calibration, sign=1, gm_min_m=.15)
        holdout = BoundaryObservation(1, angle, 1000., 80., .2)
        self.assertGreater(holdout.residual_kgm(), reserve)
        # The candidate passes the measured affine row but violates exact capacity.
        my = 85.
        self.assertLess(affine_outward_demand(my, 1000., sign=1,
            limit_rad=angle, reserve_kgm=reserve), 0.)
        self.assertGreater(my-holdout.exact_boundary_my_kgm, 0.)


if __name__ == "__main__":
    unittest.main()
