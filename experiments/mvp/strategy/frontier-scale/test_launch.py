"""Pure scheduling/policy checks; never invoke planners or mutate run artifacts."""
import importlib.util
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("frontier_scale_launch_tests", HERE / "launch.py")
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class SchedulingChecks(unittest.TestCase):
    def test_explicit_hybrid_status(self):
        for text in ("**Status: final.**", "Status: **FINAL**", "Report status: FINAL (audited)"):
            self.assertTrue(launch.hybrid_final_text(text)[0], text)
        for text in ("**Status: incomplete; gate results are provisional.**", "# HYBRID FINAL",
                     "Status: **IN PROGRESS / NOT FINAL**", "Status: NOT FINAL", "The report is FINAL",
                     "Status: FINALIZING", ""):
            self.assertFalse(launch.hybrid_final_text(text)[0], text)

    def test_frozen_sort_and_base_barrier(self):
        manifest = json.loads((HERE / "manifest.json").read_text())
        tasks = launch.tasks(manifest)
        self.assertEqual(len(tasks), 15)
        self.assertEqual([b for b, _ in tasks], ["base"] * 12 + ["x4"] * 3)
        for budget in ("base", "x4"):
            keys = [(j["n_boxes"], j["map_id"]) for b, j in tasks if b == budget]
            self.assertEqual(keys, sorted(keys))
        self.assertEqual(launch.scheduling_phase(tasks[11:], []), "base")
        self.assertEqual(launch.scheduling_phase(tasks[12:], [("base", "publishing-result")]), "base")
        self.assertEqual(launch.scheduling_phase(tasks[12:], []), "x4")

    def test_parallel_projection_and_cap_reduction(self):
        self.assertEqual(launch.phase_duration([10, 10, 10], [], 2), 20)
        self.assertEqual(launch.phase_duration([10, 10, 10], [], 3), 10)
        # Existing workers drain above a reduced target. No new task starts
        # while two of those three workers remain active at target two.
        self.assertEqual(launch.phase_duration([10], [5, 10, 20], 2), 20)
        self.assertEqual(launch.phase_duration([10], [5, 10, 20], 1), 30)
        manifest = json.loads((HERE / "manifest.json").read_text())
        tasks = launch.tasks(manifest)
        base_key = (tasks[0][0], tasks[0][1]["map_id"])
        before = launch.estimate(tasks, workers=2)
        after = launch.estimate(tasks, active={base_key: 10}, workers=2)
        self.assertEqual(after["analytic_remaining_budget_seconds"], before["analytic_remaining_budget_seconds"] - 10)
        self.assertEqual(after["active_map_workers"], 1)
        self.assertLess(before["parallel_budget_projection_at_3_maps_seconds"], before["parallel_budget_projection_at_2_maps_seconds"])
        self.assertNotIn("initial_wall_range_hours", before)


if __name__ == "__main__":
    unittest.main()
