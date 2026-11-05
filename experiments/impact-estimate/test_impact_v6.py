"""Offline accounting and visible report-number checks for the v6 projection."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
_spec = spec_from_file_location("stowlm_impact_v6", HERE / "impact_v6.py")
impact = module_from_spec(_spec)
_spec.loader.exec_module(impact)


class ImpactV6Tests(unittest.TestCase):
    def test_default_projection_is_json_able_and_not_measured(self):
        result = impact.calculate()
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        self.assertEqual(result["label"], "projection")
        self.assertIsNone(result["measured_pipeline_results"])
        self.assertNotIn("BLIND_TWO_STEP", json.dumps(result))
        self.assertEqual(result["inputs"]["context"]["pilot_max_boxes"]["value"], 195)

    def test_mid_accounts_for_disjoint_work_and_only_extra_moves(self):
        result = impact.calculate()["pilot"]["mid"]
        per_call = result["per_call"]
        self.assertAlmostEqual(per_call["baseline_extra_rehandles"], 150 * 0.05)
        self.assertAlmostEqual(per_call["avoided_extra_rehandles"], 150 * 0.05 * 0.2)
        self.assertAlmostEqual(per_call["initial_planner_hours_saved"], 2.5 - 0.75)
        self.assertAlmostEqual(per_call["instruction_planner_hours_saved"], 2 * (45 - 5) / 60)
        self.assertAlmostEqual(per_call["rework_planner_hours_saved"], 0.25 * 60 / 60)
        self.assertAlmostEqual(per_call["total_planner_hours_saved"], 10 / 3)
        self.assertAlmostEqual(per_call["handling_charge_equivalent_usd"], 1.5 * 20.86)
        self.assertAlmostEqual(per_call["planner_labor_equivalent_usd"], 100)
        self.assertAlmostEqual(per_call["total_equivalent_usd"], 131.29)
        for key, value in per_call.items():
            self.assertAlmostEqual(result["annual"][key], value * 260)

    def test_zero_effects_return_zero_and_added_work_is_signed(self):
        inputs = impact.default_inputs()
        row = inputs["pilot"]["low"]
        row["rehandle_reduction"]["value"] = 0
        row["initial_assisted_hours"]["value"] = row["initial_baseline_hours"]["value"]
        row["instruction_assisted_minutes"]["value"] = row["instruction_baseline_minutes"]["value"]
        row["rework_events_avoided_per_call"]["value"] = 0
        result = impact.calculate(inputs)["pilot"]["low"]["per_call"]
        self.assertEqual(result["total_equivalent_usd"], 0)
        row["initial_assisted_hours"]["value"] += 1
        result = impact.calculate(inputs)["pilot"]["low"]["per_call"]
        self.assertEqual(result["total_planner_hours_saved"], -1)
        self.assertEqual(result["total_equivalent_usd"], -20)

    def test_scale_calls_are_derived_from_adopting_throughput(self):
        result = impact.calculate()
        for name in ("low", "mid", "high"):
            row = result["inputs"]["scale"][name]
            case = result["scale"][name]
            throughput = 920_300_805 * row["adopted_throughput_share"]["value"]
            self.assertAlmostEqual(case["annual_throughput_teu"], throughput)
            self.assertAlmostEqual(case["annual_primary_moves"], throughput / 1.5)
            self.assertAlmostEqual(case["annual_calls"], throughput / 1.5 / row["primary_moves_per_call"]["value"])
            self.assertLessEqual(case["annual_throughput_teu"], 920_300_805)
        self.assertNotIn("annual_calls", result["inputs"]["scale"]["high"])

    def test_annual_calls_scale_time_and_moves_once(self):
        inputs = impact.default_inputs()
        before = impact.calculate(inputs)["pilot"]["low"]
        inputs["pilot"]["low"]["annual_calls"]["value"] *= 2
        after = impact.calculate(inputs)["pilot"]["low"]
        self.assertEqual(after["per_call"], before["per_call"])
        for key in before["annual"]:
            self.assertAlmostEqual(after["annual"][key], 2 * before["annual"][key])

    def test_each_numeric_input_has_explicit_provenance(self):
        result = impact.calculate()
        for path, value in result["report_numbers"].items():
            if not path.startswith("inputs."):
                continue
            field = result
            for part in path.split("."):
                field = field[part]
            self.assertEqual(field["value"], value)
            self.assertIn(field["status"], {"sourced", "computed", "design", "assumption", "commercial_estimate"})
            self.assertTrue(field["note"])
            if field["status"] != "assumption":
                self.assertTrue(field["source"])

    def test_input_bundles_are_independent_and_no_mutation(self):
        inputs = impact.default_inputs()
        before = deepcopy(inputs)
        result = impact.calculate(inputs)
        self.assertEqual(before, inputs)
        result["inputs"]["pilot"]["low"]["annual_calls"]["value"] = 999
        self.assertEqual(impact.default_inputs()["pilot"]["low"]["annual_calls"]["value"], 52)

    def test_rejects_nonfinite_bad_provenance_and_schema(self):
        for value in (-1, float("nan"), float("inf"), True, "12"):
            inputs = impact.default_inputs()
            inputs["pilot"]["low"]["annual_calls"]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                impact.calculate(inputs)
        for field, value in (("rehandle_reduction", 1.1), ("primary_moves_per_call", 0), ("primary_moves_per_call", 196)):
            inputs = impact.default_inputs()
            inputs["pilot"]["low"][field]["value"] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                impact.calculate(inputs)
        inputs = impact.default_inputs()
        inputs["context"]["world_container_throughput_teu"]["source"] = None
        with self.assertRaises(ValueError):
            impact.calculate(inputs)
        inputs = impact.default_inputs()
        inputs["pilot"]["low"]["unrecognized"] = {}
        with self.assertRaises(ValueError):
            impact.calculate(inputs)
        inputs = impact.default_inputs()
        inputs["context"]["electric_quay_auxiliary_kwh_per_move"]["value"] = 7
        with self.assertRaises(ValueError):
            impact.calculate(inputs)

    def test_conditional_emissions_intensity_is_not_an_annual_claim(self):
        result = impact.calculate()
        climate = result["conditional_emissions"]
        self.assertAlmostEqual(climate["marginal_kwh_per_comparable_electric_quay_move"], 4)
        self.assertAlmostEqual(climate["kgco2e_per_comparable_avoided_electric_quay_move"], 2.2248)
        for section in ("pilot", "scale"):
            for case in result[section].values():
                self.assertFalse(any("co2" in key or "emission" in key for key in case["annual"]))

    def test_prize_formula_with_independent_synthetic_accounting_case(self):
        inputs = impact.default_inputs()
        for field, value in (("world_container_throughput_teu", 1500), ("teu_per_primary_move", 1.5)):
            inputs["context"][field].update(value=value, status="assumption", source=None, note="Synthetic accounting test")
        overrides = {
            "ship_extra_restow_fraction_of_total_moves": 0.2,
            "yard_extra_rehandles_per_relevant_primary_move": 0.1,
            "yard_eligible_throughput_fraction": 0.5,
            "ship_extra_lift_charge_usd": 20,
            "yard_extra_lift_charge_usd": 10,
            "global_container_calls_per_year": 100,
            "berth_hours_per_call": 10,
            "charter_usd_per_day": 2400,
            "reducible_berth_share": 0.1,
            "carrier_planner_hours_per_call": 2,
            "carrier_planner_usd_per_hour": 30,
            "capture_fraction": 0.05,
            "marginal_electric_quay_kwh_per_extra_lift": 4,
            "marginal_yard_kwh_per_extra_lift": 2,
            "electricity_kgco2_per_kwh": 0.5,
            "container_ship_auxiliary_kw_at_berth": 10,
            "berth_auxiliary_kgco2_per_kwh": 0.75,
        }
        for field, value in overrides.items():
            inputs["prize"]["mid"][field].update(value=value, status="assumption", source=None, note="Synthetic accounting test")
        case = impact.calculate(inputs)["prize"]["mid"]
        expected = {
            "annual_primary_ship_moves": 1000,
            "annual_extra_ship_lifts": 250,
            "annual_extra_yard_lifts": 50,
            "annual_baseline_berth_hours": 1000,
            "avoided_berth_hours": 100,
            "ship_handling_equivalent_usd": 5000,
            "yard_handling_equivalent_usd": 500,
            "handling_equivalent_usd": 5500,
            "ship_time_equivalent_usd": 10000,
            "carrier_planner_hours": 200,
            "planner_labor_equivalent_usd": 6000,
            "total_gross_prize_usd": 21500,
            "captured_opportunity_usd": 1075,
            "total_gross_prize_million_usd": 0.0215,
            "total_gross_prize_billion_usd": 0.0000215,
            "captured_opportunity_million_usd": 0.001075,
        }
        for field, value in expected.items():
            with self.subTest(field=field):
                self.assertAlmostEqual(case[field], value)
        emissions = case["emissions"]
        for field, value in {
            "ship_extra_lift_kwh": 1000, "yard_extra_lift_kwh": 100,
            "berth_auxiliary_kwh": 1000, "ship_extra_lift_kgco2": 500,
            "yard_extra_lift_kgco2": 50, "berth_auxiliary_kgco2": 750,
            "total_gross_kgco2": 1300, "captured_kgco2": 65,
        }.items():
            with self.subTest(emissions=field):
                self.assertAlmostEqual(emissions[field], value)

    def test_prize_ship_share_is_normalized_against_total_moves(self):
        result = impact.calculate()
        for name in ("low", "mid", "high"):
            row = result["inputs"]["prize"][name]
            case = result["prize"][name]
            share = row["ship_extra_restow_fraction_of_total_moves"]["value"]
            extra = case["annual_extra_ship_lifts"]
            primary = case["annual_primary_ship_moves"]
            self.assertAlmostEqual(extra / (primary + extra), share)
            self.assertGreater(extra, primary * share)
            expected_yard = primary * row["yard_eligible_throughput_fraction"]["value"] * row["yard_extra_rehandles_per_relevant_primary_move"]["value"]
            self.assertAlmostEqual(case["annual_extra_yard_lifts"], expected_yard)

    def test_reviewed_prize_defaults_do_not_calibrate_restows_from_stress_voyage(self):
        inputs = impact.default_inputs()
        for name, share, ship_price, yard_price in (
            ("low", 0.003, 30, 10), ("mid", 0.009, 75, 25), ("high", 0.022, 150, 50),
        ):
            row = inputs["prize"][name]
            restows = row["ship_extra_restow_fraction_of_total_moves"]
            self.assertEqual(restows["value"], share)
            self.assertEqual(restows["status"], "assumption")
            self.assertIn("srilanka-ports-authority", restows["source"])
            self.assertNotIn("jensen", json.dumps(restows).lower())
            self.assertNotIn("ajspur", json.dumps(restows).lower())
            self.assertNotIn("calibrated", restows["note"].lower())
            self.assertEqual(row["ship_extra_lift_charge_usd"]["value"], ship_price)
            self.assertEqual(row["yard_extra_lift_charge_usd"]["value"], yard_price)
        result = impact.calculate()
        self.assertEqual([format(result["prize"][name]["total_gross_prize_billion_usd"], ".1f") for name in ("low", "mid", "high")], ["0.3", "2.3", "12.5"])

    def test_exposure_uses_total_mid_berth_time_and_stays_separate_from_prize(self):
        result = impact.calculate()
        exposure = result["exposure"]
        # Full vessel time: 520000 calls x 26.25 berth hours x 39725 USD/day.
        self.assertAlmostEqual(exposure["berth_time_usd"], 22_593_593_750)
        self.assertAlmostEqual(exposure["low"]["total_billion_usd"], 85.26359375)
        self.assertAlmostEqual(exposure["mid"]["total_billion_usd"], 98.09359375)
        self.assertAlmostEqual(exposure["high"]["total_billion_usd"], 98.09359375)
        self.assertEqual(exposure["headline_billion_usd"], 90)
        self.assertEqual(exposure["headline_one_percent_billion_usd"], 0.9)
        self.assertIn("lower-end", exposure["headline_rounding_rule"])
        inputs = impact.default_inputs()
        for row in inputs["prize"].values():
            row["ship_extra_restow_fraction_of_total_moves"]["value"] = 0
            row["yard_extra_rehandles_per_relevant_primary_move"]["value"] = 0
            row["reducible_berth_share"]["value"] = 0
            row["carrier_planner_hours_per_call"]["value"] = 0
        changed = impact.calculate(inputs)
        self.assertEqual(changed["exposure"], exposure)
        self.assertTrue(all(case["total_gross_prize_usd"] == 0 for case in changed["prize"].values()))

    def test_exposure_uses_only_mid_duration_and_charter_but_never_reducible_share(self):
        inputs = impact.default_inputs()
        before = impact.calculate(inputs)["exposure"]
        for name in ("low", "high"):
            inputs["prize"][name]["global_container_calls_per_year"]["value"] *= 2
            inputs["prize"][name]["berth_hours_per_call"]["value"] *= 2
            inputs["prize"][name]["charter_usd_per_day"]["value"] *= 2
        inputs["prize"]["mid"]["reducible_berth_share"]["value"] = 1
        self.assertEqual(impact.calculate(inputs)["exposure"], before)
        inputs["prize"]["mid"]["charter_usd_per_day"]["value"] *= 2
        after = impact.calculate(inputs)["exposure"]
        self.assertAlmostEqual(after["berth_time_usd"], 2 * before["berth_time_usd"])
        for name in ("low", "mid", "high"):
            self.assertAlmostEqual(after[name]["total_usd"] - before[name]["total_usd"], before["berth_time_usd"])

    def test_terminal_revenue_crosscheck_is_a_ratio_not_additional_value(self):
        result = impact.calculate()
        for case in ("low", "mid", "high"):
            prize = result["prize"][case]
            for benchmark in ("low", "mid", "high"):
                revenue = result["inputs"]["context"][f"terminal_revenue_{benchmark}_usd"]["value"]
                ratios = result["revenue_crosscheck"][case][benchmark]
                self.assertAlmostEqual(ratios["ship_handling_share"], prize["ship_handling_equivalent_usd"] / revenue)
                self.assertAlmostEqual(ratios["handling_share"], prize["handling_equivalent_usd"] / revenue)
                self.assertAlmostEqual(ratios["total_prize_share"], prize["total_gross_prize_usd"] / revenue)
        self.assertGreater(result["revenue_crosscheck"]["mid"]["high"]["ship_handling_share"], 0.005)
        self.assertLess(result["revenue_crosscheck"]["mid"]["low"]["ship_handling_share"], 0.0067)
        for benchmark in ("low", "mid", "high"):
            field = result["inputs"]["context"][f"terminal_revenue_{benchmark}_usd"]
            self.assertEqual(field["status"], "commercial_estimate")
            self.assertTrue(field["source"])

    def test_terminal_revenue_validation_rejects_zero_and_reversed_benchmarks(self):
        inputs = impact.default_inputs()
        inputs["context"]["terminal_revenue_low_usd"]["value"] = 0
        with self.assertRaises(ValueError):
            impact.calculate(inputs)
        inputs = impact.default_inputs()
        inputs["context"]["terminal_revenue_low_usd"]["value"] = inputs["context"]["terminal_revenue_high_usd"]["value"] + 1
        with self.assertRaises(ValueError):
            impact.calculate(inputs)

    def test_prize_capture_is_applied_once_to_whole_pool_not_throughput(self):
        inputs = impact.default_inputs()
        result = impact.calculate(inputs)
        for name, fraction in (("low", 0.01), ("mid", 0.05), ("high", 0.20)):
            case = result["prize"][name]
            self.assertEqual(case["capture_fraction"], fraction)
            self.assertAlmostEqual(case["captured_opportunity_usd"], fraction * case["total_gross_prize_usd"])
        before = result["prize"]["mid"]
        inputs["prize"]["mid"]["capture_fraction"]["value"] = 0.1
        inputs["scale"]["mid"]["adopted_throughput_share"]["value"] = 0.9
        after = impact.calculate(inputs)["prize"]["mid"]
        for field in ("annual_primary_ship_moves", "annual_extra_ship_lifts", "annual_extra_yard_lifts", "ship_time_equivalent_usd", "planner_labor_equivalent_usd", "total_gross_prize_usd"):
            self.assertEqual(before[field], after[field])
        self.assertAlmostEqual(after["captured_opportunity_usd"], before["captured_opportunity_usd"] * 2)

    def test_prize_calls_multiply_time_and_carrier_labor_not_throughput(self):
        inputs = impact.default_inputs()
        before = impact.calculate(inputs)["prize"]["mid"]
        inputs["prize"]["mid"]["global_container_calls_per_year"]["value"] *= 2
        after = impact.calculate(inputs)["prize"]["mid"]
        for field in ("annual_baseline_berth_hours", "avoided_berth_hours", "ship_time_equivalent_usd", "carrier_planner_hours", "planner_labor_equivalent_usd"):
            self.assertAlmostEqual(after[field], before[field] * 2)
        for field in ("annual_primary_ship_moves", "annual_extra_ship_lifts", "annual_extra_yard_lifts", "handling_equivalent_usd"):
            self.assertEqual(after[field], before[field])
        self.assertAlmostEqual(after["total_gross_prize_usd"], after["handling_equivalent_usd"] + after["ship_time_equivalent_usd"] + after["planner_labor_equivalent_usd"])

    def test_prize_emissions_are_separate_and_never_monetized(self):
        inputs = impact.default_inputs()
        before = impact.calculate(inputs)["prize"]["mid"]
        for field in ("marginal_electric_quay_kwh_per_extra_lift", "marginal_yard_kwh_per_extra_lift", "container_ship_auxiliary_kw_at_berth"):
            inputs["prize"]["mid"][field]["value"] *= 2
        after = impact.calculate(inputs)["prize"]["mid"]
        for field in before:
            if field.endswith("_usd"):
                self.assertEqual(before[field], after[field])
        emissions = after["emissions"]
        self.assertAlmostEqual(emissions["total_gross_kgco2"], before["emissions"]["total_gross_kgco2"] * 2)
        self.assertAlmostEqual(emissions["total_gross_kgco2"], emissions["ship_extra_lift_kgco2"] + emissions["yard_extra_lift_kgco2"] + emissions["berth_auxiliary_kgco2"])
        self.assertAlmostEqual(emissions["total_gross_million_tonnes_co2"], emissions["total_gross_kgco2"] / 1_000_000_000)
        self.assertFalse(any(key.endswith("_usd") for key in emissions))

    def test_prize_rejects_bad_shares_and_all_inputs_have_provenance(self):
        for field, value in (("ship_extra_restow_fraction_of_total_moves", 1), ("ship_extra_restow_fraction_of_total_moves", -0.1), ("yard_eligible_throughput_fraction", 1.1), ("reducible_berth_share", 1.1), ("capture_fraction", 1.1), ("global_container_calls_per_year", float("nan"))):
            inputs = impact.default_inputs()
            inputs["prize"]["low"][field]["value"] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                impact.calculate(inputs)
        for name, row in impact.default_inputs()["prize"].items():
            for field, metadata in row.items():
                with self.subTest(scenario=name, field=field):
                    self.assertTrue(metadata["unit"])
                    self.assertTrue(metadata["note"])
                    self.assertIn(metadata["status"], {"sourced", "computed", "design", "assumption"})
                    if metadata["status"] != "assumption":
                        self.assertTrue(metadata["source"])

    def test_marked_visible_numbers_fail_if_drifted(self):
        valid = "Value $<!-- calc:pilot.mid.per_call.total_equivalent_usd|,.2f -->131.29<!-- /calc -->"
        self.assertEqual(impact.check_report_numbers(valid), 1)
        for bad in (valid.replace("131.29", "131.30"), valid.replace("pilot.mid", "pilot.nope"), "no markers", "<!-- calc:bad -->42"):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                impact.check_report_numbers(bad)
        share = "<!-- calc:inputs.scale.high.adopted_throughput_share|.0% -->20%<!-- /calc -->"
        self.assertEqual(impact.check_report_numbers(share), 1)

    def test_report_numbers_match_calculator(self):
        text = (HERE / "impact-v6.md").read_text()
        self.assertGreaterEqual(impact.check_report_numbers(text), 100)
        paths = {match.group(1) for match in impact.MARKER.finditer(text)}
        for section in ("pilot", "prize"):
            for name in ("low", "mid", "high"):
                suffix = "annual.total_equivalent_usd" if section == "pilot" else "total_gross_prize_billion_usd"
                self.assertIn(f"{section}.{name}.{suffix}", paths)
        for case in ("low", "mid", "high"):
            for rate in ("low", "mid", "high"):
                self.assertIn(f"capture.{case}.{rate}.opportunity_million_usd", paths)
        # The task 3.1 placeholders were filled with measured figures on 2 Nov; none may remain.
        self.assertNotIn("{{", text)
        self.assertNotIn("@@", text)
        self.assertIn("exposure.headline_billion_usd", paths)
        self.assertIn("exposure.headline_one_percent_billion_usd", paths)
        self.assertIn("revenue_crosscheck.mid.low.ship_handling_share", paths)
        self.assertIn("revenue_crosscheck.mid.high.ship_handling_share", paths)
        self.assertIn("local_projection.laem_chabang.share_of_world_calls", paths)
        self.assertIn("local_projection.laem_chabang.headline_mid_pool_million_usd", paths)
        self.assertIn("more compute for the search, a larger model for reading", text)

    def test_pitch_video_and_card_fit_requested_limits(self):
        text = (HERE / "impact-v6.md").read_text()
        visible = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        top = "\n".join(visible.splitlines()[:20])
        self.assertIn("Total annual prize", top)
        self.assertIn("Capture of that prize", top)
        self.assertIn("About $90 billion a year of port-call cost", top)
        self.assertNotIn("26.7", visible.splitlines()[2])
        self.assertIn("Full-ship, multi-port planning works", visible)
        self.assertNotIn("$19.9", visible)
        pitch = visible.split("## Pitch paragraph\n", 1)[1].split("\n## Video line", 1)[0].strip()
        self.assertLessEqual(len(re.split(r"(?<=[.!?])\s+", pitch)), 4)
        video = visible.split("## Video line\n", 1)[1].split("\n\nOn-screen card:", 1)[0].strip()
        self.assertLessEqual(len(re.findall(r"\b[\w]+(?:[-’'][\w]+)*\b", video)), 22)
        card = visible.split("On-screen card:\n\n", 1)[1].split("\n\n##", 1)[0].strip().splitlines()
        self.assertEqual(len(card), 2)
        self.assertTrue(all(len(line.strip()) <= 100 for line in card))
        for phrase in ("solves real ships", "industry level", "industry grade", "production ready", "beats navis", "fine-tuning cut moves"):
            self.assertNotIn(phrase, visible.lower())

    def test_capture_matrix_applies_each_fraction_to_each_full_prize(self):
        inputs = impact.default_inputs()
        result = impact.calculate(inputs)
        for case, row in result["capture"].items():
            for rate, value in row.items():
                fraction = inputs["prize"][rate]["capture_fraction"]["value"]
                self.assertEqual(value["fraction"], fraction)
                self.assertAlmostEqual(value["opportunity_billion_usd"], result["prize"][case]["total_gross_prize_usd"] * fraction / 1e9)

    def test_recorded_projection_matches_calculator(self):
        fixture = json.loads((HERE / "fixtures" / "impact-v6.json").read_text())
        self.assertEqual(fixture, impact.calculate())

    def test_named_port_uses_unrounded_call_share_and_same_mid_pool(self):
        result = impact.calculate()
        local = result["local_projection"]["laem_chabang"]
        self.assertEqual(local["label"], "projection")
        self.assertAlmostEqual(local["share_of_world_calls"], 9325 / 520000)
        self.assertEqual(format(local["share_of_world_calls"], ".1%"), "1.8%")
        expected = (9325 / 520000) * result["prize"]["mid"]["total_gross_prize_usd"]
        self.assertAlmostEqual(local["mid_pool_usd"], expected)
        self.assertAlmostEqual(local["mid_pool_million_usd"], 41.373419293211136)
        self.assertEqual(local["headline_mid_pool_million_usd"], 40)
        self.assertNotAlmostEqual(local["mid_pool_usd"], 0.018 * result["prize"]["mid"]["total_gross_prize_usd"])
        inputs = impact.default_inputs()
        inputs["context"]["laem_chabang_container_calls_per_year"]["value"] *= 2
        changed = impact.calculate(inputs)
        self.assertEqual(changed["prize"], result["prize"])
        self.assertAlmostEqual(changed["local_projection"]["laem_chabang"]["mid_pool_usd"], 2 * expected)

    def test_named_port_is_unavailable_for_invalid_global_population(self):
        for calls in (0, 100):
            inputs = impact.default_inputs()
            inputs["prize"]["mid"]["global_container_calls_per_year"]["value"] = calls
            local = impact.calculate(inputs)["local_projection"]["laem_chabang"]
            self.assertIsNone(local["share_of_world_calls"])
            self.assertIsNone(local["mid_pool_usd"])

    def test_scale_retains_workflow_assumptions_and_covers_both_directions(self):
        inputs = impact.default_inputs()
        for name in ("low", "mid", "high"):
            pilot, scale = inputs["pilot"][name], inputs["scale"][name]
            for key in pilot.keys() & scale.keys() - {"primary_moves_per_call"}:
                self.assertEqual(pilot[key], scale[key])
            self.assertIn("load AND discharge", scale["primary_moves_per_call"]["note"])
            self.assertIn("one container per lift", scale["primary_moves_per_call"]["note"])

    def test_cli_offline_json_and_custom_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = impact.default_inputs()
            inputs["pilot"]["low"]["annual_calls"]["value"] = 1
            (root / "inputs.json").write_text(json.dumps(inputs))
            process = subprocess.run(
                [sys.executable, str(HERE / "impact_v6.py"), "--inputs", str(root / "inputs.json"), "--json", str(root / "result.json")],
                check=True, capture_output=True, text=True,
            )
            self.assertEqual(process.stdout, "")
            result = json.loads((root / "result.json").read_text())
            self.assertEqual(result["pilot"]["low"]["annual_calls"], 1)
            self.assertEqual(result["pilot"]["low"]["annual"], result["pilot"]["low"]["per_call"])


if __name__ == "__main__":
    unittest.main()
