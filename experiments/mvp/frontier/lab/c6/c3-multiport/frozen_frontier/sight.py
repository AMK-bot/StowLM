"""Fast physics gate for plans that have no ballast yet: SOLAS V/22 line of sight and lashing.

Plans with little digging tend to put a lot on deck, and the checker then refuses them: the blind sectors that deck stacks
cast must leave at least 5 degrees clear between them (V/22.1.2). Lashing refuses some stack patterns outright. The ballast
finisher can't fix either, since no tank fill moves a stack, so it spends its whole budget failing. This gate runs the
checker's own visibility and lashing parts on the plan's cargo, with the tank fill of a finished plan for the same call
standing in for the trim and draught.

APPROXIMATION: the stand-in trim is the reference plan's. The finisher and full checker still decide.
"""
from __future__ import annotations

from stowage.checker.lashing import check_lashing
from stowage.checker.visibility import check_visibility
from stowage.moves import apply_plan


def sight_clear(instance, plan, reference_tanks) -> tuple[bool, dict]:
    replay = apply_plan(instance, plan)
    ship = replay.final_state.ship
    ship = ship.model_copy(update={'tank_masses_kg': {**ship.tank_masses_kg, **reference_tanks}})
    rules = []
    for part in (check_visibility, check_lashing):
        report = part(instance, ship, call_index=instance.call_index)
        rules += [v.rule_id for v in report.violations if v.severity == 'hard']
        if not report.ran or report.error:
            rules.append(f'{part.__name__}: not run')
    return not rules, dict(rules=sorted(set(rules)))
