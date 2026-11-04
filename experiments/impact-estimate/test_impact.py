"""Accounting safeguards for the pitch sensitivity, independent of its inputs."""

from dataclasses import replace
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import sys
import unittest

# Explicit file loading works with repository-wide pytest importlib mode without
# changing sys.path or requiring this hyphenated experiment to be a package.
_spec = spec_from_file_location("stowlm_impact_estimate", Path(__file__).with_name("impact.py"))
impact = module_from_spec(_spec)
sys.modules[_spec.name] = impact
_spec.loader.exec_module(impact)
SCENARIOS, estimate = impact.SCENARIOS, impact.estimate


class ImpactAccountingTests(unittest.TestCase):
    def test_published_mid_ten_percent_values(self):
        result = estimate(SCENARIOS[1], 0.1)
        for name, expected in {
            "yard_moves": 15, "quay_moves": 1, "yard_usd": 300,
            "quay_usd": 75, "hatch_moves": 1.6, "hatch_usd": 120, "total_usd": 495,
            "weighted_lifts": 17.6,
            "yard_equipment_hours": 0.75, "quay_crane_hours": 0.104,
            "berth_hours": 0.052,
        }.items():
            with self.subTest(name=name):
                self.assertAlmostEqual(getattr(result, name), expected)

    def test_default_report_uses_provisional_blind_bundle_without_dollars(self):
        output = StringIO()
        with redirect_stdout(output):
            impact.main([])
        text = output.getvalue()
        self.assertIn("BLIND_TWO_STEP_V0_8_1_PROVISIONAL_REPLACE_WITH_TEST_V3", text)
        self.assertIn("low | 1.694829 | 0.000000 | 1.694829 | 7.242857%", text)
        self.assertIn("mid | 1.694829 | 3.715586 | 5.410414 | 23.121429%", text)
        self.assertIn("high | 1.694829 | 7.431171 | 9.126000 | 39.000000%", text)
        self.assertNotIn("USD/yr", text)
        self.assertNotIn("10%", text)

    def test_exact_sourced_split_matches_published_test_table(self):
        split = impact.observed_test_split()
        self.assertEqual(split["maps"], 14)
        self.assertEqual(split["totals"]["frontier"],
                         {"this_port_lifts": 124, "projected_later_lifts": 56, "total_lifts": 180})
        self.assertEqual(split["totals"]["two_step"],
                         {"this_port_lifts": 150, "projected_later_lifts": 170, "total_lifts": 320})
        self.assertAlmostEqual(split["this_port_share_of_saved_lifts"], 26 / 140)

    def test_low_credits_no_projected_lifts_and_mid_is_sensitivity(self):
        report = impact.impact_report()
        low, mid, high = [report["cases"][key] for key in ("low", "mid", "high")]
        self.assertEqual(low["credited_projected_later_lifts_per_map"], 0)
        self.assertAlmostEqual(low["credited_saved_lifts_per_map"], 23.4 * 0.39 * 26 / 140)
        self.assertAlmostEqual(mid["credited_saved_lifts_per_map"],
                               (low["credited_saved_lifts_per_map"] + high["credited_saved_lifts_per_map"]) / 2)
        self.assertAlmostEqual(high["credited_saved_lifts_per_map"], 23.4 * 0.39)

    def test_replacement_bundle_changes_baseline_reduction_and_split(self):
        replacement = impact.SavingsInputs("test-v3-example", 20, 0.2, 0.75, 10,
                                          "unit-test fixture", "unit-test fixture")
        self.assertEqual(impact.credited_savings(replacement, 0)["credited_saved_lifts_per_map"], 3)
        self.assertEqual(impact.credited_savings(replacement, 1)["credited_saved_lifts_per_map"], 4)
        for value in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                impact.credited_savings(replacement, value)
            for key in ("avoided_fraction", "this_port_share_of_saved_lifts"):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    replace(replacement, **{key: value})

    def test_measured_signed_tradeoff_includes_added_repair_moves(self):
        baseline = impact.MoveCounts(yard_rehandles=150, restowed_boxes=5)
        # Candidate includes all preparation/repair/fallback moves in its totals.
        candidate = impact.MoveCounts(yard_rehandles=165, restowed_boxes=4)
        result = impact.compare_counts(SCENARIOS[1], baseline, candidate)
        self.assertEqual(result.yard_moves, -15)
        self.assertEqual(result.quay_moves, 2)
        self.assertEqual(result.total_usd, -150)
        self.assertAlmostEqual(result.yard_equipment_hours, -0.75)
        self.assertAlmostEqual(result.quay_crane_hours, 0.08)
        self.assertAlmostEqual(result.berth_hours, 0.04)

    def test_measured_zero_baseline_and_no_change(self):
        zero = impact.MoveCounts(0, 0)
        self.assertEqual(impact.compare_counts(SCENARIOS[1], zero, zero).total_usd, 0)
        result = impact.compare_counts(SCENARIOS[1], zero, impact.MoveCounts(2, 1))
        self.assertEqual(result.total_usd, -190)
        self.assertEqual(result.quay_moves, -2)
        self.assertAlmostEqual(result.berth_hours, -0.04)

    def test_measured_comparison_reverses_every_category(self):
        baseline = impact.MoveCounts(yard_rehandles=150, restowed_boxes=5)
        candidate = impact.MoveCounts(yard_rehandles=165, restowed_boxes=4)
        forward = impact.compare_counts(SCENARIOS[1], baseline, candidate)
        reverse = impact.compare_counts(SCENARIOS[1], candidate, baseline)
        for name in forward.__dataclass_fields__:
            with self.subTest(name=name):
                self.assertAlmostEqual(getattr(reverse, name), -getattr(forward, name))
        self.assertEqual(reverse.total_usd, 150)

    def test_measured_comparison_uses_explicit_fractional_counts(self):
        scenario = replace(SCENARIOS[1], retrieved_boxes=0, restowed_boxes=0)
        result = impact.compare_counts(
            scenario, impact.MoveCounts(0.5, 0.25), impact.MoveCounts(0, 0),
        )
        self.assertEqual(result.yard_moves, 0.5)
        self.assertEqual(result.quay_moves, 0.5)
        self.assertEqual(result.total_usd, 47.5)

    def test_measured_counts_reject_invalid_inputs(self):
        for value in (-1, float("nan"), float("inf")):
            for field in ("yard_rehandles", "restowed_boxes"):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    impact.MoveCounts(**{field: value})

    def test_zero_and_linear_sensitivity(self):
        for scenario in SCENARIOS:
            self.assertEqual(estimate(scenario, 0).total_usd, 0)
            low = estimate(scenario, 0.05)
            high = estimate(scenario, 0.20)
            for name in low.__dataclass_fields__:
                self.assertAlmostEqual(getattr(high, name), 4 * getattr(low, name))

    def test_separate_savings_and_two_lifts_per_restow(self):
        scenario = replace(SCENARIOS[1], restowed_boxes=1)
        quay_only = estimate(scenario, 0, 1)
        self.assertEqual(quay_only.yard_moves, 0)
        self.assertEqual(quay_only.quay_moves, 2)
        self.assertEqual(quay_only.quay_usd, 2 * scenario.quay_usd_per_move)
        yard_only = estimate(scenario, 1, 0)
        self.assertEqual(yard_only.quay_moves, 0)
        self.assertEqual(yard_only.berth_hours, 0)
        self.assertEqual(yard_only.yard_moves, scenario.yard_rehandles)

    def test_berth_hours_never_sum_parallel_crane_or_yard_work(self):
        for factor in (0, 0.5, 1):
            scenario = replace(SCENARIOS[1], berth_critical_path_fraction=factor)
            result = estimate(scenario, 1)
            self.assertAlmostEqual(result.berth_hours, result.quay_crane_hours * factor)
            self.assertLessEqual(result.berth_hours, result.quay_crane_hours)
            yard_only = replace(scenario, restowed_boxes=0, hatch_cover_lifts=0)
            self.assertEqual(estimate(yard_only, 1).berth_hours, 0)

    def test_reject_invalid_inputs(self):
        for value in (-0.1, 1.1, float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    estimate(SCENARIOS[1], value)
                with self.assertRaises(ValueError):
                    estimate(SCENARIOS[1], 0.1, value)
                with self.assertRaises(ValueError):
                    replace(SCENARIOS[1], berth_critical_path_fraction=value)
        for field in ('retrieved_boxes', 'yard_rehandles_per_box', 'restowed_boxes',
                      'yard_usd_per_move', 'quay_usd_per_move',
                      'yard_minutes_per_move', 'quay_minutes_per_move',
                      'hatch_cover_lifts', 'hatch_usd_per_move', 'hatch_minutes_per_move'):
            for value in (-1, float('nan'), float('inf')):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    replace(SCENARIOS[1], **{field: value})


class HatchCoverTests(unittest.TestCase):
    """D85: a hatch-cover lift is priced and timed like one quay-crane move."""

    def test_hatch_lifts_priced_like_quay_moves(self):
        mid = SCENARIOS[1]
        self.assertEqual(mid.hatch_usd_per_move, mid.quay_usd_per_move)
        self.assertEqual(mid.hatch_minutes_per_move, mid.quay_minutes_per_move)

    def test_hatch_only_saving_is_valued(self):
        # The tiny case behind D85: the plan beats the baseline only on projected later-port hatch lifts.
        result = impact.compare_counts(SCENARIOS[1], impact.MoveCounts(hatch_cover_lifts=4),
                                       impact.MoveCounts(hatch_cover_lifts=2))
        self.assertEqual(result.hatch_moves, 2)
        self.assertEqual(result.total_usd, 2 * SCENARIOS[1].hatch_usd_per_move)
        self.assertAlmostEqual(result.quay_crane_hours, 2 * SCENARIOS[1].hatch_minutes_per_move / 60)
        self.assertAlmostEqual(result.berth_hours, result.quay_crane_hours * SCENARIOS[1].berth_critical_path_fraction)
        self.assertEqual(result.weighted_lifts, 2)

    def test_counts_without_hatch_keep_previous_values(self):
        result = impact.compare_counts(SCENARIOS[1], impact.MoveCounts(150, 5), impact.MoveCounts(165, 4))
        self.assertEqual(result.hatch_moves, 0)
        self.assertEqual(result.total_usd, -150)


if __name__ == '__main__':
    unittest.main()
