"""D56 envelope diagnostic, separate from actual-condition full-checker results.

Only the securing model's motion GM is raised to the declared design GM. The
actual equilibrium, cargo, tank geometry and three D57 draughts are retained.
This is a hypothetical securing-envelope stress test, not a legal ship state
or permission to alter the production loading-condition/checker calculation.
"""
from dataclasses import replace
from unittest.mock import patch


def design_lashing(instance, result):
    from stowage.checker import check_lashing, departure_condition
    from stowage.checker import visibility
    from stowage.generate.background_acceptance import background_instance
    from stowage.model import MovePlan
    from stowage.model.plan import BallastCondition
    from stowage.solver.ballast_arrival import arrival_condition, mid_voyage_condition

    background = background_instance(instance)
    adjustable = {tank.id for tank in instance.vessel.tanks if tank.adjustable}
    plan = MovePlan(id="design-lashing-diagnostic", instance_id=background.id,
        call_index=background.call_index, moves=[], ballast=BallastCondition(
            tank_masses_kg={key: value for key, value in result.tank_masses_kg.items()
                            if key in adjustable}, source="finisher"))
    ship = departure_condition(background, plan)
    conditions = {"departure": (background, ship),
                  "mid_voyage": mid_voyage_condition(background, ship),
                  "arrival": arrival_condition(background, ship)}
    actual_loading_condition = visibility._loading_condition
    design_gm = background.vessel.hull.max_gm_lashing_m
    def at_design_gm(condition_instance, condition_ship):
        return replace(actual_loading_condition(condition_instance, condition_ship), gm_m=design_gm)
    reports = {}
    for phase, (condition_instance, condition_ship) in conditions.items():
        with patch.object(visibility, "_loading_condition", side_effect=at_design_gm):
            report = check_lashing(condition_instance, condition_ship, call_index=condition_instance.call_index)
        reports[phase] = dict(passed=report.ran and not report.error and not report.violations,
                              error=report.error, metrics=report.metrics,
                              reasons=sorted({v.rule_id for v in report.violations}))
    return reports
