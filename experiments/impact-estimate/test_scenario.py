"""Accounting and provenance safeguards for the optional extrapolation."""

from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = spec_from_file_location(name, HERE / filename)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


impact = load("stowlm_scenario_impact", "impact.py")
scenario = load("stowlm_scenario_accounting", "scenario.py")
ship = load("stowlm_scenario_ship", "ship_time.py")


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.measured = impact.impact_report()
        self.report = scenario.scenario_report(self.measured)

    def test_source_categories_and_no_second_restow_multiplier(self):
        raw = self.report["raw_category_reconstruction"]
        self.assertEqual(raw["maps"], 14)
        self.assertEqual(raw["saved"], {"relocations": 20, "restow_now": 0,
                                        "hatch_now": 6, "restow_later": 80, "hatch_later": 34})
        mid = self.report["cases"]["mid"]["per_call"]
        self.assertAlmostEqual(mid["credited_restow_later_lifts"], 9.126 * 80 / 140 * 500 / 22 * .25 * .5)
        self.assertEqual(self.report["derived_price_proxies"]["restow_usd_per_lift"], 41.72 / 2)
        self.assertEqual(self.report["derived_price_proxies"]["hatch_usd_per_lift"], 56.78 / 2)

    def test_low_has_only_this_port_and_no_ship_time(self):
        low = self.report["cases"]["low"]["per_call"]
        self.assertEqual(low["credited_projected_later_lifts"], 0)
        self.assertEqual(low["credited_later_handling_charge_proxy_usd"], 0)
        self.assertEqual(low["berth_hours"], 0)
        self.assertEqual(low["ship_time_opportunity_usd"], 0)
        self.assertGreater(low["yard_equipment_hours"], 0)
        self.assertEqual(low["planner_hours"], 0)

    def test_published_arithmetic(self):
        expected = {
            "low": (3.8518831168831174, 54.86563051948053, 0, 0, 54.86563051948053),
            "mid": (30.740990259740254, 624.9569245129868, .19444602272727268, 149.84496626420452, 774.8018907771914),
            "high": (103.70454545454542, 2225.499545454545, 1.4814935064935062, 2221.1291396103893, 4446.628685064934),
        }
        for name, values in expected.items():
            result = self.report["cases"][name]["per_call"]
            for key, target in zip(("total_credited_lifts", "handling_charge_proxy_usd", "berth_hours",
                                    "ship_time_opportunity_usd", "combined_proxy_usd"), values):
                with self.subTest(case=name, key=key):
                    self.assertAlmostEqual(result[key], target)

    def test_elapsed_time_excludes_yard_and_keeps_ports_separate(self):
        for name, case in self.report["cases"].items():
            r = case["per_call"]
            factor = self.report["inputs"]["quay_critical_path_fraction"]["value"][name]
            self.assertAlmostEqual(r["berth_hours"], r["quay_work_hours"] * factor)
            self.assertAlmostEqual(r["berth_hours"], r["this_port_berth_hours"] + r["credited_later_berth_hours"])
            self.assertAlmostEqual(r["handling_charge_proxy_usd"], r["this_port_handling_charge_proxy_usd"] + r["credited_later_handling_charge_proxy_usd"])

    def test_annual_populations_floor_calls_and_never_use_teu(self):
        for name, calls, planner in (("low", 93, 0), ("mid", 466, .5), ("high", 932, 1)):
            case = self.report["cases"][name]
            r = case["per_call"]
            self.assertEqual(case["annual_adoption_subset"]["calls"], calls)
            self.assertEqual(case["annual_52_call_stream"]["calls"], 52)
            self.assertAlmostEqual(r["planner_hours"], planner)
            for scope, population in (("annual_52_call_stream", 52), ("annual_adoption_subset", calls)):
                for key, quantity in r.items():
                    if isinstance(quantity, (int, float)):
                        self.assertAlmostEqual(case[scope][key], quantity * population)

    def test_every_input_has_honest_provenance_and_scaled_result_label(self):
        for name, item in self.report["inputs"].items():
            with self.subTest(name=name):
                for required in ("value", "unit", "status", "url", "source_date", "access_date", "note"):
                    self.assertIn(required, item)
                if item["status"] == "ASSUMPTION":
                    self.assertIsNone(item["url"])
                    self.assertEqual(item["declared_date"], "2026-10-31")
                elif item["status"] == "sourced":
                    self.assertEqual(item["access_date"], "2026-10-31")
        for case in self.report["cases"].values():
            self.assertIn("EXTRAPOLATION from 12–22 boxes", case["label"])
            for scope in ("per_call", "annual_52_call_stream", "annual_adoption_subset"):
                self.assertIn("not measured", case[scope]["label"])

    def test_combined_json_adds_only_scenario_key_and_preserves_measured_values(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "results.json"
            with redirect_stdout(StringIO()):
                impact.main(["--scenario", "--json", str(path)])
            result = json.loads(path.read_text())
        self.assertEqual(set(result), set(self.measured) | {"scenario_layer"})
        for key, value in self.measured.items():
            self.assertEqual(result[key], value)
        self.assertEqual(impact.impact_report(), self.measured)

    def test_ship_time_cli_uses_same_scenario_and_leaves_default_unchanged(self):
        output = StringIO()
        with redirect_stdout(output):
            ship.main(["--scenario"])
        text = output.getvalue()
        self.assertIn("EXTRAPOLATION FROM 12–22 BOXES; NOT MEASURED OPERATIONAL SAVINGS", text)
        self.assertIn("ANNUAL ASSUMED ADOPTION SUBSET", text)
        self.assertIn("774.801891", text)
        default = StringIO()
        with redirect_stdout(default):
            ship.main([])
        self.assertNotIn("handling-charge proxy USD", default.getvalue())


if __name__ == "__main__":
    unittest.main()
