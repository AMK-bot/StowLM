"""Signed archival accounting and current default-report safeguards."""

from contextlib import redirect_stdout
from io import StringIO

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import unittest

_spec = spec_from_file_location("stowlm_ship_time", Path(__file__).with_name("ship_time.py"))
ship_time_module = module_from_spec(_spec)
sys.modules[_spec.name] = ship_time_module
_spec.loader.exec_module(ship_time_module)
impact = ship_time_module.impact


class ShipTimeTests(unittest.TestCase):
    def test_default_does_not_turn_projected_lifts_into_ship_time_dollars(self):
        output = StringIO()
        with redirect_stdout(output):
            ship_time_module.main([])
        text = output.getvalue()
        self.assertIn("BLIND_TWO_STEP_V0_8_1_PROVISIONAL_REPLACE_WITH_TEST_V3", text)
        self.assertIn("SHIP/PLANNER TIME: not estimated", text)
        self.assertNotIn("handling USD/call", text)
        self.assertNotIn("planner-hours/yr", text)

    def test_mid_ten_percent_values(self):
        name, r = ship_time_module.table(0.1)[1]
        self.assertEqual(name, "mid")
        self.assertAlmostEqual(r.handling_usd, 495)  # D85: includes 1.6 hatch-cover lifts at $75
        self.assertAlmostEqual(r.berth_hours, 0.052 + 0.75 * 0.10)
        self.assertAlmostEqual(r.ship_time_usd, 0.127 * 15_700 / 24)
        self.assertAlmostEqual(r.total_usd, 495 + 0.127 * 15_700 / 24)

    def test_zero_saving_is_zero(self):
        for _, r in ship_time_module.table(0.0):
            self.assertEqual(r.total_usd, 0)
            self.assertEqual(r.berth_hours, 0)

    def test_measured_tradeoff_keeps_sign(self):
        # A candidate adding yard moves but saving restows: signs pass through, nothing clipped.
        layer1 = impact.compare_counts(impact.SCENARIOS[1], impact.MoveCounts(150, 5), impact.MoveCounts(165, 4))
        r = ship_time_module.ship_time(layer1, ship_time_module.SHIP_SCENARIOS[1])
        self.assertLess(r.handling_usd, 0)
        expected_berth = layer1.berth_hours + layer1.yard_equipment_hours * 0.10
        self.assertAlmostEqual(r.berth_hours, expected_berth)

    def test_scenarios_pair_with_layer_one(self):
        self.assertEqual([s.name for s in impact.SCENARIOS],
                         [s.name for s in ship_time_module.SHIP_SCENARIOS])

    def test_invalid_fraction_rejected(self):
        with self.assertRaises(ValueError):
            ship_time_module.ShipTimeScenario("bad", 1.0, 1.5, 3.0)


if __name__ == "__main__":
    unittest.main()
