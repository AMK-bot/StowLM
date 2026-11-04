"""Compare planner score paths with actual full-checker report metrics.

The hull-free synthetic case tests metric agreement, not physical approval.
Exact terminal acceptance and constructor assignment physics/acceptance are
controlled doubles; the mask, moves, static checker and metric report are real.
"""
from time import monotonic
from types import SimpleNamespace

from stowage.actions import get_action_space
from stowage.actions.expander import Expander
from stowage.actions.space import Place
from stowage.checker import check
from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
from stowage.model import BallastCondition, Instance
from stowage.model.fixtures import build_instance
from stowage.moves import apply_plan
from stowage.rules.catalogue import rule_freeze_v1
from stowage.solver.securing_geometry import level_four_tiebreak


def test_all_solver_level_four_values_match_same_full_checker_plan(monkeypatch):
    from stowage.solver import construct, exact, exact_engine, scalable, twostep, twostep_terminal
    import stowage.checker as checker

    data = build_instance().model_dump()
    box = data["containers"][1]
    box.update(gross_mass_kg=23000., dg=None, special_stowage=[], oog={"over_height_m": .2})
    data.update(containers=[box], load_list=[box["id"]], discharge_list=[])
    data["vessel"].update(hull=None, bridge=None, fixed_loads=[])
    data["yard"]["stacks"] = [dict(key="A-001-01", length_ft=20, boxes=[box["id"]])]
    config = FeasibilityConfig(extra={"global_screen": 0})
    with rule_freeze_v1():
        instance = Instance.model_validate(data)
        space = get_action_space()
        action = Place(box["id"], "0010182")
        plan = space.expand(instance, [action])
        replay = apply_plan(instance, plan)
        assert replay.legal
        report = check(instance, plan)
        values = report.parts["rules"].metrics
        expected = (values["heavy_on_deck_count"], values["killed_slots"])
        assert all(value > 0 for value in expected)  # both fields are exercised
        assert level_four_tiebreak(instance, replay.final_state.ship) == expected

        expanded = Expander(instance)
        expanded.step(action)
        assert scalable._soft_rank(instance, expanded) == expected
        carrier = twostep._carrier_action_score(instance, Expander(instance), (), action,
                                                context=twostep._slot_context(instance, ()))
        assert carrier[1:3] == expected

        scaled = scalable.complete(instance, [action], time_limit_s=2., config=scalable.ScalableConfig(
            max_neighborhoods=0, feasibility_config=config))
        assert scaled.status == "ok", scaled.message
        assert scaled.plan.moves == plan.moves
        assert (scaled.metrics["heavy_on_deck_count"], scaled.metrics["killed_slots"]) == expected

        prestow = twostep.PreStowPlan(slots=(twostep.SlotCategory(action.slot,
            twostep.category_of(instance.containers[0], instance), action.box),), actions=(action,))
        terminal, metrics, status, message = twostep_terminal.terminal_plan(instance, prestow,
            deadline=monotonic()+2., seed=0, engine_factory=make_feasibility_engine,
            feasibility_config=config)
        assert status == "ok", message
        assert terminal.moves == plan.moves
        assert (metrics["heavy_on_deck_count"], metrics["killed_slots"]) == expected

        class FixedMask:
            def __init__(self, backend=None):
                self.backend = backend or make_feasibility_engine(instance, space, config=config)
                self.instance, self.space = instance, space

            def copy(self):
                return FixedMask(self.backend.copy())

            def legal_actions(self):
                return [action] if self.backend.is_legal(action) else []

            def push(self, choice):
                self.backend.push(choice)

            def is_complete(self):
                return self.backend.is_complete()

            def dead_end(self):
                return self.backend.dead_end()

        monkeypatch.setattr(exact_engine, "make_feasibility_engine", lambda *args, **kw: FixedMask())
        monkeypatch.setattr(exact, "_validate_terminal", lambda i, p, ship, **kw:
            ("yes", BallastCondition(source="planner", tank_masses_kg={}), p.provenance))
        solved = exact_engine.solve_engine(instance, time_limit_s=2., max_nodes=20,
                                           seed=0, num_workers=1, feasibility_config=config)
        assert solved.plan is not None
        assert solved.plan.moves == plan.moves
        assert (solved.metrics["heavy_on_deck_count"], solved.metrics["killed_slots"]) == expected

        build = construct._build_assignment_model

        def fixed_assignment(inst, **kwargs):
            model = build(inst, physics=False, **kwargs)
            model.solver.Add(model.x[action.box, action.slot] == 1)
            model.physics = SimpleNamespace(row_names=(), approximations=(), certificate_blockers=(),
                ballast_condition=lambda: BallastCondition(source="planner", tank_masses_kg={}))
            return model

        monkeypatch.setattr(construct, "_build_assignment_model", fixed_assignment)
        # Only numerical admission is controlled. Every metric below comes
        # from the actual full report for the identical replayed cargo plan.
        monkeypatch.setattr(checker, "check", lambda *args, **kwargs: report.model_copy(update={"passed": True}))
        built = construct.construct(instance, time_limit_s=3., max_rounds=1, feasibility_config=config)
        assert built.status == "ok", built.stats
        assert built.plan.moves == plan.moves
        assert tuple(built.stats["best_objective"][1:]) == expected
