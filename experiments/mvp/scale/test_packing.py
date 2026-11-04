"""Small structural packing tests: no corpus or held-out inputs."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace as NS
import time
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import packing


def toy(*, deck=False, short_heights=(2600, 2600), mass_cap=100000):
    """One adjacent bay pair, two tiers, explicitly selected box domains."""
    from stowage.conventions import format_slot

    cells = [format_slot(bay=b, row=1, tier=t) for b, t in [(1, 82 if deck else 2),
              (1, 84 if deck else 4), (3, 82 if deck else 2), (3, 84 if deck else 4)]]
    slots = [*cells, format_slot(bay=2, row=1, tier=82 if deck else 2),
             format_slot(bay=2, row=1, tier=84 if deck else 4)]
    static = np.array([[1, 1, 1, 1, 0, 0], [1, 1, 1, 1, 0, 0], [0, 0, 0, 0, 1, 1]], bool)
    tables = NS(box_ids=["a", "b", "c"], box_index={"a": 0, "b": 1, "c": 2},
        slot_ids=slots, static=static, length=np.array([20, 20, 40]), rank=np.array([1, 1, 1]),
        e1_threshold=None, e2_tol=0, closes_stack=np.zeros(3, bool), mass=np.array([10000, 10000, 20000]),
        stack_ids=["A", "B"], cell_ids=cells, stack_cells=[[0, 1], [2, 3]],
        stack_deck=np.array([deck, deck]), stack_base_z=np.zeros(2),
        stack_hmax=np.array([6., 6.]), stack_lim_20=np.array([mass_cap, mass_cap]),
        stack_lim_40=np.array([mass_cap, mass_cap]), stack_lim_mixed=np.array([mass_cap, mass_cap]),
        extra={"stack_visibility_top": np.array([6., 6.])},
        slot_deck=np.full(6, deck), slot_k=np.array([[0,-1],[1,-1],[0,-1],[1,-1],[0,0],[1,1]]))
    vessel_cells = [NS(id=c, tcg_m=(-2 if i < 2 else 2), lcg_m=50., vcg_base_m=float(i % 2 * 3))
                    for i, c in enumerate(cells)]
    instance = NS(containers=[], onboard=NS(placements=[]), vessel=NS(cells=vessel_cells, hull=NS(lpp_m=100.)))
    groups = [packing.BookingGroup(["a"], 0, short_heights[0], 10000),
              packing.BookingGroup(["b"], 1, short_heights[1], 10000),
              packing.BookingGroup(["c"], 2, 2600, 10000)]
    return instance, tables, groups


class JointPackingTests(unittest.TestCase):
    def solve(self, instance, tables, groups):
        from ortools.sat.python import cp_model
        with patch.object(packing, "booking_groups", return_value=groups):
            model, _, variables = packing.build_model(instance, tables, deadline=time.monotonic() + 5)
        solver = cp_model.CpSolver()
        solver.parameters.num_search_workers = 1
        solver.parameters.max_time_in_seconds = 2
        result = solver.Solve(model)
        return result, solver, variables

    def test_hold_columns_cannot_sell_the_same_cells_to_short_and_long(self):
        from ortools.sat.python import cp_model
        status, _, _ = self.solve(*toy())
        self.assertEqual(status, cp_model.INFEASIBLE)

    def test_deck_short_supports_can_carry_long_roof(self):
        from ortools.sat.python import cp_model
        instance, tables, groups = toy(deck=True)
        status, solver, variables = self.solve(instance, tables, groups)
        self.assertIn(status, (cp_model.OPTIMAL, cp_model.FEASIBLE))
        chosen = [key for key, var in variables.items() if solver.BooleanValue(var)]
        self.assertIn((2, 5), chosen)
        self.assertEqual({si for gi, si in chosen if gi < 2}, {0, 2})

    def test_unequal_short_support_roofs_cannot_carry_a_long_box(self):
        from ortools.sat.python import cp_model
        status, _, _ = self.solve(*toy(deck=True, short_heights=(2600, 2900)))
        self.assertEqual(status, cp_model.INFEASIBLE)

    def test_conservative_group_mass_cannot_exceed_either_stack_envelope(self):
        from ortools.sat.python import cp_model
        status, _, _ = self.solve(*toy(deck=True, mass_cap=19000))
        self.assertEqual(status, cp_model.INFEASIBLE)

    def test_static_eligibility_is_not_widened(self):
        from ortools.sat.python import cp_model
        instance, tables, groups = toy(deck=True)
        tables.static[0] = False
        status, _, _ = self.solve(instance, tables, groups)
        self.assertEqual(status, cp_model.INFEASIBLE)

    def test_rounding_never_rounds_group_height_or_mass_down(self):
        box = NS(id="a")
        instance = NS(containers=[box], load_list=["a"], onboard=NS(placements=[]))
        tables = NS(box_index={"a": 0}, rank=np.array([1]), e1_threshold=None,
                    mass=np.array([10000.25]), closes_stack=np.array([False]),
                    height=np.array([2.60001]), share=np.array([10000.25]),
                    static=np.array([[True]]))
        with patch("stowage.solver.twostep.category_of", return_value=(20,)):
            groups = packing.booking_groups(instance, tables)
        self.assertEqual(groups[0].height_mm, 2601)
        self.assertEqual(groups[0].share_kg, 10001)

    def test_installation_restores_only_its_own_wrapper(self):
        module = NS(_preplan=lambda: "original")
        original = module._preplan
        replacement = lambda: "replacement"
        handle = packing.Installation(module, original, replacement)
        module._preplan = replacement
        handle.restore()
        self.assertIs(module._preplan, original)
        subsequent = lambda: "other"
        module._preplan = subsequent
        handle.restore()
        self.assertIs(module._preplan, subsequent)

    def test_native_rejection_never_pushes_a_cp_selected_placement(self):
        instance, tables, groups = toy(deck=True)
        instance.containers = [NS(id=cid, gross_mass_kg=10000.) for cid in ("a", "b", "c")]
        engine = NS(is_legal=lambda action: False)
        with patch("stowage.solver.twostep_adapter.make_planning_engine", return_value=engine), \
             patch("stowage.actions.expander.Expander", return_value=NS()):
            proposal, metrics = packing.replay_assignment(instance, tables, groups,
                [(0, 0), (1, 2), (2, 5)], deadline=time.monotonic() + 5)
        self.assertIsNone(proposal)
        self.assertEqual(metrics["packing_replay_status"], "native_rejected")
        self.assertEqual(metrics["packing_replay_depth"], 0)


if __name__ == "__main__":
    unittest.main()
