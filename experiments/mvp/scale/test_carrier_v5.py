"""Mocked deadline and geometry-scope checks; no solver or corpus input."""
from contextlib import ExitStack
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import carrier_v5


class CarrierV5Tests(unittest.TestCase):
    def test_native_greedy_sixty_then_direct_constructor_gets_ninety_and_original_geometry(self):
        from stowage.solver import twostep
        from stowage.solver.twostep import PreStowPlan
        clock = [0.]
        instance = NS(onboard=NS(placements=[]))
        geometry = twostep.securing_geometry_violation

        def greedy(*args, deadline, **kwargs):
            self.assertEqual(deadline, 60.)
            self.assertIsNot(twostep.securing_geometry_violation, geometry)
            clock[0] = 60.
            return PreStowPlan(status="timeout")

        def construct(*args, deadline, **kwargs):
            self.assertEqual(deadline, 150.)
            self.assertIs(twostep.securing_geometry_violation, geometry)
            clock[0] = 145.
            return PreStowPlan(), {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(carrier_v5, "monotonic", side_effect=lambda: clock[0]))
            stack.enter_context(patch("stowage.rules.catalogue.rule_freeze_v1_enabled", return_value=False))
            stack.enter_context(patch.object(twostep, "_use_fast_prestow", return_value=True))
            stack.enter_context(patch.object(twostep, "_canonical_instance", return_value=instance))
            stack.enter_context(patch.object(twostep, "make_feasibility_engine", return_value=NS()))
            greedy_mock = stack.enter_context(patch.object(twostep, "_fast_prestow", side_effect=greedy))
            constructor = stack.enter_context(patch("stowage.solver.twostep_construct.construct_preplan", side_effect=construct))
            cp = stack.enter_context(patch("packing_v3.joint_preplan", side_effect=AssertionError("CP omitted")))
            handle = carrier_v5.install()
            stack.callback(handle.restore)
            result = twostep._preplan(instance, beam_width=4, deadline=150.)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.search_metrics["carrier_stages"]["constructor"]["budget_s"], 90.)
        self.assertEqual(set(result.search_metrics["carrier_stages"]), {"extended_greedy", "constructor"})
        self.assertEqual((greedy_mock.call_count, constructor.call_count), (1, 1))
        cp.assert_not_called()
        self.assertIs(twostep.securing_geometry_violation, geometry)

    def test_deck_screen_preserves_native_action_set_and_hold_result(self):
        from stowage.actions.space import Place
        from stowage.conventions import format_slot
        actions = [Place("a", format_slot(1, 1, 2)), Place("b", format_slot(1, 1, 82))]
        original_actions = tuple(actions)
        calls = []

        def original(instance, ex, action, *, include_existing=False):
            calls.append((action, include_existing))
            return "20-foot box over a 40/45-foot box" if include_existing else None

        metrics = {}
        screened = carrier_v5.deck_geometry_screen(original, metrics)
        self.assertIsNone(screened(NS(), NS(), actions[0]))
        self.assertEqual(screened(NS(), NS(), actions[1]), "20-foot box over a 40/45-foot box")
        self.assertEqual(tuple(actions), original_actions)
        self.assertEqual(calls, [(actions[0], False), (actions[1], False), (actions[1], True)])
        self.assertEqual(metrics["deck_geometry_rejections"], 1)

    def test_existing_geometry_reason_is_never_replaced_or_rechecked(self):
        from stowage.actions.space import Place
        from stowage.conventions import format_slot
        calls = []

        def original(*args, **kwargs):
            calls.append(1)
            return "original geometry reason"

        screened = carrier_v5.deck_geometry_screen(original, {})
        self.assertEqual(screened(NS(), NS(), Place("a", format_slot(1, 1, 82))), "original geometry reason")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
