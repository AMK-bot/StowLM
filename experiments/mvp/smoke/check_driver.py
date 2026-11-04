"""Cheap orchestration checks with synthetic stubs; never runs a real solve."""
from contextlib import ExitStack, redirect_stdout
from concurrent.futures import Future
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_smoke as smoke
from summarize import render


class DriverWiringTests(unittest.TestCase):
    def run_stub_case(self, checker_passed=True, interrupt=False, family="random_yard", preset=None, rung="tiny"):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            cli = [
                "--constructor-mode", "v4", "--booking-slack", "0.15",
                "--booking-mass-fraction", "0.55", "--generator-family", family]
            args = smoke.build_parser().parse_args(["--preset", preset] if preset else cli)
            case_name = f"{rung}-feeder_500-0"
            args.output = Path(directory)
            args.source_files_at_start = {}
            instance = SimpleNamespace(id="synthetic-stub", containers=[], load_list=["x"],
                vessel=SimpleNamespace(cells=["x"]))
            plan_result = SimpleNamespace(status="ok", plan=object(), message="stub", metrics={})
            violation = SimpleNamespace(severity="hard", rule_id="S1",
                model_dump=lambda **kw: {"severity": "hard", "rule_id": "S1"})
            report = SimpleNamespace(passed=checker_passed,
                violations=[] if checker_passed else [violation],
                model_dump=lambda **kw: {"passed": checker_passed})
            def save_object(value, path):
                path.write_text("{}")
            generation_target = ("stowage.generate.random_yard.generate_random_instance"
                if family == "random_yard" else "stowage.generate.instances_v2_random.generate_random_v2")
            generation = stack.enter_context(patch(generation_target, return_value=instance))
            solver = stack.enter_context(patch("stowage.solver.scalable.ScalableSolver"))
            def plan(*args, **kw):
                if interrupt:
                    raise KeyboardInterrupt("synthetic interruption")
                return plan_result
            solver.side_effect = lambda config: SimpleNamespace(config=config, plan=plan)
            for target, value in (
                ("stowage.frozen.require_clean", None),
                ("stowage.planner.finalize", plan_result),
                ("stowage.checker.check", report),
                ("stowage.rules.catalogue.rule_freeze_v1_enabled", False),
            ):
                stack.enter_context(patch(target, return_value=value))
            stack.enter_context(patch("stowage.io.save_instance", side_effect=save_object))
            stack.enter_context(patch("stowage.io.save_plan", side_effect=save_object))
            stack.enter_context(patch.object(smoke, "source_hash", return_value="fixed-test-source"))
            stack.enter_context(patch.object(smoke, "source_files", return_value={}))
            def replay(instance, plan, c2, row):
                self.assertFalse(c2, "D79 default must not request C2")
                row.update(passed=True, token_ms=[1, 2], action_ms=[3])
            token = stack.enter_context(patch.object(smoke, "token_replay", side_effect=replay))
            if interrupt:
                with self.assertRaises(KeyboardInterrupt):
                    smoke.run_case("feeder_500", rung, 0, args)
                row = json.loads((args.output / case_name / "result.json").read_text())
                self.assertFalse(row["completed"])
                self.assertTrue(row["interrupted"])
                self.assertFalse(row["passed"])
                self.assertIn("wall_s", row["stages"]["constructor"])
                self.assertEqual(row["first_failure"]["stage"], "constructor")
                with self.assertRaisesRegex(ValueError, "Incomplete/stale"):
                    smoke.run_case("feeder_500", rung, 0, args)
                return row
            row = smoke.run_case("feeder_500", rung, 0, args)
            generation.assert_called_once()
            if preset and rung == "tiny":
                self.assertNotIn("booking_slack", generation.call_args.kwargs)
                self.assertNotIn("booking_mass_fraction", generation.call_args.kwargs)
                self.assertEqual(row["request"]["generator_options"], {})
            else:
                self.assertEqual(generation.call_args.kwargs["booking_slack"], .25 if preset else .15)
                self.assertEqual(generation.call_args.kwargs["booking_mass_fraction"], .55)
                self.assertEqual(row["request"]["generator_options"]["booking_mass_fraction"], .55)
            if family == "random_yard":
                self.assertFalse(generation.call_args.kwargs["c2_screen"])
            config = solver.call_args.args[0]
            self.assertEqual(config.constructor_v4, not bool(preset))
            self.assertEqual(config.feasibility_config.extra["global_screen"], 0)
            token.assert_called_once()
            self.assertTrue(row["completed"])
            self.assertEqual(set(row["stages"]),
                {"generation", "constructor", "ballast", "checker", "mask_c2_off"})
            self.assertTrue(all("wall_s" in stage for stage in row["stages"].values()))
            table = render(args.output)
            self.assertNotIn("| on |", table)
            self.assertIn("Campaign complete: False", table)
            if not checker_passed:
                self.assertIn("| S1 |", table)
            for existing in (False, True):
                args.rerun = existing
                if existing:
                    with self.assertRaisesRegex(ValueError, "immutable"):
                        smoke.run_case("feeder_500", rung, 0, args)
                else:
                    self.assertEqual(smoke.run_case("feeder_500", rung, 0, args), row)
            args.rerun = False
            args.preset = None
            args.booking_mass_fraction = .70
            with self.assertRaisesRegex(ValueError, "Cached result settings/driver differ"):
                smoke.run_case("feeder_500", rung, 0, args)
            return row

    def test_d79_and_constructor_and_slack_are_forwarded(self):
        self.assertTrue(self.run_stub_case()["passed"])

    def test_mass_fraction_is_forwarded_to_v2_generator(self):
        self.assertTrue(self.run_stub_case(family="v2_random")["passed"])

    def test_d83_preset_uses_exact_per_rung_settings_and_labels_medium(self):
        for rung in smoke.RUNGS:
            with self.subTest(rung=rung):
                row = self.run_stub_case(preset="d83", rung=rung)
                self.assertTrue(row["passed"])
                self.assertEqual(row["preset"], "d83")
                self.assertEqual(row["request"]["preset"], "d83")
                self.assertEqual(row["diagnostic_only"], rung == "medium")
                self.assertEqual(row["evidence_role"], "diagnostic" if rung == "medium" else "smoke")

    def test_d83_rejects_conflicting_overrides(self):
        for override in (["--booking-slack", ".25"], ["--booking-mass-fraction", ".55"],
                         ["--constructor-mode", "v4"], ["--compare-c2"],
                         ["--small-boxes", "12"], ["--generator-family", "v2_random"]):
            with self.subTest(override=override):
                args = smoke.build_parser().parse_args(["--preset", "d83", *override])
                with self.assertRaisesRegex(ValueError, "omit conflicting overrides"):
                    smoke.validate_opt_ins(args)

    def test_full_checker_remains_the_gate(self):
        row = self.run_stub_case(checker_passed=False)
        self.assertFalse(row["passed"])
        self.assertEqual(row["first_failure"]["stage"], "checker")
        self.assertEqual(row["first_failure"]["rule"], "S1")

    def test_interrupt_persists_incomplete_evidence_and_reraises(self):
        self.run_stub_case(interrupt=True)

    def test_rule_and_checker_json_content_is_part_of_source_pin(self):
        for name in ("src/stowage/rules/imdg/data/dgl.json",
                     "src/stowage/checker/stability_policy_contract.json"):
            with self.subTest(path=name), TemporaryDirectory() as directory:
                root = Path(directory)
                relative = Path(name)
                path = root / relative
                path.parent.mkdir(parents=True)
                path.write_text("{}")
                with patch.object(smoke, "ROOT", root):
                    before = smoke.source_hash()
                    self.assertIn(str(relative), smoke.source_files())
                    path.write_text('{"changed": true}')
                    self.assertNotEqual(before, smoke.source_hash())

    def test_default_campaign_has_eighteen_cases_and_c2_off(self):
        args = smoke.build_parser().parse_args([])
        self.assertEqual(len(args.ship) * len(args.rung) * args.replicates, 18)
        self.assertFalse(args.compare_c2)
        self.assertIsNone(args.booking_slack)
        self.assertIsNone(args.booking_mass_fraction)
        self.assertEqual(args.constructor_mode, "default")
        self.assertIsNone(args.output)

    def test_invalid_mass_fractions_fail_before_generation(self):
        for value in ("0", "-0.1", "1.01", "nan", "inf"):
            with self.subTest(value=value), patch.object(smoke.sys, "argv", [str(smoke.__file__),
                    "--validate-config", "--booking-mass-fraction", value]), \
                    patch.object(smoke, "validate_opt_ins") as validate, \
                    patch.object(smoke.argparse.ArgumentParser, "error", side_effect=SystemExit(2)):
                with self.assertRaises(SystemExit) as error:
                    smoke.main()
                self.assertEqual(error.exception.code, 2)
                validate.assert_not_called()

    def test_failed_campaign_still_renders_readiness_after_saved_results(self):
        with TemporaryDirectory() as directory, ExitStack() as stack:
            output = Path(directory).resolve()
            stack.enter_context(patch.object(smoke.sys, "argv", [str(smoke.__file__),
                "--output", str(output), "--render-readiness"]))
            stack.enter_context(patch.object(smoke, "validate_opt_ins", return_value={"c2": "off"}))
            stack.enter_context(patch.object(smoke, "source_files", return_value={}))
            stack.enter_context(patch.object(smoke, "source_hash", return_value="fixed-test-source"))
            def failed_case(ship, rung, replicate, args):
                row = dict(case=f"{rung}-{ship}-{replicate}", ship=ship, rung=rung,
                    replicate=replicate, passed=False, completed=True, stages={})
                smoke.save(output / row["case"] / "result.json", row)
                return row
            case = stack.enter_context(patch.object(smoke, "run_case", side_effect=failed_case))
            def render_readiness(run_dir):
                self.assertEqual(run_dir, output)
                self.assertTrue((output / "table.md").exists())
                campaign = json.loads((output / "results.json").read_text())
                self.assertEqual(campaign["processed"], 18)
                self.assertEqual(campaign["passed"], 0)
                self.assertTrue(campaign["campaign_complete"])
            render_hook = stack.enter_context(patch("render_readiness.render", side_effect=render_readiness))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(smoke.main(), 1)
            self.assertEqual(case.call_count, 18)
            render_hook.assert_called_once_with(output)

    def test_three_workers_use_spawn_and_submit_eighteen_distinct_cases(self):
        args = smoke.build_parser().parse_args(["--preset", "d83", "--workers", "3"])
        with patch.object(smoke, "ProcessPoolExecutor") as executor_type:
            executor = executor_type.return_value.__enter__.return_value
            def submit(function, ship, rung, replicate, forwarded):
                self.assertIs(function, smoke.run_case)
                self.assertIs(forwarded, args)
                future = Future()
                future.set_result(dict(case=f"{rung}-{ship}-{replicate}"))
                return future
            executor.submit.side_effect = submit
            rows = list(smoke.execute_cases(args))
            self.assertEqual(len(rows), 18)
            self.assertEqual(len({row["case"] for row in rows}), 18)
            self.assertEqual(executor_type.call_args.kwargs["max_workers"], 3)
            self.assertEqual(executor_type.call_args.kwargs["mp_context"].get_start_method(), "spawn")
            self.assertEqual(executor.submit.call_count, 18)


if __name__ == "__main__":
    unittest.main()
