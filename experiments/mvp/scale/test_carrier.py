"""Mocked carrier slice/deadline tests; no corpus or solver execution."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carrier


class CarrierTests(unittest.TestCase):
    def setup_portfolio(self, stack, *, greedy, cp=None, constructor=None, onboard=False):
        from stowage.solver import twostep
        from stowage.solver.twostep import PreStowPlan
        self.clock = 0.
        instance = NS(onboard=NS(placements=["retained"] if onboard else []))
        stack.enter_context(patch.object(carrier, "monotonic", side_effect=lambda: self.clock))
        stack.enter_context(patch("stowage.rules.catalogue.rule_freeze_v1_enabled", return_value=False))
        stack.enter_context(patch.object(twostep, "_use_fast_prestow", return_value=True))
        stack.enter_context(patch.object(twostep, "_canonical_instance", return_value=instance))
        stack.enter_context(patch.object(twostep, "make_feasibility_engine", return_value=NS()))
        greedy_mock = stack.enter_context(patch.object(twostep, "_fast_prestow", side_effect=greedy))
        cp_effect = cp or (lambda *a, **k: (None, {}))
        cp_mock = stack.enter_context(patch("packing_v3.joint_preplan", side_effect=cp_effect))
        construct_mock = stack.enter_context(patch("stowage.solver.twostep_construct.construct_preplan",
            side_effect=constructor or (lambda *a, **k: (PreStowPlan(status="dead_end"), {}))))
        handle = carrier.install()
        stack.callback(handle.restore)
        return instance, twostep, greedy_mock, cp_mock, construct_mock

    def test_extended_native_greedy_gets_sixty_of_one_hundred_fifty_seconds(self):
        from stowage.solver.twostep import PreStowPlan

        def greedy(*args, deadline, **kwargs):
            self.assertEqual(deadline, 60.)
            self.clock = 20.
            return PreStowPlan()

        with ExitStack() as stack:
            instance, module, _, cp, constructor = self.setup_portfolio(stack, greedy=greedy)
            result = module._preplan(instance, beam_width=4, deadline=150.)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.search_metrics["carrier_selected_stage"], "extended_greedy")
        cp.assert_not_called()
        constructor.assert_not_called()

    def test_late_greedy_is_dropped_then_cp_and_direct_constructor_get_remaining_slices(self):
        from stowage.solver.twostep import PreStowPlan

        def greedy(*args, **kwargs):
            self.clock = 61.
            return PreStowPlan()

        def cp(*args, deadline, **kwargs):
            self.assertEqual(deadline, 96.)
            self.clock = 95.
            return None, {"packing_status": "proposal_unknown"}

        def constructor(*args, deadline, **kwargs):
            self.assertEqual(deadline, 150.)
            self.clock = 140.
            return PreStowPlan(), {"constructor_status": "ok"}

        with ExitStack() as stack:
            instance, module, greedy_mock, cp_mock, construct = self.setup_portfolio(
                stack, greedy=greedy, cp=cp, constructor=constructor)
            result = module._preplan(instance, beam_width=4, deadline=150.)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.search_metrics["preplan_greedy_primary_status"], "timeout")
        self.assertEqual(result.search_metrics["carrier_stages"]["constructor"]["budget_s"], 55.)
        self.assertEqual((greedy_mock.call_count, cp_mock.call_count, construct.call_count), (1, 1, 1))

    def test_no_late_constructor_candidate_can_escape_shared_deadline(self):
        from stowage.solver.twostep import PreStowPlan

        def constructor(*args, **kwargs):
            self.clock = 151.
            return PreStowPlan(), {}

        with ExitStack() as stack:
            instance, module, *_ = self.setup_portfolio(stack, greedy=lambda *a, **k: PreStowPlan(status="dead_end"),
                                                       constructor=constructor)
            result = module._preplan(instance, beam_width=4, deadline=150.)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.search_metrics["carrier_selected_stage"], "none")

    def test_nonempty_ship_delegates_to_original_search(self):
        from stowage.solver import twostep
        from stowage.solver.twostep import PreStowPlan

        with ExitStack() as stack:
            original = stack.enter_context(patch.object(twostep, "_preplan", return_value=PreStowPlan()))
            instance, module, greedy, cp, constructor = self.setup_portfolio(stack, onboard=True,
                greedy=lambda *a, **k: self.fail("unsupported ship must delegate"))
            result = module._preplan(instance, beam_width=4, deadline=150.)
        self.assertEqual(result.status, "ok")
        original.assert_called_once()
        greedy.assert_not_called()
        cp.assert_not_called()
        constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
