"""Candidate-only tests; main frozen implementation is not modified."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import packing_v3
from test_packing import toy


class CandidateV3Tests(unittest.TestCase):
    def condition(self, *, heavy=False, hold_full=False, fixed_heavy=False):
        from ortools.sat.python import cp_model
        model = cp_model.CpModel()
        deck = model.NewBoolVar("deck")
        hold = model.NewBoolVar("hold")
        model.Add(deck == 1)
        model.Add(hold == int(hold_full))
        packing_v3.add_e6_full_hold_condition(model, deck_heavy=[deck] if heavy else [],
                                            hold_occupied=[hold], fixed_deck_heavy=fixed_heavy)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 1
        return solver.Solve(model)

    def test_low_mass_deck_cargo_does_not_require_full_hold(self):
        from ortools.sat.python import cp_model
        self.assertEqual(self.condition(), cp_model.OPTIMAL)

    def test_high_mass_deck_cargo_and_empty_hold_is_excluded(self):
        from ortools.sat.python import cp_model
        self.assertEqual(self.condition(heavy=True), cp_model.INFEASIBLE)

    def test_high_mass_deck_cargo_with_full_hold_is_allowed(self):
        from ortools.sat.python import cp_model
        self.assertEqual(self.condition(heavy=True, hold_full=True), cp_model.OPTIMAL)

    def test_retained_high_mass_deck_cargo_also_requires_full_hold(self):
        from ortools.sat.python import cp_model
        self.assertEqual(self.condition(fixed_heavy=True), cp_model.INFEASIBLE)

    def test_replay_uses_scoped_membership_even_when_full_mask_is_available(self):
        instance, tables, groups = toy(deck=True)
        instance.containers = [NS(id=cid, gross_mass_kg=10000.) for cid in ("a", "b", "c")]
        calls = []

        def scoped(action):
            calls.append(action)
            return False

        def full_mask():
            self.fail("candidate must not enumerate unrelated full legal mask")

        engine = NS(is_legal=scoped, legal_index=full_mask)
        with patch("stowage.solver.twostep_adapter.make_planning_engine", return_value=engine), \
             patch("stowage.actions.expander.Expander", return_value=NS()):
            proposal, metrics = packing_v3.replay_assignment(instance, tables, groups,
                [(0, 0), (1, 2), (2, 5)], deadline=time.monotonic() + 5)
        self.assertIsNone(proposal)
        self.assertEqual(metrics["packing_replay_status"], "native_rejected")
        self.assertEqual(len(calls), 3)

    def test_failure_diagnostics_bound_count_and_separate_public_rules_from_native_screens(self):
        from stowage.conventions import make_container_id
        instance, tables, _ = toy(deck=True)
        instance.onboard = NS(model_copy=lambda update: NS(**update))
        instance.call_index = 0
        retained = make_container_id('QZXU', 1)
        ex = NS(where={retained: ("ship", tables.slot_ids[0])})
        boxes = {cid: NS(id=cid) for cid in ("a", "b", "c", "d", "e")}
        samples = [(i, 2, cid, "native_mask", None) for i, cid in enumerate(boxes)]
        hard = NS(rule_id="expert.physical.stack_weight", severity="hard", message="Stack is too heavy.",
                  containers=["b", retained], slots=[tables.slot_ids[2]])
        calls = []

        def rules(canonical, ship, box, slot, *, call_index):
            calls.append(box.id)
            self.assertEqual(len(ship.placements), 1)
            self.assertEqual(ship.placements[0].container_id, retained)
            return [hard] if box.id == "b" else []

        with patch("stowage.checker.rules.placement_violations", side_effect=rules):
            rows = packing_v3.replay_failure_diagnostics(instance, ex, tables, boxes, samples,
                                                       deadline=time.monotonic() + 5)
        self.assertEqual(len(rows), 4)
        self.assertEqual(calls, ["a", "b", "c", "d"])
        self.assertEqual(rows[0]["classification"], "native_mask_rejected_without_public_local_hard_rule")
        self.assertEqual(rows[1]["classification"], "public_local_hard_rule")
        self.assertEqual(rows[1]["violations"][0]["containers"], ["b", retained])
        self.assertEqual(ex.where, {retained: ("ship", tables.slot_ids[0])})

    def test_failure_diagnostics_do_not_start_after_deadline(self):
        instance, tables, _ = toy(deck=True)
        instance.onboard = NS(model_copy=lambda update: NS(**update))
        with patch("stowage.checker.rules.placement_violations") as rules:
            rows = packing_v3.replay_failure_diagnostics(instance, NS(where={}), tables, {"a": NS(id="a")},
                [(0, 0, "a", "native_mask", None)], deadline=time.monotonic() - 1)
        self.assertEqual(rows, [])
        rules.assert_not_called()


if __name__ == "__main__":
    unittest.main()
