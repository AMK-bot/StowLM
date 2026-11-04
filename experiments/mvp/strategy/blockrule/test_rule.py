"""Geometry/allocation regressions; no held-out maps are opened by these tests."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from experiments.mvp.strategy.blockrule import rule
from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel


def instance(size="medium", station_count=3, pods=("EARLY", "LATE")):
    stacks, cells = [], []
    for bay in range(1, 2 * station_count, 2):
        for side, row in (("P", 2), ("S", 1)):
            for zone, deck, tier in (("hold", False, 2), ("deck", True, 82)):
                sid = f"{bay:03d}{row:02d}{'D' if deck else 'H'}"
                cid = f"{bay:03d}{row:02d}{tier:02d}"
                stacks.append(NS(id=sid, bay=bay, hatch_cover_id=f"{bay}-{side}", on_deck=deck))
                cells.append(NS(id=cid, stack_id=sid, lcg_m=100 - bay * 5))
    boxes = [NS(id=f"b-{i}", pod=pod, length_ft=20) for i, pod in enumerate(pods)]
    return NS(size_class=size, call_index=0, containers=boxes,
              rotation=NS(calls=[NS(index=0, locode="HERE"),
                                NS(index=2, locode="LATE"), NS(index=1, locode="EARLY")]),
              load_list=[box.id for box in boxes], discharge_list=[], onboard=NS(placements=[]),
              vessel=NS(stacks=stacks, cells=cells, hatch_covers=[]))


class RuleTests(unittest.TestCase):
    def layout(self, inst):
        with patch.object(rule, "booking_category", lambda box, _: (box.pod, "H")):
            return rule.port_blocking_layout(inst)

    def test_medium_groups_both_sides_and_zones(self):
        layout = self.layout(instance())
        meta = layout.metadata
        self.assertEqual(meta["port_order"], ["EARLY", "LATE"])
        self.assertTrue(meta["paired_transverse_covers"])
        self.assertFalse(meta["lateral_split_possible"])
        self.assertEqual(len(meta["stations"]), 3)
        for station in meta["stations"]:
            self.assertEqual(len(station["covers"]), 2)
            self.assertEqual(len(station["blocks"]), 4)
        early, late = meta["assignments"]
        self.assertLess(max(early["station_indices"]), min(late["station_indices"]))
        self.assertTrue(set(early["blocks"]).isdisjoint(late["blocks"]))
        self.assertGreater(meta["stations"][0]["lcg_m"], meta["stations"][-1]["lcg_m"])

    def test_small_retains_explicit_side_split_and_serializes(self):
        layout = self.layout(instance(size="small", station_count=1))
        self.assertTrue(layout.metadata["lateral_split_possible"])
        self.assertEqual(len(layout.metadata["stations"]), 2)
        self.assertTrue(layout.allowed["EARLY", "H"].isdisjoint(layout.allowed["LATE", "H"]))
        self.assertEqual(layout.to_dict(), BlockLayout.from_dict(layout.to_dict()).to_dict())

    def test_short_medium_never_splits_sides(self):
        layout = self.layout(instance(station_count=1))
        self.assertTrue(layout.metadata["shared_station_due_to_short_slice"])
        self.assertEqual(layout.allowed["EARLY", "H"], layout.allowed["LATE", "H"])
        self.assertEqual(len(layout.allowed["EARLY", "H"]), 4)

    def test_partition_prioritizes_capacity_and_assigns_every_group(self):
        self.assertEqual(rule._partition([2, 2, 8], [4, 8]), [(0, 1), (2,)])
        self.assertEqual(rule._partition([8, 2, 2], [8, 4]), [(0,), (1, 2)])
        self.assertIsNone(rule._partition([8], [4, 4]))

    def test_retained_cargo_reduces_capacity_but_discharge_does_not(self):
        inst = instance(station_count=2)
        inst.onboard.placements = [NS(container_id="through", slot=inst.vessel.cells[0].id),
                                   NS(container_id="off", slot=inst.vessel.cells[1].id)]
        inst.discharge_list = ["off"]
        layout = self.layout(inst)
        self.assertEqual([s["capacity_free_cells"] for s in layout.metadata["stations"]], [3, 4])

    def test_all_weight_classes_of_same_pod_get_same_permissions(self):
        inst = instance(pods=("EARLY", "EARLY", "LATE"))
        with patch.object(rule, "booking_category", lambda box, _: (box.pod, box.id)):
            layout = rule.port_blocking_layout(inst)
        self.assertEqual(layout.allowed["EARLY", "b-0"], layout.allowed["EARLY", "b-1"])


class CheckedSelectionTests(unittest.TestCase):
    def setUp(self):
        from experiments.mvp.strategy.blockrule.study import best_variant, choose
        self.choose, self.best_variant = choose, best_variant

    def test_default_retained_on_tie_worse_or_rule_failure(self):
        baseline = {"passed": True, "moves": 10, "finished_path": "default.json"}
        for candidate in ({"passed": True, "moves": 10}, {"passed": True, "moves": 12},
                          {"passed": False, "moves": 0}, {"solver_complete": True, "raw_moves": 1}):
            self.assertEqual(self.choose(baseline, candidate)["arm"], "default")

    def test_only_checked_improvement_or_rescue_selects_rule(self):
        self.assertEqual(self.choose({"passed": True, "moves": 10},
                                     {"passed": True, "moves": 9})["arm"], "rule")
        self.assertEqual(self.choose({"passed": False},
                                     {"passed": True, "moves": 19})["arm"], "rule")

    def test_failed_compaction_keeps_raw_and_success_can_rescue(self):
        raw = {"passed": True, "moves": 10}
        self.assertEqual(self.best_variant(raw, {"passed": False}), raw)
        self.assertEqual(self.best_variant(raw, {"passed": True, "moves": 11}), raw)
        self.assertEqual(self.best_variant(raw, {"passed": True, "moves": 10}), raw)
        compact = {"passed": True, "moves": 9}
        self.assertEqual(self.best_variant(raw, compact), compact)
        self.assertEqual(self.best_variant({"passed": False}, compact), compact)


class CampaignImportTests(unittest.TestCase):
    def test_campaign_imports_own_zero_argument_report_renderer(self):
        import inspect
        from experiments.mvp.strategy.blockrule import campaign
        self.assertEqual(campaign.render.__module__, "experiments.mvp.strategy.blockrule.report")
        self.assertEqual(len(inspect.signature(campaign.render).parameters), 0)


class ReportStatsTests(unittest.TestCase):
    def test_rule_losses_remain_visible_after_planner_fallback(self):
        from experiments.mvp.strategy.blockrule import report
        from experiments.mvp.strategy.blockrule.study import choose
        rows = []
        for default_cost, rule_cost in ((10, 8), (10, 12), (None, 20)):
            baseline = dict(passed=default_cost is not None, moves=default_cost, arm="default")
            candidate = dict(passed=True, moves=rule_cost, arm="rule")
            pair = dict(default=baseline, rule=candidate, planner=choose(baseline, candidate))
            rows.append(dict(complete=True, selected={"raw": pair}))
        with patch.object(report, "checked", lambda row: row["moves"] if row["passed"] else None):
            stats = report.stats(rows, "raw")
        self.assertEqual((stats["rule_mutual"], stats["rule_fewer"], stats["rule_more"]), (2, 1, 1))
        self.assertEqual((stats["fewer"], stats["equal"], stats["more"]), (1, 1, 0))
        self.assertEqual((stats["rescues"], stats["lost"]), (1, 0))
        self.assertEqual(stats["rule_moves_sign_p"], 1.)


class AutomationTests(unittest.TestCase):
    def test_validation_claim_precedes_launch_and_prevents_second_campaign(self):
        from experiments.mvp.strategy.blockrule import campaign
        sources = {"fake.py": "source-hash"}
        frozen = dict(sources=sources, validation_manifest_sha256="fake-hash")
        with tempfile.TemporaryDirectory(prefix="blockrule-automation-") as folder:
            here = Path(folder)
            (here / "train-freeze.json").write_text(json.dumps(frozen))
            (here / "cohort-small-val.json").write_text("{}")
            child = NS(returncode=0, poll=lambda: 0)

            def launch(name, manifest, workers):
                self.assertTrue((here / "validation-once.json").exists())
                self.assertEqual((name, workers), ("small-val", 3))
                return child

            with patch.object(campaign, "HERE", here), \
                 patch.object(campaign.study, "source_snapshot", return_value=sources), \
                 patch.object(campaign.study, "digest", return_value="fake-hash"), \
                 patch.object(campaign.study, "jobs_from_manifest", return_value=({}, [{}] * 55)), \
                 patch.object(campaign, "launch", side_effect=launch) as launcher, \
                 patch.object(campaign, "render"):
                campaign.validate(3)
                with self.assertRaises(FileExistsError):
                    campaign.validate(3)
                self.assertEqual(launcher.call_count, 1)

    def test_freeze_rejects_incomplete_train_before_writing(self):
        from experiments.mvp.strategy.blockrule import campaign
        with tempfile.TemporaryDirectory(prefix="blockrule-freeze-") as folder:
            here = Path(folder)
            out = here / "runs" / "small-train"
            out.mkdir(parents=True)
            (out / "completion.json").write_text(json.dumps({"errors": [{"map_id": "incomplete"}]}))
            with patch.object(campaign, "HERE", here):
                with self.assertRaisesRegex(RuntimeError, "incomplete maps/errors"):
                    campaign.freeze()
            self.assertFalse((here / "train-freeze.json").exists())


if __name__ == "__main__":
    unittest.main()
