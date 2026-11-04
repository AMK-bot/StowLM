"""Small synthetic artifact tests; no planner, checker, map generation or GPU."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("fillsweep_report", Path(__file__).with_name("report.py"))
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fillsweep-report-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.here = self.root / "study"
        self.here.mkdir()
        self.run = self.here / "runs/main"

    def save(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def certificate(self, arm="default", cost=10):
        report_path, finished_path = f"{arm}-report.json", f"{arm}-finished.json"
        report_hash = self.save(self.root / report_path, dict(passed=True, violations=[], count=dict(weighted_nonproductive=cost)))
        plan_hash = self.save(self.root / finished_path, dict(status="ok", plan=dict(moves=[])))
        return dict(passed=True, checker_attempted=True, checker_passed=True,
                    finalize_status="ok", layout_violations=[], report_path=report_path,
                    finished_path=finished_path, report_sha256=report_hash,
                    finished_sha256=plan_hash, moves=cost, solver_complete=True, arm=arm)

    def test_cost_requires_acceptance_and_matching_saved_artifacts(self):
        cert = self.certificate()
        self.assertEqual(report.checked_cost(cert, self.root), 10)
        for field, value in (("moves", 11), ("report_sha256", "wrong"),
                             ("finished_sha256", "wrong"), ("checker_passed", False),
                             ("finalize_status", "error"), ("layout_violations", ["bad layout"])):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                report.checked_cost({**cert, field:value}, self.root)
        # Matching hashes alone do not certify a rejecting full report.
        cert["report_sha256"] = self.save(self.root / cert["report_path"], dict(passed=False, violations=[], count=dict(weighted_nonproductive=10)))
        with self.assertRaises(AssertionError):
            report.checked_cost(cert, self.root)
        rejected = report.outcome({"input":"map.json"}, {"arms":{"default":{"raw":cert}}}, "default", "raw", self.root)
        self.assertEqual(rejected["stage"], "certificate_invalid")
        self.assertFalse(rejected["resolved"])
        self.assertIsNone(rejected["cost"])

    def test_paired_ladder_render_and_first_crossing(self):
        base, rule = self.certificate("default", 10), self.certificate("rule", 8)
        jobs = []
        for fill in report.FILLS:
            for seed in range(3):
                job = dict(map_id=f"f{fill}-s{seed}", ship="feeder_500", fill_target=fill,
                           mass_level="high", seed=seed, split="train", input="map.json",
                           generation_status="accepted", physical_cell_fill=fill/100,
                           n_loads=fill+seed, cargo_tonnes=100+fill+seed,
                           generator_mass_fraction=(fill+seed)/100,
                           fixed_background_mass_kg=1000+100*seed,
                           vessel_sha256=f"parent-{seed}",
                           weight_classes=dict(H=seed, M=fill, L=2))
                jobs.append(job)
                passed = fill < 60 or seed == 0
                certs, arms = {}, {}
                for name, cert in (("default", base), ("rule", rule)):
                    # A bogus unchecked cost must never enter mutual-pass means.
                    c = cert if passed else dict(passed=False, solver_complete=False,
                        checker_attempted=False, finalize_status="not_reached", moves=999)
                    certs[name] = c
                    arms[name] = dict(solver_complete=passed, status="ok" if passed else "dead_end",
                        metrics=dict(preplan_status="ok" if passed else "dead_end"), raw=c, compact=c)
                selected = {variant:{**certs, "planner":certs["rule"]} for variant in ("raw", "compact")}
                self.save(self.run / "maps" / job["map_id"] / "summary.json",
                          dict(job=job, complete=True, arms=arms, selected=selected))
        self.save(self.here / "cohort.json", dict(split="train", maps=jobs))
        result = report.render(self.here, self.root, self.run)
        self.assertEqual(result["planned_maps"], 15)
        self.assertEqual(result["complete_maps"], 15)
        design = next(c for c in result["design_cells"] if c["target_fill_pct"] == 40)
        self.assertEqual(design["loads"], [40, 42])
        self.assertEqual(design["cargo_tonnes"], [140, 142])
        self.assertEqual(design["generator_mass_budget_pct"], [40, 42])
        self.assertEqual(design["weight_classes"]["H"], [0, 2])
        self.assertEqual(len(result["backgrounds"]), 3)
        self.assertEqual(result["backgrounds"][0]["fixed_background_tonnes"], [1, 1])
        for variant in ("raw", "compact"):
            for crossing in result["variants"][variant]["thresholds"]:
                self.assertEqual(crossing["fill_pct"], 60)
                self.assertEqual(crossing["neighbors"]["previous"]["passes"], 3)
                self.assertEqual(crossing["neighbors"]["next"]["passes"], 1)
            for cell in result["variants"][variant]["cells"]:
                if cell["target_fill_pct"] == 60:
                    self.assertEqual(cell["passes"], 1)
                    self.assertEqual(cell["stages"]["carrier"], 2)
            for pair in result["variants"][variant]["mutual"]:
                self.assertEqual(pair["default_mean"], 10)
                self.assertEqual(pair["rule_mean"], 8)
                self.assertEqual(pair["mean_difference"], -2)
        self.assertIn("Registered maps: **90**; published planned rows: **15**", (self.here / "FILLSWEEP.md").read_text())
        self.assertNotIn("999", (self.here / "FILLSWEEP.md").read_text())

    def test_stage_attribution_and_pending(self):
        def stage(metrics, cert, complete=True):
            row = dict(arms=dict(default=dict(solver_complete=complete, metrics=metrics, raw=cert)))
            return report.outcome({"input":"map.json"}, row, "default", "raw", self.root)
        not_reached = dict(passed=False, finalize_status="not_reached")
        self.assertEqual(stage(dict(preplan_status="dead_end"), not_reached, False)["stage"], "carrier")
        self.assertEqual(stage(dict(preplan_status="ok", terminal_status="timeout"), not_reached, False)["stage"], "terminal")
        self.assertEqual(stage({}, dict(passed=False, finalize_status="compaction_unavailable"))["stage"], "compaction")
        self.assertEqual(stage({}, dict(passed=False, finalize_status="error", checker_attempted=True, checker_passed=False, rules=["visibility"]))["stage"], "finisher")
        self.assertEqual(stage({}, dict(passed=False, finalize_status="ok", checker_attempted=True, checker_passed=False))["stage"], "checker")
        infrastructure = stage({}, dict(passed=False, checker_attempted=True, error="checker exception"))
        self.assertEqual(infrastructure["stage"], "infrastructure")
        self.assertFalse(infrastructure["resolved"])
        self.assertEqual(report.outcome({}, {}, "default", "raw", self.root)["stage"], "pending")
        for status in ("exception", "error"):
            row = dict(arms=dict(default=dict(status=status, solver_complete=False,
                message="Missing pinned dependency", raw=not_reached)))
            wrapper_error = report.outcome({"input":"map.json"}, row, "default", "raw", self.root)
            self.assertEqual(wrapper_error["stage"], "infrastructure")
            self.assertFalse(wrapper_error["resolved"])
            self.assertEqual(wrapper_error["error"], "Missing pinned dependency")
            del row["arms"]["default"]["raw"]
            self.assertEqual(report.outcome({"input":"map.json"}, row, "default", "raw", self.root)["stage"], "infrastructure")

    def test_generation_denominators_and_unresolved_threshold(self):
        generation = report.outcome({"generation_status":"failed"}, {}, "default", "raw", self.root)
        self.assertEqual(generation["stage"], "generation")
        rows = [dict(job={"input":"map.json"}, outcomes={"raw":{"default":dict(stage="pass", resolved=True, cost=10, rules=[])}}),
                dict(job={"generation_status":"failed"}, outcomes={"raw":{"default":generation}})]
        cell = report.aggregate(rows, "default", "raw")
        self.assertEqual((cell["planned"], cell["generated"], cell["generation_failed"]), (2, 1, 1))
        self.assertEqual(cell["pass_fraction"], 1)
        self.assertEqual(cell["pass_fraction_bounds"], [.5, .5])
        self.assertEqual(report.threshold({40:dict(planned=3, pending=1, passes=1, generated=3)})["status"], "pending")
        # A completed exactly-half cell is not a crossing.
        groups = {40:dict(planned=2, pending=0, passes=1, generated=2, generation_failed=0),
                  50:dict(planned=3, pending=0, passes=1, generated=3, generation_failed=0)}
        self.assertEqual(report.threshold(groups)["fill_pct"], 50)


if __name__ == "__main__":
    unittest.main()
